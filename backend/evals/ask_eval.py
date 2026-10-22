"""Ask evaluation: fixed questions with known answers, run against the real AI.

Builds the bundled sample dataset with fixed cleaning steps and a fixed model (no AI), publishes it,
then asks each question through the same path the app uses. The right answer for each case comes
from the engine itself, running a hand-written query. A case passes when the AI's own queries return
those values and the numbers in its written answer check out against its results.

Run from backend/ (uses the AI provider set up in data/settings.json, or DATAWAZI_* variables):

    ../.venv/Scripts/python -m evals.ask_eval            all cases
    ../.venv/Scripts/python -m evals.ask_eval top region  cases whose name contains the words

Prints one line per case and the pass rate; the full answers are written to evals/last_run.json.
"""

import json
import math
import shutil
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from app import llm
from app.main import REPO_ROOT, create_app

SEGMENT = [{"table": "customers", "column": "Segment"}]
REGION = [{"table": "sales", "column": "Region"}]
PRODUCT = [{"table": "products", "column": "product_name"}]

# name, question, the right query (run by the engine for the expected values), what must appear
CASES = [
    ("total revenue", "What is our total revenue?", {"measures": ["Revenue"]}, "values"),
    ("order count", "How many orders do we have?", {"measures": ["Orders"]}, "values"),
    ("average order", "What is the average order value?", {"measures": ["Average order value"]}, "values"),
    ("top region", "Which region brings in the most revenue?",
     {"measures": ["Revenue"], "group_by": REGION, "sort": "desc", "limit": 1}, "first label"),
    ("by segment", "Show revenue by customer segment.", {"measures": ["Revenue"], "group_by": SEGMENT}, "values"),
    ("top products", "What are our top 3 products by revenue?",
     {"measures": ["Revenue"], "group_by": PRODUCT, "sort": "desc", "limit": 3}, "labels"),
    ("year to date", "What is revenue this year so far?", {"measures": ["Revenue"], "period": "this_year_to_date"}, "values"),
    ("last year", "What was revenue last year?", {"measures": ["Revenue"], "period": "last_year"}, "values"),
    ("last 3 months", "How much revenue did we make in the last 3 months?",
     {"measures": ["Revenue"], "period": "last_n_months", "n": 3}, "values"),
    ("why change", "Why is revenue this year so far different from the same time last year?",
     {"measures": ["Revenue"], "period": "this_year_to_date", "compare_to": "last_year_to_date", "group_by": SEGMENT},
     "total change"),
]


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def build(api) -> str:
    """The sample dataset, cleaned and modelled by fixed steps, published, with a dashboard. Returns its id."""
    pid = ok(api.post("/api/datasets", json={"name": "Eval sample"}))["id"]
    base = f"/api/datasets/{pid}"
    ok(api.post(f"{base}/sources/sample"))
    tf = f"{base}/transform"
    queries = {q["name"]: q["id"] for q in ok(api.get(tf))["queries"]}

    def step(table, op, **params):
        ok(api.post(f"{tf}/queries/{queries[table]}/steps", json={"op": op, "params": params}))

    step("sales", "trim", columns=["Region"])
    step("sales", "blanks_to_null", columns=["Region"])
    step("sales", "change_case", columns=["Region"], case="title")
    step("sales", "remove_duplicates", columns=["Order ID"])
    step("sales", "change_type", column="Order Date", to="date", date_order="dmy")
    step("sales", "change_type", column="Qty", to="integer")
    step("sales", "change_type", column="Unit Price", to="decimal")
    step("products", "remove_duplicates", columns=["product_code"])
    step("customers", "trim", columns=["Segment"])
    step("customers", "change_case", columns=["Segment"], case="title")
    m = f"{base}/model"
    ok(api.put(f"{m}/date-table", json={}))
    for a, ac, b, bc in (("sales", "Order Date", "Date", "Date"), ("sales", "Customer ID", "customers", "Customer ID"),
                         ("sales", "Product Code", "products", "product_code")):
        ok(api.post(f"{m}/relationships", json={"from_table": a, "from_column": ac, "to_table": b, "to_column": bc}))
    money = {"type": "currency", "decimals": 2, "currency": "RM"}
    for body in (
        {"name": "Revenue", "kind": "aggregate", "table": "sales", "aggregation": "sum", "expression": "[Qty] * [Unit Price]", "format": money},
        {"name": "Orders", "kind": "aggregate", "table": "sales", "aggregation": "count", "format": {"type": "integer"}},
        {"name": "Units", "kind": "aggregate", "table": "sales", "aggregation": "sum", "expression": "[Qty]", "format": {"type": "integer"}},
        {"name": "Average order value", "kind": "formula", "expression": "[Revenue] / [Orders]", "format": money},
    ):
        ok(api.post(f"{m}/measures", json=body))
    ok(api.post(f"{base}/publish", params={"confirm": True}))
    return ok(api.post("/api/dashboards", json={"name": "Eval", "dataset_id": pid}))["id"]


