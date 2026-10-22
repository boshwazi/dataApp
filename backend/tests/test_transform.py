import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import ENV_VARS


def message(content=None, tool_calls=None):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tool_calls))])


class FakeAI:
    """Answers propose_steps with a fixed list of steps."""

    def __init__(self, steps, summary="Cleans the table."):
        self.answer = {"summary": summary, "steps": steps}
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        text = json.dumps(self.answer)
        if "tools" in kwargs:
            return message(tool_calls=[SimpleNamespace(function=SimpleNamespace(name="propose_steps", arguments=text))])
        return message(content=text)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in ENV_VARS.values():
        monkeypatch.delenv(var, raising=False)


SALES = (
    "id,day,price,region,qty\n"
    "1,2024-01-05,RM 1200,Central,2\n"
    "2,05/02/2024,15.50,central ,3\n"
    "3,2024-03-01,N/A,N/A,x\n"
    "3,2024-03-01,N/A,N/A,x\n"
    "4,2024-03-02,7,,2.5\n"
)
PRODUCTS = "code,name\nA,Apple\nB,Banana\n"


class Project:
    def __init__(self, tmp_path, ai=None):
        self.api = TestClient(create_app(data_dir=tmp_path, client_factory=lambda s: ai))
        self.id = self.api.post("/api/datasets", json={"name": "T"}).json()["id"]
        self.base = f"/api/datasets/{self.id}/transform"

    def upload(self, name, text):
        return self.api.post(f"/api/datasets/{self.id}/sources/upload", files=[("files", (name, text.encode()))])

    def state(self):
        return self.api.get(self.base).json()

    def query(self, name):
        return next(q for q in self.state()["queries"] if q["name"] == name)

    def add(self, table, op, **params):
        return self.api.post(f"{self.base}/queries/{self.query(table)['id']}/steps", json={"op": op, "params": params})

    def rows(self, table, step=-1):
        query = self.query(table)
        return self.api.get(f"{self.base}/queries/{query['id']}/preview", params={"step": step}).json()

    def column(self, table, name):
        preview = self.rows(table)
        i = [c["name"] for c in preview["after"]["columns"]].index(name)
        return [row[i] for row in preview["after"]["rows"]]


@pytest.fixture
def p(tmp_path):
    project = Project(tmp_path)
    project.upload("sales.csv", SALES)
    return project


def ok(response):
    assert response.status_code == 200, response.json()
    return response.json()


# Queries follow Get Data


def test_every_source_gets_a_query_with_its_raw_output(p):
    sales = p.query("sales")
    assert sales["steps"] == [] and sales["rows"] == 5
    assert {c["type"] for c in sales["columns"]} == {"text"}
    p.upload("products.csv", PRODUCTS)
    assert [q["name"] for q in p.state()["queries"]] == ["sales", "products"]


def test_removing_a_source_removes_its_query(p):
    p.upload("products.csv", PRODUCTS)
    source = next(s for s in p.api.get(f"/api/datasets/{p.id}").json()["sources"] if s["table"] == "products")
    p.api.delete(f"/api/datasets/{p.id}/sources/{source['id']}")
    assert [q["name"] for q in p.state()["queries"]] == ["sales"]


# Operations


def test_cleaning_steps_produce_typed_clean_values(p):
    ok(p.add("sales", "blanks_to_null", columns=["price", "region"]))
    ok(p.add("sales", "trim", columns=["region"]))
    ok(p.add("sales", "change_case", columns=["region"], case="title"))
    ok(p.add("sales", "remove_duplicates"))
    ok(p.add("sales", "change_type", column="day", to="date", date_order="dmy"))
    ok(p.add("sales", "change_type", column="price", to="decimal"))
    ok(p.add("sales", "change_type", column="qty", to="integer"))
    ok(p.add("sales", "fill_blanks", column="region", value="Unknown"))

    types = {c["name"]: c["type"] for c in p.query("sales")["columns"]}
    assert types == {"id": "text", "day": "date", "price": "decimal", "region": "text", "qty": "integer"}
    preview = p.rows("sales")
    assert preview["after"]["total"] == 4
    by_id = {row[0]: row for row in preview["after"]["rows"]}
    assert by_id["1"][1:4] == ["2024-01-05", "1200.0", "Central"]
    assert by_id["2"][1] == "2024-02-05"  # day first
    assert by_id["2"][3] == "Central"
    assert by_id["3"][2] is None and by_id["3"][3] == "Unknown" and by_id["3"][4] is None
    assert by_id["4"][4] is None  # 2.5 is not a whole number


