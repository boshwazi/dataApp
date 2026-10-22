"""Stage chat agents: one per stage, each able to change only its own stage.

A chat turn sends the agent the stage's current state, what came before it (read-only),
the recent conversation and the actions it may take. It answers in words and, when the
user asks for a change, with a list of actions. Every action runs through the same
engine functions and checks as the buttons, so a chat can't do anything a click couldn't.

Modes:
  full    valid actions are applied at once, as one undoable change
  review  actions are checked on a copy and shown for approval first
Removing data always waits for approval, whatever the mode.
"""

import copy
import json
import re
from dataclasses import dataclass, field
from typing import Callable

from . import ingest, llm, model, report, transform
from .projects import new_id, now, table_name
from .transform_ops import StepError, catalogue, describe

HISTORY_TURNS = 10
MAX_MESSAGES = 200
FAILURES = (StepError, model.ModelError, report.ReportError, ingest.IngestError, ValueError, KeyError, TypeError)


class ChatError(ValueError):
    pass


@dataclass
class Ctx:
    """What an action works on. `dry_run` actions check everything but write nothing to the database."""

    project: dict
    con: object = None
    dry_run: bool = False
    store: object = None
    fetch_url: Callable | None = None


@dataclass
class Action:
    params: dict  # described to the AI
    run: Callable[[Ctx, dict], str]  # applies the action, returns a plain-words label
    destructive: bool = False


@dataclass
class Stage:
    name: str
    role: str
    actions: dict[str, Action]
    context: Callable[[Ctx], dict]
    begin: Callable[[Ctx], object] = lambda ctx: None  # before a change set (undo snapshot)
    finish: Callable[[Ctx, object], None] = lambda ctx, token: None  # after it (re-run, collapse undo)
    extra: dict = field(default_factory=dict)
    data_questions: bool = False  # may hand a question to the Ask pipeline (the Report stage)


def need(params: dict, key: str):
    value = params.get(key)
    if value in (None, "", []):
        raise ChatError(f"'{key}' is required.")
    return value


# Get Data


def find_source(project: dict, name) -> dict:
    for source in project["sources"]:
        if isinstance(name, str) and source["table"].lower() == table_name(name):
            return source
    raise ChatError(f"There is no table called '{name}'.")


def gd_rename(ctx: Ctx, p: dict) -> str:
    source = find_source(ctx.project, need(p, "table"))
    new = table_name(need(p, "name"))
    if new in {s["table"] for s in ctx.project["sources"] if s["id"] != source["id"]}:
        raise ChatError(f"A table named {new} already exists.")
    old = source["table"]
    if not ctx.dry_run:
        ctx.store.rename_source(ctx.project["id"], source["id"], new)
    return f"Rename {old} to {new}"


def gd_remove(ctx: Ctx, p: dict) -> str:
    source = find_source(ctx.project, need(p, "table"))
    if not ctx.dry_run:
        ctx.store.delete_source(ctx.project["id"], source["id"])
        with ctx.store.edit(ctx.project["id"]) as project, ctx.store.connect(project["id"]) as con:
            transform.drop_source(project, source["id"])
            transform.apply_all(con, project)
    return f"Remove {source['table']} and its data"


def gd_add_sheet(ctx: Ctx, p: dict) -> str:
    url = need(p, "url")
    ingest.gsheet_csv_url(url)  # checks the link
    if not ctx.dry_run:
        table = ingest.read_gsheet(url, ctx.fetch_url, str(p.get("name") or "").strip())
        source = ctx.store.add_source(ctx.project["id"], table.frame, table.name,
                                      {"kind": "gsheet", "url": url, "label": table.name}, table.warnings)
        return f"Add Google Sheet as {source['table']}"
    return "Add the Google Sheet"


def gd_reprofile(ctx: Ctx, p: dict) -> str:
    source = find_source(ctx.project, need(p, "table"))
    if not ctx.dry_run:
        ctx.store.reprofile(ctx.project["id"], source["id"])
    return f"Re-profile {source['table']}"


