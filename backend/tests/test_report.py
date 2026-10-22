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
        self.id = self.api.post("/api/datasets", json={"name": "R"}).json()["id"]
        base = f"/api/datasets/{self.id}"
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
        self.dataset = base
        self.publish()
        dash = self.api.post("/api/dashboards", json={"name": "Sales", "dataset_id": self.id}).json()
        self.base = f"/api/dashboards/{dash['id']}"

    def publish(self):
        return ok(self.api.post(f"{self.dataset}/publish", params={"confirm": True}))

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
    ok(p.api.delete(f"/api/datasets/{p.id}/model/date-table"))
    p.publish()
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


def test_the_house_style_advises_but_does_not_block(p):
    page = p.page()
    ok(p.visual(page, type="donut", title="Revenue share", measures=["Revenue"], group_by=SEGMENT))
    # A pie with more slices than the style likes is built, with a tip for the creator.
    ok(p.visual(page, type="pie", title="By month", measures=["Revenue"], group_by=[{"table": "Date", "column": "Year Month"}]))
    by_title = {v["title"]: v for v in p.visuals()}
    assert by_title["Revenue share"]["advice"] == []
    assert "sorted bar chart" in by_title["By month"]["advice"][0]
    assert by_title["By month"]["data"]["rows"]  # data is computed as for any chart


def test_stacked_area_and_scatter_charts(p):
    page = p.page()
    month = {"table": "Date", "column": "Year Month"}
    ok(p.visual(page, type="stacked_column", title="Revenue by month and segment", measures=["Revenue"], group_by=[month, SEGMENT[0]]))
    ok(p.visual(page, type="area", title="Revenue over time", measures=["Revenue"], group_by=[month]))
    ok(p.visual(page, type="scatter", title="Orders against revenue", measures=["Orders", "Revenue"], group_by=SEGMENT))
    stacked = next(v for v in p.visuals() if v["type"] == "stacked_column")
    assert len(stacked["data"]["columns"]) == 3  # category, stack, value
    bad = p.visual(page, type="stacked_bar", title="x", measures=["Revenue"], group_by=SEGMENT)
    assert bad.status_code == 400 and "stacked chart" in bad.json()["detail"]
    bad = p.visual(page, type="scatter", title="x", measures=["Revenue"], group_by=SEGMENT)
    assert bad.status_code == 400 and "two measures" in bad.json()["detail"]


def test_measures_that_cannot_reach_a_column_are_refused(p):
    page = p.page()
    p.api.post(f"/api/datasets/{p.id}/model/measures",
               json={"name": "Customers", "kind": "aggregate", "table": "customers", "aggregation": "count"})
    p.publish()
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
    pid = api.post("/api/datasets", json={"name": "Empty"}).json()["id"]
    api.put("/api/settings", json={"api_key": "sk-secret-value-1234"})
    api.post(f"/api/datasets/{pid}/sources/upload", files=[("files", ("t.csv", b"a\n1\n"))])
    ok(api.post(f"/api/datasets/{pid}/publish"))
    dash = api.post("/api/dashboards", json={"name": "Empty", "dataset_id": pid}).json()
    response = api.post(f"/api/dashboards/{dash['id']}/ai")
    assert response.status_code == 400 and "model first" in response.json()["detail"]


def test_running_totals_of_the_top_rows_are_recognised():
    data = {"rows": [["A", 50.0], ["B", 30.0], ["C", 15.0], ["D", 5.0]], "check": None}
    assert report.check_insight("The top three bring in 95 (95% of the total).", data)["verified"]
    assert not report.check_insight("The top three bring in 120.", data)["verified"]


# The report canvas (M13)


