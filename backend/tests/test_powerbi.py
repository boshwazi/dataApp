"""Power BI import and export: DAX translation, .pbix and .pbip import, and a full round trip."""

import io
import zipfile

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import pbi_dax, pbi_model
from app.main import create_app
from app.pbi_model import PbiColumn, PbiMeasure, PbiModel, PbiRelationship, PbiTable
from app.settings import ENV_VARS


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in ENV_VARS.values():
        monkeypatch.delenv(var, raising=False)


def ok(response):
    assert response.status_code == 200, response.json()
    return response.json()


# DAX → measures

TABLES = {"Sales": ["Amount", "Qty", "Price", "Status", "Region"], "Customers": ["Segment"]}


def translate(dax, measures=None, name="M"):
    ctx = pbi_dax.Context(tables=TABLES, measures=measures or {})
    return pbi_dax.to_measure(name, dax, ctx)


@pytest.mark.parametrize("dax, expected", [
    ("SUM ( Sales[Amount] )", {"kind": "aggregate", "table": "Sales", "aggregation": "sum", "expression": "[Amount]"}),
    ("COUNTROWS('Sales')", {"aggregation": "count", "expression": None}),
    ("DISTINCTCOUNT(Sales[Region])", {"aggregation": "count_distinct", "expression": "[Region]"}),
    ("SUMX ( Sales, Sales[Qty] * Sales[Price] )", {"aggregation": "sum", "expression": "[Qty] * [Price]"}),
    ('CALCULATE ( SUM(Sales[Amount]), Sales[Status] = "Paid", Sales[Qty] >= 2 )',
     {"filters": [{"column": "Status", "operator": "equals", "value": "Paid"},
                  {"column": "Qty", "operator": "at_least", "value": 2}]}),
    ('CALCULATE(COUNTROWS(Sales), KEEPFILTERS(Sales[Region] IN {"North", "South"}), ISBLANK(Sales[Status]))',
     {"filters": [{"column": "Region", "operator": "in", "values": ["North", "South"]},
                  {"column": "Status", "operator": "is_blank"}]}),
])
def test_common_dax_patterns_become_catalogue_measures(dax, expected):
    proposal, helpers = translate(dax)
    assert helpers == [] and all(proposal[k] == v for k, v in expected.items())


def test_formulas_and_measure_references():
    total = {"name": "Total", "kind": "aggregate", "table": "Sales", "aggregation": "sum", "expression": "[Amount]", "filters": []}
    orders = {"name": "Orders", "kind": "aggregate", "table": "Sales", "aggregation": "count", "expression": None, "filters": []}
    known = {"total": total, "orders": orders}
    assert translate("DIVIDE ( [Total], [Orders] )", known)[0] == {"name": "M", "kind": "formula", "expression": "[Total] / [Orders]"}
    pending, _ = translate('CALCULATE([Total], Sales[Status] = "Pending")', known)
    assert pending["kind"] == "aggregate" and pending["filters"] == [{"column": "Status", "operator": "equals", "value": "Pending"}]
    net, helpers = translate("[Total] - SUMX(Sales, Sales[Qty] * Sales[Price])", known, name="Net")
    assert net["expression"] == "[Total] - [Net (part 1)]" and helpers[0]["expression"] == "[Qty] * [Price]"


@pytest.mark.parametrize("dax, why", [
    ('IF([Total] > 1, "a", "b")', "a condition (IF)"),
    ("CALCULATE([Total], PARALLELPERIOD('Date'[Date], -1, YEAR))", "time intelligence"),
    ("VAR x = 1 RETURN x", "variables"),
])
def test_what_cannot_be_converted_says_why(dax, why):
    total = {"name": "Total", "kind": "aggregate", "table": "Sales", "aggregation": "sum", "expression": "[Amount]", "filters": []}
    with pytest.raises(pbi_dax.Untranslatable, match=why.replace("(", r"\(").replace(")", r"\)")):
        translate(dax, {"total": total})


