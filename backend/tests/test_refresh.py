from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from test_report import SEGMENT, Project, clean_env, ok  # noqa: F401  (clean_env is an autouse fixture)

from app import refresh
from app.dashboards import DashboardStore
from app.main import create_app
from app.projects import ProjectStore

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
    assert timedelta(minutes=59) < next_run - datetime.now(UTC) <= timedelta(hours=1)

    folder = tmp_path / "sheets" / "workspaces" / "local"
    store, boards = ProjectStore(folder / "datasets"), DashboardStore(folder / "dashboards")
    fetch.body = "region,target\nNorth,10\nSouth,20\nEast,5\n"
    assert refresh.run_due(store, boards, fetch, datetime.now(UTC)) == []  # not due yet
    assert refresh.run_due(store, boards, fetch, next_run + timedelta(seconds=1)) == [pid]
    state = api.get(f"/api/datasets/{pid}/refresh").json()
    assert state["history"][0]["trigger"] == "schedule" and state["history"][0]["sources"][0]["rows_after"] == 3
    assert datetime.fromisoformat(state["settings"]["next_run"]) > next_run
    assert api.put(f"/api/datasets/{pid}/refresh", json={"schedule": "monthly"}).status_code == 400


# M24: refresh you can rely on


def wait_for(check, seconds=10):
    """Background jobs (linked refreshes, insight checks) finish shortly after the request that started them."""
    import time
    for _ in range(int(seconds / 0.05)):
        result = check()
        if result:
            return result
        time.sleep(0.05)
    raise AssertionError("timed out waiting for a background job")


def test_shared_links_become_direct_downloads():
    import base64

    from app import ingest
    assert ingest.shared_file_url("https://drive.google.com/file/d/1AbCdEfGhIjKlMn/view?usp=sharing") == (
        "gdrive", "https://drive.google.com/uc?export=download&id=1AbCdEfGhIjKlMn")
    provider, url = ingest.shared_file_url("https://1drv.ms/x/s!AbcDef")
    token = base64.urlsafe_b64encode(b"https://1drv.ms/x/s!AbcDef").decode().rstrip("=")
    assert (provider, url) == ("onedrive", f"https://api.onedrive.com/v1.0/shares/u!{token}/root/content")
    assert ingest.shared_file_url("https://ytl.sharepoint.com/:x:/g/personal/a/EAbc?e=XyZ")[1] == (
        "https://ytl.sharepoint.com/:x:/g/personal/a/EAbc?e=XyZ&download=1")
    assert ingest.shared_file_url("https://docs.google.com/spreadsheets/d/abc/edit")[0] == "gsheet"
    for bad in ("http://1drv.ms/x", "https://example.com/file.csv", "https://evil.sharepoint.com.example.org/x"):
        try:
            ingest.shared_file_url(bad)
        except ingest.IngestError:
            continue
        raise AssertionError(f"{bad} was accepted")


class FileFetch:
    def __init__(self, body, name="Sales report.csv"):
        self.body, self.name, self.urls = body, name, []

    def __call__(self, url):
        self.urls.append(url)
        if self.body is None:
            return b"<html>Sign in</html>", "text/html", ""
        return self.body.encode(), "application/octet-stream", self.name


def test_files_from_shared_links_are_pulled_again(tmp_path):
    fetch = FileFetch("region,target\nNorth,10\n")
    api = TestClient(create_app(data_dir=tmp_path, fetch_url=fetch))
    pid = api.post("/api/datasets", json={"name": "Targets"}).json()["id"]
    added = ok(api.post(f"/api/datasets/{pid}/sources/shared-link", json={"url": "https://1drv.ms/x/s!Abc"}))["added"]
    assert added[0]["origin"]["kind"] == "link" and added[0]["origin"]["provider"] == "onedrive"
    assert fetch.urls[0].startswith("https://api.onedrive.com/v1.0/shares/u!")

    fetch.body = "region,target\nNorth,10\nSouth,20\n"
    entry = ok(api.post(f"/api/datasets/{pid}/refresh"))["entry"]
    assert entry["status"] == "ok" and entry["sources"][0]["rows_after"] == 2
    assert ok(api.put(f"/api/datasets/{pid}/refresh", json={"schedule": "daily"}))["settings"]["next_run"]

    fetch.body = None  # sharing turned off
    entry = ok(api.post(f"/api/datasets/{pid}/refresh"))["entry"]
    assert entry["status"] == "failed" and "web page" in entry["checks"][0]["text"]
    response = api.post(f"/api/datasets/{pid}/sources/shared-link", json={"url": "https://example.com/a.csv"})
    assert response.status_code == 400 and "OneDrive" in response.json()["detail"]