def test_visuals_move_and_resize_on_the_grid(p):
    page = p.page()
    ok(p.visual(page, type="bar", title="By segment", measures=["Revenue"], group_by=SEGMENT,
                layout={"x": 0, "y": 0, "w": 6, "h": 8}))
    visual = p.visuals()[0]
    assert visual["layout"] == {"x": 0, "y": 0, "w": 6, "h": 8} and visual["width"] == "half"
    moved = ok(p.api.put(f"{p.base}/pages/{page}/layout", json={"positions": {visual["id"]: {"x": 0, "y": 4, "w": 12, "h": 6}}}))
    assert moved["pages"][0]["visuals"][0]["width"] == "full"
    off_page = p.api.put(f"{p.base}/pages/{page}/layout", json={"positions": {visual["id"]: {"x": 8, "y": 0, "w": 6, "h": 6}}})
    assert off_page.status_code == 400 and "12-column" in off_page.json()["detail"]
    assert ok(p.api.post(f"{p.base}/undo"))["pages"][0]["visuals"][0]["layout"]["y"] == 0


def test_visuals_are_edited_by_hand_and_checked_again(p):
    page = p.page()
    ok(p.visual(page, type="bar", title="By segment", measures=["Revenue"], group_by=SEGMENT))
    visual = p.visuals()[0]
    changed = ok(p.api.patch(f"{p.base}/visuals/{visual['id']}", json={"type": "donut", "title": "Share by segment"}))
    shown = changed["pages"][0]["visuals"][0]
    assert shown["type"] == "donut" and shown["title"] == "Share by segment" and shown["data"]["rows"][0] == ["Retail", 50.0]
    refused = p.api.patch(f"{p.base}/visuals/{visual['id']}", json={"type": "scatter"})
    assert refused.status_code == 400 and "two measures" in refused.json()["detail"]
    fields = {t["name"]: t for t in changed["fields"]}
    assert "Revenue" in fields["sales"]["measures"] and {"name": "segment", "type": "text"} in fields["customers"]["columns"]


def test_pages_can_be_renamed(p):
    page = p.page()
    assert ok(p.api.patch(f"{p.base}/pages/{page}", json={"title": "Customers"}))["pages"][0]["title"] == "Customers"


# Slicers and page filters


RETAIL = [{"table": "customers", "column": "segment", "operator": "in", "values": ["Retail"]}]


def view_with(p, page, filters, side="draft"):
    return ok(p.api.get(p.base, params={"side": side, "page": page, "filters": json.dumps(filters)}))


def test_slicers_offer_values_and_filter_the_page_for_one_viewer(p):
    page = p.page()
    ok(p.visual(page, type="bar", title="By segment", measures=["Revenue"], group_by=SEGMENT))
    shown = ok(p.api.put(f"{p.base}/pages/{page}/slicers",
                         json={"slicers": [{"table": "customers", "column": "segment"}, {"table": "Date", "column": "Year"}]}))
    assert [s["column"] for s in shown["pages"][0]["slicers"]] == ["segment", "Year"]
    assert ok(p.api.get(f"{p.base}/values", params={"table": "customers", "column": "segment"}))["values"] == ["Online", "Retail"]

    filtered = view_with(p, page, RETAIL)["pages"][0]
    assert filtered["visuals"][0]["data"]["rows"] == [["Retail", 50.0]] and filtered["selected"][0]["values"] == ["Retail"]
    # Nothing is saved: the next look without choices shows everything.
    assert ok(p.api.get(p.base))["pages"][0]["visuals"][0]["data"]["rows"] == [["Retail", 50.0], ["Online", 14.0]]
    bad = p.api.get(p.base, params={"page": page, "filters": json.dumps([{"table": "customers", "column": "nope", "values": ["x"]}])})
    assert bad.status_code == 400


def test_a_year_slicer_keeps_kpi_comparisons(p):
    page = p.page()
    ok(p.visual(page, type="kpi", title="Revenue", measures=["Revenue"]))
    card = view_with(p, page, [{"table": "Date", "column": "Year", "operator": "in", "values": [2025]}])["pages"][0]["visuals"][0]["data"]
    # 2025 to date (to 2 Feb) against 2024 to 2 Feb: the slicer picks the year, last year is still compared.
    assert card["value"] == 24.0 and card["previous"] == 20.0 and card["delta"] == 4.0
    earlier = view_with(p, page, [{"table": "Date", "column": "Year", "operator": "in", "values": [2024]}])["pages"][0]["visuals"][0]["data"]
    assert earlier["value"] == 40.0 and earlier["previous"] is None


