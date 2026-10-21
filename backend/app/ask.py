"""The Ask agent: answers questions about a dashboard's data. Read-only, and it shows its work.

1. Plan: the AI turns the question into at most three structured queries over the model's
   measures, columns and filters. It never writes SQL.
2. Run: the app evaluates them with model.evaluate, the same engine the dashboard uses.
3. Answer: the AI writes a short answer from those results only. Numbers in the answer are
   checked against the results; unmatched ones are flagged.

Each result carries "how this was calculated": the measures and their definitions, what it
was split and filtered by, and which tables were used.
"""

import json

from . import llm, model, report

MAX_QUERIES = 3
MAX_ROWS = 50
HISTORY_TURNS = 6

PLAN_SYSTEM = (
    "You answer business questions about a dashboard's data, like Power BI Q&A. You cannot write SQL: you "
    "choose queries made of the listed measures, optionally split by columns (group_by) and filtered "
    "(filters with operator equals, not_equals, in, not_in, is_blank, is_not_blank, greater_than, less_than, "
    "at_least, at_most). Use at most 3 queries. For trends split by Date[Year Month] or Date[Year]. For 'top' "
    "questions split by the column and sort desc with a limit. If the data can't answer the question, return no "
    "queries and explain in cannot_answer. Use names exactly as listed."
)

ANSWER_SYSTEM = (
    "You explain query results to a business user in 1 to 3 short sentences. Use only numbers that appear in "
    "the results or follow directly from them (differences, shares). Round sensibly (RM 1.2M, 34%). Do not "
    "guess causes the data does not show; say what else could be checked instead. When listing three or more "
    "items, put each on its own line as a Markdown bullet (\"- **Name**: note\"); no headings or tables. Then suggest up to 3 short "
    "follow-up questions the data can answer."
)

FILTER = {
    "type": "object",
    "properties": {
        "table": {"type": "string"},
        "column": {"type": "string"},
        "operator": {"type": "string"},
        "value": {},
        "values": {"type": "array"},
    },
}

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "queries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "measures": {"type": "array", "items": {"type": "string"}},
                    "group_by": {"type": "array", "items": {"type": "object", "properties": {
                        "table": {"type": "string"}, "column": {"type": "string"}}}},
                    "filters": {"type": "array", "items": FILTER},
                    "sort": {"type": "string", "enum": ["desc", "asc", "none"]},
                    "limit": {"type": "integer"},
                    "show_as": {"type": "string", "enum": ["number", "bar", "line", "table"]},
                    "title": {"type": "string", "description": "plain-words title for the result"},
                },
                "required": ["measures"],
            },
        },
        "cannot_answer": {"type": "string"},
    },
    "required": ["queries"],
}

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "follow_ups": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["answer"],
}


def model_summary(con, project: dict) -> dict:
    found = model.tables(con, project)
    current = model.state(project)
    names = {t.id: t.name for t in found.values()}
    hidden = {(h["table"], h["column"]) for h in current["hidden"]}
    return {
        "measures": [{"name": m["name"], "definition": model.describe_measure(m, names), "format": m["format"]["type"]}
                     for m in current["measures"] if not m.get("error")],
        "columns": [f"{t.name}[{c.name}] ({c.type})" for t in found.values() for c in t.columns
                    if (t.id, c.name) not in hidden],
        "relationships": [f"{names.get(r['from_table'])}[{r['from_column']}] -> {names.get(r['to_table'])}[{r['to_column']}]"
                          for r in current["relationships"] if r.get("active") and not r.get("error")],
        "data_as_of": report.as_of(con, project),
    }


def pages_summary(pages: list[dict]) -> list[dict]:
    return [{"page": p["title"], "visuals": [v["title"] for v in p["visuals"]]} for p in pages]


def plan(client, model_name: str, capabilities: dict, con, project: dict, pages: list[dict],
         question: str, history: list[dict]) -> dict:
    turns = [{"role": h.get("role"), "text": str(h.get("content", ""))[:500]} for h in history[-HISTORY_TURNS:]]
    scope = report.describe_filters(con, project, project.get(model.PAGE_FILTERS) or [])
    prompt = (
        f"Model:\n{json.dumps(model_summary(con, project), default=str)}\n\n"
        f"Dashboard pages:\n{json.dumps(pages_summary(pages))}\n\n"
        + (f"The page is filtered to {'; '.join(scope)}. Every query result already respects this; "
           "do not add these filters again.\n\n" if scope else "")
        + f"Conversation so far:\n{json.dumps(turns)}\n\nQuestion: {question}"
    )
    raw = llm.ask_json(client, model_name, capabilities, PLAN_SYSTEM, prompt, "plan_queries", PLAN_SCHEMA)
    queries = [q for q in raw.get("queries") or [] if isinstance(q, dict)][:MAX_QUERIES]
    return {"queries": queries, "cannot_answer": str(raw.get("cannot_answer") or "")}


