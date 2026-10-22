"""Extending a dataset: inherit a published dataset's tables and model, add your own, stay in step."""

from test_refresh import NEXT_SALES, replace, wait_for
from test_report import Project, clean_env, ok  # noqa: F401  (clean_env is an autouse fixture)

TARGETS = "segment,target\nRetail,40\nOnline,30\n"


def test_an_extension_inherits_read_only_and_adds_its_own(tmp_path):
    p = Project(tmp_path)
    mine = ok(p.api.post(f"{p.dataset}/extend", json={"name": "Sales with targets"}))
    base = f"/api/datasets/{mine['id']}"
    assert mine["extends"]["name"] == "R" and all(s["origin"]["kind"] == "dataset" for s in mine["sources"])

    state = ok(p.api.get(f"{base}/model"))
    revenue = next(m for m in state["measures"] if m["name"] == "Revenue")
    assert revenue["inherited"] and revenue["value"] == 64.0
    assert all(r["inherited"] for r in state["relationships"])

    # Inherited items change in the original, not here.
    response = p.api.put(f"{base}/model/measures/{revenue['id']}", json={**revenue, "name": "Takings"})
    assert response.status_code == 400 and "comes from R" in response.json()["detail"]
    assert p.api.delete(f"{base}/model/measures/{revenue['id']}").status_code == 400
    assert p.api.delete(f"{base}/model/relationships/{state['relationships'][0]['id']}").status_code == 400
    sales = next(s for s in mine["sources"] if s["table"] == "sales")
    assert p.api.delete(f"{base}/sources/{sales['id']}").status_code == 400

    # Your own data and measures sit on top.
    p.api.post(f"{base}/sources/upload", files=[("files", ("targets.csv", TARGETS.encode()))])
    targets = next(q["id"] for q in ok(p.api.get(f"{base}/transform"))["queries"] if q["name"] == "targets")
    ok(p.api.post(f"{base}/transform/queries/{targets}/steps", json={"op": "change_type", "params": {"column": "target", "to": "integer"}}))
    ok(p.api.post(f"{base}/model/measures", json={"name": "Target", "kind": "aggregate", "table": "targets",
                                                  "aggregation": "sum", "expression": "[target]"}))
    own = next(m for m in ok(p.api.get(f"{base}/model"))["measures"] if m["name"] == "Target")
    assert not own.get("inherited") and own["value"] == 70

    # The original changes and publishes: new data and the new measure arrive; your own stay.
    ok(replace(p, "sales", "sales.csv", NEXT_SALES))
    ok(p.api.post(f"{p.dataset}/model/measures", json={"name": "Big orders", "kind": "aggregate", "table": "sales",
                                                       "aggregation": "count", "filters": [{"column": "qty", "operator": "at_least", "value": 5}]}))
    p.publish()

    def synced():
        measures = {m["name"]: m for m in ok(p.api.get(f"{base}/model"))["measures"]}
        return measures if "Big orders" in measures else None
    measures = wait_for(synced)
    assert measures["Big orders"]["inherited"] and measures["Big orders"]["value"] == 2
    assert measures["Revenue"]["value"] == 164.0 and not measures["Target"].get("inherited")
    assert ok(p.api.get(base))["extends"]["version"] == 2
