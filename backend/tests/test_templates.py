from test_report import SEGMENT, FakeAI, Project, clean_env, ok  # noqa: F401  (clean_env is an autouse fixture)

DESIGN = {
    "summary": "Sales pages.",
    "template": "sales",
    "pages": [{"title": "Are sales growing?", "visuals": [
        {"type": "kpi", "title": "Revenue", "measures": ["Revenue"], "kpi": {"direction": "higher_is_better"}},
        {"type": "bar", "title": "Revenue by segment", "measures": ["Revenue"], "group_by": SEGMENT},
    ]}],
}


def designed(tmp_path, body):
    ai = FakeAI(DESIGN)
    p = Project(tmp_path, ai)
    p.api.put("/api/settings", json={"api_key": "sk-secret-value-1234"})
    response = p.api.post(f"{p.base}/ai", json=body)
    return p, ai, response


def test_templates_are_listed(tmp_path):
    p = Project(tmp_path)
    listed = ok(p.api.get("/api/dashboard-templates"))
    assert [t["id"] for t in listed] == ["executive", "sales", "operations", "finance", "customers"]
    assert listed[0]["pages"] == ["How is the business doing?", "Where does it come from?"]


def test_a_template_and_a_description_steer_the_report_ai(tmp_path):
    p, ai, response = designed(tmp_path, {"template": "sales", "description": "for the regional managers' Monday call"})
    body = ok(response)
    prompt = ai.calls[0]["messages"][-1]["content"]
    assert "Follow this dashboard template" in prompt and "Template: Sales performance" in prompt
    assert "revenue by salesperson, agent or team (bar)" in prompt
    assert "for the regional managers' Monday call" in prompt and "within the template" in prompt
    assert body["template"] == "sales" and [pg["title"] for pg in body["pages"]] == ["Are sales growing?"]


def test_the_ai_can_choose_the_template(tmp_path):
    p, ai, response = designed(tmp_path, {"choose": True})
    body = ok(response)
    prompt = ai.calls[0]["messages"][-1]["content"]
    assert "pick the template below that fits this data best" in prompt
    assert all(f"id {t}:" in prompt for t in ("executive", "sales", "operations", "finance", "customers"))
    assert body["template"] == "sales"


def test_without_a_brief_the_ai_designs_freely_and_unknown_templates_are_refused(tmp_path):
    p, ai, response = designed(tmp_path, None)
    prompt = ai.calls[0]["messages"][-1]["content"]
    assert ok(response)["template"] is None and "Follow this dashboard template" not in prompt and "pick the template" not in prompt
    response = p.api.post(f"{p.base}/ai", json={"template": "nope"})
    assert response.status_code == 400 and "no template called 'nope'" in response.json()["detail"]