def explain(con, project: dict, query: dict) -> dict:
    """How a result was calculated, in plain words."""
    found = model.tables(con, project)
    current = model.state(project)
    names = {t.id: t.name for t in found.values()}
    by_name = {m["name"].lower(): m for m in current["measures"]}
    measures, tables = [], set()
    for name in query["measures"]:
        measure = by_name.get(str(name).lower())
        if measure:
            measures.append({"name": measure["name"], "definition": model.describe_measure(measure, names)})
            if measure["kind"] == "aggregate":
                tables.add(names.get(measure["table"], measure["table"]))
    for item in query.get("group_by") or []:
        tables.add(str(item.get("table")))
    for item in query.get("filters") or []:
        tables.add(str(item.get("table")))

    def filter_text(f):
        value = f.get("values") if f.get("operator") in ("in", "not_in") else f.get("value")
        operator = str(f.get("operator", "")).replace("_", " ")
        return f"{f.get('table')}[{f.get('column')}] {operator}" + ("" if value is None else f" {value}")

    return {
        "measures": measures,
        "split_by": [f"{g.get('table')}[{g.get('column')}]" for g in query.get("group_by") or []],
        "filters": [filter_text(f) for f in query.get("filters") or []]
        + [f"{t} (page filter)" for t in report.describe_filters(con, project, project.get(model.PAGE_FILTERS) or [])],
        "tables": sorted(t for t in tables if t and t != "None"),
    }


def run(con, project: dict, queries: list[dict]) -> list[dict]:
    results = []
    for query in queries:
        clean = {
            "measures": [m for m in query.get("measures") or [] if isinstance(m, str)],
            "group_by": [g for g in query.get("group_by") or [] if isinstance(g, dict)],
            "filters": [f for f in query.get("filters") or [] if isinstance(f, dict)],
        }
        entry = {"title": str(query.get("title") or ", ".join(clean["measures"])), "show_as": query.get("show_as") or "table",
                 "query": clean, "columns": [], "rows": [], "truncated": False, "error": None}
        try:
            if not clean["measures"]:
                raise model.ModelError("A query needs at least one measure.")
            data = model.evaluate(con, project, clean["measures"], clean["group_by"], clean["filters"])
            rows = data["rows"]
            groups = len(clean["group_by"])
            if groups and query.get("sort") in ("desc", "asc"):
                valued = sorted((r for r in rows if r[groups] is not None), key=lambda r: r[groups],
                                reverse=query["sort"] == "desc")
                rows = valued + [r for r in rows if r[groups] is None]  # blanks last either way
            limit = query.get("limit") if isinstance(query.get("limit"), int) and query["limit"] > 0 else MAX_ROWS
            limit = min(limit, MAX_ROWS)
            entry.update(columns=data["columns"], rows=rows[:limit], truncated=len(rows) > limit)
            entry["how"] = explain(con, project, clean)
        except (model.ModelError, report.ReportError, KeyError, TypeError, ValueError) as exc:
            entry["error"] = str(exc)
        results.append(entry)
    return results


def answer(client, model_name: str, capabilities: dict, question: str, results: list[dict], cannot: str) -> dict:
    usable = [{"title": r["title"], "columns": r["columns"], "rows": r["rows"], "error": r["error"]} for r in results]
    prompt = (
        f"Question: {question}\n\nQuery results:\n{json.dumps(usable, default=str)}\n\n"
        + (f"Note from planning: {cannot}\n\n" if cannot else "")
        + "Answer the question."
    )
    raw = llm.ask_json(client, model_name, capabilities, ANSWER_SYSTEM, prompt, "answer", ANSWER_SCHEMA)
    text = str(raw.get("answer") or "").strip()
    follow_ups = [str(f) for f in raw.get("follow_ups") or [] if isinstance(f, str)][:3]
    rows = [row for r in results if not r["error"] for row in r["rows"]]
    check = report.check_insight(text, {"rows": rows, "check": None}) if rows else {"verified": True, "unmatched": []}
    return {"answer": text, "follow_ups": follow_ups, "check": check}


def ask(client, model_name: str, capabilities: dict, con, project: dict, pages: list[dict],
        question: str, history: list[dict]) -> dict:
    planned = plan(client, model_name, capabilities, con, project, pages, question, history)
    results = run(con, project, planned["queries"])
    if not results and not planned["cannot_answer"]:
        planned["cannot_answer"] = "The question could not be turned into a query on this data."
    scope = report.describe_filters(con, project, project.get(model.PAGE_FILTERS) or [])
    asked = f"{question}\n(The page is filtered to {'; '.join(scope)}; say the numbers are for this selection.)" if scope else question
    written = answer(client, model_name, capabilities, asked, results, planned["cannot_answer"])
    return {**written, "results": results, "cannot_answer": planned["cannot_answer"]}
