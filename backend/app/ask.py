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
from datetime import date, timedelta

from . import llm, model, report

MAX_QUERIES = 3
MAX_ROWS = 50
HISTORY_TURNS = 6
TOP_CHANGES = 5  # contributors listed per breakdown column in a "why did it change" answer
# Relative dates, worked out by the engine from the data's latest date (never from today's date).
PERIODS = {
    "this_year_to_date": "this year to date",
    "this_year": "this year",
    "last_year": "last year",
    "last_year_to_date": "last year to date",
    "this_quarter": "this quarter so far",
    "last_quarter": "last quarter",
    "this_month": "this month so far",
    "last_month": "last month",
    "last_n_months": "the last {n} months",
    "last_n_days": "the last {n} days",
}

PLAN_SYSTEM = (
    "You answer business questions about a dashboard's data, like Power BI Q&A. You cannot write SQL: you "
    "choose queries made of the listed measures, optionally split by columns (group_by) and filtered "
    "(filters with operator equals, not_equals, in, not_in, is_blank, is_not_blank, greater_than, less_than, "
    "at_least, at_most). Use at most 3 queries. For trends split by Date[Year Month] or Date[Year]. For 'top' "
    "questions split by the column and sort desc with a limit. For relative dates (this year, last quarter, the "
    "last 3 months, year to date) set period, and n where needed, instead of writing date filters: the app works "
    "out the dates from data_as_of. For 'why did X change / grow / drop' questions use ONE query: the measure, "
    "period (the later period), compare_to (the earlier one) and group_by with up to 3 columns to break the change "
    "down by; the app finds the biggest contributors. If the data can't answer the question, return no queries "
    "and explain in cannot_answer. Use names exactly as listed."
)

