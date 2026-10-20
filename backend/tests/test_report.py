import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import report
from app.main import create_app
from app.settings import ENV_VARS


class FakeAI:
    """Answers design_report, then write_insights (insights get the real visual ids from the prompt)."""

    def __init__(self, design, insight_text="Retail made RM 40, the largest share."):
        self.design, self.insight_text = design, insight_text
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        prompt = kwargs["messages"][-1]["content"]
        if "Write the insights" in prompt:
            pages = json.loads(prompt.split("data:\n", 1)[1].rsplit("\n\nWrite", 1)[0])
            answer = {
                "visuals": [{"id": v["id"], "insight": self.insight_text} for p in pages for v in p["visuals"]],
                "pages": [{"id": p["id"], "summary": "Sales are led by Retail."} for p in pages],
            }
        else:
            answer = self.design
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(answer), tool_calls=None))])


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in ENV_VARS.values():
        monkeypatch.delenv(var, raising=False)


SALES = (
    "order,day,customer,qty,price\n"
    "1,2024-01-05,C1,2,10\n"
    "2,2024-02-10,C2,1,10\n"
    "3,2024-11-01,C1,1,10\n"
    "4,2025-01-20,C1,5,4\n"
    "5,2025-02-02,C3,1,4\n"
)
CUSTOMERS = "customer,segment\nC1,Retail\nC2,Online\nC3,Online\n"


class Project:
    def __init__(self, tmp_path, ai=None):
        self.api = TestClient(create_app(data_dir=tmp_path, client_factory=lambda s: ai))
        self.id = self.api.post("/api/projects", json={"name": "R"}).json()["id"]
        base = f"/api/projects/{self.id}"
        for name, text in (("sales.csv", SALES), ("customers.csv", CUSTOMERS)):
            self.api.post(f"{base}/sources/upload", files=[("files", (name, text.encode()))])
        queries = {q["name"]: q["id"] for q in self.api.get(f"{base}/transform").json()["queries"]}
        for column, to in (("day", "date"), ("qty", "integer"), ("price", "decimal")):
            self.api.post(f"{base}/transform/queries/{queries['sales']}/steps",
                          json={"op": "change_type", "params": {"column": column, "to": to}})
        m = f"{base}/model"
        self.api.put(f"{m}/date-table", json={})
        for a, ac, b, bc in (("sales", "day", "Date", "Date"), ("sales", "customer", "customers", "customer")):
            self.api.post(f"{m}/relationships", json={"from_table": a, "from_column": ac, "to_table": b, "to_column": bc})
        self.api.post(f"{m}/measures", json={"name": "Revenue", "kind": "aggregate", "table": "sales", "aggregation": "sum",
                                             "expression": "[qty] * [price]", "format": {"type": "currency", "decimals": 0}})
        self.api.post(f"{m}/measures", json={"name": "Orders", "kind": "aggregate", "table": "sales", "aggregation": "count"})
        self.api.post(f"{m}/measures", json={"name": "Average order", "kind": "formula", "expression": "[Revenue] / [Orders]"})
        self.base = f"{base}/report"

    def page(self, title="Sales overview"):
        return ok(self.api.post(f"{self.base}/pages", json={"title": title}))["pages"][-1]["id"]

    def visual(self, page_id, **body):
        return self.api.post(f"{self.base}/pages/{page_id}/visuals", json=body)

    def visuals(self):
        return [v for page in ok(self.api.get(self.base))["pages"] for v in page["visuals"]]


def ok(response):
    assert response.status_code == 200, response.json()
    return response.json()


@pytest.fixture
def p(tmp_path):
    return Project(tmp_path)


SEGMENT = [{"table": "customers", "column": "segment"}]


# KPI cards


def test_kpi_card_compares_year_to_date_with_the_same_period_last_year(p):
    page = p.page()
    ok(p.visual(page, type="kpi", title="Revenue", measures=["Revenue"],
                kpi={"driver": {"table": "customers", "column": "segment"}}))
    data = p.visuals()[0]["data"]
    # 2025 to 2 Feb: 20 + 4 = 24. Same period 2024 (to 2 Feb): 20.
    assert (data["value"], data["previous"], data["delta"]) == (24.0, 20.0, 4.0)
    assert data["delta_pct"] == pytest.approx(0.2)
    assert data["period"] == "2025 to date (to 2025-02-02)" and data["comparison"] == "vs same period 2024"
    # The 12 months to February 2025 start in March 2024; only months with sales are plotted.
    assert [s["label"] for s in data["sparkline"]] == ["2024-11", "2025-01", "2025-02"]
    assert data["driver"]["name"] == "Online" and data["driver"]["delta"] == 4.0


