import json
from types import SimpleNamespace

import httpx
import openai
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import DEFAULT_BASE_URL, DEFAULT_MODEL, ENV_VARS


def message(content=None, tool_calls=None):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tool_calls))])


def tool_call(name, arguments):
    return SimpleNamespace(function=SimpleNamespace(name=name, arguments=arguments))


class FakeClient:
    """Stands in for openai.OpenAI; answers the three connection-test calls."""

    def __init__(self, tools=True, json_mode=True, error=None, tool_args='{"a": 2, "b": 3}'):
        self.tools, self.json_mode, self.error, self.tool_args = tools, json_mode, error, tool_args
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        if "tools" in kwargs:
            return message(tool_calls=[tool_call("add_numbers", self.tool_args)] if self.tools else None)
        if "response_format" in kwargs:
            return message(content='{"status": "ok"}' if self.json_mode else "status ok")
        return message(content="OK")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in ENV_VARS.values():
        monkeypatch.delenv(var, raising=False)


def make(tmp_path, client=None):
    fake = client or FakeClient()
    return TestClient(create_app(data_dir=tmp_path, client_factory=lambda s: fake)), fake


def test_defaults_point_at_ilmu(tmp_path):
    api, _ = make(tmp_path)
    body = api.get("/api/settings").json()
    assert body["base_url"] == DEFAULT_BASE_URL
    assert body["model"] == DEFAULT_MODEL
    assert body["has_api_key"] is False


def test_api_key_is_never_returned(tmp_path):
    api, _ = make(tmp_path)
    body = api.put("/api/settings", json={"api_key": "sk-secret-value-1234"}).json()
    assert body["has_api_key"] is True
    assert body["api_key_hint"] == "…1234"
    assert "sk-secret" not in json.dumps(body)
    assert "sk-secret" not in json.dumps(api.get("/api/settings").json())


def test_blank_fields_leave_settings_unchanged(tmp_path):
    api, _ = make(tmp_path)
    api.put("/api/settings", json={"api_key": "sk-secret-value-1234", "model": "m1"})
    body = api.put("/api/settings", json={"api_key": "", "model": "  "}).json()
    assert body["has_api_key"] is True
    assert body["model"] == "m1"


def test_env_key_overrides_file_and_is_not_saved(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-from-env-9999")
    api, _ = make(tmp_path)
    api.put("/api/settings", json={"model": "m2"})
    body = api.get("/api/settings").json()
    assert body["api_key_from_env"] is True and body["api_key_hint"] == "…9999"
    assert "sk-from-env" not in (tmp_path / "settings.json").read_text()


def test_connection_test_needs_a_key(tmp_path):
    api, _ = make(tmp_path)
    assert api.post("/api/settings/test").status_code == 400


def test_connection_test_reports_and_saves_capabilities(tmp_path):
    api, fake = make(tmp_path, FakeClient(tools=True, json_mode=False))
    api.put("/api/settings", json={"api_key": "sk-secret-value-1234"})
    report = api.post("/api/settings/test").json()
    assert report["ok"] is True and report["reply"] == "OK"
    assert report["supports_tools"] is True
    assert report["supports_json_mode"] is False
    assert api.get("/api/settings").json()["capabilities"] == {"tools": True, "json_mode": False}
    assert all(call["model"] == DEFAULT_MODEL for call in fake.calls)


def test_wrong_tool_arguments_count_as_no_tool_support(tmp_path):
    api, _ = make(tmp_path, FakeClient(tool_args="not json"))
    api.put("/api/settings", json={"api_key": "sk-secret-value-1234"})
    assert api.post("/api/settings/test").json()["supports_tools"] is False


def test_changing_provider_clears_capabilities(tmp_path):
    api, _ = make(tmp_path)
    api.put("/api/settings", json={"api_key": "sk-secret-value-1234"})
    api.post("/api/settings/test")
    body = api.put("/api/settings", json={"model": "another-model"}).json()
    assert body["capabilities"] == {}


def test_bad_key_gives_a_readable_error(tmp_path):
    request = httpx.Request("POST", "https://api.ilmu.ai/v1/chat/completions")
    error = openai.AuthenticationError("bad key", response=httpx.Response(401, request=request), body=None)
    api, _ = make(tmp_path, FakeClient(error=error))
    api.put("/api/settings", json={"api_key": "sk-wrong-key-0000"})
    report = api.post("/api/settings/test").json()
    assert report["ok"] is False
    assert "rejected" in report["error"]
    assert api.get("/api/settings").json()["capabilities"] == {}
