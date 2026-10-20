from test_chat import ScriptedAI
from test_report import SEGMENT, Project, clean_env, ok  # noqa: F401  (clean_env is an autouse fixture)


def published(tmp_path, *answers):
    p = Project(tmp_path, ScriptedAI(*answers))
    p.api.put("/api/settings", json={"api_key": "sk-secret-value-1234"})
    page = p.page()
    ok(p.visual(page, type="kpi", title="Orders", measures=["Orders"]))
    ok(p.visual(page, type="bar", title="Revenue by segment", measures=["Revenue"], group_by=SEGMENT))
    return p, page


def test_summary_bullets_are_checked_against_every_visual(tmp_path):
    p, _ = published(tmp_path, {"bullets": [
        "Retail brings in RM 50, about 78% of revenue.",  # from the bar chart
        "There were 5 orders in total.",                    # small counts are not checked
        "Online revenue reached RM 400.",                   # matches nothing
    ]})
    ok(p.api.post(f"{p.base}/publish"))
    bullets = ok(p.api.post(f"{p.base}/export/summary"))["bullets"]
    assert [b["unmatched"] for b in bullets] == [[], [], [400.0]]


def test_summary_needs_a_published_dashboard(tmp_path):
    p, _ = published(tmp_path)
    response = p.api.post(f"{p.base}/export/summary")
    assert response.status_code == 400 and "Publish the dashboard first" in response.json()["detail"]