def test_kpi_without_a_date_table_says_why(p):
    ok(p.api.delete(f"/api/projects/{p.id}/model/date-table"))
    page = p.page()
    ok(p.visual(page, type="kpi", title="Orders", measures=["Orders"]))
    data = p.visuals()[0]["data"]
    assert data["value"] == 5 and data["delta"] is None and "no date table" in data["note"]


def test_at_most_seven_kpis_per_page(p):
    page = p.page()
    for i in range(7):
        ok(p.visual(page, type="kpi", title=f"Orders {i}", measures=["Orders"]))
    response = p.visual(page, type="kpi", title="One too many", measures=["Orders"])
    assert response.status_code == 400 and "at most 7 KPI" in response.json()["detail"]


# Charts


def test_bar_is_sorted_by_value_and_adds_up(p):
    page = p.page()
    ok(p.visual(page, type="bar", title="Revenue by segment", measures=["Revenue"], group_by=SEGMENT))
    data = p.visuals()[0]["data"]
    assert data["rows"] == [["Retail", 50.0], ["Online", 14.0]]
    assert data["check"] == {"total": 64.0, "parts": 64.0, "ok": True}


def test_line_keeps_time_order(p):
    page = p.page()
    ok(p.visual(page, type="line", title="Revenue by month", measures=["Revenue"],
                group_by=[{"table": "Date", "column": "Year Month"}]))
    rows = p.visuals()[0]["data"]["rows"]
    assert [r[0] for r in rows] == ["2024-01", "2024-02", "2024-11", "2025-01", "2025-02"]


def test_layout_puts_kpis_first_and_tables_last(p):
    page = p.page()
    ok(p.visual(page, type="table", title="Detail", measures=["Revenue", "Orders"], group_by=SEGMENT))
    ok(p.visual(page, type="bar", title="By segment", measures=["Revenue"], group_by=SEGMENT))
    ok(p.visual(page, type="kpi", title="Revenue", measures=["Revenue"]))
    assert [v["type"] for v in p.visuals()] == ["kpi", "bar", "table"]
    assert [v["width"] for v in p.visuals()] == ["half", "half", "full"]


@pytest.mark.parametrize(
    "body, message",
    [
        ({"type": "gauge", "title": "x", "measures": ["Revenue"]}, "Gauges"),
        ({"type": "bar", "title": "x", "measures": ["Revenue"]}, "exactly one column"),
        ({"type": "kpi", "title": "x", "measures": ["Revenue", "Orders"]}, "exactly one measure"),
        ({"type": "bar", "title": "x", "measures": ["Nope"], "group_by": SEGMENT}, "no measure called"),
        ({"type": "bar", "title": " ", "measures": ["Revenue"], "group_by": SEGMENT}, "plain-words title"),
        ({"type": "bar", "title": "x", "measures": ["Revenue"], "group_by": [{"table": "sales", "column": "nope"}]}, "no column"),
    ],
)
def test_bad_visuals_are_refused(p, body, message):
    page = p.page()
    response = p.visual(page, **body)
    assert response.status_code == 400 and message in response.json()["detail"]


def test_donut_only_for_three_slices_or_fewer(p):
    page = p.page()
    ok(p.visual(page, type="donut", title="Revenue share", measures=["Revenue"], group_by=SEGMENT))
    response = p.visual(page, type="donut", title="By month", measures=["Revenue"],
                        group_by=[{"table": "Date", "column": "Year Month"}])
    assert response.status_code == 400 and "sorted bar chart" in response.json()["detail"]


def test_measures_that_cannot_reach_a_column_are_refused(p):
    page = p.page()
    p.api.post(f"/api/projects/{p.id}/model/measures",
               json={"name": "Customers", "kind": "aggregate", "table": "customers", "aggregation": "count"})
    response = p.visual(page, type="bar", title="x", measures=["Customers"], group_by=[{"table": "Date", "column": "Year"}])
    assert response.status_code == 400 and "no relationship leads there" in response.json()["detail"]


# Insight number checks