def test_schedules_run_at_a_time_of_day_in_malaysia():
    config = {**refresh.DEFAULTS, "schedule": "daily", "at": "07:30"}
    # 22:00 UTC on a Thursday is 06:00 Friday in Malaysia: that day's 07:30 is still ahead.
    thursday_night = datetime(2026, 10, 22, 22, 0, tzinfo=UTC)
    assert refresh.next_slot(config, thursday_night) == datetime(2026, 10, 22, 23, 30, tzinfo=UTC)
    friday_noon_myt = datetime(2026, 10, 23, 4, 0, tzinfo=UTC)
    assert refresh.next_slot(config, friday_noon_myt) == datetime(2026, 10, 23, 23, 30, tzinfo=UTC)  # Saturday
    weekdays = {**config, "schedule": "weekdays"}
    assert refresh.next_slot(weekdays, friday_noon_myt) == datetime(2026, 10, 25, 23, 30, tzinfo=UTC)  # Monday
    weekly = {**config, "schedule": "weekly", "day": 2}
    assert refresh.next_slot(weekly, friday_noon_myt) == datetime(2026, 10, 27, 23, 30, tzinfo=UTC)  # Wednesday
    assert refresh.next_slot({**config, "schedule": "off"}, friday_noon_myt) is None


def test_scheduled_pulls_retry_then_raise_an_alert(tmp_path):
    fetch = SheetFetch("region,target\nNorth,10\n")
    api, pid = sheet_project(tmp_path, fetch)
    settings = ok(api.put(f"/api/datasets/{pid}/refresh", json={"schedule": "daily", "at": "08:00"}))["settings"]
    assert settings["at"] == "08:00" and api.put(f"/api/datasets/{pid}/refresh", json={"at": "25:00"}).status_code == 400
    store = ProjectStore(tmp_path / "workspaces" / "local" / "datasets")
    boards = DashboardStore(tmp_path / "workspaces" / "local" / "dashboards")

    fetch.body = None  # the sheet stopped being shared
    for attempt in range(len(refresh.RETRY_MINUTES)):
        when = datetime.fromisoformat(api.get(f"/api/datasets/{pid}/refresh").json()["settings"]["next_run"])
        assert refresh.run_due(store, boards, fetch, when + timedelta(seconds=1)) == [pid]
        state = api.get(f"/api/datasets/{pid}/refresh").json()
        assert state["history"][0]["retry_at"] and state["settings"]["retry"] == attempt + 1
        assert state["alert"] is None and api.get("/api/refresh-alerts").json() == []  # still retrying
    when = datetime.fromisoformat(state["settings"]["next_run"])
    refresh.run_due(store, boards, fetch, when + timedelta(seconds=1))
    state = api.get(f"/api/datasets/{pid}/refresh").json()
    assert state["settings"]["retry"] == 0 and "retry_at" not in state["history"][0]
    alerts = api.get("/api/refresh-alerts").json()
    assert alerts[0]["dataset_name"] == "Targets" and "Anyone with the link" in alerts[0]["text"]
    assert datetime.fromisoformat(state["settings"]["next_run"]).astimezone(refresh.MYT).hour == 8

    ok(api.post(f"/api/datasets/{pid}/refresh/alert/dismiss"))
    assert api.get("/api/refresh-alerts").json() == []


def test_append_loads_add_new_rows_and_skip_ones_already_loaded(tmp_path):
    p = Project(tmp_path)
    sid = source_id(p, "sales")
    response = p.api.put(f"{p.dataset}/sources/{sid}/load", json={"mode": "append", "key": "nope"})
    assert response.status_code == 400 and "no column 'nope'" in response.json()["detail"]
    ok(p.api.put(f"{p.dataset}/sources/{sid}/load", json={"mode": "append", "key": "order"}))

    # A file with two orders already loaded and one new one.
    entry = ok(replace(p, "sales", "sales.csv", "order,day,customer,qty,price\n4,2025-01-20,C1,5,4\n"
                                                 "5,2025-02-02,C3,1,4\n6,2025-03-01,C2,10,10\n"))["entry"]
    assert entry["sources"] == [{"table": "sales", "rows_before": 5, "rows_after": 6}]
    assert "appended 1 new rows, skipped 2 already loaded (same order)" in entry["checks"][0]["text"]
    assert revenue(p) == 164.0

    entry = ok(replace(p, "sales", "sales.csv", "order,day,customer,qty,price,extra\n7,2025-03-02,C2,1,1,x\n"))["entry"]
    assert entry["status"] == "failed" and "columns sales doesn't: extra" in entry["checks"][0]["text"]
    state = ok(p.api.put(f"{p.dataset}/sources/{sid}/load", json={"mode": "replace"}))
    assert next(s for s in state["sources"] if s["id"] == sid)["load"] == {"mode": "replace", "key": None}


