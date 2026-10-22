"""Row-level security, sharing, groups and the audit log: viewers see only what they're meant to."""

import pytest
from fastapi.testclient import TestClient
from test_workspaces import CONFIG, Person, clean_env, invite, ok  # noqa: F401  (clean_env is an autouse fixture)

from app import model, security
from app.main import create_app

SALES = ("order,region,rep,qty\n1,North,vic@example.com,2\n2,North,pat@example.com,1\n3,South,pat@example.com,4\n"
         "4,South,sam@example.com,3\n5,East,sam@example.com,5\n")
TARGETS = "region,target\nNorth,10\nSouth,20\nEast,30\n"


@pytest.fixture
def api(tmp_path):
    return TestClient(create_app(data_dir=tmp_path, client_factory=lambda s: None, auth=CONFIG))


@pytest.fixture
def shop(api):
    """Ana's workspace: sales by region and rep, a separate targets table, a published dashboard, three viewers."""
    ana = Person(api, "ana")
    ana.workspace = ok(ana.post("/api/workspaces", json={"name": "Ana Trading"}))["id"]
    dataset = ok(ana.post("/api/datasets", json={"name": "Sales"}))["id"]
    base = f"/api/datasets/{dataset}"
    for name, text in (("sales.csv", SALES), ("targets.csv", TARGETS)):
        ok(ana.post(f"{base}/sources/upload", files=[("files", (name, text.encode()))]))
    sales = next(q["id"] for q in ok(ana.get(f"{base}/transform"))["queries"] if q["name"] == "sales")
    ok(ana.post(f"{base}/transform/queries/{sales}/steps", json={"op": "change_type", "params": {"column": "qty", "to": "integer"}}))
    targets = next(q["id"] for q in ok(ana.get(f"{base}/transform"))["queries"] if q["name"] == "targets")
    ok(ana.post(f"{base}/transform/queries/{targets}/steps", json={"op": "change_type", "params": {"column": "target", "to": "integer"}}))
    for measure in ({"name": "Orders", "kind": "aggregate", "table": "sales", "aggregation": "count"},
                    {"name": "Qty", "kind": "aggregate", "table": "sales", "aggregation": "sum", "expression": "[qty]"},
                    {"name": "Target", "kind": "aggregate", "table": "targets", "aggregation": "sum", "expression": "[target]"}):
        ok(ana.post(f"{base}/model/measures", json=measure))
    ok(ana.post(f"{base}/publish"))
    dash = ok(ana.post("/api/dashboards", json={"name": "Sales board", "dataset_id": dataset}))["id"]
    page = ok(ana.post(f"/api/dashboards/{dash}/pages", json={"title": "Overview"}))["pages"][-1]["id"]
    ok(ana.post(f"/api/dashboards/{dash}/pages/{page}/visuals", json={
        "type": "table", "title": "By region", "measures": ["Orders", "Qty"], "group_by": [{"table": "sales", "column": "region"}]}))
    ok(ana.post(f"/api/dashboards/{dash}/publish"))
    viewers = {}
    for name in ("vic", "pat", "zed"):
        Person(api, name).post(f"/api/invites/{invite(ana, f'{name}@example.com', 'viewer')}/accept")
        viewers[name] = Person(api, name, ana.workspace)
    return ana, viewers, dataset, dash, page


NORTH = {"name": "North", "filters": [{"table": "sales", "column": "region", "operator": "in", "values": ["North"]}],
         "members": {"people": ["vic@example.com"]}}
REPS = {"name": "Reps", "per_person": {"table": "sales", "column": "rep"}, "members": {"people": ["pat@example.com"]}}


def rows(person, dash, **params):
    return ok(person.get(f"/api/dashboards/{dash}", params=params))["pages"][0]["visuals"][0]["data"]["rows"]


def test_viewers_see_only_their_rows_and_viewers_without_a_role_see_none(shop):
    ana, viewers, dataset, dash, page = shop
    everything = rows(ana, dash)
    assert sorted(everything) == [["East", 1, 5], ["North", 2, 3], ["South", 2, 7]]
    roles = ok(ana.call("PUT", f"/api/datasets/{dataset}/security", json={"roles": [NORTH, REPS]}))["roles"]
    assert roles[0]["reach"] == {"restricted": ["sales"], "not_restricted": ["targets"]}  # said, not hidden

    assert rows(viewers["vic"], dash) == [["North", 2, 3]]
    assert sorted(rows(viewers["pat"], dash)) == [["North", 1, 1], ["South", 1, 4]]  # pat's own orders
    blocked = ok(viewers["zed"].get(f"/api/dashboards/{dash}"))["pages"][0]["visuals"][0]
    assert blocked["data"] is None and "don't have access" in blocked["error"]
    assert sorted(rows(ana, dash)) == sorted(everything)  # creators and admins see everything

    # Slicers offer only the asker's values; restricted viewers don't get insights written about all the data.
    values = ok(viewers["vic"].get(f"/api/dashboards/{dash}/values", params={"table": "sales", "column": "region"}))
    assert values["values"] == ["North"]
    assert ok(viewers["vic"].get(f"/api/dashboards/{dash}"))["restricted"] is True
    assert viewers["zed"].get(f"/api/dashboards/{dash}/values", params={"table": "sales", "column": "region"}).status_code == 400

    # Creators can check what a role sees.
    north_id = roles[0]["id"]
    assert rows(ana, dash, as_role=north_id) == [["North", 2, 3]]
    reps_id = roles[1]["id"]
    assert rows(ana, dash, as_role=reps_id, as_email="sam@example.com") == [["East", 1, 5], ["South", 1, 3]]
    assert viewers["vic"].get(f"/api/dashboards/{dash}", params={"as_role": reps_id}).status_code == 403