def test_replace_values_ignores_case_and_spaces(p):
    ok(p.add("sales", "replace_values", column="region", replacements=[{"from": "CENTRAL", "to": "Central Region"}]))
    assert p.column("sales", "region")[:2] == ["Central Region", "Central Region"]


def test_rename_remove_and_filter(p):
    ok(p.add("sales", "rename_column", column="qty", to="Quantity"))
    ok(p.add("sales", "remove_columns", columns=["price"]))
    ok(p.add("sales", "filter_rows", column="region", operator="is_not_blank"))
    preview = p.rows("sales")
    assert [c["name"] for c in preview["after"]["columns"]] == ["id", "day", "region", "Quantity"]
    assert preview["after"]["total"] == 4


def test_filter_compares_typed_values(p):
    ok(p.add("sales", "remove_duplicates"))
    ok(p.add("sales", "change_type", column="qty", to="integer"))
    ok(p.add("sales", "filter_rows", column="qty", operator="at_least", value=3))
    assert p.column("sales", "qty") == ["3"]


def test_append_stacks_tables_by_column_name(p):
    p.upload("more.csv", "region,id\nSouth,9\n")
    ok(p.add("sales", "append", queries=["more"]))
    assert p.rows("sales")["after"]["total"] == 6
    assert "Append more" in p.query("sales")["steps"][0]["label"]


# Checks


@pytest.mark.parametrize(
    "op, params, message",
    [
        ("change_type", {"column": "nope", "to": "date"}, "does not exist"),
        ("change_type", {"column": "price", "to": "money"}, "must be one of"),
        ("trim", {"columns": []}, "at least one"),
        ("remove_columns", {"columns": ["id", "day", "price", "region", "qty"]}, "must remain"),
        ("rename_column", {"column": "qty", "to": "ID"}, "already exists"),
        ("explode", {}, "Unknown operation"),
        ("merge", {"query": "sales", "left_on": ["id"], "right_on": ["id"], "columns": ["day"]}, "depends on"),
    ],
)
def test_invalid_steps_are_refused(p, op, params, message):
    response = p.add("sales", op, **params)
    assert response.status_code == 400 and message in response.json()["detail"]
    assert p.query("sales")["steps"] == []


def test_text_operations_need_text_columns(p):
    ok(p.add("sales", "change_type", column="price", to="decimal"))
    response = p.add("sales", "trim", columns=["price"])
    assert response.status_code == 400 and "needs text" in response.json()["detail"]


def test_merge_brings_in_columns_and_refuses_repeated_keys(p):
    p.upload("products.csv", "code,name\n1,One\n2,Two\n3,Three\n3,Three again\n")
    response = p.add("sales", "merge", query="products", left_on=["id"], right_on=["code"], columns=["name"])
    assert response.status_code == 400 and "key repeats" in response.json()["detail"]

    ok(p.add("products", "remove_duplicates", columns=["code"]))
    ok(p.add("sales", "merge", query="products", left_on=["id"], right_on=["code"], columns=["name"]))
    assert p.column("sales", "name") == ["One", "Two", "Three", "Three", None]
    assert p.rows("sales")["after"]["total"] == 5

    # Removing the de-duplication would now multiply sales rows, so it is refused.
    products = p.query("products")
    response = p.api.delete(f"{p.base}/queries/{products['id']}/steps/{products['steps'][0]['id']}")
    assert response.status_code == 400 and "break 'sales'" in response.json()["detail"]


def test_merge_survives_renaming_the_other_table(p):
    p.upload("products.csv", "code,name\n1,One\n")
    ok(p.add("sales", "merge", query="products", left_on=["id"], right_on=["code"], columns=["name"]))
    ok(p.api.patch(f"{p.base}/queries/{p.query('products')['id']}", json={"name": "Product List"}))
    sales = p.query("sales")
    assert sales["error"] is None and sales["steps"][0]["label"] == "Merge name from product_list"


