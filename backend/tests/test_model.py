import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import model_expr
from app.main import create_app
from app.settings import ENV_VARS


class FakeAI:
    """Answers design_model with a fixed proposal, as JSON text."""

    def __init__(self, answer):
        self.answer = answer
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        content = json.dumps(self.answer)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=None))])


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in ENV_VARS.values():
        monkeypatch.delenv(var, raising=False)


SALES = (
    "order,day,customer,product,qty,price\n"
    "1,2024-01-05,C1,P1,2,10\n"
    "2,2024-02-10,C2,P1,1,10\n"
    "3,2025-03-01,C1,P2,5,4\n"
    "4,2025-03-02,C9,P2,1,4\n"
)
CUSTOMERS = "customer,segment,joined\nC1,Retail,2024-01-01\nC2,Online,2024-06-01\nC3,Retail,2025-01-01\n"
PRODUCTS = "product,category\nP1,Food\nP2,Drink\nP2,Drink again\n"


class Project:
    def __init__(self, tmp_path, ai=None):
        self.api = TestClient(create_app(data_dir=tmp_path, client_factory=lambda s: ai))
        self.id = self.api.post("/api/datasets", json={"name": "M"}).json()["id"]
        self.model = f"/api/datasets/{self.id}/model"
        for name, text in (("sales.csv", SALES), ("customers.csv", CUSTOMERS), ("products.csv", PRODUCTS)):
            self.api.post(f"/api/datasets/{self.id}/sources/upload", files=[("files", (name, text.encode()))])
        tf = f"/api/datasets/{self.id}/transform"
        queries = {q["name"]: q["id"] for q in self.api.get(tf).json()["queries"]}
        for column, to in (("day", "date"), ("qty", "integer"), ("price", "decimal")):
            self.api.post(f"{tf}/queries/{queries['sales']}/steps", json={"op": "change_type", "params": {"column": column, "to": to}})
        self.transform, self.queries = tf, queries

    def state(self):
        return self.api.get(self.model).json()

    def relate(self, a, ac, b, bc):
        return self.api.post(f"{self.model}/relationships", json={"from_table": a, "from_column": ac, "to_table": b, "to_column": bc})

    def measure(self, **body):
        return self.api.post(f"{self.model}/measures", json=body)

    def evaluate(self, measures, group_by=(), filters=()):
        return self.api.post(f"{self.model}/evaluate", json={"measures": measures, "group_by": list(group_by), "filters": list(filters)})


def ok(response):
    assert response.status_code == 200, response.json()
    return response.json()


@pytest.fixture
def p(tmp_path):
    return Project(tmp_path)


REVENUE = {"name": "Revenue", "kind": "aggregate", "table": "sales", "aggregation": "sum", "expression": "[qty] * [price]",
           "format": {"type": "currency", "decimals": 2, "currency": "RM"}}


# Expressions


def test_expressions_parse_and_compute():
    tree = model_expr.parse("([A] + 2) * [B Name] / -[C]")
    assert model_expr.refs(tree) == ["A", "B Name", "C"]
    assert model_expr.evaluate(tree, {"A": 1, "B Name": 4, "C": 2}) == -6
    assert model_expr.evaluate(model_expr.parse("[A] / [B]"), {"A": 1, "B": 0}) is None
    assert model_expr.to_sql(model_expr.parse("[A] / 2"), lambda n: f'"{n}"') == '("A" / NULLIF(2.0, 0))'


@pytest.mark.parametrize("text", ["", "[A] +", "([A]", "[A]; DROP TABLE x", "SUM([A])"])
def test_bad_expressions_are_refused(text):
    with pytest.raises(model_expr.ExprError):
        model_expr.parse(text)


# Relationships


def test_relationship_checks_and_direction(p):
    rel = ok(p.relate("sales", "customer", "customers", "customer"))["relationships"][0]
    assert (rel["from_name"], rel["to_name"], rel["active"]) == ("sales", "customers", True)
    assert rel["checks"]["match_rate"] == 0.75 and rel["checks"]["unmatched_examples"] == ["C9"]
    assert rel["checks"]["cardinality"] == "many_to_one"

    # Proposed the wrong way round: the engine flips it, then sees it already exists.
    response = p.relate("customers", "customer", "sales", "customer")
    assert response.status_code == 400 and "already exists" in response.json()["detail"]


@pytest.mark.parametrize(
    "args, message",
    [
        (("sales", "product", "products", "product"), "repeated key"),
        (("sales", "qty", "customers", "customer"), "is integer but"),
        (("sales", "order", "customers", "customer"), "probably do not belong"),
        (("sales", "nope", "customers", "customer"), "has no column"),
        (("sales", "customer", "sales", "customer"), "two different tables"),
    ],
)
def test_bad_relationships_are_refused(p, args, message):
    response = p.relate(*args)
    assert response.status_code == 400 and message in response.json()["detail"]
    assert p.state()["relationships"] == []


