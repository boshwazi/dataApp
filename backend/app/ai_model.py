"""The Model agent: designs relationships, the date table and measures in one pass.

It sees the clean tables' profiles, the Get Data notes, candidate key pairs already
checked by the engine (match rate, duplicates) and the current model. Everything it
proposes goes through the same checks as a manual change; refused items come back
with the reason.
"""

import json

from . import llm, model, transform
from .model import AGGREGATIONS, FORMATS

SYSTEM = (
    "You are the Model agent in an AI data analytics app, like Power BI's Model view. Design a star schema "
    "over the cleaned tables: many-to-one relationships from fact tables to dimension tables (from_table is the "
    "many side), a date table linked to the main date column of each fact table, and the measures a business "
    "user would put on a dashboard. Use only the candidate key pairs whose checks passed; if a pair has a "
    "problem, leave it out and mention it in the summary. Measures: an 'aggregate' measure sums, averages, counts "
    "or count_distincts an expression over ONE table's columns, written with [Column] references, e.g. "
    "[Qty] * [Unit Price]; a 'formula' measure combines other measures, e.g. [Revenue] / [Orders]. "
    "Use currency format with the right symbol for money (RM for Malaysian ringgit), percent for ratios, "
    "integer for counts. Prefer 5 to 10 useful measures over many trivial ones. Give short reasons. "
    "There may be several fact tables (e.g. sales and targets, or orders and returns): relate each one to the "
    "shared dimensions (Date, Product, Customer, Region), never fact to fact, so their measures can sit side by "
    "side when split by a shared dimension. Tables linked from other datasets are regular tables here."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "date_table": {
            "type": "object",
            "properties": {
                "create": {"type": "boolean"},
                "link": {
                    "type": "array",
                    "items": {"type": "object", "properties": {"table": {"type": "string"}, "column": {"type": "string"}}},
                    "description": "Date columns to relate to the date table.",
                },
                "reason": {"type": "string"},
            },
        },
        "relationships": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "from_table": {"type": "string"},
                    "from_column": {"type": "string"},
                    "to_table": {"type": "string"},
                    "to_column": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["from_table", "from_column", "to_table", "to_column"],
            },
        },
        "measures": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "kind": {"type": "string", "enum": ["aggregate", "formula"]},
                    "table": {"type": "string", "description": "aggregate only"},
                    "aggregation": {"type": "string", "enum": list(AGGREGATIONS)},
                    "expression": {"type": "string", "description": "Omit for a count of rows."},
                    "filters": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "Optional, aggregate only: {column, operator, value | values}.",
                    },
                    "format": {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string", "enum": list(FORMATS)},
                            "decimals": {"type": "integer"},
                            "currency": {"type": "string"},
                        },
                    },
                    "description": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["name", "kind"],
            },
        },
        "hidden_columns": {
            "type": "array",
            "items": {"type": "object", "properties": {"table": {"type": "string"}, "column": {"type": "string"}}},
            "description": "Key columns on the many side that users should not group by.",
        },
    },
    "required": ["summary", "relationships", "measures"],
}


def build_prompt(con, project: dict) -> str:
    found = model.tables(con, project)
    queries = {q["id"]: q for q in transform.state(project)["queries"]}
    sources = {s["id"]: s for s in project["sources"]}
    names_by_id = transform.query_names(project)
    described = []
    for table in found.values():
        if table.id == model.DATE_ID:
            continue
        query = queries[table.id]
        notes = (sources.get(query["source_id"]) or {}).get("ai") or {}
        profile = {c["name"]: c for c in query["output"]["profile"]["columns"]}
        described.append({
            "table": table.name,
            "rows": query["output"]["profile"]["rows"],
            "notes": {k: notes.get(k) for k in ("summary", "grain", "table_role")} if notes else None,
            "lookup_split_from": names_by_id.get(query.get("base_query")),
            "columns": [
                {
                    "name": c.name,
                    "type": c.type,
                    **{k: profile[c.name][k] for k in ("distinct", "unique", "blanks", "min", "max") if c.name in profile},
                    "top_values": [t["value"] for t in profile.get(c.name, {}).get("top_values", [])[:3]],
                }
                for c in table.columns
            ],
        })
    current = model.state(project)
    names = {t.id: t.name for t in found.values()}
    existing = {
        "date_table": bool(current["date_table"]),
        "relationships": [
            f"{names.get(r['from_table'])}[{r['from_column']}] -> {names.get(r['to_table'])}[{r['to_column']}]"
            for r in current["relationships"]
        ],
        "measures": [m["name"] for m in current["measures"]],
    }
    return (
        f"Tables:\n{json.dumps(described, default=str)}\n\n"
        f"Candidate key pairs (checked):\n{json.dumps(model.candidates(con, found), default=str)}\n\n"
        f"Current model (add only what is missing):\n{json.dumps(existing)}\n\n"
        f"The date table, if created, is called '{model.DATE_NAME}' with columns Date, Year, Quarter, Month Number, "
        "Month, Year Month, Day of Week Number, Day of Week.\n\nDesign the model."
    )


