from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app import refresh
from app.dashboards import DashboardStore
from app.main import create_app
from app.projects import ProjectStore
from test_report import SEGMENT, Project, clean_env, ok  # noqa: F401  (clean_env is an autouse fixture)

NEXT_SALES = (
    "order,day,customer,qty,price\n"
    "1,2024-01-05,C1,2,10\n"
    "2,2024-02-10,C2,1,10\n"
    "3,2024-11-01,C1,1,10\n"
    "4,2025-01-20,C1,5,4\n"
    "5,2025-02-02,C3,1,4\n"
    "6,2025-03-01,C2,10,10\n"
)


def source_id(p, table):
    return next(s["id"] for s in p.api.get(p.dataset).json()["sources"] if s["table"] == table)


def replace(p, table, name, text):
    return p.api.post(f"{p.dataset}/sources/{source_id(p, table)}/replace", files={"file": (name, text.encode())})


def revenue(p):
    measures = p.api.get(f"{p.dataset}/model").json()["measures"]
    return next(m["value"] for m in measures if m["name"] == "Revenue")


def test_replacing_a_file_keeps_steps_model_and_dashboards(tmp_path):
    p = Project(tmp_path)
    page = p.page()
    ok(p.visual(page, type="bar", title="Revenue by segment", measures=["Revenue"], group_by=SEGMENT))
    assert revenue(p) == 64.0

    body = ok(replace(p, "sales", "sales_march.csv", NEXT_SALES))
    entry = body["entry"]
    assert entry["status"] == "ok" and entry["trigger"] == "replace"
    assert entry["sources"] == [{"table": "sales", "rows_before": 5, "rows_after": 6}]
    assert entry["published_version"] is None  # auto-publish is off
    assert revenue(p) == 164.0  # same steps and measures, new rows
    sales = next(q for q in p.api.get(f"{p.dataset}/transform").json()["queries"] if q["name"] == "sales")
    assert len(sales["steps"]) == 3 and sales["error"] is None
    source = next(s for s in body["sources"] if s["table"] == "sales")
    assert source["file"] == "sales_march.csv"

    # Dashboards still read the published version until it is published.
    assert p.visuals()[0]["data"]["check"]["total"] == 64.0


def test_auto_publish_puts_good_data_live(tmp_path):
    p = Project(tmp_path)
    page = p.page()
    ok(p.visual(page, type="bar", title="Revenue by segment", measures=["Revenue"], group_by=SEGMENT))
    ok(p.api.put(f"{p.dataset}/refresh", json={"auto_publish": True}))
    entry = ok(replace(p, "sales", "sales.csv", NEXT_SALES))["entry"]
    assert entry["published_version"] == 2
    assert p.visuals()[0]["data"]["check"]["total"] == 164.0


def test_failed_checks_stop_auto_publish(tmp_path):
    p = Project(tmp_path)
    page = p.page()
    ok(p.visual(page, type="bar", title="Revenue by segment", measures=["Revenue"], group_by=SEGMENT))
    ok(p.api.put(f"{p.dataset}/refresh", json={"auto_publish": True}))
    # The new file lost the qty column, which a step and the Revenue measure rely on.
    entry = ok(replace(p, "sales", "sales.csv", "order,day,customer,price\n1,2025-01-01,C1,10\n"))["entry"]
    assert entry["status"] == "failed" and entry["published_version"] is None
    texts = " ".join(c["text"] for c in entry["checks"])
    assert "columns no longer in the data: qty" in texts and "Transform sales" in texts
    assert p.visuals()[0]["error"] is None  # dashboards keep the last good version
    history = p.api.get(f"{p.dataset}/refresh").json()["history"]
    assert [h["status"] for h in history] == ["failed"]


def test_big_row_drops_are_flagged(tmp_path):
    p = Project(tmp_path)
    entry = ok(replace(p, "sales", "sales.csv", "order,day,customer,qty,price\n1,2025-01-01,C1,1,1\n"))["entry"]
    assert entry["status"] == "warnings"
    assert any("rows fell from 5 to 1" in c["text"] for c in entry["checks"])


