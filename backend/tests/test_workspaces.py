"""Sign-in, workspaces and roles: one company never sees another's data, and roles hold."""

import re
import time
import uuid
from pathlib import Path

import jwt
import psycopg
import pytest
from dotenv import dotenv_values
from fastapi.testclient import TestClient

from app.auth import AuthConfig
from app.db import Database
from app.main import create_app
from app.settings import ENV_VARS

# The Postgres runs use the project's real Supabase database (DATABASE_URL in .env), each in a
# throwaway schema that is dropped afterwards. Without it, only the local file store is tested.
DATABASE_URL = dotenv_values(Path(__file__).resolve().parents[2] / ".env").get("DATABASE_URL")

URL = "https://demo.supabase.co"
SECRET = "test-secret-that-is-long-enough-for-hs256"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in ENV_VARS.values():
        monkeypatch.delenv(var, raising=False)


def uid(user: str) -> str:
    """Supabase user ids are UUIDs; each test person gets a stable one."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"datawazi-test/{user}"))


def token(user: str, email: str | None = None, **overrides) -> str:
    key = overrides.pop("key", SECRET)
    claims = {"sub": uid(user), "email": email or f"{user}@example.com", "aud": "authenticated",
              "iss": f"{URL}/auth/v1", "exp": int(time.time()) + 3600, **overrides}
    return jwt.encode(claims, key, algorithm="HS256")


class Person:
    """One signed-in user talking to the API, optionally inside a chosen workspace."""

    def __init__(self, api: TestClient, user: str, workspace: str | None = None):
        self.api, self.user, self.workspace = api, user, workspace

    def headers(self):
        h = {"Authorization": f"Bearer {token(self.user)}"}
        if self.workspace:
            h["X-Workspace"] = self.workspace
        return h

    def call(self, method: str, path: str, **kw):
        return self.api.request(method, path, headers=self.headers(), **kw)

    def get(self, path, **kw):
        return self.call("GET", path, **kw)

    def post(self, path, **kw):
        return self.call("POST", path, **kw)


def ok(response):
    assert response.status_code == 200, response.json()
    return response.json()


CONFIG = AuthConfig(supabase_url=URL, anon_key="sb_publishable_test", jwt_secret=SECRET,
                    platform_admins={"ops@example.com"})


@pytest.fixture(params=["files", "postgres"])
def database(request):
    if request.param == "files":
        yield None
        return
    if not DATABASE_URL:
        pytest.skip("No DATABASE_URL in .env")
    schema = f"dwtest_{uuid.uuid4().hex[:10]}"
    db = Database(DATABASE_URL, schema=schema, pool_size=3)
    yield db
    db.close()
    with psycopg.connect(DATABASE_URL, autocommit=True) as con:
        con.execute(f"drop schema if exists {schema} cascade")


@pytest.fixture
def api(tmp_path, database):
    return TestClient(create_app(data_dir=tmp_path, client_factory=lambda s: None, auth=CONFIG, database=database))


SALES = "order,qty\n1,2\n2,1\n3,4\n"


def company(api, user: str, name: str) -> tuple[Person, dict]:
    """A user with a workspace holding a published dataset and a published dashboard with one KPI."""
    person = Person(api, user)
    workspace = ok(person.post("/api/workspaces", json={"name": name}))
    person.workspace = workspace["id"]
    dataset = ok(person.post("/api/datasets", json={"name": f"{name} sales"}))
    base = f"/api/datasets/{dataset['id']}"
    ok(person.post(f"{base}/sources/upload", files=[("files", ("sales.csv", SALES.encode()))]))
    ok(person.post(f"{base}/model/measures", json={"name": "Orders", "kind": "aggregate", "table": "sales", "aggregation": "count"}))
    ok(person.post(f"{base}/publish", params={"confirm": True}))
    dash = ok(person.post("/api/dashboards", json={"name": f"{name} board", "dataset_id": dataset["id"]}))
    page = ok(person.post(f"/api/dashboards/{dash['id']}/pages", json={"title": "Overview"}))["pages"][-1]["id"]
    ok(person.post(f"/api/dashboards/{dash['id']}/pages/{page}/visuals", json={"type": "kpi", "title": "Orders", "measures": ["Orders"]}))
    ok(person.post(f"/api/dashboards/{dash['id']}/publish"))
    draft = ok(person.post("/api/dashboards", json={"name": f"{name} draft board", "dataset_id": dataset["id"]}))
    source = dataset_sources(person, dataset["id"])[0]
    return person, {"workspace": workspace["id"], "dataset": dataset["id"], "dashboard": dash["id"],
                    "draft": draft["id"], "page": page, "source": source, "name": name}


def dataset_sources(person, dataset_id):
    return [s["id"] for s in ok(person.get(f"/api/datasets/{dataset_id}"))["sources"]]


# Signing in


def test_requests_without_a_valid_token_are_refused(api):
    assert api.get("/api/health").status_code == 200
    assert ok(api.get("/api/auth/config")) == {"mode": "supabase", "supabase_url": URL, "supabase_key": "sb_publishable_test"}
    assert api.get("/api/datasets").status_code == 401
    bad = {
        "forged": token("eve", key="some-other-secret-that-is-long-enough"),
        "expired": token("eve", exp=int(time.time()) - 10),
        "other audience": token("eve", aud="anon"),
        "other project": token("eve", iss="https://other.supabase.co/auth/v1"),
        "garbage": "not-a-token",
    }
    for case, value in bad.items():
        response = api.get("/api/me", headers={"Authorization": f"Bearer {value}"})
        assert response.status_code == 401, case
    expired = api.get("/api/me", headers={"Authorization": f"Bearer {bad['expired']}"})
    assert "expired" in expired.json()["detail"]


def test_a_new_user_creates_their_first_workspace(api):
    ana = Person(api, "ana")
    assert ok(ana.get("/api/me"))["workspaces"] == []
    response = ana.get("/api/datasets")
    assert response.status_code == 409 and response.json()["detail"]["code"] == "no_workspace"
    workspace = ok(ana.post("/api/workspaces", json={"name": "Ana Trading"}))
    assert workspace["role"] == "admin"
    me = ok(ana.get("/api/me"))
    assert me["workspaces"] == [{"id": workspace["id"], "name": "Ana Trading", "role": "admin"}]
    assert ok(ana.get("/api/datasets")) == []  # the first workspace is used without a header


# Isolation between companies


def test_one_company_never_reaches_another_companys_data(api):
    ana, a = company(api, "ana", "Ana Trading")
    ben, b = company(api, "ben", "Ben Foods")

    # Lists only show your own workspace.
    assert [d["id"] for d in ok(ben.get("/api/datasets"))] == [b["dataset"]]
    assert {d["id"] for d in ok(ben.get("/api/dashboards"))} == {b["dashboard"], b["draft"]}

    # Naming the other workspace is refused outright.
    intruder = Person(api, "ben", a["workspace"])
    assert intruder.get("/api/datasets").status_code == 403
    assert intruder.get(f"/api/dashboards/{a['dashboard']}").status_code == 403

    # Every dataset and dashboard endpoint, called with Ana's ids from Ben's workspace, finds nothing.
    ids = {"project_id": a["dataset"], "dashboard_id": a["dashboard"], "source_id": a["source"],
           "page_id": a["page"], "stage": "model", "query_id": "000000000000", "step_id": "000000000000",
           "visual_id": "000000000000", "rel_id": "000000000000", "measure_id": "000000000000",
           "change_id": "000000000000", "decision": "approve", "step": 1, "version": 1}
    checked = 0
    for template, operations in api.app.openapi()["paths"].items():
        if not re.search(r"\{(project_id|dashboard_id)\}", template):
            continue
        path = template.format(**ids)
        for method in (m.upper() for m in operations):
            response = ben.call(method, path, json={}, files=None)
            assert response.status_code >= 400, f"{method} {path} answered {response.status_code}"
            assert a["name"] not in response.text, f"{method} {path} leaked Ana's data"
            checked += 1
    assert checked > 60
    for path in (f"/api/datasets/{a['dataset']}", f"/api/dashboards/{a['dashboard']}",
                 f"/api/datasets/{a['dataset']}/model", f"/api/datasets/{a['dataset']}/sources/{a['source']}/preview"):
        assert ben.get(path).status_code == 404, path

    # Ana's data is untouched by all of that.
    assert ok(ana.get(f"/api/datasets/{a['dataset']}"))["name"] == "Ana Trading sales"
    assert len(ok(ana.get(f"/api/dashboards/{a['dashboard']}"))["pages"]) == 1


def test_files_live_in_separate_workspace_folders(api, tmp_path):
    _, a = company(api, "ana", "Ana Trading")
    _, b = company(api, "ben", "Ben Foods")
    assert (tmp_path / "workspaces" / a["workspace"] / "datasets" / a["dataset"] / "data.duckdb").exists()
    assert (tmp_path / "workspaces" / b["workspace"] / "datasets" / b["dataset"] / "data.duckdb").exists()
    assert not (tmp_path / "workspaces" / b["workspace"] / "datasets" / a["dataset"]).exists()


# Invites and roles


def invite(admin: Person, email: str, role: str) -> str:
    sent = ok(admin.post("/api/workspaces/current/invites", json={"email": email, "role": role}))
    assert "token" not in sent["invite"] and "token_hash" not in sent["invite"]  # shown once, as the link
    return sent["link"].rsplit("/", 1)[1]


def test_invites_go_to_the_named_email_only(api):
    ana, a = company(api, "ana", "Ana Trading")
    link = invite(ana, "vic@example.com", "viewer")
    assert ok(Person(api, "vic").get(f"/api/invites/{link}"))["workspace"] == "Ana Trading"
    assert Person(api, "eve").post(f"/api/invites/{link}/accept").status_code == 403
    joined = ok(Person(api, "vic").post(f"/api/invites/{link}/accept"))
    assert joined["id"] == a["workspace"] and joined["role"] == "viewer"
    assert Person(api, "vic").post(f"/api/invites/{link}/accept").status_code == 404  # used up
    assert ana.post("/api/workspaces/current/invites", json={"email": "vic@example.com", "role": "viewer"}).status_code == 409
    assert ana.post("/api/workspaces/current/invites", json={"email": "not-an-email", "role": "viewer"}).status_code == 400
    assert ana.post("/api/workspaces/current/invites", json={"email": "x@example.com", "role": "owner"}).status_code == 400


def test_viewers_see_published_dashboards_only(api):
    ana, a = company(api, "ana", "Ana Trading")
    Person(api, "vic").post(f"/api/invites/{invite(ana, 'vic@example.com', 'viewer')}/accept")
    vic = Person(api, "vic", a["workspace"])

    assert [d["id"] for d in ok(vic.get("/api/dashboards"))] == [a["dashboard"]]  # not the draft-only one
    board = ok(vic.get(f"/api/dashboards/{a['dashboard']}", params={"side": "draft"}))
    assert board["side"] == "published"  # viewers are always shown the published version
    assert vic.get(f"/api/dashboards/{a['draft']}").status_code == 404
    assert vic.post(f"/api/dashboards/{a['dashboard']}/ask", json={"question": "x", "side": "draft"}).status_code in (400, 403)

    refused = [
        ("GET", "/api/datasets"),
        ("GET", f"/api/datasets/{a['dataset']}"),
        ("POST", "/api/datasets"),
        ("POST", "/api/dashboards"),
        ("POST", f"/api/dashboards/{a['dashboard']}/publish"),
        ("GET", f"/api/dashboards/{a['dashboard']}/chat"),
        ("POST", f"/api/dashboards/{a['dashboard']}/pages"),
        ("GET", "/api/workspaces/current/members"),
        ("POST", "/api/workspaces/current/invites"),
    ]
    for method, path in refused:
        assert vic.call(method, path, json={}).status_code == 403, f"{method} {path}"
    assert ok(vic.get("/api/workspaces/current"))["role"] == "viewer"


def test_creators_build_but_do_not_manage_people(api):
    ana, a = company(api, "ana", "Ana Trading")
    Person(api, "cat").post(f"/api/invites/{invite(ana, 'cat@example.com', 'creator')}/accept")
    cat = Person(api, "cat", a["workspace"])
    assert ok(cat.post("/api/datasets", json={"name": "Cat's data"}))["workspace_id"] == a["workspace"]
    assert ok(cat.get(f"/api/dashboards/{a['draft']}"))["side"] == "draft"
    assert cat.get("/api/workspaces/current/members").status_code == 403
    assert cat.call("PATCH", "/api/workspaces/current", json={"name": "Mine now"}).status_code == 403


def test_admins_manage_roles_and_keep_one_admin(api):
    ana, a = company(api, "ana", "Ana Trading")
    Person(api, "cat").post(f"/api/invites/{invite(ana, 'cat@example.com', 'creator')}/accept")
    members = "/api/workspaces/current/members"
    assert ana.call("PUT", f"{members}/{uid('ana')}", json={"role": "viewer"}).status_code == 409
    assert ana.call("DELETE", f"{members}/{uid('ana')}").status_code == 409
    roles = {m["email"]: m["role"] for m in ok(ana.call("PUT", f"{members}/{uid('cat')}", json={"role": "admin"}))["members"]}
    assert roles == {"ana@example.com": "admin", "cat@example.com": "admin"}
    ok(ana.call("PUT", f"{members}/{uid('ana')}", json={"role": "viewer"}))  # another admin exists now
    cat = Person(api, "cat", a["workspace"])
    left = ok(cat.call("DELETE", f"{members}/{uid('ana')}"))
    assert [m["email"] for m in left["members"]] == ["cat@example.com"]
    assert ok(Person(api, "ana").get("/api/me"))["workspaces"] == []

    pending = invite(cat, "dan@example.com", "viewer")
    invite_id = ok(cat.get("/api/workspaces/current/members"))["invites"][0]["id"]
    ok(cat.call("DELETE", f"/api/workspaces/current/invites/{invite_id}"))
    assert Person(api, "dan").post(f"/api/invites/{pending}/accept").status_code == 404
    renamed = ok(cat.call("PATCH", "/api/workspaces/current", json={"name": "Ana & Cat Trading"}))
    assert renamed["name"] == "Ana & Cat Trading"
    Person(api, "ana").post(f"/api/invites/{invite(cat, 'ana@example.com', 'viewer')}/accept")
    viewer = Person(api, "ana", a["workspace"])
    assert viewer.call("PATCH", "/api/workspaces/current", json={"name": "Renamed by a viewer"}).status_code == 403
    assert viewer.call("PUT", "/api/workspaces/current/ai-privacy", json={"samples": False}).status_code == 403


# AI provider settings belong to the people running the service


def test_only_platform_admins_change_the_ai_provider(api):
    company(api, "ana", "Ana Trading")
    ana = Person(api, "ana")
    assert ana.get("/api/settings").status_code == 200  # everyone may see whether the AI is ready
    assert ana.call("PUT", "/api/settings", json={"model": "x"}).status_code == 403
    assert ana.post("/api/settings/test").status_code == 403
    ops = Person(api, "ops")
    assert ok(ops.call("PUT", "/api/settings", json={"model": "x"}))["model"] == "x"
    response = api.put("/api/settings", json={"model": "y"})
    assert response.status_code == 401


def test_data_from_before_sign_in_goes_to_the_first_platform_admin(tmp_path, database):
    local = TestClient(create_app(data_dir=tmp_path, client_factory=lambda s: None))
    dataset = ok(local.post("/api/datasets", json={"name": "Built offline"}))
    api = TestClient(create_app(data_dir=tmp_path, client_factory=lambda s: None, auth=CONFIG, database=database))
    assert ok(Person(api, "ana").get("/api/me"))["workspaces"] == []  # not a platform admin: nothing inherited
    ops = Person(api, "ops")
    assert ok(ops.get("/api/me"))["workspaces"] == [{"id": "local", "name": "Local workspace", "role": "admin"}]
    assert [d["id"] for d in ok(ops.get("/api/datasets"))] == [dataset["id"]]


def test_local_mode_needs_no_sign_in(tmp_path):
    api = TestClient(create_app(data_dir=tmp_path, client_factory=lambda s: None))
    me = ok(api.get("/api/me"))
    assert me["mode"] == "local" and me["workspaces"] == [{"id": "local", "name": "Local workspace", "role": "admin"}]
    assert api.post("/api/workspaces", json={"name": "Second"}).status_code == 400