def test_catalogue_measures_become_dax():
    measure = {"kind": "aggregate", "aggregation": "sum", "expression": "[Qty] * [Price]",
               "filters": [{"column": "Status", "operator": "in", "values": ["Paid", "Open"]}]}
    assert pbi_dax.from_measure(measure, "Sales") == \
        "CALCULATE(SUMX('Sales', ('Sales'[Qty] * 'Sales'[Price])), KEEPFILTERS('Sales'[Status] IN {\"Paid\", \"Open\"}))"
    assert pbi_dax.from_measure({"kind": "formula", "expression": "[A] / [B]"}, None) == "DIVIDE([A], [B])"
    assert pbi_dax.format_string({"type": "currency", "decimals": 2, "currency": "RM"}) == '"RM "#,##0.00'
    assert pbi_dax.format_from('"RM "#,##0.00') == {"type": "currency", "decimals": 2, "currency": "RM"}
    assert pbi_dax.format_from("0.0%") == {"type": "percent", "decimals": 1}


# Import from a .pbix (simulated: the model and data PBIXRay would return)

SALES = pd.DataFrame({
    "Order Date": pd.to_datetime(["2024-01-05", "2024-02-10", "2025-01-03", "2025-03-01"]),
    "CustomerKey": [1, 2, 1, 3], "Amount": [100.0, 50.0, 70.0, 30.0], "Status": ["Paid", "Paid", "Pending", "Paid"],
})
CUSTOMERS = pd.DataFrame({"CustomerKey": [1, 2, 3], "Segment": ["Retail", "Online", "Online"]})
DATES = pd.DataFrame({"Date": pd.date_range("2024-01-01", "2025-12-31")})


def fake_pbix() -> PbiModel:
    def kind(dtype):
        text = str(dtype)
        return "date" if text.startswith("datetime") else {"int64": "integer", "float64": "decimal"}.get(text, "text")

    cols = lambda frame: [PbiColumn(c, kind(t)) for c, t in frame.dtypes.items()]
    return PbiModel(
        name="Shop", kind="pbix",
        tables=[PbiTable("Sales", cols(SALES), data=SALES), PbiTable("Customers", cols(CUSTOMERS), data=CUSTOMERS),
                PbiTable("Date", cols(DATES), data=DATES, is_date_table=True), PbiTable("_Measures", [PbiColumn("Column")])],
        measures=[PbiMeasure("Revenue", "_Measures", "SUM(Sales[Amount])", '"RM "#,##0'),
                  PbiMeasure("Paid", "_Measures", 'CALCULATE([Revenue], Sales[Status] = "Paid")'),
                  PbiMeasure("Share paid", "_Measures", "DIVIDE([Paid], [Revenue])", "0%"),
                  PbiMeasure("Colour", "_Measures", 'IF([Revenue] > 100, "#f00", "#0f0")')],
        relationships=[PbiRelationship("Sales", "CustomerKey", "Customers", "CustomerKey"),
                       PbiRelationship("Sales", "Order Date", "Date", "Date")])


@pytest.fixture
def api(tmp_path):
    return TestClient(create_app(data_dir=tmp_path, client_factory=lambda s: None))


def test_a_pbix_brings_its_model_and_data(api, monkeypatch):
    monkeypatch.setattr(pbi_model, "read_pbix", lambda data, name: fake_pbix())
    ds = ok(api.post("/api/datasets", json={"name": "Shop"}))["id"]
    body = ok(api.post(f"/api/datasets/{ds}/sources/powerbi", files=[("file", ("Shop.pbix", b"x"))]))
    assert [s["table"] for s in body["added"]] == ["sales", "customers"]  # Date and _Measures are not loaded as data
    report = body["report"]
    assert [m["measure"] for m in report["measures"]] == ["Revenue", "Paid", "Share paid"]
    assert report["not_converted"][0]["measure"] == "Colour" and "IF" in report["not_converted"][0]["why"]
    assert all(r["added"] for r in report["relationships"])
    assert any("replaced by datawazi's Date table" in n for n in report["notes"])
    assert "snapshot" in report["notes"][0] and "Refresh from a newer .pbix" in report["notes"][0]

    result = ok(api.post(f"/api/datasets/{ds}/model/evaluate",
                         json={"measures": ["Revenue", "Paid", "Share paid"], "group_by": [{"table": "Date", "column": "Year"}]}))
    assert result["rows"] == [[2024, 150.0, 150.0, 1.0], [2025, 100.0, 30.0, 0.3]]
    measures = {m["name"]: m for m in ok(api.get(f"/api/datasets/{ds}/model"))["measures"]}
    assert measures["Revenue"]["format"] == {"type": "currency", "decimals": 0, "currency": "RM"}
    info = ok(api.get(f"/api/datasets/{ds}"))["powerbi"]
    assert info["file"] == "Shop" and info["waiting_for"] == [] and "pending" not in info


