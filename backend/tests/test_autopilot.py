import json
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import ENV_VARS


SALES = (
    "order,day,customer,qty,price\n"
    "1,2024-01-05,C1,2,10\n"
    "2,2024-02-10,C2,1,10\n"
    "3,2025-01-20,C1,5,4\n"
)
CUSTOMERS = "customer,segment\nC1,Retail\nC2,Online\n"

DESCRIBE = {"summary": "Sales orders.", "grain": "one order", "table_role": "fact", "columns": []}
CLEAN = {"summary": "Types fixed.", "steps": [
    {"op": "change_type", "params": {"column": "day", "to": "date"}, "reason": "dates"},
    {"op": "change_type", "params": {"column": "qty", "to": "integer"}, "reason": "counts"},
    {"op": "change_type", "params": {"column": "price", "to": "decimal"}, "reason": "money"},
]}
MODEL = {
    "summary": "Sales with customers.",
    "date_table": {"create": True, "link": [{"table": "sales", "column": "day"}], "reason": "time"},
    "relationships": [{"from_table": "sales", "from_column": "customer", "to_table": "customers", "to_column": "customer"}],
    "measures": [{"name": "Revenue", "kind": "aggregate", "table": "sales", "aggregation": "sum", "expression": "[qty] * [price]"}],
}
DESIGN = {"summary": "One page.", "pages": [{"title": "Sales", "visuals": [
    {"type": "kpi", "title": "Revenue", "measures": ["Revenue"], "kpi": {"direction": "higher_is_better"}},
    {"type": "bar", "title": "Revenue by segment", "measures": ["Revenue"],
     "group_by": [{"table": "customers", "column": "segment"}]},
]}]}


class ScriptedAI:
    """Answers each agent by what its system prompt says it is."""

    def __init__(self, model=MODEL):
        self.model = model
        self.agents = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        system, prompt = kwargs["messages"][0]["content"], kwargs["messages"][-1]["content"]
        if "Write the insights" in prompt:
            pages = json.loads(prompt.split("data:\n", 1)[1].rsplit("\n\nWrite", 1)[0])
            agent, answer = "insights", {"visuals": [], "pages": [{"id": p["id"], "summary": "Sales."} for p in pages]}
        else:
            agent, answer = next((name, answer) for name, answer in (
                ("Get Data agent", DESCRIBE), ("Transform agent", CLEAN), ("Model agent", self.model),
                ("Report agent", DESIGN)) if name in system)
        self.agents.append(agent)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(answer), tool_calls=None))])


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in ENV_VARS.values():
        monkeypatch.delenv(var, raising=False)


def setup(tmp_path, ai, key=True):
    api = TestClient(create_app(data_dir=tmp_path, client_factory=lambda s: ai))
    pid = api.post("/api/datasets", json={"name": "Shop"}).json()["id"]
    for name, text in (("sales.csv", SALES), ("customers.csv", CUSTOMERS)):
        api.post(f"/api/datasets/{pid}/sources/upload", files=[("files", (name, text.encode()))])
    if key:
        api.put("/api/settings", json={"api_key": "sk-secret-value-1234"})
    return api, f"/api/datasets/{pid}"


def finished(api, base):
    for _ in range(200):
        run = api.get(f"{base}/autopilot").json()["run"]
        if run["status"] != "running":
            return run
        time.sleep(0.05)
    raise AssertionError("Autopilot did not finish")


def test_autopilot_goes_from_data_to_a_draft_dashboard(tmp_path):
    ai = ScriptedAI()
    api, base = setup(tmp_path, ai)
    started = api.post(f"{base}/autopilot", json={})
    assert started.status_code == 200 and started.json()["status"] == "running"
    run = finished(api, base)

    assert run["status"] == "done", (run["error"], run["steps"])
    assert [s["status"] for s in run["steps"]] == ["done"] * 5
    assert "relationship" in run["steps"][2]["detail"] and "kept as a draft" in run["steps"][4]["detail"]
    assert "3 suggestions didn't pass the checks" in run["steps"][1]["detail"]  # sales steps tried on customers
    assert ai.agents[:2] == ["Get Data agent", "Get Data agent"] and "Report agent" in ai.agents

    # Everything was accepted and is marked as Autopilot's.
    steps = [s for q in api.get(f"{base}/transform").json()["queries"] for s in q["steps"]]
    assert steps and all(s["status"] == "Approved" and s["by"] == "autopilot" for s in steps)
    measures = api.get(f"{base}/model").json()["measures"]
    assert [(m["name"], m["status"], m["by"]) for m in measures] == [("Revenue", "Approved", "autopilot")]
    assert api.get(base).json()["status"]["version"] == 1

    dash = api.get(f"/api/dashboards/{run['dashboard_id']}").json()
    assert dash["name"] == "Shop dashboard" and not dash["published"]
    visuals = dash["pages"][0]["visuals"]
    assert [v["type"] for v in visuals] == ["kpi", "bar"] and all(v["by"] == "autopilot" for v in visuals)


def test_autopilot_stops_with_a_plain_message_and_where_to_fix_it(tmp_path):
    api, base = setup(tmp_path, ScriptedAI(model={**MODEL, "measures": []}))
    api.post(f"{base}/autopilot", json={"dashboard_name": "Mine"})
    run = finished(api, base)
    assert run["status"] == "failed" and run["fix_at"] == "model"
    assert [s["status"] for s in run["steps"]] == ["done", "done", "failed", "pending", "pending"]
    assert "anything to measure" in run["error"]


def test_autopilot_needs_a_key_and_data(tmp_path):
    api, base = setup(tmp_path, ScriptedAI(), key=False)
    response = api.post(f"{base}/autopilot", json={})
    assert response.status_code == 400 and "API key" in response.json()["detail"]
    assert api.get(f"{base}/autopilot").json() == {"run": None}