def gd_context(ctx: Ctx) -> dict:
    return {
        "tables": [
            {
                "table": s["table"],
                "from": s["origin"].get("file") or s["origin"].get("url") or s["origin"]["kind"],
                "rows": s["profile"]["rows"],
                "duplicate_rows": s["profile"]["duplicate_rows"],
                "about": (s.get("ai") or {}).get("summary"),
                "columns": [{"name": c["name"], "looks_like": c["detected_type"], "issues": c["issues"]}
                            for c in s["profile"]["columns"]],
            }
            for s in ctx.project["sources"]
        ]
    }


GET_DATA = Stage(
    name="get_data",
    role="the Get Data agent: you manage the dataset's sources (files and Google Sheets)",
    actions={
        "rename_table": Action({"table": "current name", "name": "new name"}, gd_rename),
        "add_google_sheet": Action({"url": "Google Sheets link", "name": "optional table name"}, gd_add_sheet),
        "reprofile": Action({"table": "name"}, gd_reprofile),
        "remove_table": Action({"table": "name"}, gd_remove, destructive=True),
    },
    context=gd_context,
)


# Transform


def query(ctx: Ctx, name) -> dict:
    return transform.find_query(ctx.project, need({"table": name}, "table"))


def step_at(q: dict, number) -> dict:
    try:
        index = int(number) - 1
    except (TypeError, ValueError):
        raise ChatError("Steps are given by their number, starting at 1.")
    if not 0 <= index < len(q["steps"]):
        raise ChatError(f"{q['name']} has no step {number}.")
    return q["steps"][index]


def tf_add_step(ctx: Ctx, p: dict) -> str:
    q = query(ctx, p.get("table"))
    position = p.get("position")
    at = int(position) - 1 if position not in (None, "") else None
    step = {"op": need(p, "op"), "params": p.get("params") or {}, "reason": p.get("reason", "")}
    added, rejected = transform.add_steps(ctx.con, ctx.project, q["id"], [step], "AI", at)
    if rejected:
        raise StepError(rejected[0]["error"])
    return f"{q['name']}: {describe(step['op'], added[0]['params'], transform.query_names(ctx.project))}"


def tf_remove_step(ctx: Ctx, p: dict) -> str:
    q = query(ctx, p.get("table"))
    step = step_at(q, need(p, "step"))
    label = describe(step["op"], step["params"], transform.query_names(ctx.project))
    transform.remove_step(ctx.con, ctx.project, q["id"], step["id"])
    return f"{q['name']}: remove step '{label}'"


def tf_move_step(ctx: Ctx, p: dict) -> str:
    q = query(ctx, p.get("table"))
    step = step_at(q, need(p, "step"))
    to = int(need(p, "to"))
    transform.move_step(ctx.con, ctx.project, q["id"], step["id"], to - 1)
    return f"{q['name']}: move '{describe(step['op'], step['params'], transform.query_names(ctx.project))}' to step {to}"


def tf_split(ctx: Ctx, p: dict) -> str:
    made = transform.split_table(ctx.con, ctx.project, need(p, "from_table"), need(p, "columns"), p.get("key"),
                                 p.get("name"), p.get("reason", ""), "AI")
    return f"Split lookup table {made['name']} off {p['from_table']}"


def tf_remove_table(ctx: Ctx, p: dict) -> str:
    q = query(ctx, p.get("table"))
    transform.remove_table(ctx.con, ctx.project, q["id"])
    return f"Remove lookup table {q['name']}"


def tf_rename_table(ctx: Ctx, p: dict) -> str:
    q = query(ctx, p.get("table"))
    old = q["name"]
    transform.rename_query(ctx.con, ctx.project, q["id"], need(p, "name"))
    return f"Rename table {old} to {q['name']}"


