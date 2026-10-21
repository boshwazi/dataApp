"""The Model stage: relationships, a date table and measures over the Transform output.

Tables are the `clean` tables Transform produces, plus an optional generated date table
in the `model` schema. Every relationship is checked before it is kept: the "one" side
must have one row per key, the key types must match, and enough keys must match.
Measures are structured definitions (an aggregation over a row expression, or a formula
over other measures), compiled to SQL by this module. `evaluate` computes measures
grouped and filtered by columns of related tables, following relationships from the
measure's table out to its dimensions, as Power BI does.
"""

import copy
import re
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from . import model_expr, transform
from .projects import new_id, now, quote
from .transform_ops import Column, StepError, logical_type, typed_literal

MODEL_SCHEMA = "model"
DATE_ID = "date"
DATE_NAME = "Date"
HISTORY_LIMIT = 30
MIN_MATCH_RATE = 0.5
AGGREGATIONS = ("sum", "average", "count", "count_distinct", "min", "max")
NUMERIC = ("integer", "decimal")
FORMATS = ("number", "integer", "currency", "percent")
FILTER_OPERATORS = (
    "equals", "not_equals", "in", "not_in", "is_blank", "is_not_blank", "greater_than", "less_than", "at_least", "at_most",
)
MAX_GROUPS = 1000


class ModelError(ValueError):
    pass


@dataclass
class Table:
    id: str
    name: str
    sql: str
    columns: list[Column]

    def column(self, name) -> Column:
        if not isinstance(name, str):
            raise ModelError(f"Name a column of {self.name}.")
        for column in self.columns:
            if column.name.lower() == name.strip().lower():
                return column
        raise ModelError(f"{self.name} has no column '{name}'.")


# State


def state(project: dict) -> dict:
    return project.setdefault(
        "model", {"relationships": [], "measures": [], "date_table": None, "hidden": [], "history": []}
    )


def snapshot(project: dict) -> None:
    model = state(project)
    saved = {k: copy.deepcopy(model[k]) for k in ("relationships", "measures", "date_table", "hidden")}
    model["history"] = (model["history"] + [saved])[-HISTORY_LIMIT:]


def undo(project: dict) -> None:
    model = state(project)
    if not model["history"]:
        raise ModelError("Nothing to undo.")
    model.update(model["history"].pop())


def tables(con, project: dict) -> dict[str, Table]:
    found = {}
    for query in transform.state(project)["queries"]:
        if query["output"] and not query["error"]:
            columns = [Column(c["name"], c["type"]) for c in query["output"]["columns"]]
            found[query["id"]] = Table(query["id"], query["name"], f"{transform.OUTPUT_SCHEMA}.{quote(query['name'])}", columns)
    if state(project)["date_table"]:
        exists = con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema = ? AND table_name = ?",
            [MODEL_SCHEMA, DATE_ID],
        ).fetchone()[0]
        if exists:
            sql = f"{MODEL_SCHEMA}.{quote(DATE_ID)}"
            columns = [Column(n, logical_type(t)) for n, t, *_ in con.execute(f"DESCRIBE {sql}").fetchall()]
            found[DATE_ID] = Table(DATE_ID, DATE_NAME, sql, columns)
    return found


def find_table(found: dict[str, Table], ref) -> Table:
    if isinstance(ref, str):
        if ref in found:
            return found[ref]
        for table in found.values():
            if table.name.lower() == ref.strip().lower():
                return table
    raise ModelError(f"There is no table called '{ref}'.")


# Date table


def date_range(con, project: dict, found: dict[str, Table]) -> tuple[int, int]:
    config = state(project)["date_table"] or {}
    years = []
    for table in found.values():
        if table.id == DATE_ID:
            continue
        for column in table.columns:
            if column.type == "date":
                low, high = con.execute(f"SELECT min({quote(column.name)}), max({quote(column.name)}) FROM {table.sql}").fetchone()
                years += [d.year for d in (low, high) if d is not None]
    start = config.get("start_year") or (min(years) if years else None)
    end = config.get("end_year") or (max(years) if years else None)
    if start is None or end is None:
        raise ModelError("No table has a date column, so give the date table a start and end year.")
    if start > end:
        raise ModelError("The date table must start before it ends.")
    if end - start > 50:
        raise ModelError("The date table can cover at most 50 years.")
    return int(start), int(end)