def test_moving_a_step_before_what_it_needs_is_refused(p):
    ok(p.add("sales", "remove_duplicates"))
    ok(p.add("sales", "rename_column", column="qty", to="quantity"))
    ok(p.add("sales", "change_type", column="quantity", to="integer"))
    sales = p.query("sales")
    response = p.api.post(f"{p.base}/queries/{sales['id']}/steps/{sales['steps'][2]['id']}/move", json={"to": 1})
    assert response.status_code == 400
    assert [s["op"] for s in p.query("sales")["steps"]] == ["remove_duplicates", "rename_column", "change_type"]


# Undo, approval, preview


def test_undo_restores_the_previous_steps(p):
    ok(p.add("sales", "trim", columns=["region"]))
    ok(p.add("sales", "remove_duplicates"))
    sales = p.query("sales")
    ok(p.api.delete(f"{p.base}/queries/{sales['id']}/steps/{sales['steps'][0]['id']}"))
    assert len(p.query("sales")["steps"]) == 1
    ok(p.api.post(f"{p.base}/undo"))
    assert [s["op"] for s in p.query("sales")["steps"]] == ["trim", "remove_duplicates"]
    ok(p.api.post(f"{p.base}/undo"))
    ok(p.api.post(f"{p.base}/undo"))
    assert p.query("sales")["steps"] == []
    assert p.api.post(f"{p.base}/undo").status_code == 400


def test_user_steps_are_approved_and_labelled(p):
    ok(p.add("sales", "trim", columns=["region"]))
    step = p.query("sales")["steps"][0]
    assert step["status"] == "Approved" and step["label"] == "Trim spaces in region"


def test_preview_reports_impact(p):
    ok(p.add("sales", "blanks_to_null", columns=["price"]))
    ok(p.add("sales", "remove_duplicates"))
    first = p.rows("sales", step=1)
    assert first["impact"]["blank_changes"] == [{"column": "price", "before": 0, "after": 2}]
    second = p.rows("sales", step=2)
    assert (second["impact"]["rows_before"], second["impact"]["rows_after"]) == (5, 4)
    assert p.rows("sales", step=0)["impact"] is None


def test_steps_replay_from_the_raw_table(p):
    ok(p.add("sales", "remove_duplicates"))
    # Re-running the queries (as any change does) gives the same rows, not a second de-duplication.
    ok(p.add("sales", "trim", columns=["region"]))
    assert p.rows("sales")["after"]["total"] == 4


# AI cleaning


def ai_project(tmp_path, steps):
    ai = FakeAI(steps)
    project = Project(tmp_path, ai)
    project.api.put("/api/settings", json={"api_key": "sk-secret-value-1234"})
    project.upload("sales.csv", SALES)
    return project, ai


def test_ai_steps_are_checked_and_marked(tmp_path):
    p, ai = ai_project(tmp_path, [
        {"op": "blanks_to_null", "params": {"columns": ["region"]}, "reason": "N/A means unknown"},
        {"op": "change_type", "params": {"column": "nope", "to": "date"}, "reason": "invented column"},
        {"op": "trim", "params": {"columns": ["region"]}, "reason": "stray spaces"},
    ])
    body = ok(p.api.post(f"{p.base}/queries/{p.query('sales')['id']}/ai"))
    assert [s["op"] for s in body["added"]] == ["blanks_to_null", "trim"]
    assert body["rejected"][0]["op"] == "change_type" and "does not exist" in body["rejected"][0]["error"]
    steps = p.query("sales")["steps"]
    assert [s["status"] for s in steps] == ["AI", "AI"] and steps[0]["reason"] == "N/A means unknown"

    ok(p.api.post(f"{p.base}/queries/{p.query('sales')['id']}/steps/{steps[0]['id']}/approve"))
    assert p.query("sales")["steps"][0]["status"] == "Approved"

    # Undo first reverts the approval, then removes the whole AI run at once.
    ok(p.api.post(f"{p.base}/undo"))
    assert p.query("sales")["steps"][0]["status"] == "AI"
    ok(p.api.post(f"{p.base}/undo"))
    assert p.query("sales")["steps"] == []


