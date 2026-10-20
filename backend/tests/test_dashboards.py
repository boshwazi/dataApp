import json
import shutil

from fastapi.testclient import TestClient

from app.main import create_app
from test_report import SEGMENT, Project, clean_env, ok  # noqa: F401  (clean_env is an autouse fixture)

import pytest


@pytest.fixture
def p(tmp_path):
    return Project(tmp_path)


def revenue_card(p, side="draft"):
    page = p.api.get(p.base, params={"side": side}).json()["pages"][0]
    return page["visuals"][0]


def kpi_page(p):
    page = p.page()
    ok(p.visual(page, type="bar", title="Revenue by segment", measures=["Revenue"], group_by=SEGMENT))
    return page


def test_dashboards_read_the_published_dataset_only(p):
    kpi_page(p)
    assert revenue_card(p)["data"]["check"]["total"] == 64.0

    # A draft change to the dataset doesn't reach the dashboard...
    sales = next(q for q in p.api.get(f"{p.dataset}/transform").json()["queries"] if q["name"] == "sales")
    ok(p.api.post(f"{p.dataset}/transform/queries/{sales['id']}/steps",
                  json={"op": "filter_rows", "params": {"column": "qty", "operator": "at_least", "value": 2}}))
    status = p.api.get(p.dataset).json()["status"]
    assert status["unpublished_changes"] is True and status["version"] == 1
    assert revenue_card(p)["data"]["check"]["total"] == 64.0

    # ...until it is published.
    p.publish()
    assert revenue_card(p)["data"]["check"]["total"] == 40.0
    status = p.api.get(p.dataset).json()["status"]
    assert status["unpublished_changes"] is False and status["version"] == 2


def test_publishing_lists_visuals_it_would_break(p):
    kpi_page(p)
    measures = p.api.get(f"{p.dataset}/model").json()["measures"]
    revenue = next(m for m in measures if m["name"] == "Revenue")
    average = next(m for m in measures if m["name"] == "Average order")
    ok(p.api.delete(f"{p.dataset}/model/measures/{average['id']}"))
    ok(p.api.delete(f"{p.dataset}/model/measures/{revenue['id']}"))

    response = p.api.post(f"{p.dataset}/publish")
    assert response.status_code == 409
    problems = response.json()["detail"]["problems"]
    assert problems[0]["visual"] == "Revenue by segment" and "no measure called 'Revenue'" in problems[0]["error"]
    assert revenue_card(p)["error"] is None  # nothing changed yet

    body = p.publish()  # confirmed
    assert body["broken_visuals"] and "no measure called" in revenue_card(p)["error"]


def test_datasets_used_by_dashboards_cannot_be_deleted(p):
    response = p.api.delete(p.dataset)
    assert response.status_code == 409 and "Dashboards use this dataset: Sales" in response.json()["detail"]
    assert p.api.get(p.dataset).json()["dashboards"][0]["name"] == "Sales"


def test_dashboard_draft_and_published_versions(p):
    response = p.api.post(f"{p.base}/publish")
    assert response.status_code == 400 and "at least one page" in response.json()["detail"]

    kpi_page(p)
    body = ok(p.api.post(f"{p.base}/publish"))
    assert body["published"] is True and body["unpublished_changes"] is False

    ok(p.api.post(f"{p.base}/pages", json={"title": "Draft only"}))
    assert p.api.get(p.base).json()["unpublished_changes"] is True
    published = p.api.get(p.base, params={"side": "published"}).json()
    assert [page["title"] for page in published["pages"]] == ["Sales overview"]


def test_dashboards_refuse_to_publish_broken_visuals(p):
    kpi_page(p)
    measures = p.api.get(f"{p.dataset}/model").json()["measures"]
    for name in ("Average order", "Revenue"):
        measure = next(m for m in measures if m["name"] == name)
        p.api.delete(f"{p.dataset}/model/measures/{measure['id']}")
    p.publish()
    response = p.api.post(f"{p.base}/publish")
    assert response.status_code == 400 and "Revenue by segment" in response.json()["detail"]


def test_dashboard_on_an_unpublished_dataset_waits(tmp_path):
    api = TestClient(create_app(data_dir=tmp_path, client_factory=lambda s: None))
    dataset = api.post("/api/datasets", json={"name": "New data"}).json()
    response = api.post(f"/api/datasets/{dataset['id']}/publish")
    assert response.status_code == 400 and "Load some data" in response.json()["detail"]
    dash = ok(api.post("/api/dashboards", json={"name": "New", "dataset_id": dataset["id"]}))
    assert dash["dataset"]["published"] is False
    body = ok(api.get(f"/api/dashboards/{dash['id']}"))
    assert body["pages"] == []
    response = api.post(f"/api/dashboards/{dash['id']}/pages", json={"title": "x"})
    assert response.status_code == 400 and "Publish the dataset first" in response.json()["detail"]
    assert api.post("/api/dashboards", json={"name": "x", "dataset_id": "000000000000"}).status_code == 404


def test_old_projects_become_a_dataset_and_a_dashboard(tmp_path):
    # Build a dataset with a report page the new way, then put it back into the old shape.
    p = Project(tmp_path / "build")
    kpi_page(p)
    pages = p.api.get(p.base).json()["pages"]
    old = tmp_path / "old"
    shutil.copytree(tmp_path / "build" / "workspaces" / "local" / "datasets", old / "projects")
    record_path = old / "projects" / p.id / "project.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record.pop("published")
    record["report"] = {"pages": [{k: v for k, v in page.items()} for page in pages], "history": []}
    for page in record["report"]["pages"]:
        page["visuals"] = [{k: v for k, v in visual.items() if k not in ("data", "error")} for visual in page["visuals"]]
    record_path.write_text(json.dumps(record), encoding="utf-8")
    for leftover in (old / "projects" / p.id).glob("published-v*.duckdb"):
        leftover.unlink()

    api = TestClient(create_app(data_dir=old, client_factory=lambda s: None))
    # Old top-level folders move into the local workspace, and projects become datasets.
    moved = old / "workspaces" / "local"
    assert not (old / "projects").exists() and not (moved / "projects").exists() and (moved / "datasets").exists()
    dashboards = api.get("/api/dashboards").json()
    assert [d["name"] for d in dashboards] == ["R"] and dashboards[0]["dataset"]["version"] == 1
    page = api.get(f"/api/dashboards/{dashboards[0]['id']}").json()["pages"][0]
    assert page["visuals"][0]["data"]["check"]["total"] == 64.0
    assert "report" not in json.loads((moved / "datasets" / p.id / "project.json").read_text(encoding="utf-8"))


def test_dataset_list_shows_which_dashboards_use_it(p):
    listed = next(d for d in p.api.get("/api/datasets").json() if d["id"] == p.id)
    assert [d["name"] for d in listed["dashboards"]] == ["Sales"]