def test_earlier_published_versions_can_be_rolled_back_to(tmp_path):
    p = Project(tmp_path)  # published as version 1
    page = p.page()
    ok(p.visual(page, type="bar", title="Revenue by segment", measures=["Revenue"], group_by=SEGMENT))
    ok(replace(p, "sales", "sales.csv", NEXT_SALES))
    p.publish()
    assert p.visuals()[0]["data"]["check"]["total"] == 164.0
    versions = ok(p.api.get(f"{p.dataset}/versions"))["versions"]
    assert [(v["version"], v["live"]) for v in versions] == [(2, True), (1, False)]

    body = ok(p.api.post(f"{p.dataset}/versions/1/roll-back"))
    assert [(v["version"], v["rolled_back_from"]) for v in body["versions"]][:2] == [(3, 1), (2, None)]
    assert p.visuals()[0]["data"]["check"]["total"] == 64.0  # dashboards read version 1's data again
    assert revenue(p) == 164.0  # the draft keeps the newer data
    folder = tmp_path / "workspaces" / "local" / "datasets" / p.id
    assert {f.name for f in folder.glob("published-v*.duckdb")} == {"published-v1.duckdb", "published-v2.duckdb",
                                                                    "published-v3.duckdb"}
    assert p.api.post(f"{p.dataset}/versions/9/roll-back").status_code == 400

    for _ in range(refresh.publishing.KEEP_VERSIONS):
        p.publish()
    assert len(ok(p.api.get(f"{p.dataset}/versions"))["versions"]) == refresh.publishing.KEEP_VERSIONS
    assert len(list(folder.glob("published-v*.duckdb"))) == refresh.publishing.KEEP_VERSIONS


def test_linked_tables_follow_their_dataset_when_it_publishes(tmp_path):
    p = Project(tmp_path)
    other = p.api.post("/api/datasets", json={"name": "Follower"}).json()["id"]
    ok(p.api.post(f"/api/datasets/{other}/sources/link", json={"dataset_id": p.id, "table": "sales"}))
    ok(replace(p, "sales", "sales.csv", NEXT_SALES))
    p.publish()

    def pulled():
        history = p.api.get(f"/api/datasets/{other}/refresh").json()["history"]
        return history and history[0]
    entry = wait_for(pulled)
    assert entry["trigger"] == "linked" and entry["sources"] == [{"table": "sales", "rows_before": 5, "rows_after": 6}]


def test_insights_are_rechecked_when_new_data_is_published(tmp_path):
    p = Project(tmp_path)
    page = p.page()
    visual = ok(p.visual(page, type="bar", title="Revenue by segment", measures=["Revenue"], group_by=SEGMENT))
    visual_id = visual["pages"][-1]["visuals"][-1]["id"]
    dash_id = p.base.rsplit("/", 1)[1]
    boards = DashboardStore(tmp_path / "workspaces" / "local" / "dashboards")
    with boards.edit(dash_id) as dash:
        target = next(v for pg in dash["draft"]["pages"] for v in pg["visuals"] if v["id"] == visual_id)
        target["insight"] = {"text": "Online made RM 14.", "check": {"verified": True, "unmatched": []}}
    ok(p.api.put(f"{p.dataset}/refresh", json={"auto_publish": True}))
    ok(replace(p, "sales", "sales.csv", NEXT_SALES))  # Online grows from RM 14 to RM 114

    def insight():
        found = next(v for v in p.visuals() if v["id"] == visual_id)["insight"]
        return found if found.get("stale") else None
    found = wait_for(insight)
    assert found["check"]["verified"] is False and found["text"] == "Online made RM 14."