def test_role_filters_hold_where_page_filters_are_left_out(shop):
    project = {"id": "x", "workspace_id": "w", "name": "x", "sources": [], "model": {}}
    project[model.ROLE_FILTERS] = [{"deny": True}]
    with pytest.raises(model.ModelError, match="don't have access"):
        model.evaluate(None, project, ["Orders"], page_filters="none")


def test_roles_are_checked(shop):
    ana, viewers, dataset, dash, page = shop
    url = f"/api/datasets/{dataset}/security"
    bad = [
        ([{"name": "X", "filters": [{"table": "sales", "column": "nope", "operator": "in", "values": ["a"]}]}], "nope"),
        ([{"name": "X", "per_person": {"table": "sales", "column": "qty"}}], "must be a text column"),
        ([{"name": "X"}], "give the role a filter"),
        ([NORTH, {**REPS, "members": {"people": ["vic@example.com"]}}], "one role per dataset"),
        ([NORTH, NORTH], "two roles called North"),
    ]
    for roles, message in bad:
        response = ana.call("PUT", url, json={"roles": roles})
        assert response.status_code == 400 and message in response.json()["detail"], message
    assert viewers["vic"].call("PUT", url, json={"roles": [NORTH]}).status_code == 403  # viewers never edit roles


def test_dashboards_can_be_shared_with_chosen_people_and_groups(shop):
    ana, viewers, dataset, dash, page = shop
    groups = ok(ana.call("PUT", "/api/workspaces/current/groups",
                         json={"groups": [{"name": "Field team", "members": ["PAT@example.com"]}]}))["groups"]
    assert groups[0]["members"] == ["pat@example.com"]
    shared = ok(ana.call("PUT", f"/api/dashboards/{dash}/sharing",
                         json={"mode": "people", "people": ["vic@example.com"], "groups": [groups[0]["id"]]}))
    assert shared["sharing"]["people"] == ["vic@example.com"] and shared["owner"] == "ana@example.com"

    for name, sees in (("vic", True), ("pat", True), ("zed", False)):
        listed = [d["id"] for d in ok(viewers[name].get("/api/dashboards"))]
        assert (dash in listed) is sees, name
        assert (viewers[name].get(f"/api/dashboards/{dash}").status_code == 200) is sees, name
    assert viewers["zed"].get(f"/api/dashboards/{dash}/values", params={"table": "sales", "column": "region"}).status_code == 404
    assert viewers["vic"].call("PUT", "/api/workspaces/current/groups", json={"groups": []}).status_code == 403

    ok(ana.call("PUT", f"/api/dashboards/{dash}/sharing", json={"mode": "workspace"}))
    assert dash in [d["id"] for d in ok(viewers["zed"].get("/api/dashboards"))]

    audit = ok(ana.get("/api/workspaces/current/audit"))["entries"]
    actions = [e["action"] for e in audit]
    assert actions[:3] == ["sharing", "sharing", "groups"] and "members" in actions and "publish" in actions
    assert audit[0]["by"] == "ana@example.com" and "whole workspace" in audit[0]["detail"]
    assert viewers["vic"].get("/api/workspaces/current/audit").status_code == 403


def test_power_bi_role_filters_are_read_in_their_simple_shapes():
    assert security.from_dax("sales", '[Region] = "North"') == {
        "filter": {"table": "sales", "column": "Region", "operator": "in", "values": ["North"]}}
    assert security.from_dax("sales", "'Sales'[Region] IN {\"North\", \"South\"}")["filter"]["values"] == ["North", "South"]
    assert security.from_dax("sales", "[Rep Email] = USERPRINCIPALNAME()") == {
        "per_person": {"table": "sales", "column": "Rep Email"}}
    assert security.from_dax("sales", "[Amount] > 100 && [Region] = \"North\"") is None


def test_power_bi_roles_are_imported_without_members(tmp_path):
    from types import SimpleNamespace

    from test_report import Project

    from app import pbi_import
    from app.projects import ProjectStore
    p = Project(tmp_path)
    store = ProjectStore(tmp_path / "workspaces" / "local" / "datasets")
    pbi = SimpleNamespace(roles=[
        {"role": "Retail team", "table": "Customers", "filter": '[segment] = "Retail"'},
        {"role": "Own orders", "table": "Customers", "filter": "[customer] = USERPRINCIPALNAME()"},
        {"role": "Complicated", "table": "Customers", "filter": "LEN([segment]) > 3"},
    ])
    report = {"notes": []}
    with store.edit(p.id) as project, store.connect(p.id) as con:
        pbi_import.import_roles(con, project, pbi, {"Customers": "customers"}, report)
        roles = project["security"]["roles"]
    assert [r["name"] for r in roles] == ["Retail team", "Own orders"]
    assert roles[0]["filters"][0]["values"] == ["Retail"] and roles[1]["per_person"]["column"] == "customer"
    assert all(r["members"] == {"people": [], "groups": []} for r in roles)
    assert any("Complicated" in n and "can't read" in n for n in report["notes"])
