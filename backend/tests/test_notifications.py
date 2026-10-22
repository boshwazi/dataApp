"""In-app notifications and dashboard digests reach the right people, and only them."""

from datetime import UTC, datetime

from test_security import NORTH, api, rows, shop  # noqa: F401  (fixtures)
from test_workspaces import Person, clean_env, invite, ok  # noqa: F401

from app import digests, mail


def titles(person):
    return [n["title"] for n in ok(person.get("/api/notifications"))["notifications"]]


def test_sharing_and_publishing_notify_who_can_see_it(shop):
    ana, viewers, dataset, dash, page = shop
    ok(ana.call("PUT", f"/api/dashboards/{dash}/sharing", json={"mode": "people", "people": ["vic@example.com"]}))
    assert titles(viewers["vic"]) == ["Sales board was shared with you", "Sales board was published"]
    assert titles(viewers["zed"]) == [] and titles(ana) == []  # not to others, nor to whoever did it

    ok(ana.post(f"/api/dashboards/{dash}/publish"))
    assert titles(viewers["vic"])[0] == "Sales board was updated"
    assert titles(viewers["zed"]) == []  # zed can't see the dashboard, so isn't told about it


class Outbox:
    def __init__(self):
        self.sent = []

    def send(self, to, subject, text, html=None):
        self.sent.append({"to": to, "subject": subject, "text": text, "html": html})


def test_digests_go_to_members_it_is_shared_with_by_email_and_in_the_app(shop, monkeypatch):
    ana, viewers, dataset, dash, page = shop
    outbox = Outbox()
    monkeypatch.setattr(mail.Mailer, "from_env", classmethod(lambda cls: outbox))
    ok(ana.post(f"/api/dashboards/{dash}/pages/{page}/visuals", json={"type": "kpi", "title": "Orders", "measures": ["Orders"]}))
    ok(ana.post(f"/api/dashboards/{dash}/publish"))
    ok(ana.call("PUT", f"/api/dashboards/{dash}/sharing", json={"mode": "people", "people": ["vic@example.com", "pat@example.com"]}))
    settings = ok(ana.call("PUT", f"/api/dashboards/{dash}/digest", json={
        "frequency": "weekly", "day": 0, "at": "08:00",
        "people": ["vic@example.com", "zed@example.com", "stranger@elsewhere.com"]}))
    assert settings["recipients"] == ["vic@example.com"]  # zed isn't shared with; the stranger isn't a member
    assert settings["email"] is True
    assert datetime.fromisoformat(settings["next_run"]).astimezone(digests.MYT).strftime("%a %H:%M") == "Mon 08:00"

    sent = ok(ana.post(f"/api/dashboards/{dash}/digest/send"))
    assert sent["emailed"] == ["vic@example.com"] and [m["to"] for m in outbox.sent] == ["vic@example.com"]
    assert "Orders: 5" in outbox.sent[0]["text"] and f"/dashboards/{dash}" in outbox.sent[0]["text"]
    assert titles(viewers["vic"])[0] == "Sales board: your weekly update"

    # With row-level security, a digest carries no numbers: one person's mustn't reach another.
    ok(ana.call("PUT", f"/api/datasets/{dataset}/security", json={"roles": [NORTH]}))
    outbox.sent.clear()
    ok(ana.post(f"/api/dashboards/{dash}/digest/send"))
    assert "Orders:" not in outbox.sent[0]["text"] and "see your numbers" in outbox.sent[0]["text"]

    bad = ana.call("PUT", f"/api/dashboards/{dash}/digest", json={"frequency": "daily", "people": ["vic@example.com"]})
    assert bad.status_code == 400
    assert viewers["vic"].call("PUT", f"/api/dashboards/{dash}/digest", json={"frequency": "off"}).status_code == 403


def test_digest_schedules():
    config = {**digests.settings({}), "frequency": "monthly", "at": "09:30"}
    after = datetime(2026, 10, 22, 4, 0, tzinfo=UTC)
    assert digests.next_slot(config, after) == datetime(2026, 11, 1, 1, 30, tzinfo=UTC)
    weekly = {**config, "frequency": "weekly", "day": 4}  # Friday
    assert digests.next_slot(weekly, after) == datetime(2026, 10, 23, 1, 30, tzinfo=UTC)
    assert digests.next_slot({**config, "frequency": "off"}, after) is None


def test_scheduled_refresh_problems_reach_creators(tmp_path):
    from datetime import timedelta

    from test_refresh import SheetFetch, sheet_project

    from app import refresh
    from app.dashboards import DashboardStore
    from app.projects import ProjectStore
    fetch = SheetFetch("region,target\nNorth,10\n")
    api_, pid = sheet_project(tmp_path, fetch)
    ok(api_.put(f"/api/datasets/{pid}/refresh", json={"schedule": "daily"}))
    store = ProjectStore(tmp_path / "workspaces" / "local" / "datasets")
    store.preferences = None  # the app's own store has them; this one only runs the refresh
    fetch.body = None
    refresh.RETRY_MINUTES, saved = (), refresh.RETRY_MINUTES  # no retries: straight to the alert
    try:
        from app.guidance import PreferenceStore
        store.preferences = PreferenceStore(tmp_path / "workspaces" / "local" / "preferences", store.workspace)
        when = datetime.fromisoformat(api_.get(f"/api/datasets/{pid}/refresh").json()["settings"]["next_run"])
        refresh.run_due(store, DashboardStore(tmp_path / "workspaces" / "local" / "dashboards"), fetch, when + timedelta(seconds=1))
    finally:
        refresh.RETRY_MINUTES = saved
    notes = ok(api_.get("/api/notifications"))["notifications"]
    assert notes[0]["title"] == "Targets: the scheduled refresh failed" and notes[0]["link"] == f"/datasets/{pid}/refresh"