def tf_context(ctx: Ctx) -> dict:
    names = transform.query_names(ctx.project)
    tables = []
    for q in transform.state(ctx.project)["queries"]:
        profile = (q["output"] or {}).get("profile") or {}
        tables.append({
            "table": q["name"],
            "kind": "lookup table" if q.get("base_query") else "loaded table",
            "error": q["error"],
            "rows": profile.get("rows"),
            "steps": [f"{i}. {describe(s['op'], s['params'], names)} [{s['status']}]" for i, s in enumerate(q["steps"], 1)],
            # Which rows each step dropped, so the agent can show a removed duplicate or a filtered row.
            "rows_removed_by_steps": transform.removals(ctx.con, ctx.project, q),
            "warnings": q.get("warnings", []),
            "columns": [
                {"name": c["name"], "type": t["type"], "issues": c["issues"], "distinct": c["distinct"],
                 "top_values": [v["value"] for v in c["top_values"][:3]]}
                for c, t in zip(profile.get("columns", []), (q["output"] or {}).get("columns", []))
            ],
        })
    return {"tables": tables, "operation_catalogue": catalogue(),
            "note": "rows_removed_by_steps lists real rows each step dropped (up to 5 examples). When asked which "
                    "rows a step removed, quote them, and say the step's preview has a Removed rows tab with all of them."}


def tf_begin(ctx: Ctx):
    return len(transform.state(ctx.project)["history"])


def tf_finish(ctx: Ctx, before) -> None:
    history = transform.state(ctx.project)["history"]
    del history[before + 1:]  # one undo reverts the whole chat change
    if not ctx.dry_run:
        transform.apply_all(ctx.con, ctx.project)


TRANSFORM = Stage(
    name="transform",
    role="the Transform agent: you clean and shape tables with Applied Steps from the operation catalogue",
    actions={
        "add_step": Action({"table": "name", "op": "catalogue operation", "params": "the operation's params",
                            "position": "optional step number to insert at", "reason": "why"}, tf_add_step),
        "remove_step": Action({"table": "name", "step": "step number"}, tf_remove_step),
        "move_step": Action({"table": "name", "step": "step number", "to": "new step number"}, tf_move_step),
        "split_table": Action({"from_table": "name", "columns": ["key and attribute columns"], "key": "key column",
                               "name": "new table name", "reason": "why"}, tf_split),
        "remove_table": Action({"table": "a lookup table's name"}, tf_remove_table),
        "rename_table": Action({"table": "name", "name": "new name"}, tf_rename_table),
    },
    context=tf_context,
    begin=tf_begin,
    finish=tf_finish,
)


# Model


def find_relationship(ctx: Ctx, p: dict) -> dict:
    found = model.tables(ctx.con, ctx.project)
    a, b = model.find_table(found, p.get("from_table")), model.find_table(found, p.get("to_table"))
    for rel in model.state(ctx.project)["relationships"]:
        pairs = {(rel["from_table"], rel["from_column"].lower()), (rel["to_table"], rel["to_column"].lower())}
        if pairs == {(a.id, str(p.get("from_column", "")).lower()), (b.id, str(p.get("to_column", "")).lower())}:
            return rel
    raise ChatError("There is no such relationship.")


def find_measure(ctx: Ctx, name) -> dict:
    for measure in model.state(ctx.project)["measures"]:
        if isinstance(name, str) and measure["name"].lower() == name.strip().lower():
            return measure
    raise ChatError(f"There is no measure called '{name}'.")


def table_names(ctx: Ctx) -> dict:
    return transform.query_names(ctx.project) | {model.DATE_ID: model.DATE_NAME}


def md_add_relationship(ctx: Ctx, p: dict) -> str:
    rel = model.add_relationship(ctx.con, ctx.project, p, "AI")
    names = table_names(ctx)
    state = "" if rel["active"] else " (inactive: another path already joins these tables)"
    return (f"Relate {names[rel['from_table']]}[{rel['from_column']}] → {names[rel['to_table']]}[{rel['to_column']}], "
            f"{rel['checks']['match_rate']:.0%} match{state}")


def md_remove_relationship(ctx: Ctx, p: dict) -> str:
    rel = find_relationship(ctx, p)
    model.state(ctx.project)["relationships"].remove(rel)
    names = table_names(ctx)
    return f"Remove relationship {names.get(rel['from_table'])}[{rel['from_column']}] → {names.get(rel['to_table'])}[{rel['to_column']}]"


def md_add_measure(ctx: Ctx, p: dict) -> str:
    measure = model.add_measure(ctx.con, ctx.project, p, "AI")
    return f"Add measure {measure['name']} = {model.describe_measure(measure, table_names(ctx))}"