def build_date_table(con, project: dict) -> None:
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {MODEL_SCHEMA}")
    if not state(project)["date_table"]:
        con.execute(f"DROP TABLE IF EXISTS {MODEL_SCHEMA}.{quote(DATE_ID)}")
        return
    start, end = date_range(con, project, tables(con, project))
    con.execute(
        f"""
        CREATE OR REPLACE TABLE {MODEL_SCHEMA}.{quote(DATE_ID)} AS
        SELECT
            CAST(d AS DATE) AS "Date",
            year(d) AS "Year",
            'Q' || quarter(d) AS "Quarter",
            month(d) AS "Month Number",
            strftime(d, '%b') AS "Month",
            strftime(d, '%Y-%m') AS "Year Month",
            isodow(d) AS "Day of Week Number",
            strftime(d, '%a') AS "Day of Week"
        FROM range(DATE '{start}-01-01', DATE '{end}-12-31' + INTERVAL 1 DAY, INTERVAL 1 DAY) AS t(d)
        """
    )


def set_date_table(con, project: dict, start_year=None, end_year=None, status="Approved", reason="") -> dict:
    for year in (start_year, end_year):
        if year is not None and (not isinstance(year, int) or not 1900 <= year <= 2200):
            raise ModelError("Years must be whole numbers between 1900 and 2200.")
    model = state(project)
    model["date_table"] = {"start_year": start_year, "end_year": end_year, "status": status, "reason": reason}
    build_date_table(con, project)
    return model["date_table"]


# Relationships


def check_relationship(con, found: dict[str, Table], rel: dict, others: list[dict]) -> dict:
    """Validate a relationship in place (ids, direction) and return its checks. Raises ModelError."""
    many, one = find_table(found, rel.get("from_table")), find_table(found, rel.get("to_table"))
    if many.id == one.id:
        raise ModelError("A relationship needs two different tables.")
    many_col, one_col = many.column(rel.get("from_column")), one.column(rel.get("to_column"))
    if many_col.type != one_col.type:
        raise ModelError(
            f"{many.name}[{many_col.name}] is {many_col.type} but {one.name}[{one_col.name}] is {one_col.type}. "
            "Change one of them in Transform so they match."
        )
    if many_col.type == "decimal":
        raise ModelError("Decimal columns cannot be keys.")

    def repeats(table, column):
        c = quote(column.name)
        return con.execute(
            f"SELECT count({c}) - count(DISTINCT {c}), "
            f"(SELECT list(k) FROM (SELECT {c} AS k FROM {table.sql} WHERE {c} IS NOT NULL GROUP BY 1 HAVING count(*) > 1 ORDER BY 1 LIMIT 3)) "
            f"FROM {table.sql}"
        ).fetchone()

    one_dups, one_examples = repeats(one, one_col)
    if one_dups:
        many_dups, _ = repeats(many, many_col)
        if many_dups == 0:  # proposed the wrong way round
            many, one, many_col, one_col = one, many, one_col, many_col
        else:
            examples = ", ".join(str(e) for e in one_examples)
            raise ModelError(
                f"{one.name}[{one_col.name}] has {one_dups} repeated {'key' if one_dups == 1 else 'keys'} (e.g. {examples}), "
                "so it cannot be the one side. "
                f"Remove duplicates by {one_col.name} in Transform."
            )
    rel.update(from_table=many.id, from_column=many_col.name, to_table=one.id, to_column=one_col.name)

    for other in others:
        if (other["from_table"], other["from_column"], other["to_table"], other["to_column"]) == (
            many.id, many_col.name, one.id, one_col.name
        ):
            raise ModelError("That relationship already exists.")

    m, o = quote(many_col.name), quote(one_col.name)
    rows, keyed, matched = con.execute(
        f"SELECT count(*), count({m}), count({m}) FILTER (WHERE {m} IN (SELECT {o} FROM {one.sql})) FROM {many.sql}"
    ).fetchone()
    if keyed == 0:
        raise ModelError(f"{many.name}[{many_col.name}] is always blank.")
    rate = matched / keyed
    unmatched = [
        str(v) for (v,) in con.execute(
            f"SELECT DISTINCT {m} FROM {many.sql} WHERE {m} IS NOT NULL AND {m} NOT IN (SELECT {o} FROM {one.sql} WHERE {o} IS NOT NULL) "
            f"ORDER BY 1 LIMIT 3"
        ).fetchall()
    ]
    if rate < MIN_MATCH_RATE:
        raise ModelError(
            f"Only {rate:.0%} of {many.name}[{many_col.name}] values exist in {one.name}[{one_col.name}]; "
            "these columns probably do not belong together."
        )
    many_unique = con.execute(f"SELECT count({m}) = count(DISTINCT {m}) FROM {many.sql}").fetchone()[0]
    return {
        "rows": rows,
        "keyed_rows": keyed,
        "matched_rows": matched,
        "match_rate": round(rate, 4),
        "blank_keys": rows - keyed,
        "unmatched_examples": unmatched,
        "cardinality": "one_to_one" if many_unique else "many_to_one",
    }