def test_bad_uploads_are_explained(api):
    ds = ok(api.post("/api/datasets", json={"name": "x"}))["id"]
    for name, data, message in (("model.pbip", b"{}", "Zip the whole project folder"),
                                ("notes.txt", b"hi", "Upload a .pbix file"),
                                ("project.zip", b"not a zip", "as a .zip")):
        response = api.post(f"/api/datasets/{ds}/sources/powerbi", files=[("file", (name, data))])
        assert response.status_code == 400 and message in response.json()["detail"], name


# Export to .pbip, then back in: the same model and the same numbers

SALES_CSV = "order,day,customer,qty,price,status\n1,2024-01-05,C1,2,10,Paid\n2,2024-02-10,C2,1,10,Open\n3,2025-03-01,C1,4,2.5,Paid\n"
CUSTOMERS_CSV = "customer,segment\nC1,Retail\nC2,Online\n"


def build_dataset(api) -> str:
    ds = ok(api.post("/api/datasets", json={"name": "Round trip"}))["id"]
    for name, text in (("sales.csv", SALES_CSV), ("customers.csv", CUSTOMERS_CSV)):
        ok(api.post(f"/api/datasets/{ds}/sources/upload", files=[("files", (name, text.encode()))]))
    queries = {q["name"]: q["id"] for q in ok(api.get(f"/api/datasets/{ds}/transform"))["queries"]}
    for column, to in (("day", "date"), ("qty", "integer"), ("price", "decimal")):
        ok(api.post(f"/api/datasets/{ds}/transform/queries/{queries['sales']}/steps",
                    json={"op": "change_type", "params": {"column": column, "to": to}}))
    m = f"/api/datasets/{ds}/model"
    ok(api.put(f"{m}/date-table", json={}))
    for a, ac, b, bc in (("sales", "day", "Date", "Date"), ("sales", "customer", "customers", "customer")):
        ok(api.post(f"{m}/relationships", json={"from_table": a, "from_column": ac, "to_table": b, "to_column": bc}))
    for body in (
        {"name": "Revenue", "kind": "aggregate", "table": "sales", "aggregation": "sum", "expression": "[qty] * [price]",
         "format": {"type": "currency", "decimals": 2, "currency": "RM"}},
        {"name": "Orders", "kind": "aggregate", "table": "sales", "aggregation": "count"},
        {"name": "Paid revenue", "kind": "aggregate", "table": "sales", "aggregation": "sum", "expression": "[qty] * [price]",
         "filters": [{"column": "status", "operator": "equals", "value": "Paid"}]},
        {"name": "Average order", "kind": "formula", "expression": "[Revenue] / [Orders]"},
    ):
        ok(api.post(f"{m}/measures", json=body))
    return ds


def numbers(api, ds):
    return ok(api.post(f"/api/datasets/{ds}/model/evaluate",
                       json={"measures": ["Revenue", "Orders", "Paid revenue", "Average order"],
                             "group_by": [{"table": "Date", "column": "Year"}, {"table": "customers", "column": "segment"}]}))["rows"]


def test_export_writes_a_power_bi_project(api):
    ds = build_dataset(api)
    response = api.get(f"/api/datasets/{ds}/powerbi/export")
    assert response.status_code == 200 and response.headers["content-type"] == "application/zip"
    files = zipfile.ZipFile(io.BytesIO(response.content)).namelist()
    for path in ("Round trip/Round trip.pbip", "Round trip/Round trip.SemanticModel/definition.pbism",
                 "Round trip/Round trip.SemanticModel/definition/tables/sales.tmdl",
                 "Round trip/Round trip.SemanticModel/definition/relationships.tmdl",
                 "Round trip/Round trip.Report/definition.pbir", "Round trip/data/sales.parquet", "Round trip/data/Date.parquet"):
        assert path in files, path
    sales = zipfile.ZipFile(io.BytesIO(response.content)).read("Round trip/Round trip.SemanticModel/definition/tables/sales.tmdl").decode()
    assert "measure Revenue = SUMX('sales', ('sales'[qty] * 'sales'[price]))" in sales
    assert "measure 'Average order' = DIVIDE([Revenue], [Orders])" in sales
    assert 'Parquet.Document(File.Contents(DataFolder & "sales.parquet"))' in sales
    date = zipfile.ZipFile(io.BytesIO(response.content)).read("Round trip/Round trip.SemanticModel/definition/tables/Date.tmdl").decode()
    assert "dataCategory: Time" in date and "isKey" in date


