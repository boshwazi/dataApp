from test_report import CUSTOMERS, Project, clean_env, ok  # noqa: F401  (clean_env is an autouse fixture)

TICKETS = "ticket,customer,minutes\nT1,C1,30\nT2,C1,10\nT3,C2,20\n"


def support_dataset(p):
    """A second published dataset sharing the customers table, with its own Orders measure."""
    sid = p.api.post("/api/datasets", json={"name": "Support"}).json()["id"]
    base = f"/api/datasets/{sid}"
    for name, text in (("tickets.csv", TICKETS), ("customers.csv", CUSTOMERS)):
        p.api.post(f"{base}/sources/upload", files=[("files", (name, text.encode()))])
    tickets = next(q["id"] for q in p.api.get(f"{base}/transform").json()["queries"] if q["name"] == "tickets")
    p.api.post(f"{base}/transform/queries/{tickets}/steps", json={"op": "change_type", "params": {"column": "minutes", "to": "integer"}})
    ok(p.api.post(f"{base}/model/relationships", json={"from_table": "tickets", "from_column": "customer",
                                                       "to_table": "customers", "to_column": "customer"}))
    for measure in ({"name": "Orders", "kind": "aggregate", "table": "tickets", "aggregation": "count"},
                    {"name": "Minutes", "kind": "aggregate", "table": "tickets", "aggregation": "sum", "expression": "[minutes]"},
                    {"name": "Minutes per order", "kind": "formula", "expression": "[Minutes] / [Orders]"}):
        ok(p.api.post(f"{base}/model/measures", json=measure))
    ok(p.api.post(f"{base}/publish"))
    return sid


def test_merging_models_shares_dimensions_and_renames_clashing_measures(tmp_path):
    p = Project(tmp_path)
    support = support_dataset(p)
    body = ok(p.api.post("/api/semantic-models/merge", json={"name": "Customer 360", "dataset_ids": [p.id, support]}))
    merged = body["dataset"]
    assert merged["semantic"]["how"] == "merge" and [d["name"] for d in merged["semantic"]["from"]] == ["R", "Support"]
    assert sorted(s["table"] for s in merged["sources"]) == ["customers", "sales", "tickets"]  # customers kept once
    assert all(s["origin"]["kind"] == "dataset" for s in merged["sources"])
    assert body["notes"]["conformed"] == ["customers is in R and Support with the same columns: kept once"]
    assert body["notes"]["renamed"] == ["Orders from Support is now Orders (Support)"]

    state = ok(p.api.get(f"/api/datasets/{merged['id']}/model"))
    values = {m["name"]: m["value"] for m in state["measures"]}
    assert values["Revenue"] == 64.0 and values["Orders"] == 5 and values["Orders (Support)"] == 3
    assert values["Minutes per order"] == 20.0  # the formula follows the rename
    assert {(r["from_name"], r["to_name"]) for r in state["relationships"]} == {
        ("sales", "customers"), ("sales", "Date"), ("tickets", "customers")}
    assert all(not m["error"] for m in state["measures"])

    # Tickets and sales now meet on customers: one question can use both.
    ok(p.api.post(f"/api/datasets/{merged['id']}/publish"))
    dash = ok(p.api.post("/api/dashboards", json={"name": "360", "dataset_id": merged["id"]}))
    page = ok(p.api.post(f"/api/dashboards/{dash['id']}/pages", json={"title": "Customers"}))["pages"][-1]["id"]
    table = ok(p.api.post(f"/api/dashboards/{dash['id']}/pages/{page}/visuals", json={
        "type": "table", "title": "By segment", "measures": ["Revenue", "Orders (Support)"],
        "group_by": [{"table": "customers", "column": "segment"}]}))
    rows = table["pages"][-1]["visuals"][-1]["data"]["rows"]
    assert sorted(rows) == [["Online", 14.0, 1], ["Retail", 50.0, 2]]


def test_building_from_picked_tables_keeps_only_what_they_support(tmp_path):
    p = Project(tmp_path)
    support = support_dataset(p)
    body = ok(p.api.post("/api/semantic-models", json={"name": "Tickets and sales", "tables": [
        {"dataset_id": p.id, "table": "sales"}, {"dataset_id": support, "table": "tickets"}]}))
    state = ok(p.api.get(f"/api/datasets/{body['dataset']['id']}/model"))
    # Neither customers table was picked, so the relationships to it stay behind; the date table comes along.
    assert {(r["from_name"], r["to_name"]) for r in state["relationships"]} == {("sales", "Date")}
    assert {m["name"] for m in state["measures"]} == {"Revenue", "Orders", "Average order", "Orders (Support)",
                                                      "Minutes", "Minutes per order"}


def test_semantic_models_need_published_datasets(tmp_path):
    p = Project(tmp_path)
    draft = p.api.post("/api/datasets", json={"name": "Draft only"}).json()["id"]
    response = p.api.post("/api/semantic-models/merge", json={"name": "X", "dataset_ids": [p.id, draft]})
    assert response.status_code == 400 and "isn't published yet" in response.json()["detail"]
    assert p.api.post("/api/semantic-models/merge", json={"name": "X", "dataset_ids": [p.id]}).status_code == 400
    response = p.api.post("/api/semantic-models", json={"name": "X", "tables": [{"dataset_id": p.id, "table": "nope"}]})
    assert response.status_code == 400 and "no usable table called nope" in response.json()["detail"]
    assert sorted(d["name"] for d in p.api.get("/api/datasets").json()) == ["Draft only", "R"]  # nothing half-built left