def test_excel_replacement_is_matched_by_sheet(tmp_path):
    import io
    from openpyxl import Workbook

    def book(rows):
        workbook = Workbook()
        workbook.remove(workbook.active)
        for title, data in rows.items():
            sheet = workbook.create_sheet(title)
            for row in data:
                sheet.append(row)
        buffer = io.BytesIO()
        workbook.save(buffer)
        return buffer.getvalue()

    api = TestClient(create_app(data_dir=tmp_path))
    pid = api.post("/api/datasets", json={"name": "Book"}).json()["id"]
    first = book({"Jan": [["id", "amount"], [1, 10]], "Feb": [["id", "amount"], [2, 20]]})
    added = api.post(f"/api/datasets/{pid}/sources/upload", files={"files": ("Sales.xlsx", first)}).json()["added"]
    feb = next(s for s in added if s["table"] == "sales_feb")
    newer = book({"Jan": [["id", "amount"], [1, 10]], "Feb": [["id", "amount"], [2, 20], [3, 30]]})
    body = ok(api.post(f"/api/datasets/{pid}/sources/{feb['id']}/replace", files={"file": ("Sales v2.xlsx", newer)}))
    assert body["entry"]["sources"] == [{"table": "sales_feb", "rows_before": 1, "rows_after": 2}]


class SheetFetch:
    def __init__(self, body):
        self.body = body

    def __call__(self, url):
        if self.body is None:
            return b"<html>Sign in</html>", "text/html", ""
        return self.body.encode(), "text/csv", "Targets - Sheet1.csv"


def sheet_project(tmp_path, fetch):
    api = TestClient(create_app(data_dir=tmp_path, fetch_url=fetch))
    pid = api.post("/api/datasets", json={"name": "Targets"}).json()["id"]
    ok(api.post(f"/api/datasets/{pid}/sources/gsheet", json={"url": "https://docs.google.com/spreadsheets/d/abc/edit"}))
    return api, pid


def test_google_sheets_are_pulled_again(tmp_path):
    fetch = SheetFetch("region,target\nNorth,10\n")
    api, pid = sheet_project(tmp_path, fetch)
    fetch.body = "region,target\nNorth,10\nSouth,20\n"
    entry = ok(api.post(f"/api/datasets/{pid}/refresh"))["entry"]
    assert entry["sources"] == [{"table": "targets_sheet1", "rows_before": 1, "rows_after": 2}]

    fetch.body = None  # sharing was turned off
    entry = ok(api.post(f"/api/datasets/{pid}/refresh"))["entry"]
    assert entry["status"] == "failed" and entry["failed_sources"] == ["targets_sheet1"]
    assert "Anyone with the link" in entry["checks"][0]["text"]


def test_schedules_only_for_sheets_and_run_when_due(tmp_path):
    p = Project(tmp_path)
    response = p.api.put(f"{p.dataset}/refresh", json={"schedule": "daily"})
    assert response.status_code == 400 and "Only Google Sheets" in response.json()["detail"]

    fetch = SheetFetch("region,target\nNorth,10\n")
    api, pid = sheet_project(tmp_path / "sheets", fetch)
    settings = ok(api.put(f"/api/datasets/{pid}/refresh", json={"schedule": "hourly"}))["settings"]
    next_run = datetime.fromisoformat(settings["next_run"])
    assert timedelta(minutes=59) < next_run - datetime.now(timezone.utc) <= timedelta(hours=1)

    store, boards = ProjectStore(tmp_path / "sheets" / "datasets"), DashboardStore(tmp_path / "sheets" / "dashboards")
    fetch.body = "region,target\nNorth,10\nSouth,20\nEast,5\n"
    assert refresh.run_due(store, boards, fetch, datetime.now(timezone.utc)) == []  # not due yet
    assert refresh.run_due(store, boards, fetch, next_run + timedelta(seconds=1)) == [pid]
    state = api.get(f"/api/datasets/{pid}/refresh").json()
    assert state["history"][0]["trigger"] == "schedule" and state["history"][0]["sources"][0]["rows_after"] == 3
    assert datetime.fromisoformat(state["settings"]["next_run"]) > next_run
    assert api.put(f"/api/datasets/{pid}/refresh", json={"schedule": "monthly"}).status_code == 400