ANSWER_SYSTEM = (
    "You explain query results to a business user in 1 to 3 short sentences. Use only numbers that appear in "
    "the results or follow directly from them (differences, shares). Round sensibly (RM 1.2M, 34%). Do not "
    "guess causes the data does not show; say what else could be checked instead. For a change breakdown, give the "
    "total change, then the biggest contributors with their change, in plain words. Say which dates a period "
    "covers when it matters. When listing three or more "
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
                    "period": {"type": "string", "enum": list(PERIODS)},
                    "n": {"type": "integer", "description": "for last_n_months and last_n_days"},
                    "compare_to": {"type": "string", "enum": list(PERIODS), "description": "why-did-it-change questions only"},
                    "compare_n": {"type": "integer"},
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
        tables.add(str(names.get(item.get("table"), item.get("table"))))

    def filter_text(f):
        value = f.get("values") if f.get("operator") in ("in", "not_in") else f.get("value")
        operator = str(f.get("operator", "")).replace("_", " ")
        return f"{names.get(f.get('table'), f.get('table'))}[{f.get('column')}] {operator}" + ("" if value is None else f" {value}")

    return {
        "measures": measures,
        "split_by": [f"{g.get('table')}[{g.get('column')}]" for g in query.get("group_by") or []],
        "filters": [filter_text(f) for f in query.get("filters") or []]
        + [f"{t} (page filter)" for t in report.describe_filters(con, project, project.get(model.PAGE_FILTERS) or [])],
        "tables": sorted(t for t in tables if t and t != "None"),
    }


def period_range(period: str, n, latest: date) -> tuple[date, date]:
    """The dates a relative period covers, counted back from the data's latest date."""
    def quarter_start(day: date) -> date:
        return date(day.year, 3 * ((day.month - 1) // 3) + 1, 1)

    def month_start(day: date) -> date:
        return day.replace(day=1)

    def add_months(day: date, months: int) -> date:
        month = day.month - 1 + months
        year, month = day.year + month // 12, month % 12 + 1
        return date(year, month, 1)

    n = n if isinstance(n, int) and 0 < n <= 366 else 3
    if period == "this_year_to_date":
        return date(latest.year, 1, 1), latest
    if period == "this_year":
        return date(latest.year, 1, 1), date(latest.year, 12, 31)
    if period == "last_year":
        return date(latest.year - 1, 1, 1), date(latest.year - 1, 12, 31)
    if period == "last_year_to_date":
        return date(latest.year - 1, 1, 1), report.same_day_last_year(latest)
    if period == "this_quarter":
        return quarter_start(latest), latest
    if period == "last_quarter":
        start = add_months(quarter_start(latest), -3)
        return start, quarter_start(latest) - timedelta(days=1)
    if period == "this_month":
        return month_start(latest), latest
    if period == "last_month":
        start = add_months(month_start(latest), -1)
        return start, month_start(latest) - timedelta(days=1)
    if period == "last_n_months":
        return add_months(month_start(latest), -(n - 1)), latest
    if period == "last_n_days":
        return latest - timedelta(days=n - 1), latest
    raise model.ModelError(f"Unknown period '{period}'.")


def period_label(period: str, n, start: date, end: date) -> str:
    name = PERIODS[period].format(n=n if isinstance(n, int) and n > 0 else 3)
    return f"{name} ({start.isoformat()} to {end.isoformat()})"


def latest_date(con, project: dict) -> date:
    if model.DATE_ID not in model.tables(con, project):
        raise model.ModelError("Relative dates need the date table; add one in the Model.")
    as_of = report.as_of(con, project)
    if not as_of:
        raise model.ModelError("No dated data to count back from.")
    return date.fromisoformat(as_of[:10])


def change_breakdown(con, project: dict, query: dict, clean: dict) -> dict:
    """Why did a measure change: its total in both periods, then the biggest movers per breakdown column."""
    latest = latest_date(con, project)
    measure = clean["measures"][0]
    after = period_range(query["period"], query.get("n"), latest)
    before = period_range(query["compare_to"], query.get("compare_n"), latest)
    a_label = period_label(query["period"], query.get("n"), *after)
    b_label = period_label(query["compare_to"], query.get("compare_n"), *before)

    def value(rng, group_by=()):
        return model.evaluate(con, project, [measure], list(group_by), clean["filters"] + report.date_filters(*rng))

    total_before = value(before)["rows"][0][-1]
    total_after = value(after)["rows"][0][-1]
    rows = [["Total", "", total_before, total_after, (total_after or 0) - (total_before or 0)]]
    skipped = []
    for group in clean["group_by"][:3]:
        # A column the measure can't reach is left out, not the whole answer.
        try:
            then = {r[0]: r[1] for r in value(before, [group])["rows"]}
            now = {r[0]: r[1] for r in value(after, [group])["rows"]}
        except model.ModelError as exc:
            skipped.append(f"{group.get('table')}[{group.get('column')}]: {exc}")
            continue
        moves = [(k, then.get(k), now.get(k), (now.get(k) or 0) - (then.get(k) or 0)) for k in set(then) | set(now)]
        moves.sort(key=lambda m: (-abs(m[3]), str(m[0])))
        label = f"{group.get('table')}[{group.get('column')}]"
        rows += [[label, "(blank)" if k is None else k, b, a, d] for k, b, a, d in moves[:TOP_CHANGES] if d]
    return {"columns": ["Split by", "Value", b_label, a_label, "Change"], "rows": rows, "truncated": False,
            "periods": {"before": b_label, "after": a_label}, "skipped": skipped}


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
            if query.get("compare_to") in PERIODS and query.get("period") in PERIODS:
                entry.update(change_breakdown(con, project, query, clean), show_as="change")
                entry["how"] = explain(con, project, clean)
                results.append(entry)
                continue
            if query.get("period") in PERIODS:
                start, end = period_range(query["period"], query.get("n"), latest_date(con, project))
                clean["filters"] = clean["filters"] + report.date_filters(start, end)
                entry["period"] = period_label(query["period"], query.get("n"), start, end)
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
    usable = [{"title": r["title"], "period": r.get("period"), "columns": r["columns"], "rows": r["rows"], "error": r["error"],
               **({"could_not_split_by": r["skipped"]} if r.get("skipped") else {})} for r in results]
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