def md_change_measure(ctx: Ctx, p: dict) -> str:
    measure = find_measure(ctx, need(p, "name"))
    fields = {k: v for k, v in p.items() if k in ("kind", "table", "aggregation", "expression", "filters", "format",
                                                   "description", "base", "calc", "months") and v is not None}
    if p.get("new_name"):
        fields["name"] = p["new_name"]
    updated = {**copy.deepcopy(measure), **copy.deepcopy(fields), "status": "AI"}
    model.check_measure(model.tables(ctx.con, ctx.project), model.state(ctx.project)["measures"], updated)
    measures = model.state(ctx.project)["measures"]
    measures[measures.index(measure)] = updated
    return f"Change measure {measure['name']} to {model.describe_measure(updated, table_names(ctx))}"


def md_remove_measure(ctx: Ctx, p: dict) -> str:
    measure = find_measure(ctx, need(p, "name"))
    model.state(ctx.project)["measures"].remove(measure)
    return f"Remove measure {measure['name']}"


def md_date_table(ctx: Ctx, p: dict) -> str:
    start, end = p.get("start_year"), p.get("end_year")
    start = int(start) if start not in (None, "") else None
    end = int(end) if end not in (None, "") else None
    if ctx.dry_run:
        if start and end and start > end:
            raise ChatError("The date table must start before it ends.")
    else:
        model.set_date_table(ctx.con, ctx.project, start, end, status="AI", reason=str(p.get("reason") or ""))
    span = f" from {start}" if start else ""
    return f"Set up the date table{span}{f' to {end}' if end else ''}"


def md_remove_date_table(ctx: Ctx, p: dict) -> str:
    current = model.state(ctx.project)
    if not current["date_table"]:
        raise ChatError("There is no date table.")
    current["date_table"] = None
    current["relationships"] = [r for r in current["relationships"] if model.DATE_ID not in (r["from_table"], r["to_table"])]
    if not ctx.dry_run:
        model.build_date_table(ctx.con, ctx.project)
    return "Remove the date table and its relationships"


def md_hide(ctx: Ctx, p: dict) -> str:
    hidden = p.get("hidden", True) is not False
    model.set_hidden(ctx.con, ctx.project, need(p, "table"), need(p, "column"), hidden)
    return f"{'Hide' if hidden else 'Show'} {p['table']}[{p['column']}]"


def md_context(ctx: Ctx) -> dict:
    found = model.tables(ctx.con, ctx.project)
    current = model.state(ctx.project)
    names = table_names(ctx)
    measures = []
    for m in current["measures"]:
        value = None
        if not m.get("error"):
            try:
                value = model.evaluate(ctx.con, ctx.project, [m["name"]])["rows"][0][0]
            except model.ModelError:
                pass
        measures.append({"name": m["name"], "definition": model.describe_measure(m, names), "value": value,
                         "format": m["format"], "error": m.get("error")})
    return {
        "tables": [{"table": t.name, "columns": [f"{c.name} ({c.type})" for c in t.columns]} for t in found.values()],
        "relationships": [
            {"from": f"{names.get(r['from_table'])}[{r['from_column']}]", "to": f"{names.get(r['to_table'])}[{r['to_column']}]",
             "active": r["active"], "match_rate": (r.get("checks") or {}).get("match_rate"), "error": r.get("error")}
            for r in current["relationships"]
        ],
        "date_table": current["date_table"] and {k: current["date_table"].get(k) for k in ("start_year", "end_year", "error")},
        "measures": measures,
        "hidden_columns": [f"{names.get(h['table'])}[{h['column']}]" for h in current["hidden"]],
        "candidate_key_pairs": model.candidates(ctx.con, found),
        "modelling_rules": "Several fact tables are fine: relate each to the shared dimensions (Date, Product, "
                           "Customer...), never fact to fact. Measures from different facts combine when split by "
                           "a shared dimension's column.",
        "measure_rules": "aggregate: aggregation (sum, average, count, count_distinct, min, max) over a [Column] "
                         "expression of ONE table; formula: arithmetic over [Measure] names; Expressions may also use comparisons (= <> < > <= >=), IF(condition, then, else), SWITCH(value, match, result, ..., default), AND, OR and NOT; text only as something to compare with, e.g. IF([Region] = \"North\", [Amount], 0). a 'time' measure works out another measure over time: calc ytd, qtd, mtd (to date), prior_year (same period last year), prior_year_ytd, or rolling with months, e.g. {kind: time, base: Revenue, calc: prior_year}; it needs the date table. Year-on-year change is then a formula, e.g. ([Revenue] - [Revenue LY]) / [Revenue LY]. format.type: number, "
                         "integer, currency (currency: 'RM'), percent.",
    }