def test_page_filters_apply_for_everyone_and_are_checked(p):
    page = p.page()
    ok(p.visual(page, type="bar", title="By segment", measures=["Revenue"], group_by=SEGMENT))
    ok(p.api.put(f"{p.base}/pages/{page}/filters",
                 json={"filters": [{"table": "customers", "column": "segment", "operator": "in", "values": ["Online"]}]}))
    ok(p.api.post(f"{p.base}/publish"))
    published = ok(p.api.get(p.base, params={"side": "published"}))["pages"][0]
    assert published["visuals"][0]["data"]["rows"] == [["Online", 14.0]]
    wrong_type = p.api.put(f"{p.base}/pages/{page}/filters",
                           json={"filters": [{"table": "Date", "column": "Year", "operator": "equals", "value": "soon"}]})
    assert wrong_type.status_code == 400


def test_a_click_filters_the_rest_of_the_page_but_not_the_visual_clicked(p):
    page = p.page()
    ok(p.visual(page, type="bar", title="By segment", measures=["Revenue"], group_by=SEGMENT))
    ok(p.visual(page, type="kpi", title="Orders", measures=["Orders"]))
    bar = next(v for v in p.visuals() if v["type"] == "bar")
    picked = [{**RETAIL[0], "source": bar["id"]}]
    shown = {v["type"]: v for v in view_with(p, page, picked)["pages"][0]["visuals"]}
    assert shown["bar"]["data"]["rows"] == [["Retail", 50.0], ["Online", 14.0]]  # the clicked chart keeps every bar
    assert shown["kpi"]["data"]["value"] == 1  # Retail orders in 2025 to date; the card follows the click


def test_date_charts_drill_down_for_one_viewer(p):
    page = p.page()
    ok(p.visual(page, type="column", title="Revenue by year", measures=["Revenue"], group_by=[{"table": "Date", "column": "Year"}]))
    visual = p.visuals()[0]
    assert visual["drill_next"] == "Year Month" and visual["data"]["rows"] == [[2024, 40.0], [2025, 24.0]]
    drill = {visual["id"]: {"column": "Year Month", "filters": [{"table": "Date", "column": "Year", "operator": "in", "values": [2024]}]}}
    down = ok(p.api.get(p.base, params={"drill": json.dumps(drill)}))["pages"][0]["visuals"][0]
    assert down["group_by"][0]["column"] == "Year Month" and down["drill_next"] == "Date"
    assert [r[0] for r in down["data"]["rows"] if r[1]] == ["2024-01", "2024-02", "2024-11"]
    assert p.visuals()[0]["group_by"][0]["column"] == "Year"  # saved visual unchanged


# Time intelligence (M12)


def time_measure(p, name, base, calc, **extra):
    return p.api.post(f"{p.dataset}/model/measures", json={"name": name, "kind": "time", "base": base, "calc": calc,
                                                          "format": {"type": "currency", "decimals": 0}, **extra})


def by(p, measure, *columns, filters=()):
    groups = [{"table": t, "column": c} for t, c in columns]
    rows = ok(p.api.post(f"{p.dataset}/model/evaluate", json={"measures": [measure], "group_by": groups,
                                                              "filters": list(filters)}))["rows"]
    return {tuple(r[:-1]) if len(r) > 2 else r[0]: r[-1] for r in rows} if groups else rows[0][0]


MONTH = ("Date", "Year Month")