def test_ai_sees_all_values_of_small_text_columns_but_not_all_rows(tmp_path):
    p, ai = ai_project(tmp_path, [])
    ok(p.api.post(f"{p.base}/queries/{p.query('sales')['id']}/ai"))
    prompt = ai.calls[0]["messages"][-1]["content"]
    assert '"all_values": {"N/A": 2' in prompt and '"central ": 1' in prompt
    assert '"op": "merge"' in prompt  # the catalogue is included


def test_ai_cleaning_needs_a_key(p):
    assert p.api.post(f"{p.base}/queries/{p.query('sales')['id']}/ai").status_code == 400


def test_approve_all_is_one_undoable_change(tmp_path):
    p, _ = ai_project(tmp_path, [
        {"op": "trim", "params": {"columns": ["region"]}, "reason": "spaces"},
        {"op": "remove_duplicates", "params": {}, "reason": "dupes"},
    ])
    ok(p.api.post(f"{p.base}/queries/{p.query('sales')['id']}/ai"))
    ok(p.api.post(f"{p.base}/approve-all"))
    assert {s["status"] for s in p.query("sales")["steps"]} == {"Approved"}
    ok(p.api.post(f"{p.base}/undo"))
    assert {s["status"] for s in p.query("sales")["steps"]} == {"AI"}


# Dates and data-loss checks

TRIPS = "start,miles\n6/28/2016 23:34,5.1\n01-02-2016 21:11,2\n3/5/2016 1:05 PM,1\n2016-07-01 08:00,4\n"


def test_dates_with_times_and_the_other_order_still_parse(tmp_path):
    p = Project(tmp_path)
    p.upload("trips.csv", TRIPS)
    # Day first: 01-02-2016 is 1 February and 3/5 is 3 May; 6/28/2016 can only be month first.
    ok(p.add("trips", "change_type", column="start", to="date", date_order="dmy"))
    assert p.column("trips", "start") == ["2016-06-28", "2016-02-01", "2016-05-03", "2016-07-01"]


def test_profile_recognises_dates_with_times(tmp_path):
    p = Project(tmp_path)
    p.upload("trips.csv", TRIPS)
    source = p.api.get(f"/api/datasets/{p.id}").json()["sources"][0]
    profile = p.api.get(f"/api/datasets/{p.id}/sources/{source['id']}").json()["profile"]
    assert profile["columns"][0]["detected_type"] == "date"


def test_steps_that_blank_most_of_a_column_are_refused(p):
    response = p.add("sales", "change_type", column="region", to="date")
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "would blank 4 of 4 values" in detail and "'Central'" in detail


def test_replacements_that_match_nothing_are_refused(p):
    response = p.add("sales", "replace_values", column="region", replacements=[{"from": "Atlantis", "to": "x"}])
    assert response.status_code == 400 and "change nothing" in response.json()["detail"]


# Lookup tables

ORDERS = (
    "order,product,product name,category,qty\n"
    "1,P1,Apple,Fruit,2\n"
    "2,P2,Bread,Bakery,1\n"
    "3,P1,Apple,Fruit,5\n"
    "4,,,,1\n"
)


@pytest.fixture
def orders(tmp_path):
    project = Project(tmp_path)
    project.upload("orders.csv", ORDERS)
    return project


def split(p, **body):
    return p.api.post(f"{p.base}/split", json=body)


def test_split_makes_a_lookup_table_with_one_row_per_key(orders):
    p = orders
    state = ok(split(p, from_table="orders", columns=["product", "product name", "category"], name="Products"))
    lookup = next(q for q in state["queries"] if q["name"] == "products")
    assert lookup["kind"] == "lookup" and lookup["base_table"] == "orders" and lookup["status"] == "Approved"
    assert [c["name"] for c in lookup["columns"]] == ["product", "product name", "category"]
    assert sorted(p.column("products", "product")) == ["P1", "P2"]  # no blank key, one row each

    # The original can now drop the moved attributes without changing the lookup table.
    ok(p.add("orders", "remove_columns", columns=["product name", "category"]))
    assert [c["name"] for c in p.query("orders")["columns"]] == ["order", "product", "qty"]
    assert p.query("products")["rows"] == 2 and p.query("products")["error"] is None