def test_round_trip_keeps_the_model_and_its_numbers(api):
    original = build_dataset(api)
    project = api.get(f"/api/datasets/{original}/powerbi/export").content

    copy = ok(api.post("/api/datasets", json={"name": "Copy"}))["id"]
    body = ok(api.post(f"/api/datasets/{copy}/sources/powerbi", files=[("file", ("Round trip.zip", project))]))
    assert body["added"] == [] and set(body["waiting_for"]) == {"sales", "customers"}  # a project carries no data
    for name, text in (("sales.csv", SALES_CSV), ("customers.csv", CUSTOMERS_CSV)):
        ok(api.post(f"/api/datasets/{copy}/sources/upload", files=[("files", (name, text.encode()))]))
    queries = {q["name"]: q["id"] for q in ok(api.get(f"/api/datasets/{copy}/transform"))["queries"]}
    for column, to in (("day", "date"), ("qty", "integer"), ("price", "decimal")):
        ok(api.post(f"/api/datasets/{copy}/transform/queries/{queries['sales']}/steps",
                    json={"op": "change_type", "params": {"column": column, "to": to}}))
    finished = ok(api.post(f"/api/datasets/{copy}/powerbi/apply"))
    assert finished["waiting_for"] == [] and finished["report"]["not_converted"] == []
    assert numbers(api, copy) == numbers(api, original)
    formats = {m["name"]: m["format"] for m in ok(api.get(f"/api/datasets/{copy}/model"))["measures"]}
    assert formats["Revenue"] == {"type": "currency", "decimals": 2, "currency": "RM"}


# Sources, snapshots and refresh (M11b)


@pytest.mark.parametrize("m, name", [
    ('Sql.Database("srv", "db")', "SQL Server"),
    ('Excel.Workbook(Web.Contents("https://x.sharepoint.com/a.xlsx"))', "Excel on SharePoint"),
    ('Excel.Workbook(File.Contents("C:\a.xlsx"))', "Excel"),
    ("Table.FromRows(Json.Document(Binary.Decompress(Binary.FromText(\"x\"))))", "Entered data"),
    ("Salesforce.Data()", "Salesforce"),
    (None, "Unknown source"),
])
def test_the_original_connector_is_named(m, name):
    assert pbi_model.connector_of(m) == name


def test_live_connection_reports_are_explained():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("Version", "1.28")
        archive.writestr("Connections", "{}")
        archive.writestr("Report/Layout", "{}")
    with pytest.raises(pbi_model.PbiError, match="live connection"):
        pbi_model.read_pbix(buffer.getvalue(), "Thin report")


def test_directquery_tables_are_flagged_and_sources_are_labelled(api, monkeypatch):
    model = fake_pbix()
    model.tables[0].source_m = 'Sql.Database("srv", "sales")'
    model.tables[0].refreshed_at = "2026-06-04T05:30"
    model.tables[1].mode, model.tables[1].data = "directquery", None
    monkeypatch.setattr(pbi_model, "read_pbix", lambda data, name: model)
    ds = ok(api.post("/api/datasets", json={"name": "Shop"}))["id"]
    body = ok(api.post(f"/api/datasets/{ds}/sources/powerbi", files=[("file", ("Shop.pbix", b"x"))]))
    customers = next(t for t in body["plan"]["tables"] if t["name"] == "Customers")
    assert customers["role"] == "needs_data" and customers["why"].startswith("DirectQuery")
    assert body["waiting_for"] == ["Customers"] and any("DirectQuery" in n for n in body["report"]["notes"])
    assert "last refreshed it on 2026-06-04" in body["report"]["notes"][0]
    origin = ok(api.get(f"/api/datasets/{ds}"))["sources"][0]["origin"]
    assert origin["connector"] == "SQL Server" and origin["snapshot"] == "2026-06-04T05:30" and origin["mode"] == "import"