def test_year_to_date_and_last_year_by_month_and_year(p):
    ok(time_measure(p, "Revenue YTD", "Revenue", "ytd"))
    ok(time_measure(p, "Revenue LY", "revenue", "prior_year"))
    ok(time_measure(p, "Revenue LY YTD", "Revenue", "prior_year_ytd"))
    ytd = by(p, "Revenue YTD", MONTH)
    assert [ytd[m] for m in ("2024-01", "2024-02", "2024-10", "2024-11", "2024-12", "2025-01", "2025-02", "2025-12")] == \
        [20, 30, 30, 40, 40, 20, 24, 24]
    ly = by(p, "Revenue LY", ("Date", "Year"))
    assert ly.get(2024) is None and ly[2025] == 40
    ly_month = by(p, "Revenue LY", MONTH)
    assert (ly_month["2025-01"], ly_month["2025-02"], ly_month["2025-11"]) == (20, 10, 10)
    assert by(p, "Revenue LY YTD", MONTH)["2025-02"] == 30  # 2024, 1 Jan to 28 Feb
    # No date on show: up to the end of the dates in view, like Power BI.
    assert by(p, "Revenue YTD") == 24
    assert by(p, "Revenue YTD", filters=[{"table": "Date", "column": "Year", "operator": "equals", "value": 2024}]) == 40
    shown = {m["name"]: m for m in ok(p.api.get(f"{p.dataset}/model"))["measures"]}
    assert shown["Revenue LY"]["base"] == "Revenue" and shown["Revenue YTD"]["label"] == "[Revenue], year to date"


def test_rolling_months_formulas_and_other_columns(p):
    ok(time_measure(p, "Revenue 3M", "Revenue", "rolling", months=3))
    ok(time_measure(p, "Average order YTD", "Average order", "ytd"))
    ok(time_measure(p, "Revenue LY", "Revenue", "prior_year"))
    rolling = by(p, "Revenue 3M", MONTH)
    assert (rolling["2024-03"], rolling["2025-01"], rolling["2025-02"]) == (30, 30, 24)
    assert by(p, "Average order YTD", MONTH)["2025-02"] == 12  # Revenue YTD 24 over 2 orders YTD
    by_segment = by(p, "Revenue LY", ("customers", "segment"), ("Date", "Year"))
    assert by_segment[("Retail", 2025)] == 30 and by_segment[("Online", 2025)] == 10


def test_time_measures_are_checked(p):
    assert "needs a base measure" in time_measure(p, "X", "Profit", "ytd").json()["detail"]
    assert "must be one of" in time_measure(p, "X", "Revenue", "fortnightly").json()["detail"]
    assert "1 to 36 months" in time_measure(p, "X", "Revenue", "rolling", months=48).json()["detail"]
    ok(time_measure(p, "Revenue YTD", "Revenue", "ytd"))
    assert "another time measure" in time_measure(p, "X", "Revenue YTD", "prior_year").json()["detail"]
    # A rename follows into time measures.
    revenue = next(m for m in ok(p.api.get(f"{p.dataset}/model"))["measures"] if m["name"] == "Revenue")
    body = {k: revenue[k] for k in ("kind", "table", "aggregation", "expression", "filters", "format")}
    renamed = ok(p.api.put(f"{p.dataset}/model/measures/{revenue['id']}", json={**body, "name": "Sales"}))
    assert next(m for m in renamed["measures"] if m["name"] == "Revenue YTD")["base"] == "Sales"


def test_time_measures_need_the_date_table(p):
    ok(time_measure(p, "Revenue YTD", "Revenue", "ytd"))
    ok(p.api.delete(f"{p.dataset}/model/date-table"))
    broken = next(m for m in ok(p.api.get(f"{p.dataset}/model"))["measures"] if m["name"] == "Revenue YTD")
    assert "date table" in (broken["error"] or "")


def test_time_measures_become_dax():
    from app import pbi_dax
    dax = pbi_dax.from_measure({"kind": "time", "base": "Revenue", "calc": "rolling", "months": 3}, None)
    assert dax == "CALCULATE([Revenue], DATESINPERIOD('Date'[Date], MAX('Date'[Date]), -3, MONTH))"
    assert pbi_dax.from_measure({"kind": "time", "base": "Revenue", "calc": "ytd"}, None) == "TOTALYTD([Revenue], 'Date'[Date])"