@pytest.mark.parametrize(
    "text, verified",
    [
        ("Retail brought in RM 50, about 78% of revenue.", True),
        ("Revenue reached RM 64 across 2 segments.", True),
        ("Online grew to RM 900.", False),
        ("Retail is 3.6 times Online.", True),
    ],
)
def test_insight_numbers_are_checked_against_the_data(text, verified):
    data = {"rows": [["Retail", 50.0], ["Online", 14.0]], "check": {"total": 64.0, "parts": 64.0, "ok": True}}
    assert report.check_insight(text, data)["verified"] is verified


def test_kpi_insight_numbers(tmp_path):
    data = {"value": 24.0, "previous": 20.0, "delta": 4.0, "delta_pct": 0.2, "sparkline": [], "driver": None}
    assert report.check_insight("Revenue is RM 24, up 20% on last year.", data)["verified"]
    assert not report.check_insight("Revenue is up 35% on last year.", data)["verified"]


def test_rounded_numbers_match_at_the_precision_written():
    data = {"value": 97, "previous": 103, "delta": -6, "delta_pct": -0.0583, "sparkline": [], "driver": None}
    assert report.check_insight("Orders are down 6 (6%) on last year.", data)["verified"]  # 5.83% written as 6%
    assert not report.check_insight("Orders are down 5.0% on last year.", data)["verified"]  # 5.0 is too precise to be 5.83
    money = {"value": 1_234_567.0, "previous": None, "delta": None, "delta_pct": None, "sparkline": [], "driver": None}
    assert report.check_insight("Revenue reached RM 1.2M.", money)["verified"]


# Undo and AI


def test_undo_and_remove(p):
    page = p.page()
    ok(p.visual(page, type="bar", title="By segment", measures=["Revenue"], group_by=SEGMENT))
    visual = p.visuals()[0]
    ok(p.api.delete(f"{p.base}/visuals/{visual['id']}"))
    assert p.visuals() == []
    ok(p.api.post(f"{p.base}/undo"))
    assert [v["title"] for v in p.visuals()] == ["By segment"]


def test_ai_designs_pages_writes_checked_insights_and_undoes_as_one(tmp_path):
    ai = FakeAI({
        "summary": "One sales page.",
        "pages": [
            {"title": "How are sales going?", "question": "Are sales growing?", "visuals": [
                {"type": "kpi", "title": "Revenue", "measures": ["Revenue"], "kpi": {"direction": "higher_is_better"}},
                {"type": "bar", "title": "Revenue by segment", "measures": ["Revenue"], "group_by": SEGMENT},
                {"type": "gauge", "title": "Target", "measures": ["Revenue"]},
            ]},
            {"title": "Empty page", "visuals": [{"type": "bar", "title": "Bad", "measures": ["Nope"], "group_by": SEGMENT}]},
        ],
    })
    p = Project(tmp_path, ai)
    p.api.put("/api/settings", json={"api_key": "sk-secret-value-1234"})
    body = ok(p.api.post(f"{p.base}/ai"))

    assert [page["title"] for page in body["pages"]] == ["How are sales going?"]  # the page with nothing valid is dropped
    assert {r["label"] for r in body["refused"]} == {"Target", "Bad"}
    visuals = body["pages"][0]["visuals"]
    assert [v["type"] for v in visuals] == ["kpi", "bar"] and all(v["status"] == "AI" for v in visuals)
    bar = visuals[1]
    assert bar["insight"]["text"] == "Retail made RM 40, the largest share."
    assert bar["insight"]["check"] == {"verified": False, "unmatched": [40.0]}  # Retail is RM 50, so it is flagged
    assert body["pages"][0]["summary"] == "Sales are led by Retail."
    assert "Measures:" in ai.calls[0]["messages"][-1]["content"]

    ok(p.api.post(f"{p.base}/undo"))
    assert ok(p.api.get(p.base))["pages"] == []


def test_ai_report_needs_measures(tmp_path):
    api = TestClient(create_app(data_dir=tmp_path, client_factory=lambda s: None))
    pid = api.post("/api/projects", json={"name": "Empty"}).json()["id"]
    api.put("/api/settings", json={"api_key": "sk-secret-value-1234"})
    response = api.post(f"/api/projects/{pid}/report/ai")
    assert response.status_code == 400 and "model first" in response.json()["detail"]
