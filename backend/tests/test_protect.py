"""Protections around every request: headers, rate limits, local mode answering only this computer, platform admins
only from verified sign-ins, the AI provider's details only for platform admins, and no fetching private addresses."""

import pytest
from fastapi.testclient import TestClient

from app import ingest, protect
from app.main import create_app
from tests.test_workspaces import CONFIG, Person


@pytest.fixture
def api(tmp_path):
    return TestClient(create_app(data_dir=tmp_path, client_factory=lambda s: None, auth=CONFIG))


def test_every_response_carries_the_security_headers(api):
    response = Person(api, "ann").get("/api/me")
    csp = response.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp and CONFIG.supabase_url in csp
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"  # company data never cached
    assert "strict-transport-security" not in response.headers  # only over https
    assert "strict-transport-security" in api.get("/api/health", headers={"X-Forwarded-Proto": "https"}).headers


def test_a_local_copy_answers_only_this_computer(tmp_path):
    local = create_app(data_dir=tmp_path, client_factory=lambda s: None)
    assert TestClient(local).get("/api/me").status_code == 200
    from_network = TestClient(local, client=("192.168.1.20", 50000))
    response = from_network.get("/api/me")
    assert response.status_code == 403 and "only answers the computer it runs on" in response.json()["detail"]


def test_too_many_changes_in_a_minute_are_slowed_down(api, monkeypatch):
    monkeypatch.setattr(protect, "WRITES", protect.RateLimit(3, 60, "slow down"))
    ann = Person(api, "ann")
    codes = [ann.post("/api/workspaces", json={"name": f"W{i}"}).status_code for i in range(4)]
    assert codes[:3] == [200, 200, 200] and codes[3] == 429
    assert Person(api, "bob").post("/api/workspaces", json={"name": "B"}).status_code == 200  # per person


def test_platform_admins_come_only_from_sign_ins_that_verify_the_email(api):
    ok = Person(api, "ops", provider="google")
    spoofed = Person(api, "ops", provider="azure")  # a Microsoft tenant can claim any address
    assert ok.call("PUT", "/api/settings", json={"base_url": "https://api.example.com/v1", "model": "m"}).status_code == 200
    assert spoofed.call("PUT", "/api/settings", json={"base_url": "https://evil.example/v1", "model": "m"}).status_code == 403


def test_only_platform_admins_see_which_ai_provider_and_key(api):
    Person(api, "ops").call("PUT", "/api/settings", json={"base_url": "https://api.example.com/v1", "model": "m",
                                                           "api_key": "sk-test-12345678"})
    assert Person(api, "ops").get("/api/settings").json()["api_key_hint"] == "…5678"
    seen = Person(api, "ann").get("/api/settings").json()
    assert seen == {"has_api_key": True, "capabilities": seen["capabilities"]}


@pytest.mark.parametrize("url", ["http://docs.google.com/x", "https://127.0.0.1/x", "https://localhost/x",
                                 "https://169.254.169.254/latest/meta-data", "https://10.0.0.5/file.csv"])
def test_links_to_private_addresses_are_never_fetched(url):
    with pytest.raises(ingest.IngestError):
        ingest.http_get(url)


def test_a_redirect_to_a_private_address_is_refused():
    handler = ingest.CheckedRedirects()
    with pytest.raises(ingest.IngestError, match="redirected"):
        handler.redirect_request(None, None, 302, "Found", {}, "https://169.254.169.254/latest/meta-data")