REVENUE_PBI = {"name": "Revenue", "kind": "aggregate", "table": "Sales", "aggregation": "sum", "expression": "[Amount]", "filters": []}


@pytest.mark.parametrize("dax, calc, months", [
    ("TOTALYTD([Revenue], 'Date'[Date])", "ytd", None),
    ("CALCULATE([Revenue], DATESMTD('Date'[Date]))", "mtd", None),
    ("CALCULATE([Revenue], SAMEPERIODLASTYEAR('Date'[Date]))", "prior_year", None),
    ("CALCULATE([Revenue], DATEADD('Date'[Date], -1, YEAR))", "prior_year", None),
    ("CALCULATE([Revenue], DATESYTD(SAMEPERIODLASTYEAR('Date'[Date])))", "prior_year_ytd", None),
    ("CALCULATE([Revenue], DATESINPERIOD('Date'[Date], MAX('Date'[Date]), -3, MONTH))", "rolling", 3),
])
def test_power_bi_time_intelligence_is_imported(dax, calc, months):
    from app import pbi_dax
    ctx = pbi_dax.Context({"Sales": ["Amount"], "Date": ["Date"]}, {"revenue": REVENUE_PBI})
    proposal, helpers = pbi_dax.to_measure("Revenue T", dax, ctx)
    assert proposal == {"name": "Revenue T", "kind": "time", "base": "Revenue", "calc": calc, "months": months} and helpers == []


def test_power_bi_time_over_an_aggregation_and_inside_arithmetic():
    from app import pbi_dax
    ctx = pbi_dax.Context({"Sales": ["Amount"], "Date": ["Date"]}, {"revenue": REVENUE_PBI})
    proposal, helpers = pbi_dax.to_measure("Sales YTD", "TOTALYTD(SUM(Sales[Amount]), 'Date'[Date])", ctx)
    assert proposal["base"] == "Sales YTD (base)" and helpers[0]["aggregation"] == "sum"
    growth, parts = pbi_dax.to_measure(
        "Growth", "DIVIDE([Revenue] - CALCULATE([Revenue], SAMEPERIODLASTYEAR('Date'[Date])), "
                  "CALCULATE([Revenue], SAMEPERIODLASTYEAR('Date'[Date])))", ctx)
    assert growth["kind"] == "formula" and [p["calc"] for p in parts] == ["prior_year", "prior_year"]


# Measure filters on related tables (M12)


def test_measures_filter_on_related_tables(p):
    retail = {"name": "Retail revenue", "kind": "aggregate", "table": "sales", "aggregation": "sum", "expression": "[qty] * [price]",
              "filters": [{"table": "customers", "column": "segment", "operator": "equals", "value": "Retail"}]}
    shown = ok(p.api.post(f"{p.dataset}/model/measures", json=retail))
    measure = next(m for m in shown["measures"] if m["name"] == "Retail revenue")
    assert measure["value"] == 50 and "customers[segment] equals Retail" in measure["label"]
    assert by(p, "Retail revenue", ("Date", "Year")) == {2024: 30, 2025: 20}
    ok(time_measure(p, "Retail revenue LY", "Retail revenue", "prior_year"))
    assert by(p, "Retail revenue LY", ("Date", "Year"))[2025] == 30
    bad = p.api.post(f"{p.dataset}/model/measures", json={**retail, "name": "X", "filters": [
        {"table": "customers", "column": "nope", "operator": "equals", "value": "Retail"}]})
    assert bad.status_code == 400