def propose(client, model_name: str, capabilities: dict, con, project: dict) -> dict:
    raw = llm.ask_json(client, model_name, capabilities, SYSTEM, build_prompt(con, project), "design_model", SCHEMA)
    return raw if isinstance(raw, dict) else {}


def apply(con, project: dict, proposal: dict) -> dict:
    """Add what passes the checks, as one undoable change. Returns what was added and refused."""
    model.snapshot(project)
    current = model.state(project)
    added, refused = [], []

    def attempt(kind: str, label: str, action):
        try:
            item = action()
            added.append({"kind": kind, "label": label})
            return item
        except (model.ModelError, KeyError, TypeError) as exc:
            if "already exists" not in str(exc):  # re-proposing what is there is not worth reporting
                refused.append({"kind": kind, "label": label, "error": str(exc)})

    date_plan = proposal.get("date_table") or {}
    if date_plan.get("create") and not current["date_table"]:
        attempt("date table", "Date table", lambda: model.set_date_table(con, project, status="AI", reason=str(date_plan.get("reason") or "")))
    links = [{"from_table": l.get("table"), "from_column": l.get("column"), "to_table": model.DATE_ID, "to_column": "Date",
              "reason": "Links dates to the date table."} for l in date_plan.get("link") or [] if isinstance(l, dict)]

    # Fact-to-dimension relationships go first, then date links from fact tables, then from dimensions:
    # only one active path may join two tables, and a dimension's own date (e.g. a join date) is the one
    # that should end up inactive.
    def relate(rel):
        target = "Date" if rel.get("to_table") == model.DATE_ID else rel.get("to_table")
        label = f"{rel.get('from_table')}[{rel.get('from_column')}] → {target}[{rel.get('to_column')}]"
        attempt("relationship", label, lambda: model.add_relationship(con, project, rel, "AI"))

    for rel in [r for r in proposal.get("relationships") or [] if isinstance(r, dict)]:
        relate(rel)
    found = model.tables(con, project)
    dimensions = {r["to_table"] for r in current["relationships"]}

    def is_dimension(link):
        try:
            return model.find_table(found, link["from_table"]).id in dimensions
        except model.ModelError:
            return False

    for rel in sorted(links, key=is_dimension):
        relate(rel)

    # Formulas can refer to measures proposed after them, so retry the ones that failed once.
    pending = [m for m in proposal.get("measures") or [] if isinstance(m, dict)]
    for _ in range(2):
        retry = []
        for measure in pending:
            try:
                model.add_measure(con, project, measure, "AI")
                added.append({"kind": "measure", "label": str(measure.get("name"))})
            except (model.ModelError, KeyError, TypeError) as exc:
                retry.append((measure, str(exc)))
        pending = [m for m, _ in retry]
        if not pending:
            break
    refused += [{"kind": "measure", "label": str(m.get("name")), "error": e} for m, e in retry] if pending else []

    for item in proposal.get("hidden_columns") or []:
        if isinstance(item, dict):
            attempt("hidden column", f"{item.get('table')}[{item.get('column')}]",
                    lambda item=item: model.set_hidden(con, project, item.get("table"), item.get("column"), True))

    if not added:
        current["history"].pop()
    return {"summary": str(proposal.get("summary") or ""), "added": added, "refused": refused}
