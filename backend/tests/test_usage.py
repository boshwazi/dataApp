"""AI usage: every AI call is recorded against its workspace, and admins read it by month, person and feature."""

import json
import threading
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import usage, workspaces
from app.main import create_app
from app.settings import ENV_VARS

ANSWER = {"table_role": "fact", "summary": "Orders.", "grain": "one order", "columns": {}}


class FakeAI:
    """Answers with fixed JSON and reports token usage like the OpenAI API (or none, when `tokens` is None)."""

    def __init__(self, tokens=(1200, 300, 200)):
        self.tokens = tokens
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        message = SimpleNamespace(content=json.dumps(ANSWER), tool_calls=None)
        response = SimpleNamespace(choices=[SimpleNamespace(message=message)], model="stand-in-model")
        if self.tokens is not None:
            prompt, completion, cached = self.tokens
            response.usage = SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion,
                                             prompt_tokens_details=SimpleNamespace(cached_tokens=cached))
        return response


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in ENV_VARS.values():
        monkeypatch.delenv(var, raising=False)


def describe_once(tmp_path, ai):
    api = TestClient(create_app(data_dir=tmp_path, client_factory=lambda s: ai))
    api.put("/api/settings", json={"api_key": "sk-secret-value-1234"})
    pid = api.post("/api/datasets", json={"name": "Demo"}).json()["id"]
    added = api.post(f"/api/datasets/{pid}/sources/upload", files=[("files", ("orders.csv", b"id,amount\n1,10\n"))])
    sid = added.json()["added"][0]["id"]
    assert api.post(f"/api/datasets/{pid}/sources/{sid}/describe").status_code == 200
    return api


def test_every_ai_call_is_recorded_with_its_feature_and_person(tmp_path):
    api = describe_once(tmp_path, FakeAI())
    got = api.get("/api/workspaces/current/usage").json()
    assert got["total"]["calls"] == 1
    assert got["total"]["prompt_tokens"] == 1200 and got["total"]["completion_tokens"] == 300
    assert got["total"]["credits"] == round((1200 * usage.RATES["prompt"] + 300 * usage.RATES["completion"]) / 1000, 1)
    assert [r["feature"] for r in got["by_feature"]] == ["Describing tables"]
    assert [r["person"] for r in got["by_person"]] == ["you@local"]
    entry = json.loads(next((tmp_path / "workspaces" / "local" / "usage").glob("*.jsonl")).read_text().splitlines()[0])
    assert entry["model"] and entry["cached_tokens"] == 200 and entry["ms"] >= 0


def test_a_reply_without_usage_is_counted_as_a_call_with_no_tokens(tmp_path):
    api = describe_once(tmp_path, FakeAI(tokens=None))
    got = api.get("/api/workspaces/current/usage").json()
    assert got["total"]["calls"] == 1 and got["total"]["credits"] == 0


def test_another_month_is_empty_and_a_bad_month_is_refused(tmp_path):
    api = describe_once(tmp_path, FakeAI())
    assert api.get("/api/workspaces/current/usage", params={"month": "2020-01"}).json()["total"]["calls"] == 0
    assert api.get("/api/workspaces/current/usage", params={"month": "2026-13"}).status_code == 400


def test_the_wrapper_keeps_instructions_and_passes_other_attributes_through(tmp_path):
    ai = FakeAI()
    ai.base_url = "https://example.ai"
    client = usage.metered(lambda s: ai, usage.FileLedger(tmp_path))(None)
    client.datawazi_instructions = "Use our fiscal year."
    assert client.datawazi_instructions == "Use our fiscal year." and client.base_url == "https://example.ai"
    assert usage.metered(lambda s: None, usage.FileLedger(tmp_path))(None) is None  # no AI configured


def test_calls_outside_a_workspace_are_not_recorded(tmp_path):
    ledger = usage.FileLedger(tmp_path)
    usage.metered(lambda s: FakeAI(), ledger)(None).chat.completions.create(model="m", messages=[])
    assert not any(tmp_path.rglob("*.jsonl"))


def test_calls_from_many_threads_are_all_counted(tmp_path):
    ledger = usage.FileLedger(tmp_path)
    client = usage.metered(lambda s: FakeAI(), ledger)(None)

    def work():
        with workspaces.use("ws1"):
            for _ in range(25):
                client.chat.completions.create(model="m", messages=[])

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    month = next(tmp_path.rglob("*.jsonl")).stem
    assert len(ledger.month("ws1", month)) == 200


def test_the_ledger_failing_never_fails_the_ai_step(tmp_path):
    class Broken:
        def add(self, entry):
            raise OSError("disk full")

    client = usage.metered(lambda s: FakeAI(), Broken())(None)
    with workspaces.use("ws1"):
        assert client.chat.completions.create(model="m", messages=[]).choices


@pytest.mark.parametrize("path, name", [
    ("/api/datasets/a/transform/queries/b/ai", "Cleaning"),
    ("/api/datasets/a/model/ai", "Model"),
    ("/api/dashboards/a/ask", "Ask"),
    ("/api/dashboards/a/chat", "Chat"),
    ("/api/datasets/a/autopilot", "Autopilot"),
    ("/api/dashboards/a/export/summary", "Exports"),
    ("/api/dashboards/a/ai", "Dashboards"),
    ("", "Scheduled"),
])
def test_features_come_from_the_request_path(path, name):
    assert usage.feature(path) == name