def connected(relationships: list[dict], a: str, b: str) -> bool:
    links: dict[str, set[str]] = {}
    for rel in relationships:
        if rel.get("active") and not rel.get("error"):
            links.setdefault(rel["from_table"], set()).add(rel["to_table"])
            links.setdefault(rel["to_table"], set()).add(rel["from_table"])
    seen, queue = {a}, deque([a])
    while queue:
        for nxt in links.get(queue.popleft(), ()):
            if nxt == b:
                return True
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return False


def add_relationship(con, project: dict, proposal: dict, status: str) -> dict:
    model = state(project)
    found = tables(con, project)
    rel = {
        "id": new_id(),
        "from_table": proposal.get("from_table"),
        "from_column": proposal.get("from_column"),
        "to_table": proposal.get("to_table"),
        "to_column": proposal.get("to_column"),
        "reason": str(proposal.get("reason") or ""),
        "status": status,
        "added_at": now(),
        "error": None,
    }
    rel["checks"] = check_relationship(con, found, rel, model["relationships"])
    # Only one active path between two tables, as in Power BI; extra ones are kept inactive.
    rel["active"] = not connected(model["relationships"], rel["from_table"], rel["to_table"])
    model["relationships"].append(rel)
    return rel


# Measures


def measure_tree(measure: dict):
    expression = measure.get("expression")
    if measure["kind"] == "aggregate" and measure["aggregation"] == "count" and not expression:
        return None
    try:
        return model_expr.parse(expression)
    except model_expr.ExprError as exc:
        raise ModelError(f"Measure '{measure['name']}': {exc}") from exc


def filter_sql(table: Table, alias: str, item: dict) -> str:
    column = table.column(item.get("column"))
    operator = item.get("operator")
    if operator not in FILTER_OPERATORS:
        raise ModelError(f"Filter operator must be one of: {', '.join(FILTER_OPERATORS)}.")
    ref = f"{alias}.{quote(column.name)}"
    if operator == "is_blank":
        return f"{ref} IS NULL" + (f" OR trim({ref}) = ''" if column.type == "text" else "")
    if operator == "is_not_blank":
        return f"{ref} IS NOT NULL" + (f" AND trim({ref}) <> ''" if column.type == "text" else "")
    try:
        if operator in ("in", "not_in"):
            values = item.get("values")
            if not isinstance(values, list) or not values:
                raise ModelError("'values' must list at least one value.")
            listed = ", ".join(typed_literal(v, column) for v in values)
            return f"{ref} IN ({listed})" if operator == "in" else f"({ref} IS NULL OR {ref} NOT IN ({listed}))"
        if item.get("value") is None:
            raise ModelError("A filter needs a 'value'.")
        value = typed_literal(item["value"], column)
    except StepError as exc:
        raise ModelError(str(exc)) from exc
    if operator == "not_equals":
        return f"{ref} IS DISTINCT FROM {value}"
    symbol = {"equals": "=", "greater_than": ">", "less_than": "<", "at_least": ">=", "at_most": "<="}[operator]
    return f"{ref} {symbol} {value}"