def md_begin(ctx: Ctx):
    model.snapshot(ctx.project)


def md_finish(ctx: Ctx, token) -> None:
    if not ctx.dry_run:
        model.revalidate(ctx.con, ctx.project)


MODEL = Stage(
    name="model",
    role="the Model agent: you design relationships, the date table and measures (a star schema)",
    actions={
        "add_relationship": Action({"from_table": "many side", "from_column": "", "to_table": "one side",
                                    "to_column": "", "reason": ""}, md_add_relationship),
        "remove_relationship": Action({"from_table": "", "from_column": "", "to_table": "", "to_column": ""},
                                      md_remove_relationship),
        "add_measure": Action({"name": "", "kind": "aggregate | formula | time", "table": "aggregate only",
                               "base": "time only: a measure name", "calc": "time only", "months": "rolling only",
                               "aggregation": "", "expression": "[Column] arithmetic, or [Measure] arithmetic for formulas",
                               "filters": "optional [{column, operator, value}]", "format": {"type": "", "decimals": 0},
                               "description": ""}, md_add_measure),
        "change_measure": Action({"name": "existing measure", "new_name": "optional", "expression": "optional",
                                  "aggregation": "optional", "table": "optional", "filters": "optional",
                                  "format": "optional"}, md_change_measure),
        "remove_measure": Action({"name": ""}, md_remove_measure),
        "set_date_table": Action({"start_year": "optional", "end_year": "optional", "reason": ""}, md_date_table),
        "remove_date_table": Action({}, md_remove_date_table),
        "hide_column": Action({"table": "", "column": "", "hidden": "true or false"}, md_hide),
    },
    context=md_context,
    begin=md_begin,
    finish=md_finish,
)


# Report (a dashboard's draft pages)


def find_page(ctx: Ctx, title) -> dict:
    for page in report.state(ctx.project)["pages"]:
        if isinstance(title, str) and page["title"].lower() == title.strip().lower():
            return page
    raise ChatError(f"There is no page called '{title}'.")


def find_visual(ctx: Ctx, title, page_title=None) -> tuple[dict, dict]:
    pages = [find_page(ctx, page_title)] if page_title else report.state(ctx.project)["pages"]
    for page in pages:
        for visual in page["visuals"]:
            if isinstance(title, str) and visual["title"].lower() == title.strip().lower():
                return page, visual
    raise ChatError(f"There is no visual called '{title}'.")


def rp_add_page(ctx: Ctx, p: dict) -> str:
    page = report.add_page(ctx.project, need(p, "title"), p.get("question", ""), "AI")
    return f"Add page '{page['title']}'"


def rp_remove_page(ctx: Ctx, p: dict) -> str:
    page = find_page(ctx, need(p, "page"))
    report.state(ctx.project)["pages"].remove(page)
    return f"Remove page '{page['title']}'"


def rp_rename_page(ctx: Ctx, p: dict) -> str:
    page = find_page(ctx, need(p, "page"))
    old = page["title"]
    page["title"] = str(need(p, "title")).strip()
    return f"Rename page '{old}' to '{page['title']}'"


def visual_spec(p: dict) -> dict:
    return {k: p.get(k) for k in ("type", "title", "measures", "group_by", "kpi", "width", "reason")}


def rp_add_visual(ctx: Ctx, p: dict) -> str:
    page = find_page(ctx, need(p, "page"))
    visual = report.add_visual(ctx.con, ctx.project, page["id"], visual_spec(p), "AI")
    return f"Add {visual['type']} '{visual['title']}' to '{page['title']}'"


def rp_remove_visual(ctx: Ctx, p: dict) -> str:
    page, visual = find_visual(ctx, need(p, "visual"), p.get("page"))
    page["visuals"].remove(visual)
    return f"Remove '{visual['title']}' from '{page['title']}'"


