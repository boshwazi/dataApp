"""The Transform operation catalogue: the only changes the AI (or a user) can make to data.

Each operation checks its parameters against the columns available at that point in
the Applied Steps and compiles to a SELECT over the previous step. Values become escaped
SQL literals and names become quoted identifiers, so no one ever writes SQL directly.
"""

import math
import re
from dataclasses import dataclass
from typing import Callable

from . import model_expr
from .profiling import BOOLEANS, DAY_FIRST, MONTH_FIRST, NUMBER_NOISE, PLACEHOLDERS, TRUE_WORDS, WORDY_DATES, quote, with_times

LOGICAL_TYPES = ("text", "integer", "decimal", "date", "boolean")
SQL_TYPES = {"text": "VARCHAR", "integer": "BIGINT", "decimal": "DOUBLE", "date": "DATE", "boolean": "BOOLEAN"}
DATE_ORDERS = {
    # Tried after ISO dates (2024-01-31), which always parse first. The chosen order decides
    # ambiguous dates like 05/01/2024; a date only valid the other way (6/28/2016) still parses.
    "dmy": with_times(DAY_FIRST + WORDY_DATES + MONTH_FIRST),
    "mdy": with_times(MONTH_FIRST + WORDY_DATES + DAY_FIRST),
}
FILTER_OPERATORS = (
    "equals", "not_equals", "in", "not_in", "is_blank", "is_not_blank",
    "greater_than", "less_than", "at_least", "at_most",
)
CASES = ("upper", "lower", "title")


class StepError(ValueError):
    pass


@dataclass
class Column:
    name: str
    type: str  # one of LOGICAL_TYPES


@dataclass
class Context:
    """What an operation can see: the previous step's columns and the other queries."""

    columns: list[Column]
    source: str  # SQL name of the previous step
    resolve_query: Callable[[str], tuple[str, list[Column], str]]  # id or name -> (sql, columns, id)


def logical_type(duckdb_type: str) -> str:
    t = duckdb_type.upper()
    if t in ("BIGINT", "INTEGER", "SMALLINT", "TINYINT", "HUGEINT", "UBIGINT", "UINTEGER"):
        return "integer"
    if t in ("DOUBLE", "FLOAT", "REAL") or t.startswith("DECIMAL"):
        return "decimal"
    if t == "DATE" or t.startswith("TIMESTAMP"):
        return "date"
    if t == "BOOLEAN":
        return "boolean"
    return "text"