def check_measure(found: dict[str, Table], measures: list[dict], measure: dict) -> None:
    """Validate a measure in place (table id, defaults). Raises ModelError."""
    name = measure.get("name")
    if not isinstance(name, str) or not name.strip() or "[" in name or "]" in name:
        raise ModelError("A measure needs a name without square brackets.")
    measure["name"] = name.strip()
    if any(m["name"].lower() == measure["name"].lower() and m["id"] != measure["id"] for m in measures):
        raise ModelError(f"A measure named '{measure['name']}' already exists.")
    fmt = measure.setdefault("format", {"type": "number", "decimals": 0})
    if not isinstance(fmt, dict) or fmt.get("type") not in FORMATS:
        raise ModelError(f"Format type must be one of: {', '.join(FORMATS)}.")
    decimals = fmt.get("decimals", 0)
    if not isinstance(decimals, int) or not 0 <= decimals <= 4:
        raise ModelError("Decimals must be 0 to 4.")
    if fmt["type"] == "currency" and not isinstance(fmt.get("currency", "RM"), str):
        raise ModelError("Currency must be a symbol like RM.")

    kind = measure.get("kind")
    if kind == "aggregate":
        table = find_table(found, measure.get("table"))
        measure["table"] = table.id
        if measure.get("aggregation") not in AGGREGATIONS:
            raise ModelError(f"Aggregation must be one of: {', '.join(AGGREGATIONS)}.")
        tree = measure_tree(measure)
        if tree is None:
            pass
        else:
            columns = [table.column(r) for r in model_expr.refs(tree)]
            single = tree[0] == "ref"
            if not columns and measure["aggregation"] != "sum":
                raise ModelError("The expression must use at least one column.")
            if (not single or measure["aggregation"] in ("sum", "average")) and any(c.type not in NUMERIC for c in columns):
                raise ModelError(f"Measure '{measure['name']}': arithmetic needs number columns.")
        for item in measure.setdefault("filters", []):
            filter_sql(table, "a0", item)
    elif kind == "formula":
        tree = measure_tree(measure)
        names = {m["name"].lower(): m for m in measures if m["id"] != measure["id"]}
        for ref in model_expr.refs(tree):
            if ref.lower() not in names:
                raise ModelError(f"Measure '{measure['name']}' uses [{ref}], which is not a measure.")
        # Refuse loops: a formula may not depend on itself through other formulas.
        stack, seen = [r.lower() for r in model_expr.refs(tree)], set()
        while stack:
            ref = stack.pop()
            if ref == measure["name"].lower():
                raise ModelError(f"Measure '{measure['name']}' would depend on itself.")
            if ref in seen or ref not in names:
                continue
            seen.add(ref)
            other = names[ref]
            if other["kind"] == "formula":
                stack += [r.lower() for r in model_expr.refs(model_expr.parse(other["expression"]))]
    else:
        raise ModelError("A measure's kind must be 'aggregate' or 'formula'.")


def check_relationship_only(con, project: dict, proposal: dict) -> dict:
    """What adding this relationship would do, without adding it: its direction, checks and whether it is active."""
    model = state(project)
    rel = {k: proposal.get(k) for k in ("from_table", "from_column", "to_table", "to_column")}
    checks = check_relationship(con, tables(con, project), rel, model["relationships"])
    return {**rel, "checks": checks, "active": not connected(model["relationships"], rel["from_table"], rel["to_table"])}


def measure_from(proposal: dict, measure_id: str, status: str) -> dict:
    return {
        "id": measure_id,
        "name": proposal.get("name"),
        "kind": proposal.get("kind"),
        "table": proposal.get("table"),
        "aggregation": proposal.get("aggregation"),
        "expression": proposal.get("expression"),
        "filters": copy.deepcopy(proposal.get("filters") or []),
        "format": copy.deepcopy(proposal.get("format") or {"type": "number", "decimals": 0}),
        "description": str(proposal.get("description") or ""),
        "folder": str(proposal.get("folder") or ""),
        "reason": str(proposal.get("reason") or ""),
        "status": status,
        "added_at": now(),
        "error": None,
    }


def try_measure(con, project: dict, proposal: dict, measure_id: str | None = None) -> dict:
    """Check a new or changed measure and work out its total, without saving it (the formula bar's live check)."""
    trial = copy.deepcopy(project)
    measures = state(trial)["measures"]
    measure = measure_from(proposal, measure_id or new_id(), "Approved")
    found = tables(con, trial)
    check_measure(found, measures, measure)
    state(trial)["measures"] = [m for m in measures if m["id"] != measure["id"]] + [measure]
    value = evaluate(con, trial, [measure["name"]])["rows"][0][0]
    return {"value": value, "label": describe_measure(measure, {t.id: t.name for t in found.values()}), "table": measure["table"]}


def update_measure(con, project: dict, measure_id: str, proposal: dict) -> dict:
    measures = state(project)["measures"]
    old = next((m for m in measures if m["id"] == measure_id), None)
    if old is None:
        raise ModelError("Measure not found.")
    measure = measure_from(proposal, measure_id, old.get("status", "Approved"))
    measure["added_at"] = old.get("added_at", now())
    check_measure(tables(con, project), measures, measure)
    if old["name"] != measure["name"]:
        # Formulas refer to measures by name, so they follow the rename.
        pattern = re.compile(r"\[\s*" + re.escape(old["name"]) + r"\s*\]", re.IGNORECASE)
        for other in measures:
            if other["kind"] == "formula" and other is not old and other.get("expression"):
                other["expression"] = pattern.sub(f"[{measure['name']}]", other["expression"])
    measures[measures.index(old)] = measure
    return measure