def rp_replace_visual(ctx: Ctx, p: dict) -> str:
    page, old = find_visual(ctx, need(p, "visual"), p.get("page"))
    spec = {**{k: old.get(k) for k in ("type", "title", "measures", "group_by", "kpi", "width")},
            **{k: v for k, v in visual_spec(p).items() if v is not None}}
    page["visuals"].remove(old)
    try:
        new = report.add_visual(ctx.con, ctx.project, page["id"], spec, "AI")
    except FAILURES:
        page["visuals"].append(old)
        raise
    return f"Replace '{old['title']}' with a {new['type']} '{new['title']}'"


VISUAL_ROWS = 24  # rows of each visual's data the agent sees; enough for 2 years of months


def _round(value):
    if isinstance(value, float):
        return round(value, 4) if abs(value) < 1 else round(value, 2)
    return value


def visual_numbers(con, project: dict, visual: dict) -> dict:
    """The numbers behind one visual, trimmed for the prompt: what the chart shows, so questions about it
    are answered from real values rather than guessed."""
    try:
        data = report.visual_data(con, project, visual)
    except (report.ReportError, model.ModelError) as exc:
        return {"error": str(exc)}
    if visual["type"] == "kpi":
        card = {k: _round(data.get(k)) for k in ("value", "previous", "delta", "delta_pct", "comparison", "period", "note")}
        card["monthly"] = [[p["label"], _round(p["value"])] for p in data.get("sparkline", [])][-VISUAL_ROWS:]
        return card
    rows = [[_round(v) for v in row] for row in data["rows"][:VISUAL_ROWS]]
    out = {"columns": data["columns"], "rows": rows, "total_rows": data["total_rows"]}
    if data["total_rows"] > VISUAL_ROWS:
        out["note"] = f"showing the first {VISUAL_ROWS} of {data['total_rows']} rows"
    return out


def rp_context(ctx: Ctx) -> dict:
    found = model.tables(ctx.con, ctx.project)
    current = model.state(ctx.project)
    names = table_names(ctx)
    return {
        "pages": [
            {"title": page["title"], "question": page["question"],
             "visuals": [{"title": v["title"], "type": v["type"], "measures": v["measures"],
                          "split_by": [f"{names.get(g['table'])}[{g['column']}]" for g in v["group_by"]],
                          "data": visual_numbers(ctx.con, ctx.project, v)}
                         for v in page["visuals"]]}
            for page in report.state(ctx.project)["pages"]
        ],
        "data_as_of": report.as_of(ctx.con, ctx.project),
        "measures": [{"name": m["name"], "definition": model.describe_measure(m, names)}
                     for m in current["measures"] if not m.get("error")],
        "columns_to_split_by": [f"{t.name}[{c.name}] ({c.type})" for t in found.values() for c in t.columns],
        "active_relationships": [f"{names.get(r['from_table'])}[{r['from_column']}] -> {names.get(r['to_table'])}[{r['to_column']}]"
                                 for r in current["relationships"] if r.get("active") and not r.get("error")],
        "visual_rules": "types: kpi (one measure, no split), bar (sorted, one split), column (ordered categories), "
                        "stacked_bar / stacked_column (one measure; group_by = [category, stacks]), "
                        "line and area (trend over Date[Year Month]), pie and donut (one measure, one split), "
                        "scatter (two measures, one split), table (1-3 splits). group_by is [{table, column}]. "
                        "At most 7 KPI cards per page. No gauges or dual axes. Style: sorted bars compare best and "
                        "pies/donuts suit 3 slices or fewer, but when the user explicitly asks for a chart type, "
                        "make it; the app attaches a tip if the style would differ. You may mention the tip once.",
    }


def rp_begin(ctx: Ctx):
    report.snapshot(ctx.project)