def test_split_refuses_unknown_columns_and_taken_names(orders):
    assert "has no column" in split(orders, from_table="orders", columns=["nope"]).json()["detail"]
    assert "already exists" in split(orders, from_table="orders", columns=["product"], name="orders").json()["detail"]
    everything = ["order", "product", "product name", "category", "qty"]
    assert "not all of them" in split(orders, from_table="orders", columns=everything).json()["detail"]


def test_steps_the_lookup_depends_on_cannot_be_removed(orders):
    p = orders
    ok(p.add("orders", "change_case", columns=["category"], case="upper"))
    ok(split(p, from_table="orders", columns=["product", "category"]))
    assert sorted(p.column("product", "category")) == ["BAKERY", "FRUIT"]
    step = p.query("orders")["steps"][0]
    response = p.api.delete(f"{p.base}/queries/{p.query('orders')['id']}/steps/{step['id']}")
    assert response.status_code == 400 and "break 'product'" in response.json()["detail"]


def test_lookup_tables_can_be_removed_and_undone(orders):
    p = orders
    ok(split(p, from_table="orders", columns=["product", "category"]))
    ok(p.api.delete(f"{p.base}/queries/{p.query('product')['id']}"))
    assert [q["name"] for q in p.state()["queries"]] == ["orders"]
    ok(p.api.post(f"{p.base}/undo"))
    assert [q["name"] for q in p.state()["queries"]] == ["orders", "product"]
    ok(p.api.post(f"{p.base}/undo"))
    assert [q["name"] for q in p.state()["queries"]] == ["orders"]
    # Tables loaded in Get Data are removed there, not here.
    assert p.api.delete(f"{p.base}/queries/{p.query('orders')['id']}").status_code == 400


def test_ai_splits_after_cleaning_and_removes_moved_columns_last(tmp_path):
    ai = FakeAI([
        {"op": "remove_columns", "params": {"columns": ["product name", "category"]}, "reason": "moved to products"},
        {"op": "change_case", "params": {"columns": ["category"], "case": "upper"}, "reason": "consistent case"},
    ])
    ai.answer["new_tables"] = [
        {"name": "Products", "key": "product", "columns": ["product", "product name", "category"], "reason": "product details"},
        {"name": "Bad", "key": "nope", "columns": ["nope"], "reason": "invented"},
    ]
    p = Project(tmp_path, ai)
    p.api.put("/api/settings", json={"api_key": "sk-secret-value-1234"})
    p.upload("orders.csv", ORDERS)
    body = ok(p.api.post(f"{p.base}/queries/{p.query('orders')['id']}/ai"))

    assert body["new_tables"] == ["products"]
    assert any("Lookup table 'Bad'" in r["error"] for r in body["rejected"])
    assert [s["op"] for s in p.query("orders")["steps"]] == ["change_case", "remove_columns"]
    products = p.query("products")
    assert products["status"] == "AI" and products["split_after_step"] == 1
    assert sorted(p.column("products", "category")) == ["BAKERY", "FRUIT"]

    ok(p.api.post(f"{p.base}/approve-all"))
    assert p.query("products")["status"] == "Approved"
    ok(p.api.post(f"{p.base}/undo"))  # the approval
    ok(p.api.post(f"{p.base}/undo"))  # the whole AI run: steps and the lookup table
    assert [q["name"] for q in p.state()["queries"]] == ["orders"] and p.query("orders")["steps"] == []


# Seeing what a step removed


def test_a_step_shows_exactly_the_rows_it_removed(p):
    ok(p.add("sales", "trim", columns=["region"]))
    ok(p.add("sales", "remove_duplicates"))
    query = p.query("sales")
    removed = ok(p.api.get(f"{p.base}/queries/{query['id']}/steps/2/removed"))
    assert removed["comparable"] and removed["total"] == 1
    assert removed["rows"] == [["3", "2024-03-01", "N/A", "N/A", "x"]]  # the second copy of order 3
    nothing = ok(p.api.get(f"{p.base}/queries/{query['id']}/steps/1/removed"))
    assert nothing["total"] == 0  # trimming keeps every row
    assert p.api.get(f"{p.base}/queries/{query['id']}/steps/9/removed").status_code == 400

    ok(p.add("sales", "rename_column", column="qty", to="quantity"))  # changes columns, so no row diff
    assert ok(p.api.get(f"{p.base}/queries/{query['id']}/steps/3/removed"))["comparable"] is False