def set_layout(project: dict, positions: dict) -> None:
    """Where each table card sits on the model diagram."""
    clean = {}
    for table_id, box in (positions or {}).items():
        x, y = (box or {}).get("x"), (box or {}).get("y")
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            raise ModelError("Positions are given as {x, y}.")
        clean[str(table_id)] = {"x": max(0, round(x)), "y": max(0, round(y))}
    state(project)["layout"] = clean


def add_measure(con, project: dict, proposal: dict, status: str) -> dict:
    model = state(project)
    measure = measure_from(proposal, new_id(), status)
    check_measure(tables(con, project), model["measures"], measure)
    model["measures"].append(measure)
    return measure


def describe_measure(measure: dict, names: dict[str, str]) -> str:
    if measure["kind"] == "formula":
        return measure["expression"]
    table = names.get(measure["table"], measure["table"])
    what = measure.get("expression") or "rows"
    label = {"count_distinct": "distinct count", "average": "average"}.get(measure["aggregation"], measure["aggregation"])
    text = f"{label} of {what} in {table}"
    if measure.get("filters"):
        parts = []
        for f in measure["filters"]:
            value = f.get("values") if f.get("operator") in ("in", "not_in") else f.get("value")
            parts.append(f"{f.get('column')} {f.get('operator', '').replace('_', ' ')} {value}")
        text += " where " + " and ".join(parts)
    return text


# Checking everything after upstream changes


def revalidate(con, project: dict) -> None:
    """Re-check every relationship and measure against the current tables; mark broken ones."""
    model = state(project)
    if model["date_table"]:
        try:
            build_date_table(con, project)
            model["date_table"]["error"] = None
        except ModelError as exc:
            model["date_table"]["error"] = str(exc)
    found = tables(con, project)
    kept = []
    for rel in model["relationships"]:
        try:
            rel["checks"] = check_relationship(con, found, dict(rel), [r for r in kept])
            rel["error"] = None
        except ModelError as exc:
            rel["error"] = str(exc)
        kept.append(rel)
    for measure in model["measures"]:
        try:
            check_measure(found, model["measures"], measure)
            measure["error"] = None
        except ModelError as exc:
            measure["error"] = str(exc)


# Evaluating measures


def paths_from(start: str, relationships: list[dict]) -> dict[str, list[dict]]:
    """Every table reachable from `start` by following active relationships from many to one."""
    out: dict[str, list[dict]] = {}
    for rel in relationships:
        if rel.get("active") and not rel.get("error"):
            out.setdefault(rel["from_table"], []).append(rel)
    paths = {start: []}
    queue = deque([start])
    while queue:
        table = queue.popleft()
        for rel in out.get(table, []):
            if rel["to_table"] not in paths:
                paths[rel["to_table"]] = paths[table] + [rel]
                queue.append(rel["to_table"])
    return paths


