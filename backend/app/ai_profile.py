"""The Get Data agent's profiling pass: what a table is and what each column means.

The AI sees the column profiles and a few sample rows (values cut to 80 characters),
never the full table.
"""

import json

from . import llm

SAMPLE_ROWS = 5
MAX_VALUE_LENGTH = 80
ROLES = ("id", "dimension", "measure", "date", "attribute", "text")
TYPES = ("integer", "decimal", "date", "boolean", "text")

SYSTEM = (
    "You are the Get Data agent in an AI data analytics app, like the Get Data step of Power BI. "
    "You describe newly loaded tables so later stages (cleaning, modeling, reporting) can use them. "
    "You only see column profiles and a few sample rows. Be concrete and brief."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "One or two sentences on what the table contains."},
        "grain": {"type": "string", "description": "What one row represents, e.g. 'one order line'."},
        "table_role": {"type": "string", "enum": ["fact", "dimension", "unknown"]},
        "columns": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Exact column name from the profile."},
                    "description": {"type": "string", "description": "What the column means, under 15 words."},
                    "role": {"type": "string", "enum": list(ROLES)},
                    "suggested_type": {"type": "string", "enum": list(TYPES)},
                    "issues": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Data quality problems worth fixing in Transform that the profile's own issues "
                            "list does not already state. Empty when there are none."
                        ),
                    },
                },
                "required": ["name", "description", "role", "suggested_type"],
            },
        },
    },
    "required": ["summary", "grain", "table_role", "columns"],
}


def build_prompt(table: str, profile: dict, samples: list[dict]) -> str:
    columns = [
        {k: c[k] for k in ("name", "detected_type", "type_confidence", "filled", "blanks", "placeholders",
                           "distinct", "unique", "min", "max", "top_values", "issues")}
        for c in profile["columns"]
    ]
    rows = [{k: shorten(v) for k, v in row.items()} for row in samples]
    return (
        f"Table `{table}` has {profile['rows']} rows and {profile['duplicate_rows']} exact duplicate rows.\n\n"
        f"Column profiles:\n{json.dumps(columns, default=str)}\n\n"
        f"Sample rows:\n{json.dumps(rows, default=str)}\n\n"
        "Describe the table and every column. The profile issues are already shown to the user, "
        "so only add issues they miss (for example values that look wrong for the column's meaning)."
    )


def shorten(value):
    if isinstance(value, str) and len(value) > MAX_VALUE_LENGTH:
        return value[:MAX_VALUE_LENGTH] + "…"
    return value


def describe(client, model: str, capabilities: dict, table: str, profile: dict, samples: list[dict]) -> dict:
    raw = llm.ask_json(client, model, capabilities, SYSTEM, build_prompt(table, profile, samples), "describe_table", SCHEMA)
    return clean(raw, [c["name"] for c in profile["columns"]])


def is_issue(text) -> bool:
    # Models sometimes fill the list with "None" or "No issues" instead of leaving it empty.
    words = str(text or "").strip().lower()
    return bool(words) and not words.startswith(("none", "no issue", "n/a"))


def clean(raw: dict, column_names: list[str]) -> dict:
    """Keep only well-formed answers about columns that exist."""
    by_name = {}
    for item in raw.get("columns") or []:
        if not isinstance(item, dict) or item.get("name") not in column_names:
            continue
        by_name[item["name"]] = {
            "description": str(item.get("description") or ""),
            "role": item.get("role") if item.get("role") in ROLES else "attribute",
            "suggested_type": item.get("suggested_type") if item.get("suggested_type") in TYPES else "text",
            "issues": [str(i) for i in item.get("issues") or [] if is_issue(i)][:5],
        }
    table_role = raw.get("table_role")
    return {
        "summary": str(raw.get("summary") or ""),
        "grain": str(raw.get("grain") or ""),
        "table_role": table_role if table_role in ("fact", "dimension") else "unknown",
        "columns": by_name,
        "status": "AI",
    }