def test_second_path_between_tables_is_inactive(p):
    p.api.post(f"{p.transform}/queries/{p.queries['customers']}/steps",
               json={"op": "change_type", "params": {"column": "joined", "to": "date"}})
    ok(p.api.put(f"{p.model}/date-table", json={}))
    ok(p.relate("sales", "day", "Date", "Date"))
    ok(p.relate("sales", "customer", "customers", "customer"))
    # customers -> sales -> Date already connects customers to Date, so a direct link would be ambiguous.
    rels = ok(p.relate("customers", "joined", "Date", "Date"))["relationships"]
    assert [r["active"] for r in rels] == [True, True, False]
    # Inactive relationships are not used when splitting measures.
    ok(p.measure(name="Customers", kind="aggregate", table="customers", aggregation="count"))
    assert p.evaluate(["Customers"], [{"table": "Date", "column": "Year"}]).status_code == 400


def test_relationships_break_when_transform_changes_a_key(p):
    ok(p.relate("sales", "customer", "customers", "customer"))
    p.api.post(f"{p.transform}/queries/{p.queries['customers']}/steps",
               json={"op": "rename_column", "params": {"column": "customer", "to": "id"}})
    rel = p.state()["relationships"][0]
    assert "has no column 'customer'" in rel["error"]


# Date table


def test_date_table_covers_whole_years_of_the_data(p):
    date_table = ok(p.api.put(f"{p.model}/date-table", json={}))["date_table"]
    assert (date_table["first"], date_table["last"]) == ("2024-01-01", "2025-12-31")
    table = next(t for t in p.state()["tables"] if t["name"] == "Date")
    assert table["rows"] == 731 and table["kind"] == "date"
    assert ok(p.api.put(f"{p.model}/date-table", json={"start_year": 2023}))["date_table"]["first"] == "2023-01-01"
    assert p.api.put(f"{p.model}/date-table", json={"start_year": 2030, "end_year": 2020}).status_code == 400


# Measures


def test_measures_compute_and_format_is_kept(p):
    ok(p.measure(**REVENUE))
    ok(p.measure(name="Orders", kind="aggregate", table="sales", aggregation="count"))
    ok(p.measure(name="Average order", kind="formula", expression="[Revenue] / [Orders]"))
    ok(p.measure(name="Online orders", kind="aggregate", table="sales", aggregation="count",
                 filters=[{"column": "customer", "operator": "equals", "value": "C2"}]))
    ok(p.measure(name="Priced orders", kind="aggregate", table="sales", aggregation="count",
                 filters=[{"column": "price", "operator": "is_not_blank"}]))
    values = {m["name"]: m["value"] for m in p.state()["measures"]}
    assert values == {"Revenue": 54.0, "Orders": 4, "Average order": 13.5, "Online orders": 1, "Priced orders": 4}
    revenue = p.state()["measures"][0]
    assert revenue["format"]["currency"] == "RM" and revenue["label"] == "sum of [qty] * [price] in sales"


@pytest.mark.parametrize(
    "body, message",
    [
        ({**REVENUE, "expression": "[customer] * 2"}, "number columns"),
        ({**REVENUE, "expression": "[missing]"}, "has no column"),
        ({**REVENUE, "aggregation": "median"}, "Aggregation must be"),
        ({**REVENUE, "format": {"type": "money"}}, "Format type"),
        ({"name": "X", "kind": "formula", "expression": "[Nope] * 2"}, "not a measure"),
        ({"name": "X", "kind": "magic"}, "kind must be"),
        ({**REVENUE, "name": "re[venue]"}, "square brackets"),
    ],
)
def test_bad_measures_are_refused(p, body, message):
    response = p.measure(**body)
    assert response.status_code == 400 and message in response.json()["detail"]


def test_duplicate_measure_names_are_refused(p):
    ok(p.measure(**REVENUE))
    assert "already exists" in p.measure(**{**REVENUE, "name": "revenue"}).json()["detail"]


def test_measures_split_by_related_columns(p):
    ok(p.api.put(f"{p.model}/date-table", json={}))
    ok(p.relate("sales", "day", "Date", "Date"))
    ok(p.relate("sales", "customer", "customers", "customer"))
    ok(p.measure(**REVENUE))
    ok(p.measure(name="Orders", kind="aggregate", table="sales", aggregation="count"))
    ok(p.measure(name="Average order", kind="formula", expression="[Revenue] / [Orders]"))

    by_year = ok(p.evaluate(["Revenue", "Average order"], [{"table": "Date", "column": "Year"}]))
    assert by_year["rows"] == [[2024, 30.0, 15.0], [2025, 24.0, 12.0]]

    by_segment = ok(p.evaluate(["Revenue"], [{"table": "customers", "column": "segment"}]))
    assert by_segment["rows"] == [["Online", 10.0], ["Retail", 40.0], [None, 4.0]]  # C9 has no customer row

    filtered = ok(p.evaluate(["Orders"], filters=[{"table": "Date", "column": "Year", "operator": "equals", "value": 2025}]))
    assert filtered["rows"] == [[2]]

    response = p.evaluate(["Revenue"], [{"table": "products", "column": "category"}])
    assert response.status_code == 400 and "no relationship leads there" in response.json()["detail"]