def test_power_bi_filters_on_related_tables_import_and_export():
    from app import pbi_dax
    ctx = pbi_dax.Context({"Sales": ["Amount"], "Customers": ["Segment"]}, {})
    proposal, _ = pbi_dax.to_measure("Retail", "CALCULATE(SUM(Sales[Amount]), Customers[Segment] IN {\"Retail\", \"SME\"})", ctx)
    assert proposal["filters"] == [{"column": "Segment", "operator": "in", "values": ["Retail", "SME"], "table": "Customers"}]
    dax = pbi_dax.from_measure({**proposal, "table": "s1", "filters": [{**proposal["filters"][0], "table": "c1"}]},
                               "Sales", {"s1": "Sales", "c1": "Customers"})
    assert dax == "CALCULATE(SUM('Sales'[Amount]), KEEPFILTERS('Customers'[Segment] IN {\"Retail\", \"SME\"}))"


# Conditions in measures (M12)


def measure(p, **body):
    return p.api.post(f"{p.dataset}/model/measures", json={"format": {"type": "number", "decimals": 0}, **body})


def test_conditions_in_row_expressions_and_formulas(p):
    # Row level: only the big lines, and a text comparison on a related lookup-free column.
    shown = ok(measure(p, name="Big line revenue", kind="aggregate", table="sales", aggregation="sum",
                       expression="IF([qty] >= 2, [qty] * [price], 0)"))
    assert next(m for m in shown["measures"] if m["name"] == "Big line revenue")["value"] == 40  # 2x10 + 5x4
    shown = ok(measure(p, name="C1 orders", kind="aggregate", table="sales", aggregation="sum",
                       expression='SWITCH([customer], "C1", 1, 0)'))
    assert next(m for m in shown["measures"] if m["name"] == "C1 orders")["value"] == 3
    # Formula level: guard a ratio, and AND / NOT.
    ok(time_measure(p, "Revenue LY", "Revenue", "prior_year"))
    ok(measure(p, name="Growth", kind="formula", expression="IF([Revenue LY] > 0, ([Revenue] - [Revenue LY]) / [Revenue LY], 0)"))
    growth = by(p, "Growth", ("Date", "Year"))
    assert growth[2024] == 0 and growth[2025] == -0.4  # 24 against 40
    ok(measure(p, name="Busy", kind="formula", expression="IF(AND([Orders] > 1, NOT([Revenue] < 10)), 1, 0)"))
    assert by(p, "Busy", ("Date", "Year")) == {2024: 1, 2025: 1}


@pytest.mark.parametrize("expression, message", [
    ('IF([qty] > 1, "big", "small")', "can only be compared"),
    ("ROUND([qty])", "Unknown function"),
    ("IF([qty] > 1)", "IF takes"),
    ("[customer] * 2", "number columns"),
])
def test_bad_conditions_are_refused(p, expression, message):
    response = measure(p, name="X", kind="aggregate", table="sales", aggregation="sum", expression=expression)
    assert response.status_code == 400 and message in response.json()["detail"]


def test_power_bi_conditions_import_and_export():
    from app import pbi_dax
    ctx = pbi_dax.Context({"Sales": ["Qty", "Price", "Region"]}, {"revenue": REVENUE_PBI})
    row, _ = pbi_dax.to_measure("Big", "SUMX(Sales, IF(Sales[Qty] >= 2 && Sales[Region] = \"North\", Sales[Qty] * Sales[Price], 0))", ctx)
    assert row["expression"] == 'IF(AND(([Qty] >= 2), ([Region] = "North")), ([Qty] * [Price]), 0)'
    tiers, _ = pbi_dax.to_measure("Tier", "SWITCH(TRUE(), [Revenue] > 100, 3, [Revenue] > 10, 2, 1)", ctx)
    assert tiers["expression"] == "IF(([Revenue] > 100), 3, IF(([Revenue] > 10), 2, 1))"
    back = pbi_dax.from_measure({"kind": "formula", "expression": tiers["expression"]}, None)
    assert back == "IF(([Revenue] > 100), 3, IF(([Revenue] > 10), 2, 1))"
    from app import model_expr
    assert model_expr.parse(row["expression"])  # what was imported parses