def test_refresh_from_a_newer_pbix_replaces_data_and_keeps_the_model(api, monkeypatch):
    monkeypatch.setattr(pbi_model, "read_pbix", lambda data, name: fake_pbix())
    ds = ok(api.post("/api/datasets", json={"name": "Shop"}))["id"]
    ok(api.post(f"/api/datasets/{ds}/sources/powerbi", files=[("file", ("Shop.pbix", b"x"))]))
    ok(api.post(f"/api/datasets/{ds}/model/measures", json={"name": "Orders", "kind": "aggregate", "table": "sales", "aggregation": "count"}))

    newer = fake_pbix()
    extra = pd.DataFrame({"Order Date": pd.to_datetime(["2025-06-01"]), "CustomerKey": [2], "Amount": [500.0], "Status": ["Paid"]})
    newer.tables[0].data = pd.concat([SALES, extra], ignore_index=True)
    newer.tables[0].refreshed_at = "2026-10-01T08:00"
    newer.tables.append(PbiTable("Targets", [PbiColumn("Target", "decimal")], data=pd.DataFrame({"Target": [1.0]})))
    monkeypatch.setattr(pbi_model, "read_pbix", lambda data, name: newer)
    entry = ok(api.post(f"/api/datasets/{ds}/powerbi/refresh", files=[("file", ("Shop v2.pbix", b"y"))]))
    assert entry["trigger"] == "powerbi" and entry["status"] in ("ok", "warnings")
    assert {"table": "sales", "rows_before": 4, "rows_after": 5} in entry["sources"]
    assert entry["not_added"] == ["Targets"]  # new tables aren't added by a refresh
    measures = {m["name"] for m in ok(api.get(f"/api/datasets/{ds}/model"))["measures"]}
    assert {"Revenue", "Paid", "Orders"} <= measures  # the model, including edits made in datawazi, is kept
    revenue = ok(api.post(f"/api/datasets/{ds}/model/evaluate", json={"measures": ["Revenue"]}))["rows"][0][0]
    assert revenue == 750.0
    sales = next(s for s in ok(api.get(f"/api/datasets/{ds}"))["sources"] if s["table"] == "sales")
    assert sales["origin"]["snapshot"] == "2026-10-01T08:00"  # labelled with the newer file's refresh
    assert api.post(f"/api/datasets/{ds}/powerbi/refresh", files=[("file", ("x.zip", b"y"))]).status_code == 400


def test_a_dashboard_exports_as_power_bi_report_pages(tmp_path):
    import io
    import json as _json
    import zipfile

    from test_report import SEGMENT, Project
    from test_report import ok as ok_
    p = Project(tmp_path)
    page = p.page("Sales overview")
    for body in ({"type": "kpi", "title": "Revenue", "measures": ["Revenue"]},
                 {"type": "bar", "title": "Revenue by segment", "measures": ["Revenue"], "group_by": SEGMENT},
                 {"type": "line", "title": "Revenue by month", "measures": ["Revenue"],
                  "group_by": [{"table": "Date", "column": "Year Month"}]},
                 {"type": "table", "title": "Detail", "measures": ["Revenue", "Orders"], "group_by": SEGMENT}):
        ok_(p.visual(page, **body))
    response = p.api.get(f"{p.base}/powerbi")
    assert response.status_code == 200 and response.headers["content-type"] == "application/zip"
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    files = {n.split("/", 1)[1]: archive.read(n) for n in archive.namelist()}
    report = "Sales.Report/definition"
    order = _json.loads(files[f"{report}/pages/pages.json"])["pageOrder"]
    assert len(order) == 1
    assert _json.loads(files[f"{report}/pages/{order[0]}/page.json"])["displayName"] == "Sales overview"
    visuals = [_json.loads(v) for k, v in files.items() if k.startswith(f"{report}/pages/{order[0]}/visuals/")]
    by_type = {v["visual"]["visualType"]: v["visual"] for v in visuals}
    assert set(by_type) == {"cardVisual", "clusteredBarChart", "lineChart", "tableEx"}
    bar = by_type["clusteredBarChart"]["query"]
    assert bar["queryState"]["Y"]["projections"][0]["queryRef"] == "sales.Revenue"
    assert bar["queryState"]["Category"]["projections"][0]["queryRef"] == "customers.segment"
    assert bar["sortDefinition"]["sort"][0]["direction"] == "Descending"
    assert [p_["queryRef"] for p_ in by_type["tableEx"]["query"]["queryState"]["Values"]["projections"]] == [
        "customers.segment", "sales.Revenue", "sales.Orders"]
    assert by_type["lineChart"]["query"]["queryState"]["Category"]["projections"][0]["queryRef"] == "Date.Year Month"
    assert any(n.startswith("Sales.SemanticModel/definition/tables/") for n in files)  # the model comes along