# Undo, approval, hiding


def test_undo_and_approve(p):
    ok(p.measure(**REVENUE))
    ok(p.relate("sales", "customer", "customers", "customer"))
    ok(p.api.post(f"{p.model}/undo"))
    state = p.state()
    assert state["relationships"] == [] and [m["name"] for m in state["measures"]] == ["Revenue"]
    ok(p.api.post(f"{p.model}/undo"))
    assert p.state()["measures"] == [] and p.api.post(f"{p.model}/undo").status_code == 400


def test_hidden_columns(p):
    ok(p.api.post(f"{p.model}/hidden", json={"table": "sales", "column": "customer"}))
    sales = next(t for t in p.state()["tables"] if t["name"] == "sales")
    assert next(c for c in sales["columns"] if c["name"] == "customer")["hidden"] is True


# AI


def test_ai_builds_the_model_and_reports_refusals(tmp_path):
    ai = FakeAI({
        "summary": "Star schema around sales.",
        "date_table": {"create": True, "link": [{"table": "sales", "column": "day"}], "reason": "time analysis"},
        "relationships": [
            {"from_table": "sales", "from_column": "customer", "to_table": "customers", "to_column": "customer", "reason": "who bought"},
            {"from_table": "sales", "from_column": "product", "to_table": "products", "to_column": "product", "reason": "what"},
        ],
        "measures": [
            {"name": "Average order", "kind": "formula", "expression": "[Revenue] / [Orders]", "format": {"type": "currency", "decimals": 2}},
            {"name": "Revenue", "kind": "aggregate", "table": "sales", "aggregation": "sum", "expression": "[qty] * [price]"},
            {"name": "Orders", "kind": "aggregate", "table": "sales", "aggregation": "count"},
            {"name": "Broken", "kind": "aggregate", "table": "sales", "aggregation": "sum", "expression": "[nope]"},
        ],
        "hidden_columns": [{"table": "sales", "column": "customer"}],
    })
    p = Project(tmp_path, ai)
    p.api.put("/api/settings", json={"api_key": "sk-secret-value-1234"})
    body = ok(p.api.post(f"{p.model}/ai"))

    assert {(a["kind"], a["label"]) for a in body["added"]} >= {("date table", "Date table"), ("measure", "Average order")}
    refused = {r["label"]: r["error"] for r in body["refused"]}
    assert "repeated key" in refused["sales[product] → products[product]"]
    assert "has no column" in refused["Broken"]
    assert {m["name"] for m in body["measures"]} == {"Average order", "Revenue", "Orders"}
    assert all(m["status"] == "AI" for m in body["measures"])
    assert {(r["from_name"], r["to_name"]) for r in body["relationships"]} == {("sales", "Date"), ("sales", "customers")}

    prompt = ai.calls[0]["messages"][-1]["content"]
    assert "Candidate key pairs" in prompt and "repeated key" in prompt  # the AI is told about the products problem

    ok(p.api.post(f"{p.model}/approve"))
    assert {m["status"] for m in p.state()["measures"]} == {"Approved"}
    ok(p.api.post(f"{p.model}/undo"))  # the approval
    ok(p.api.post(f"{p.model}/undo"))  # the whole AI run
    state = p.state()
    assert state["measures"] == [] and state["relationships"] == [] and state["date_table"] is None


def test_ai_links_fact_dates_before_dimension_dates(tmp_path):
    ai = FakeAI({
        "summary": "",
        # Listed dimension-first on purpose: the engine still keeps sales -> customers active.
        "date_table": {"create": True, "link": [{"table": "customers", "column": "joined"}, {"table": "sales", "column": "day"}]},
        "relationships": [{"from_table": "sales", "from_column": "customer", "to_table": "customers", "to_column": "customer"}],
        "measures": [],
    })
    p = Project(tmp_path, ai)
    p.api.post(f"{p.transform}/queries/{p.queries['customers']}/steps",
               json={"op": "change_type", "params": {"column": "joined", "to": "date"}})
    p.api.put("/api/settings", json={"api_key": "sk-secret-value-1234"})
    rels = ok(p.api.post(f"{p.model}/ai"))["relationships"]
    active = {(r["from_name"], r["to_name"]): r["active"] for r in rels}
    assert active == {("sales", "customers"): True, ("sales", "Date"): True, ("customers", "Date"): False}


def test_ai_model_needs_a_key(p):
    assert p.api.post(f"{p.model}/ai").status_code == 400