# Inactive relationships (M12)


ORDERS = "order,ordered,shipped,amount\n1,2024-01-30,2024-02-02,10\n2,2024-02-10,2024-02-11,5\n3,2024-03-30,2024-04-01,1\n"


def test_a_measure_can_use_an_inactive_relationship(tmp_path):
    api = TestClient(create_app(data_dir=tmp_path, client_factory=lambda s: None))
    pid = api.post("/api/datasets", json={"name": "O"}).json()["id"]
    base = f"/api/datasets/{pid}"
    api.post(f"{base}/sources/upload", files=[("files", ("orders.csv", ORDERS.encode()))])
    query = api.get(f"{base}/transform").json()["queries"][0]["id"]
    for column, to in (("ordered", "date"), ("shipped", "date"), ("amount", "decimal")):
        ok(api.post(f"{base}/transform/queries/{query}/steps", json={"op": "change_type", "params": {"column": column, "to": to}}))
    m = f"{base}/model"
    ok(api.put(f"{m}/date-table", json={}))
    ok(api.post(f"{m}/relationships", json={"from_table": "orders", "from_column": "ordered", "to_table": "Date", "to_column": "Date"}))
    rels = ok(api.post(f"{m}/relationships", json={"from_table": "orders", "from_column": "shipped", "to_table": "Date", "to_column": "Date"}))["relationships"]
    shipped = next(r for r in rels if r["from_column"] == "shipped")
    assert shipped["active"] is False  # only one path between two tables is active, as in Power BI
    amount = {"kind": "aggregate", "table": "orders", "aggregation": "sum", "expression": "[amount]", "format": {"type": "number"}}
    ok(api.post(f"{m}/measures", json={"name": "Ordered", **amount}))
    shown = ok(api.post(f"{m}/measures", json={"name": "Shipped", **amount, "use_relationship": shipped["id"]}))
    assert next(x for x in shown["measures"] if x["name"] == "Shipped")["label"].endswith("by orders[shipped]")

    def month(name):
        rows = ok(api.post(f"{m}/evaluate", json={"measures": [name], "group_by": [{"table": "Date", "column": "Year Month"}]}))["rows"]
        return {r[0]: r[1] for r in rows if r[1]}

    assert month("Ordered") == {"2024-01": 10, "2024-02": 5, "2024-03": 1}
    assert month("Shipped") == {"2024-02": 15, "2024-04": 1}
    ok(api.post(f"{m}/measures", json={"name": "Shipped YTD", "kind": "time", "base": "Shipped", "calc": "ytd", "format": {"type": "number"}}))
    assert month("Shipped YTD")["2024-04"] == 16
    refused = api.post(f"{m}/measures", json={"name": "X", "kind": "formula", "expression": "[Ordered] * 2", "use_relationship": shipped["id"]})
    assert refused.status_code == 400 and "Only a measure that adds up" in refused.json()["detail"]
    ok(api.delete(f"{m}/relationships/{shipped['id']}"))
    broken = next(x for x in ok(api.get(m))["measures"] if x["name"] == "Shipped")
    assert "was removed" in (broken["error"] or "")


def test_power_bi_userelationship_imports_and_exports():
    from app import pbi_dax
    ctx = pbi_dax.Context({"Orders": ["Amount", "Shipped"], "Date": ["Date"]}, {})
    proposal, _ = pbi_dax.to_measure("Shipped", "CALCULATE(SUM(Orders[Amount]), USERELATIONSHIP(Orders[Shipped], 'Date'[Date]))", ctx)
    assert proposal["use_relationship"] == {"from": ["Orders", "Shipped"], "to": ["Date", "Date"]} and proposal["filters"] == []
    dax = pbi_dax.from_measure({**proposal, "_uses": ("Orders", "Shipped", "Date", "Date")}, "Orders")
    assert dax == "CALCULATE(SUM('Orders'[Amount]), USERELATIONSHIP('Orders'[Shipped], 'Date'[Date]))"