def plain(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def aggregate_sql(found, relationships, measure: dict, group_by: list[dict], filters: list[dict]) -> str:
    base = found[measure["table"]]
    paths = paths_from(base.id, relationships)
    aliases = {base.id: "a0"}
    joins = []

    def alias_for(table: Table) -> str:
        if table.id not in paths:
            raise ModelError(f"'{measure['name']}' cannot be split or filtered by {table.name}: no relationship leads there.")
        for rel in paths[table.id]:
            if rel["to_table"] not in aliases:
                aliases[rel["to_table"]] = f"a{len(aliases)}"
                one = found[rel["to_table"]]
                joins.append(
                    f"LEFT JOIN {one.sql} AS {aliases[rel['to_table']]} "
                    f"ON {aliases[rel['from_table']]}.{quote(rel['from_column'])} = {aliases[rel['to_table']]}.{quote(rel['to_column'])}"
                )
        return aliases[table.id]

    groups = []
    for i, item in enumerate(group_by):
        table = find_table(found, item.get("table"))
        column = table.column(item.get("column"))
        groups.append(f"{alias_for(table)}.{quote(column.name)} AS g{i}")
    conditions = [filter_sql(base, "a0", f) for f in measure.get("filters") or []]
    for item in filters:
        table = find_table(found, item.get("table"))
        conditions.append(filter_sql(table, alias_for(table), item))

    tree = measure_tree(measure)
    expr = model_expr.to_sql(tree, lambda name: f"a0.{quote(base.column(name).name)}") if tree else None
    aggregation = measure["aggregation"]
    value = {
        "sum": f"sum({expr})",
        "average": f"avg({expr})",
        "min": f"min({expr})",
        "max": f"max({expr})",
        "count": f"count({expr})" if expr else "count(*)",
        "count_distinct": f"count(DISTINCT {expr})",
    }[aggregation]
    select = ", ".join(groups + [f"{value} AS v"])
    where = f" WHERE {' AND '.join(f'({c})' for c in conditions)}" if conditions else ""
    group = f" GROUP BY {', '.join(f'g{i}' for i in range(len(groups)))}" if groups else ""
    return f"SELECT {select} FROM {base.sql} AS a0 {' '.join(joins)}{where}{group}"


def evaluate(con, project: dict, measure_names: list[str], group_by: list[dict] | None = None,
             filters: list[dict] | None = None, limit: int = MAX_GROUPS) -> dict:
    model = state(project)
    found = tables(con, project)
    by_name = {m["name"].lower(): m for m in model["measures"]}
    group_by, filters = group_by or [], filters or []
    results: dict[str, dict[tuple, object]] = {}

    def compute(name: str) -> dict[tuple, object]:
        measure = by_name.get(str(name).lower())
        if measure is None:
            raise ModelError(f"There is no measure called '{name}'.")
        if measure.get("error"):
            raise ModelError(f"Measure '{measure['name']}' is broken: {measure['error']}")
        key = measure["name"]
        if key in results:
            return results[key]
        if measure["kind"] == "aggregate":
            sql = aggregate_sql(found, model["relationships"], measure, group_by, filters)
            rows = con.execute(sql).fetchall()
            results[key] = {tuple(plain(v) for v in row[:-1]): plain(row[-1]) for row in rows}
        else:
            tree = model_expr.parse(measure["expression"])
            parts = {ref: compute(ref) for ref in model_expr.refs(tree)}
            keys = set().union(*[p.keys() for p in parts.values()]) if parts else {()}
            results[key] = {k: plain(model_expr.evaluate(tree, {r: parts[r].get(k) for r in parts})) for k in keys}
        return results[key]

    names = [by_name[str(n).lower()]["name"] if str(n).lower() in by_name else n for n in measure_names]
    columns = [compute(n) for n in names]
    keys = set().union(*[c.keys() for c in columns]) if columns else set()
    if not group_by:
        keys = {()}
    ordered = sorted(keys, key=lambda k: tuple((v is None, str(v) if not isinstance(v, (int, float)) else v) for v in k))
    rows = [list(k) + [c.get(k) for c in columns] for k in ordered[:limit]]
    headers = [f"{g.get('table')}[{g.get('column')}]" for g in group_by] + names
    return {"columns": headers, "rows": rows, "truncated": len(ordered) > limit}


# Hidden columns


def set_hidden(con, project: dict, table_ref: str, column_name: str, hidden: bool) -> None:
    table = find_table(tables(con, project), table_ref)
    column = table.column(column_name)
    model = state(project)
    key = {"table": table.id, "column": column.name}
    model["hidden"] = [h for h in model["hidden"] if h != key]
    if hidden:
        model["hidden"].append(key)


# Suggestions for the AI


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def candidates(con, found: dict[str, Table]) -> list[dict]:
    """Likely key pairs between tables, with their checks, so the AI picks from facts not guesses."""
    out = []
    for one in found.values():
        if one.id == DATE_ID:
            continue
        for one_col in one.columns:
            if one_col.type not in ("text", "integer"):
                continue
            for many in found.values():
                if many.id in (one.id, DATE_ID):
                    continue
                for many_col in many.columns:
                    a, b = norm(many_col.name), norm(one_col.name)
                    if many_col.type != one_col.type or len(a) < 2 or len(b) < 2:
                        continue
                    if not (a == b or a.endswith(b) or b.endswith(a)):
                        continue
                    rel = {"from_table": many.id, "from_column": many_col.name, "to_table": one.id, "to_column": one_col.name}
                    entry = {"from": f"{many.name}[{many_col.name}]", "to": f"{one.name}[{one_col.name}]"}
                    try:
                        entry["checks"] = check_relationship(con, found, rel, [])
                        if rel["to_table"] != one.id:
                            continue  # the reverse direction is listed on its own
                    except ModelError as exc:
                        entry["problem"] = str(exc)
                    out.append(entry)
    return out
