"""The Transform agent's cleaning pass: proposes Applied Steps for one table.

The AI sees the table's current profile (after any existing steps), the distinct values
of low-cardinality text columns so it can unify spellings, 5 sample rows, the steps so
far and the other tables' columns. It answers with steps from the operation catalogue;
the engine checks each one and keeps only those that pass.
"""

import json
import re

from . import llm, transform
from .projects import quote
from .transform_ops import OPERATIONS, catalogue, describe

SAMPLE_ROWS = 5
MAX_DISTINCT_LISTED = 30
MAX_VALUE_LENGTH = 80

SYSTEM = (
    "You are the Transform agent in an AI data analytics app, like Power Query in Power BI. "
    "You clean one table at a time by adding Applied Steps from a fixed catalogue of operations. "
    "Goals: correct types for numbers, dates and flags; placeholders such as N/A turned into blanks; "
    "consistent spelling of categories; no exact duplicate rows. Rules: use only catalogue operations; "
    "never invent column names; do not drop rows or columns unless they are clearly junk; keep fact and "
    "dimension tables separate (the Model stage relates them), so only merge when told to; order steps so "
    "each one sees the columns it needs (e.g. blanks_to_null and trim before change_type or replace_values); "
    "add nothing if the table is already clean. Give each step a short reason a business user understands. "
    "If the table repeats descriptive attributes of something with its own key (e.g. product code, name and "
    "category on every sales row), you may split those columns off into a lookup table with new_tables; the key "
    "stays in this table, and you may add a remove_columns step for the moved attributes. Do not split off a "
    "single column with no attributes, and never split dates (the Model stage adds a date table). "
    "If repeated_keys lists a code or ID column of a lookup table (products, customers, branches) whose few "
    "repeats stop it being one row per key, add remove_duplicates with that column, and name the repeated values "
    "in the reason: the Model needs one row per key to relate the table. Don't do this for fact tables, where "
    "repeats are normal."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "One sentence on what the steps fix."},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "op": {"type": "string", "enum": list(OPERATIONS)},
                    "params": {"type": "object"},
                    "reason": {"type": "string"},
                },
                "required": ["op", "params", "reason"],
            },
        },
        "new_tables": {
            "type": "array",
            "description": "Optional lookup tables to split off this table, made after your steps run.",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "key": {"type": "string", "description": "The column identifying each row, e.g. product code."},
                    "columns": {"type": "array", "items": {"type": "string"}, "description": "Key plus its attributes."},
                    "reason": {"type": "string"},
                },
                "required": ["name", "key", "columns", "reason"],
            },
        },
    },
    "required": ["summary", "steps"],
}


def shorten(value):
    if isinstance(value, str) and len(value) > MAX_VALUE_LENGTH:
        return value[:MAX_VALUE_LENGTH] + "…"
    return value


KEY_NAME = re.compile(r"(^|[\W_])(id|code|key|no|number|sku)($|[\W_])|code$", re.IGNORECASE)
CAMEL_ID = re.compile(r"[a-z]I[Dd]$")  # ProductID, customerId; not "paid" or "valid"
MAX_KEY_REPEATS = 0.05  # up to 5% of rows repeating: a lookup table with a few stray copies, not a fact table


def repeated_keys(con, table: str, profile: dict) -> list[dict]:
    """Code or ID columns that are nearly, but not quite, one row per value."""
    out = []
    for c in profile["columns"]:
        extra = c["filled"] - c["distinct"]
        if not (KEY_NAME.search(c["name"]) or CAMEL_ID.search(c["name"])) or extra <= 0 or extra > max(1, MAX_KEY_REPEATS * profile["rows"]):
            continue
        values = [str(v) for (v,) in con.execute(
            f"SELECT {quote(c['name'])} FROM {table} WHERE {quote(c['name'])} IS NOT NULL "
            f"GROUP BY 1 HAVING count(*) > 1 ORDER BY 1 LIMIT 5").fetchall()]
        out.append({"column": c["name"], "repeated_values": values, "extra_rows": extra})
    return out


def build_prompt(con, project: dict, query: dict) -> str:
    profile = query["output"]["profile"]
    table = f"{transform.OUTPUT_SCHEMA}.{quote(query['name'])}"
    types = {c["name"]: c["type"] for c in query["output"]["columns"]}
    columns = []
    for c in profile["columns"]:
        entry = {
            "name": c["name"],
            "current_type": types.get(c["name"], "text"),
            **{k: c[k] for k in ("detected_type", "type_confidence", "filled", "blanks", "placeholders", "distinct",
                                 "unique", "min", "max", "issues")},
        }
        if types.get(c["name"]) == "text" and c["distinct"] <= MAX_DISTINCT_LISTED:
            entry["all_values"] = {
                str(v): n
                for v, n in con.execute(
                    f"SELECT {quote(c['name'])}, count(*) FROM {table} GROUP BY 1 ORDER BY 2 DESC"
                ).fetchall()
            }
        else:
            entry["top_values"] = c["top_values"]
        columns.append(entry)

    result = con.execute(f"SELECT * FROM {table} LIMIT {SAMPLE_ROWS}")
    names = [d[0] for d in result.description]
    samples = [{k: shorten(None if v is None else str(v)) for k, v in zip(names, row)} for row in result.fetchall()]

    source = next((s for s in project["sources"] if s["id"] == query["source_id"]), {})  # lookup tables have none
    query_names = transform.query_names(project)
    others = [
        {"table": q["name"], "columns": [c["name"] for c in (q["output"] or {}).get("columns", [])]}
        for q in transform.state(project)["queries"]
        if q["id"] != query["id"] and q["output"]
    ]
    context = {
        "table": query["name"],
        "rows": profile["rows"],
        "exact_duplicate_rows": profile["duplicate_rows"],
        "get_data_notes": source.get("ai"),
        "lookup_split_from": transform.query_names(project).get(query.get("base_query")),
        "steps_so_far": [describe(s["op"], s["params"], query_names) for s in query["steps"]],
        "columns": columns,
        "repeated_keys": repeated_keys(con, table, profile),
        "sample_rows": samples,
        "other_tables": others,
    }
    return (
        f"Operation catalogue:\n{json.dumps(catalogue())}\n\n"
        f"Table to clean (state after the steps so far):\n{json.dumps(context, default=str)}\n\n"
        "Propose the additional steps this table needs."
    )


def propose(client, model: str, capabilities: dict, con, project: dict, query: dict) -> dict:
    prompt = build_prompt(con, project, query)
    raw = llm.ask_json(client, model, capabilities, SYSTEM, prompt, "propose_steps", SCHEMA)
    steps = [s for s in raw.get("steps") or [] if isinstance(s, dict)]
    tables = [t for t in raw.get("new_tables") or [] if isinstance(t, dict)]
    return {"summary": str(raw.get("summary") or ""), "steps": steps, "new_tables": tables}