def close(a, b) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(a, b, rel_tol=0.005, abs_tol=0.01)
    return str(a).strip().lower() == str(b).strip().lower()


def expected(truth: dict, kind: str):
    rows = truth["rows"]
    if kind == "values":
        return [v for row in rows for v in row if isinstance(v, (int, float))]
    if kind == "first label":
        return [rows[0][0]]
    if kind == "labels":
        return [r[0] for r in rows]
    if kind == "total change":
        return [rows[0][4]]
    raise ValueError(kind)


def found(values: list, results: list[dict], kind: str) -> list:
    """The expected values missing from the AI's results."""
    cells = [cell for r in results if not r.get("error") for row in r["rows"] for cell in row]
    if kind == "total change":
        cells = [row[4] for r in results if r.get("show_as") == "change" for row in r["rows"][:1]]
    return [v for v in values if not any(close(v, c) for c in cells)]


def main(words: list[str]) -> int:
    from app import ask, publishing
    from app.settings import SettingsStore

    data_dir = Path(tempfile.mkdtemp(prefix="datawazi-eval-"))
    real = REPO_ROOT / "data" / "settings.json"
    if real.exists():
        shutil.copy(real, data_dir / "settings.json")
    settings = SettingsStore(data_dir / "settings.json").load()
    if not settings.api_key:
        print("No AI key: set one in the app's Settings (data/settings.json) or DATAWAZI_API_KEY.")
        return 2
    api = TestClient(create_app(data_dir=data_dir, client_factory=llm.make_client))
    try:
        dashboard = build(api)
        boards_dash = ok(api.get(f"/api/dashboards/{dashboard}"))
        dataset_id = boards_dash["dataset"]["id"]
        cases = [c for c in CASES if all(w.lower() in c[0] for w in words)]
        outcomes, passed = [], 0
        # The right answers: the engine runs the hand-written queries.
        from app import workspaces
        from app.projects import ProjectStore
        store = ProjectStore(data_dir / "workspaces" / "local" / "datasets")
        with workspaces.use("local"):
            dataset = store.get(dataset_id)
            frozen = publishing.published_project(dataset)
            with store.connect_published(dataset_id, dataset["published"]["file"]) as con:
                truths = {name: ask.run(con, frozen, [query])[0] for name, _, query, _ in cases}
        for name, question, _, kind in cases:
            truth = truths[name]
            want = expected(truth, kind) if not truth.get("error") else []
            response = api.post(f"/api/dashboards/{dashboard}/ask", json={"question": question, "side": "published"})
            if response.status_code != 200:
                outcomes.append({"case": name, "pass": False, "why": response.text})
                print(f"FAIL  {name}: {response.text[:120]}")
                continue
            body = response.json()
            missing = found(want, body["results"], kind)
            good = not truth.get("error") and not missing and body["check"]["verified"]
            passed += good
            why = ("engine: " + truth["error"]) if truth.get("error") else \
                  f"missing {missing}" if missing else "" if body["check"]["verified"] else f"unverified {body['check']['unmatched']}"
            print(f"{'pass' if good else 'FAIL'}  {name}{': ' + why if why else ''}")
            outcomes.append({"case": name, "question": question, "pass": good, "why": why, "expected": want,
                             "answer": body["answer"], "queries": [r["query"] for r in body["results"]]})
        print(f"\n{passed} of {len(cases)} passed ({settings.model})")
        (Path(__file__).parent / "last_run.json").write_text(json.dumps(outcomes, indent=2, default=str), encoding="utf-8")
        return 0 if passed == len(cases) else 1
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