REPORT = Stage(
    name="report",
    role="the Report agent: you design the dashboard's pages and visuals for business users",
    actions={
        "add_page": Action({"title": "", "question": "the question the page answers"}, rp_add_page),
        "rename_page": Action({"page": "current title", "title": "new title"}, rp_rename_page),
        "remove_page": Action({"page": "title"}, rp_remove_page),
        "add_visual": Action({"page": "page title", "type": "", "title": "plain-words title", "measures": [""],
                              "group_by": [{"table": "", "column": ""}], "kpi": {"direction": "higher_is_better"},
                              "width": "half | full"}, rp_add_visual),
        "replace_visual": Action({"visual": "current title", "page": "optional page title", "type": "optional",
                                  "title": "optional", "measures": "optional", "group_by": "optional"}, rp_replace_visual),
        "remove_visual": Action({"visual": "title", "page": "optional page title"}, rp_remove_visual),
    },
    context=rp_context,
    begin=rp_begin,
    data_questions=True,
)

STAGES = {s.name: s for s in (GET_DATA, TRANSFORM, MODEL, REPORT)}
UPSTREAM = {"get_data": [], "transform": ["get_data"], "model": ["transform"], "report": ["model"]}


# Talking to the AI


def schema(stage: Stage) -> dict:
    return {
        "type": "object",
        "properties": {
            "reply": {"type": "string", "description": "Your answer to the user, in plain words."},
            **({"used_visuals": {
                "type": "array", "items": {"type": "string"},
                "description": "Titles of the visuals whose data your reply's numbers come from (empty if none)."}}
               if stage.data_questions else {}),
            **({"data_question": {
                "type": "string",
                "description": "Only when the user asks about numbers that are NOT in the visuals' data you were given: "
                               "the question, rephrased to stand on its own. The app answers it from the data model "
                               "with checked numbers. Leave empty otherwise."}} if stage.data_questions else {}),
            "actions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": list(stage.actions)},
                        "params": {"type": "object"},
                        "reason": {"type": "string"},
                    },
                    "required": ["action", "params"],
                },
            },
        },
        "required": ["reply", "actions"],
    }


def system_prompt(stage: Stage, mode: str = "full") -> str:
    timing = (
        "Changes wait for the user's approval, so describe them as proposals (\"I'll add X once you approve\"), "
        "never as done. " if mode == "review" else
        "Changes apply right after your reply unless the app refuses them, so say what you are doing. "
    )
    return timing + (
        f"You are {stage.role}, inside an AI data analytics app like Power BI. You can only change this stage, "
        "using the listed actions; earlier stages are shown read-only for context. If the user asks a question, "
        "answer it from the state you are given (never invent numbers) and return no actions. If they ask for a "
        "change, return the actions and say briefly what you will do. If the request belongs to another stage, "
        "say which stage and return no actions. Every action is checked by the app before it runs, so keep them "
        "exact: use names exactly as they appear in the state. Be concise and friendly; no jargon. "
        "For three or more items use a short Markdown bullet list (\"- item\"), with **bold** for names; no headings or tables."
    ) + (
        " Each visual comes with its data (the numbers it shows). Answer questions about what's on the dashboard "
        "from those numbers: trends, peaks, dips, comparisons, how two measures move together. Quote numbers "
        "exactly as given, rounded sensibly, and name the visual you mean. When the answer needs numbers that "
        "aren't in any visual (another split, a filter, a period, a measure not shown), put the question in "
        "data_question and keep your reply to one short line; the app will answer it from the data model. "
        "Never say you can't see the data."
        if stage.data_questions else ""
    )


def build_prompt(stage: Stage, state: dict, upstream: dict, history: list[dict], message: str) -> str:
    actions = {name: {"params": a.params, **({"needs_approval": True} if a.destructive else {})}
               for name, a in stage.actions.items()}
    turns = [{"role": m["role"], "text": m["content"]} for m in history[-HISTORY_TURNS:]]
    return (
        f"Actions you can take:\n{json.dumps(actions)}\n\n"
        f"Current state of this stage:\n{json.dumps(state, default=str)}\n\n"
        f"Earlier stages (read-only):\n{json.dumps(upstream, default=str)}\n\n"
        f"Conversation so far:\n{json.dumps(turns)}\n\n"
        f"User: {message}"
    )


