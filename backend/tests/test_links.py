from test_report import SEGMENT, Project, clean_env, ok  # noqa: F401  (clean_env is an autouse fixture)


def new_dataset(p, name="Second"):
    return p.api.post("/api/datasets", json={"name": name}).json()["id"]


def link(p, target, table):
    return p.api.post(f"/api/datasets/{target}/sources/link", json={"dataset_id": p.id, "table": table})


def test_linking_copies_a_published_clean_table_with_its_types(tmp_path):
    p = Project(tmp_path)
    other = new_dataset(p)
    catalogue = ok(p.api.get(f"/api/datasets/{other}/link-catalogue"))
    assert catalogue[0]["dataset_name"] == "R" and {t["name"] for t in catalogue[0]["tables"]} == {"sales", "customers"}

    added = ok(link(p, other, "sales"))["added"][0]
    assert added["origin"]["kind"] == "dataset" and added["origin"]["version"] == 1
    assert added["origin"]["label"] == "sales from R"
    query = ok(p.api.get(f"/api/datasets/{other}/transform"))["queries"][0]
    types = {c["name"]: c["type"] for c in query["columns"]}
    assert types["day"] == "date" and types["qty"] == "integer" and types["price"] == "decimal"
    assert query["rows"] == 5


def test_linking_needs_a_published_other_dataset(tmp_path):
    p = Project(tmp_path)
    other = new_dataset(p)
    assert "can't link its own" in p.api.post(f"/api/datasets/{p.id}/sources/link",
                                              json={"dataset_id": p.id, "table": "sales"}).json()["detail"]
    third = new_dataset(p, "Third")
    response = p.api.post(f"/api/datasets/{other}/sources/link", json={"dataset_id": third, "table": "x"})
    assert response.status_code == 400 and "no published version" in response.json()["detail"]
    assert "no usable table" in link(p, other, "nope").json()["detail"]


def test_linked_tables_show_updates_and_pull_the_new_version(tmp_path):
    p = Project(tmp_path)
    other = new_dataset(p)
    ok(link(p, other, "sales"))
    sales = next(q for q in ok(p.api.get(f"{p.dataset}/transform"))["queries"] if q["name"] == "sales")
    ok(p.api.post(f"{p.dataset}/transform/queries/{sales['id']}/steps",
                  json={"op": "filter_rows", "params": {"column": "qty", "operator": "at_least", "value": 2}}))
    p.publish()

    source = ok(p.api.get(f"/api/datasets/{other}"))["sources"][0]
    assert source["update_available"] is True
    entry = ok(p.api.post(f"/api/datasets/{other}/refresh"))["entry"]
    assert entry["sources"] == [{"table": "sales", "rows_before": 5, "rows_after": 2}]
    source = ok(p.api.get(f"/api/datasets/{other}"))["sources"][0]
    assert source["update_available"] is False and source["origin"]["version"] == 2
    # Linked tables can follow their source on a schedule.
    assert ok(p.api.put(f"/api/datasets/{other}/refresh", json={"schedule": "daily"}))["settings"]["schedule"] == "daily"


def test_certified_datasets_come_first_and_show_who_links_them(tmp_path):
    p = Project(tmp_path)
    second = new_dataset(p, "A second")
    p.api.post(f"/api/datasets/{second}/sources/upload", files=[("files", ("t.csv", b"a\n1\n"))])
    ok(p.api.post(f"/api/datasets/{second}/publish"))
    third = new_dataset(p, "Consumer")
    assert ok(p.api.get(f"/api/datasets/{third}/link-catalogue"))[0]["dataset_name"] == "A second"

    body = ok(p.api.post(f"{p.dataset}/certify", json={"certified": True}))
    assert body["certified"] is True
    catalogue = ok(p.api.get(f"/api/datasets/{third}/link-catalogue"))
    assert [(d["dataset_name"], d["certified"]) for d in catalogue] == [("R", True), ("A second", False)]

    ok(link(p, third, "customers"))
    assert ok(p.api.get(p.dataset))["linked_by"] == [{"id": third, "name": "Consumer", "tables": ["customers"]}]


def test_measures_from_two_fact_tables_combine_by_a_shared_dimension(tmp_path):
    p = Project(tmp_path)
    p.api.post(f"{p.dataset}/sources/upload", files=[("files", ("targets.csv", b"customer,target\nC1,30\nC2,5\nC3,10\n"))])
    targets = next(q for q in ok(p.api.get(f"{p.dataset}/transform"))["queries"] if q["name"] == "targets")
    ok(p.api.post(f"{p.dataset}/transform/queries/{targets['id']}/steps",
                  json={"op": "change_type", "params": {"column": "target", "to": "integer"}}))
    ok(p.api.post(f"{p.dataset}/model/relationships", json={"from_table": "targets", "from_column": "customer",
                                                            "to_table": "customers", "to_column": "customer"}))
    ok(p.api.post(f"{p.dataset}/model/measures", json={"name": "Target", "kind": "aggregate", "table": "targets",
                                                       "aggregation": "sum", "expression": "[target]"}))
    ok(p.api.post(f"{p.dataset}/model/measures", json={"name": "Vs target", "kind": "formula",
                                                       "expression": "[Revenue] / [Target]"}))
    result = ok(p.api.post(f"{p.dataset}/model/evaluate", json={"measures": ["Revenue", "Target", "Vs target"],
                                                                "group_by": SEGMENT}))
    rows = {r[0]: r[1:] for r in result["rows"]}
    assert rows["Retail"] == [50.0, 30, 50.0 / 30]
    assert rows["Online"] == [10.0 + 4.0, 15, 14.0 / 15]


def test_lineage_runs_from_sources_to_visuals(tmp_path):
    p = Project(tmp_path)
    page = p.page()
    ok(p.visual(page, type="bar", title="Revenue by segment", measures=["Revenue"], group_by=SEGMENT))
    graph = ok(p.api.get(f"{p.dataset}/lineage"))
    nodes = {n["id"]: n for n in graph["nodes"]}
    edges = {(e["from"], e["to"], e["kind"]) for e in graph["edges"]}
    sales = next(n for n in graph["nodes"] if n["kind"] == "table" and n["label"] == "sales")
    customers = next(n for n in graph["nodes"] if n["kind"] == "table" and n["label"] == "customers")
    source = next(n for n in graph["nodes"] if n["kind"] == "source" and n["label"] == "sales")
    visual = next(n for n in graph["nodes"] if n["kind"] == "visual")
    assert (source["id"], sales["id"], "uses") in edges
    assert (sales["id"], "measure:Revenue", "uses") in edges
    assert ("measure:Revenue", "measure:Average order", "uses") in edges
    assert ("measure:Revenue", visual["id"], "uses") in edges and (customers["id"], visual["id"], "split") in edges
    assert visual["sub"] == "Sales › Sales overview (draft)" and nodes["measure:Revenue"]["column"] == 2