def test_the_transform_chat_sees_removed_rows(p):
    from app import chat, workspaces
    ok(p.add("sales", "remove_duplicates"))
    store = p.api.app.state.refresh_job[0]
    with workspaces.use("local"):
        project = store.get(p.id)
        with store.connect(p.id) as con:
            context = chat.tf_context(chat.Ctx(project=project, con=con))
    table = next(t for t in context["tables"] if t["table"] == "sales")
    assert table["rows_removed_by_steps"] == [
        {"step": 1, "rows_removed": 1, "examples": [{"id": "3", "day": "2024-03-01", "price": "N/A", "region": "N/A", "qty": "x"}]}
    ]


SAME_DAY_TRIPS = (
    "start,miles\n"
    "1/5/2016 9:10,5.1\n"
    "1/5/2016 17:45,5.1\n"   # same day, same miles, different trip
    "1/6/2016 8:00,3.0\n"
    "1/6/2016 8:00,3.0\n"    # a real duplicate
)


def test_removing_duplicates_after_dropping_times_is_flagged(tmp_path):
    p = Project(tmp_path)
    p.upload("trips.csv", SAME_DAY_TRIPS)
    ok(p.add("trips", "change_type", column="start", to="date", date_order="mdy"))
    ok(p.add("trips", "remove_duplicates"))  # allowed, but merges the two 5 Jan trips
    warnings = p.query("trips")["warnings"]
    assert len(warnings) == 1 and warnings[0]["step"] == 2
    text = warnings[0]["text"]
    assert "Removes 2 rows, but only 1 was a duplicate" in text and "step 1 (Change start to date" in text
    assert "made 1 different row identical" in text and "Move this step before step 1" in text


def test_removing_duplicates_first_keeps_real_records_without_a_warning(tmp_path):
    p = Project(tmp_path)
    p.upload("trips.csv", SAME_DAY_TRIPS)
    ok(p.add("trips", "remove_duplicates"))
    ok(p.add("trips", "change_type", column="start", to="date", date_order="mdy"))
    trips = p.query("trips")
    assert trips["warnings"] == [] and trips["rows"] == 3  # only the real duplicate went


# Row order


def numbered(n=2000, repeats=(10, 500, 1500)):
    lines = ["n,code"] + [f"{i},{'AB'[i % 2]}" for i in range(n)]
    lines += [f"{i},{'AB'[i % 2]}" for i in repeats]  # exact copies, appended at the end
    return "\n".join(lines) + "\n"


def test_rows_keep_source_order_through_duplicates_merges_and_appends(tmp_path):
    p = Project(tmp_path)
    p.upload("big.csv", numbered())
    p.upload("more.csv", "n,code\n9001,A\n9002,B\n")
    p.upload("codes.csv", "code,label\nA,Alpha\nB,Beta\n")
    ok(p.add("big", "change_type", column="n", to="integer"))
    ok(p.add("more", "change_type", column="n", to="integer"))
    ok(p.add("big", "remove_duplicates"))
    ok(p.add("big", "merge", query="codes", left_on=["code"], right_on=["code"], columns=["label"]))
    ok(p.add("big", "append", queries=["more"]))
    steps = len(p.query("big")["steps"])
    for step in range(3, steps + 1):
        seen = [p.rows("big", step)["after"]["rows"] for _ in range(5)]
        assert all(rows == seen[0] for rows in seen), f"step {step} changes order between loads"
        assert [int(r[0]) for r in seen[0][:100]] == list(range(100)), f"step {step} lost the source order"
    query = p.query("big")
    assert query["rows"] == 2002  # 2,000 unique rows plus the 2 appended
    tail = ok(p.api.get(f"{p.base}/queries/{query['id']}/preview", params={"step": steps}))
    assert tail["after"]["total"] == 2002