def ask(client, model_name: str, capabilities: dict, stage: Stage, state: dict, upstream: dict,
        history: list[dict], message: str, mode: str = "full") -> dict:
    raw = llm.ask_json(client, model_name, capabilities, system_prompt(stage, mode),
                       build_prompt(stage, state, upstream, history, message), "respond", schema(stage))
    actions = [a for a in raw.get("actions") or [] if isinstance(a, dict) and a.get("action") in stage.actions]
    question = str(raw.get("data_question") or "").strip() if stage.data_questions else ""
    used = [str(v) for v in raw.get("used_visuals") or [] if isinstance(v, str)] if stage.data_questions else []
    return {"reply": str(raw.get("reply") or "").strip(), "actions": actions, "data_question": question,
            "used_visuals": used}


def explain_reply(reply: str, used: list[str], state: dict) -> dict | None:
    """'How this was calculated' for an answer read off the dashboard: each visual it used, with its
    measures (and their definitions) and how it is split. Falls back to visuals the reply names."""
    visuals = [(page["title"], v) for page in state.get("pages", []) for v in page["visuals"]]
    wanted = {u.strip().lower() for u in used}
    chosen = [(p, v) for p, v in visuals if v["title"].lower() in wanted]
    if not chosen:
        text = (reply or "").lower()
        chosen = [(p, v) for p, v in visuals if v["title"].lower() in text]
    if not chosen:
        return None
    definitions = {m["name"]: m["definition"] for m in state.get("measures", [])}
    measures, seen = [], set()
    for _, v in chosen:
        for name in v["measures"]:
            if name not in seen:
                seen.add(name)
                measures.append({"name": name, "definition": definitions.get(name, "")})
    return {
        "visuals": [{"title": v["title"], "page": p, "type": v["type"], "split_by": v["split_by"]} for p, v in chosen],
        "measures": measures,
        "as_of": state.get("data_as_of"),
    }


def check_reply(text: str, state: dict) -> dict:
    """Numbers in a Report chat reply must come from the visuals' data, as insights and Ask answers do."""
    rows = []
    for page in state.get("pages", []):
        for visual in page["visuals"]:
            data = visual.get("data") or {}
            if "rows" in data:
                rows += data["rows"]
            elif "value" in data:
                pct = data.get("delta_pct")
                rows.append([data.get("value"), data.get("previous"), data.get("delta"),
                             pct * 100 if isinstance(pct, (int, float)) else None])
                rows += data.get("monthly", [])
    if not rows or not re.search(r"\d", text or ""):
        return {"verified": True, "unmatched": []}
    return report.check_insight(text, {"rows": rows, "check": None})


# Running actions


def run_actions(stage: Stage, ctx: Ctx, actions: list[dict]) -> list[dict]:
    """Run actions in order on ctx; each result is {action, params, reason, label, ok, error}."""
    token = stage.begin(ctx)
    results = []
    for item in actions:
        action = stage.actions[item["action"]]
        params = item.get("params") if isinstance(item.get("params"), dict) else {}
        entry = {"action": item["action"], "params": params, "reason": str(item.get("reason") or ""),
                 "label": item["action"].replace("_", " "), "ok": False, "error": None}
        try:
            entry["label"] = action.run(ctx, copy.deepcopy(params))
            entry["ok"] = True
        except FAILURES as exc:
            entry["error"] = str(exc).strip("'\"")
        results.append(entry)
    stage.finish(ctx, token)
    return results


def check_actions(stage: Stage, ctx: Ctx, actions: list[dict]) -> list[dict]:
    """Run the actions on a copy without writing anything, to show what would happen."""
    dry = Ctx(copy.deepcopy(ctx.project), ctx.con, True, ctx.store, ctx.fetch_url)
    return run_actions(stage, dry, actions)


def needs_approval(stage: Stage, actions: list[dict], mode: str) -> bool:
    return mode == "review" or any(stage.actions[a["action"]].destructive for a in actions)


def message(role: str, content: str, change: dict | None = None) -> dict:
    entry = {"id": new_id(), "role": role, "content": content, "at": now()}
    if change:
        entry["change"] = change
    return entry


def append(messages: list[dict], *entries: dict) -> None:
    messages.extend(entries)
    del messages[:-MAX_MESSAGES]


def find_change(messages: list[dict], change_id: str) -> dict:
    for entry in messages:
        change = entry.get("change")
        if change and change["id"] == change_id:
            return change
    raise ChatError("That change was not found.")