def literal(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise StepError("Numbers must be finite.")
        return repr(value)
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    raise StepError(f"Unsupported value: {value!r}")


def typed_literal(value, column: Column) -> str:
    if value is None:
        return "NULL"
    return f"CAST({literal(value)} AS {SQL_TYPES[column.type]})"


def text_list(values) -> str:
    return "[" + ", ".join(literal(v) for v in values) + "]"


# Parameter helpers


def find(columns: list[Column], name) -> Column:
    if not isinstance(name, str):
        raise StepError("A column name is required.")
    for column in columns:
        if column.name == name:
            return column
    for column in columns:  # DuckDB names are case-insensitive
        if column.name.lower() == name.lower():
            return column
    raise StepError(f"Column '{name}' does not exist at this step.")


def column_param(params: dict, ctx: Context, key: str = "column", types: tuple | None = None) -> Column:
    column = find(ctx.columns, params.get(key))
    if types and column.type not in types:
        raise StepError(f"Column '{column.name}' is {column.type}; this step needs {' or '.join(types)}.")
    return column


def columns_param(params: dict, ctx: Context, key: str = "columns", types: tuple | None = None) -> list[Column]:
    names = params.get(key)
    if isinstance(names, str):
        names = [names]
    if not isinstance(names, list) or not names:
        raise StepError(f"'{key}' must list at least one column.")
    columns = [find(ctx.columns, n) for n in names]
    if types:
        for column in columns:
            if column.type not in types:
                raise StepError(f"Column '{column.name}' is {column.type}; this step needs {' or '.join(types)}.")
    return list({c.name: c for c in columns}.values())


def choice(params: dict, key: str, options, default=None) -> str:
    value = params.get(key, default)
    if value not in options:
        raise StepError(f"'{key}' must be one of: {', '.join(options)}.")
    return value


def select(ctx: Context, replace: dict[str, str] | None = None, drop=(), extra: list[tuple[str, str]] = ()) -> str:
    """SELECT every column, with some expressions replaced, some dropped and some added."""
    replace = replace or {}
    parts = [
        f"{replace.get(c.name, quote(c.name))} AS {quote(c.name)}" for c in ctx.columns if c.name not in drop
    ]
    parts += [f"{expr} AS {quote(name)}" for name, expr in extra]
    return f"SELECT {', '.join(parts)} FROM {ctx.source}"


def as_text(column: Column) -> str:
    return quote(column.name) if column.type == "text" else f"CAST({quote(column.name)} AS VARCHAR)"


def number_text(column: Column) -> str:
    return f"regexp_replace(trim({as_text(column)}), {literal(NUMBER_NOISE)}, '', 'gi')"


# Operations: each returns the SQL for the new step.


def change_type(params, ctx):
    column = column_param(params, ctx)
    to = choice(params, "to", LOGICAL_TYPES)
    v = f"trim({as_text(column)})"
    if to == "text":
        expr = as_text(column)
    elif to == "decimal":
        expr = f"TRY_CAST({number_text(column)} AS DOUBLE)"
    elif to == "integer":
        # Whole numbers only: DuckDB would otherwise round 2.5 to 3.
        number = f"TRY_CAST({number_text(column)} AS DOUBLE)"
        expr = f"CASE WHEN {number} = floor({number}) THEN CAST({number} AS BIGINT) END"
    elif to == "date":
        order = choice(params, "date_order", tuple(DATE_ORDERS), "dmy")
        expr = f"CAST(coalesce(TRY_CAST({v} AS TIMESTAMP), try_strptime({v}, {text_list(DATE_ORDERS[order])})) AS DATE)"
    else:
        expr = (
            f"CASE WHEN lower({v}) IN ({', '.join(literal(w) for w in TRUE_WORDS)}, '1') THEN TRUE "
            f"WHEN lower({v}) IN ({', '.join(literal(w) for w in BOOLEANS if w not in TRUE_WORDS)}, '0') THEN FALSE END"
        )
    return select(ctx, {column.name: expr})


def trim(params, ctx):
    columns = columns_param(params, ctx, types=("text",))
    return select(ctx, {c.name: f"regexp_replace(trim({quote(c.name)}), '\\s+', ' ', 'g')" for c in columns})


def change_case(params, ctx):
    columns = columns_param(params, ctx, types=("text",))
    case = choice(params, "case", CASES)
    def expr(c):
        if case == "upper":
            return f"upper({quote(c.name)})"
        if case == "lower":
            return f"lower({quote(c.name)})"
        return (
            f"array_to_string(list_transform(string_split(lower({quote(c.name)}), ' '), "
            f"w -> upper(w[1]) || w[2:]), ' ')"
        )
    return select(ctx, {c.name: expr(c) for c in columns})


def replace_values(params, ctx):
    column = column_param(params, ctx, types=("text",))
    pairs = params.get("replacements")
    if not isinstance(pairs, list) or not pairs:
        raise StepError("'replacements' must list at least one {from, to} pair.")
    ignore_case = bool(params.get("ignore_case", True))
    c = quote(column.name)
    left = f"lower(trim({c}))" if ignore_case else c
    whens = []
    for pair in pairs:
        if not isinstance(pair, dict) or not isinstance(pair.get("from"), str):
            raise StepError("Each replacement needs a text 'from' value.")
        to = pair.get("to")
        if to is not None and not isinstance(to, str):
            raise StepError("Each replacement's 'to' must be text or null.")
        match = pair["from"].strip().lower() if ignore_case else pair["from"]
        whens.append(f"WHEN {left} = {literal(match)} THEN {literal(to)}")
    return select(ctx, {column.name: f"CASE {' '.join(whens)} ELSE {c} END"})


def blanks_to_null(params, ctx):
    columns = columns_param(params, ctx, types=("text",))
    values = params.get("values") or list(PLACEHOLDERS)
    if not all(isinstance(v, str) for v in values):
        raise StepError("'values' must be text.")
    words = ", ".join(literal(v.strip().lower()) for v in values)
    return select(ctx, {
        c.name: f"CASE WHEN trim({quote(c.name)}) = '' OR lower(trim({quote(c.name)})) IN ({words}) THEN NULL ELSE {quote(c.name)} END"
        for c in columns
    })


def fill_blanks(params, ctx):
    column = column_param(params, ctx)
    if "value" not in params or params["value"] is None:
        raise StepError("'value' is required.")
    return select(ctx, {column.name: f"coalesce({quote(column.name)}, {typed_literal(params['value'], column)})"})


# Grouping, joining and stacking hand rows back in whatever order the database's threads finish, so
# these steps number their input rows and put them back in that order: the preview, and every step
# after, then see the rows in source order on every load.
POS, PART = "__datawazi_row", "__datawazi_part"


def numbered(source: str) -> str:
    return f"(SELECT *, row_number() OVER () AS {POS} FROM {source})"


def plain_columns(ctx, alias: str = "") -> str:
    return ", ".join(f"{alias}{quote(c.name)}" for c in ctx.columns)


def remove_duplicates(params, ctx):
    if not params.get("columns"):
        # Exact copies: the first one stays.
        partition, order = plain_columns(ctx), POS
    else:
        # By key columns: which row stays is decided by its values, as before, so results don't change.
        partition = ", ".join(quote(c.name) for c in columns_param(params, ctx))
        order = ", ".join(f"{quote(c.name)} NULLS LAST" for c in ctx.columns)
    return (f"SELECT {plain_columns(ctx)} FROM {numbered(ctx.source)} "
            f"QUALIFY row_number() OVER (PARTITION BY {partition} ORDER BY {order}) = 1 ORDER BY {POS}")


def add_column(params, ctx):
    """A calculated column: arithmetic and conditions over this table's columns, worked out row by row."""
    name = params.get("name")
    if not isinstance(name, str) or not name.strip() or "[" in name or "]" in name:
        raise StepError("The new column needs a name without square brackets.")
    name = name.strip()
    params["name"] = name
    if any(c.name.lower() == name.lower() for c in ctx.columns):
        raise StepError(f"There is already a column called '{name}'.")
    try:
        tree = model_expr.parse(params.get("expression") or "", text_results=True)
    except model_expr.ExprError as exc:
        raise StepError(str(exc)) from exc
    for ref in model_expr.refs(tree):
        find(ctx.columns, ref)
    for ref in model_expr.arithmetic_refs(tree):
        column = find(ctx.columns, ref)
        if column.type not in ("integer", "decimal"):
            raise StepError(f"'{column.name}' is {column.type}; change it to a number before calculating with it.")
    expr = model_expr.to_sql(tree, lambda ref: quote(find(ctx.columns, ref).name))
    return select(ctx, extra=[(name, expr)])


def remove_columns(params, ctx):
    columns = columns_param(params, ctx)
    if len(columns) >= len(ctx.columns):
        raise StepError("At least one column must remain.")
    return select(ctx, drop={c.name for c in columns})


def rename_column(params, ctx):
    column = column_param(params, ctx)
    new = params.get("to")
    if not isinstance(new, str) or not new.strip():
        raise StepError("'to' must be the new column name.")
    new = new.strip()
    if any(c.name.lower() == new.lower() and c.name != column.name for c in ctx.columns):
        raise StepError(f"A column named '{new}' already exists.")
    parts = [f"{quote(c.name)} AS {quote(new if c.name == column.name else c.name)}" for c in ctx.columns]
    return f"SELECT {', '.join(parts)} FROM {ctx.source}"


def filter_rows(params, ctx):
    column = column_param(params, ctx)
    operator = choice(params, "operator", FILTER_OPERATORS)
    c = quote(column.name)
    if operator == "is_blank":
        condition = f"{c} IS NULL" + (f" OR trim({c}) = ''" if column.type == "text" else "")
    elif operator == "is_not_blank":
        condition = f"{c} IS NOT NULL" + (f" AND trim({c}) <> ''" if column.type == "text" else "")
    elif operator in ("in", "not_in"):
        values = params.get("values")
        if not isinstance(values, list) or not values:
            raise StepError("'values' must list at least one value.")
        listed = ", ".join(typed_literal(v, column) for v in values)
        condition = f"{c} IN ({listed})" if operator == "in" else f"{c} IS NULL OR {c} NOT IN ({listed})"
    else:
        if params.get("value") is None:
            raise StepError("'value' is required.")
        value = typed_literal(params["value"], column)
        symbol = {"equals": "=", "greater_than": ">", "less_than": "<", "at_least": ">=", "at_most": "<="}.get(operator)
        condition = f"{c} IS DISTINCT FROM {value}" if operator == "not_equals" else f"{c} {symbol} {value}"
    return f"SELECT * FROM {ctx.source} WHERE {condition}"


def merge(params, ctx):
    right_sql, right_columns, right_id = ctx.resolve_query(params.get("query"))
    params["query"] = right_id  # stored by id so renaming a table doesn't break the step
    left_keys = columns_param(params, ctx, "left_on")
    right_ctx = Context(right_columns, "", ctx.resolve_query)
    right_keys = columns_param(params, right_ctx, "right_on")
    if len(left_keys) != len(right_keys):
        raise StepError("'left_on' and 'right_on' must list the same number of columns.")
    how = choice(params, "how", ("left", "inner"), "left")
    wanted = columns_param(params, right_ctx, "columns")
    taken = {c.name.lower() for c in ctx.columns}
    added = []
    for column in wanted:
        name, n = column.name, 2
        while name.lower() in taken:
            name = f"{column.name}_{n}"
            n += 1
        taken.add(name.lower())
        added.append((name, f"r.{quote(column.name)}"))

    def key(alias, column, other):
        ref = f"{alias}.{quote(column.name)}"
        return ref if column.type == other.type else f"trim(CAST({ref} AS VARCHAR))"

    on = " AND ".join(f"{key('l', lk, rk)} = {key('r', rk, lk)}" for lk, rk in zip(left_keys, right_keys))
    left = ", ".join(f"l.{quote(c.name)} AS {quote(c.name)}" for c in ctx.columns)
    right = ", ".join(f"{expr} AS {quote(name)}" for name, expr in added)
    join = "LEFT JOIN" if how == "left" else "JOIN"
    return f"SELECT {left}, {right} FROM {numbered(ctx.source)} AS l {join} ({right_sql}) AS r ON {on} ORDER BY l.{POS}"


def append(params, ctx):
    ids = params.get("queries")
    if isinstance(ids, str):
        ids = [ids]
    if not isinstance(ids, list) or not ids:
        raise StepError("'queries' must list at least one table to append.")
    parts = [f"SELECT *, 0 AS {PART} FROM {numbered(ctx.source)}"]
    resolved = []
    for i, ref in enumerate(ids, start=1):
        sql, _, query_id = ctx.resolve_query(ref)
        resolved.append(query_id)
        parts.append(f"SELECT *, {i} AS {PART} FROM {numbered(f'({sql})')}")
    params["queries"] = resolved
    stacked = " UNION ALL BY NAME ".join(parts)
    # This table's rows first, then each appended table's, each in its own order.
    return f"SELECT * EXCLUDE ({PART}, {POS}) FROM ({stacked}) ORDER BY {PART}, {POS}"


@dataclass
class Operation:
    name: str
    compile: Callable[[dict, Context], str]
    summary: str
    params: dict  # JSON-Schema-style description, shown to the AI
    changes_rows: bool = False


OPERATIONS = {
    op.name: op
    for op in [
        Operation(
            "change_type", change_type,
            "Convert a column to a type. Dates keep the day and drop any time of day. Decimal and integer strip currency symbols (RM, $) and thousands "
            "separators. Values that do not convert become blank.",
            {"column": "string", "to": list(LOGICAL_TYPES),
             "date_order": {"enum": list(DATE_ORDERS), "note": "for slash dates like 05/01/2024: day first (dmy) or month first (mdy)"}},
        ),
        Operation("trim", trim, "Remove leading/trailing spaces and collapse repeated spaces in text columns.",
                  {"columns": ["string"]}),
        Operation("change_case", change_case, "Make text upper, lower or Title Case.",
                  {"columns": ["string"], "case": list(CASES)}),
        Operation(
            "replace_values", replace_values,
            "Replace exact text values (ignore_case also ignores surrounding spaces). Use it to unify spellings. "
            "'to' may be null to blank a value.",
            {"column": "string", "replacements": [{"from": "string", "to": "string or null"}], "ignore_case": "boolean (default true)"},
        ),
        Operation("blanks_to_null", blanks_to_null,
                  "Turn empty text and placeholders like N/A, null, '-' into real blanks.",
                  {"columns": ["string"], "values": "optional list of placeholder strings"}),
        Operation("fill_blanks", fill_blanks, "Replace blanks with a fixed value, e.g. 'Unknown' or 0.",
                  {"column": "string", "value": "string, number or boolean"}),
        Operation("remove_duplicates", remove_duplicates,
                  "Remove duplicate rows: exact duplicates, or rows repeating the given key columns (keeps one).",
                  {"columns": "optional list of key columns"}, changes_rows=True),
        Operation("remove_columns", remove_columns, "Remove columns that are not needed.", {"columns": ["string"]}),
        Operation(
            "add_column", add_column,
            "Add a calculated column, worked out row by row: [Column] arithmetic and conditions, e.g. [Qty] * [Price] "
            'or IF([Miles] > 20, "Long", "Short"); SWITCH, AND, OR, NOT and comparisons work too.',
            {"name": "string", "expression": "string"},
        ),
        Operation("rename_column", rename_column, "Rename a column.", {"column": "string", "to": "string"}),
        Operation("filter_rows", filter_rows, "Keep only rows that match a condition.",
                  {"column": "string", "operator": list(FILTER_OPERATORS), "value": "for comparisons", "values": "for in / not_in"},
                  changes_rows=True),
        Operation(
            "merge", merge,
            "Look up columns from another table by matching key columns (like a VLOOKUP / Merge Queries). "
            "Only use it when a value must physically live in this table; the Model stage relates tables instead.",
            {"query": "other table name", "left_on": ["string"], "right_on": ["string"], "columns": ["columns to bring in"],
             "how": ["left", "inner"]},
            changes_rows=True,
        ),
        Operation("append", append, "Stack rows of other tables with the same columns under this one (Append Queries).",
                  {"queries": ["other table names"]}, changes_rows=True),
    ]
}


def catalogue() -> list[dict]:
    return [{"op": op.name, "summary": op.summary, "params": op.params} for op in OPERATIONS.values()]


def compile_step(op: str, params: dict, ctx: Context) -> str:
    if op not in OPERATIONS:
        raise StepError(f"Unknown operation '{op}'.")
    if not isinstance(params, dict):
        raise StepError("'params' must be an object.")
    return OPERATIONS[op].compile(params, ctx)


def describe(op: str, params: dict, query_names: dict[str, str]) -> str:
    """A short human label for a step, like Power Query's Applied Steps."""
    def cols(key="columns"):
        value = params.get(key) or []
        return ", ".join(value if isinstance(value, list) else [value])

    if op == "change_type":
        extra = f" ({params.get('date_order', 'dmy')})" if params.get("to") == "date" else ""
        return f"Change {params.get('column')} to {params.get('to')}{extra}"
    if op == "trim":
        return f"Trim spaces in {cols()}"
    if op == "change_case":
        return f"Make {cols()} {params.get('case')} case"
    if op == "replace_values":
        n = len(params.get("replacements") or [])
        return f"Replace {n} value{'s' if n != 1 else ''} in {params.get('column')}"
    if op == "blanks_to_null":
        return f"Treat placeholders as blank in {cols()}"
    if op == "fill_blanks":
        return f"Fill blanks in {params.get('column')} with {params.get('value')!r}"
    if op == "remove_duplicates":
        return f"Remove duplicates by {cols()}" if params.get("columns") else "Remove duplicate rows"
    if op == "remove_columns":
        return f"Remove {cols()}"
    if op == "rename_column":
        return f"Rename {params.get('column')} to {params.get('to')}"
    if op == "add_column":
        return f"Add column {params.get('name')} = {params.get('expression')}"
    if op == "filter_rows":
        value = params.get("values") if params.get("operator") in ("in", "not_in") else params.get("value")
        label = re.sub("_", " ", str(params.get("operator")))
        return f"Keep rows where {params.get('column')} {label}" + ("" if value is None else f" {value}")
    if op == "merge":
        return f"Merge {cols()} from {query_names.get(params.get('query'), params.get('query'))}"
    if op == "append":
        ids = params.get("queries") or []
        return "Append " + ", ".join(query_names.get(i, i) for i in ids)
    return op
