"""The Transform engine: one query per source, each an ordered list of Applied Steps.

A query compiles to a chain of CTEs (s0 = the raw source table, s1 = after step 1, ...)
and is materialized as a table in the `clean` schema, which later stages read. Steps
are replayed from the raw table every time, so they repeat exactly on refreshed data.

Every change is checked before it is kept: the changed query must still compile and
run, no other query may break because of it, and a merge may not multiply rows.
Each change snapshots the previous steps, so it can be undone.
"""

import copy
from dataclasses import dataclass

import duckdb

from . import profiling
from .projects import new_id, now, quote, table_name, unique
from .transform_ops import OPERATIONS, Column, Context, StepError, compile_step, describe, logical_type

OUTPUT_SCHEMA = "clean"
HISTORY_LIMIT = 30


@dataclass
class Compiled:
    ctes: list[str]
    columns: list[list[Column]]  # columns after each step; [0] is the source

    def sql(self, step: int | None = None) -> str:
        k = len(self.ctes) - 1 if step is None else step
        return "WITH " + ", ".join(self.ctes[: k + 1]) + f" SELECT * FROM s{k}"


# State


def state(project: dict) -> dict:
    """The project's transform state, with one query for every source that lacks one."""
    transform = project.setdefault("transform", {"queries": [], "history": []})
    queries = transform["queries"]
    have = {q["source_id"] for q in queries}
    taken = {q["name"] for q in queries}
    for source in project["sources"]:
        if source["id"] not in have:
            name = unique(source["table"], taken)
            taken.add(name)
            queries.append({
                "id": new_id(),
                "workspace_id": project["workspace_id"],
                "name": name,
                "source_id": source["id"],
                "steps": [],
                "output": None,
                "error": None,
            })
    return transform


def find_query(project: dict, ref: str) -> dict:
    queries = state(project)["queries"]
    for query in queries:
        if query["id"] == ref:
            return query
    for query in queries:
        if isinstance(ref, str) and query["name"].lower() == ref.strip().lower():
            return query
    raise StepError(f"There is no table called '{ref}'.")


def query_names(project: dict) -> dict[str, str]:
    return {q["id"]: q["name"] for q in state(project)["queries"]}


def push_history(project: dict, queries: list[dict]) -> None:
    transform = state(project)
    saved = [{k: v for k, v in q.items() if k not in ("output", "error")} for q in queries]
    transform["history"] = (transform["history"] + [copy.deepcopy(saved)])[-HISTORY_LIMIT:]


def snapshot(project: dict) -> None:
    push_history(project, state(project)["queries"])


# Compiling


def columns_of(con: duckdb.DuckDBPyConnection, sql: str) -> list[Column]:
    return [Column(name, logical_type(kind)) for name, kind, *_ in con.execute(f"DESCRIBE {sql}").fetchall()]


def base_sql(con, project: dict, query: dict, visiting: frozenset) -> str:
    """The SQL a query starts from: its raw source table, or for a lookup table the output of the
    table it was split from, as it was right after the step where the split happened."""
    if not query.get("base_query"):
        source = next((s for s in project["sources"] if s["id"] == query["source_id"]), None)
        if source is None:
            raise StepError("Its source table was removed in Get Data.")
        return f"SELECT * FROM {quote(source['table'])}"
    base = next((q for q in state(project)["queries"] if q["id"] == query["base_query"]), None)
    if base is None:
        raise StepError("The table it was split from was removed.")
    if base["id"] in visiting:
        raise StepError(f"'{base['name']}' already depends on this table.")
    upto = 0
    if query.get("base_step"):
        ids = [s["id"] for s in base["steps"]]
        if query["base_step"] not in ids:
            raise StepError(f"The step of '{base['name']}' it was split after was removed.")
        upto = ids.index(query["base_step"]) + 1
    return compile_query(con, project, base, visiting).sql(upto)


def compile_query(con, project: dict, query: dict, visiting: frozenset = frozenset()) -> Compiled:
    names = query_names(project)
    visiting = visiting | {query["id"]}
    start = base_sql(con, project, query, visiting)

    def resolve(ref):
        if not isinstance(ref, str):
            raise StepError("Name another table.")
        other = find_query(project, ref)
        if other["id"] in visiting:
            raise StepError(f"'{other['name']}' already depends on this table.")
        compiled = compile_query(con, project, other, visiting)
        return compiled.sql(), compiled.columns[-1], other["id"]

    ctes = [f"s0 AS ({start})"]
    compiled = Compiled(ctes, [])
    compiled.columns.append(columns_of(con, compiled.sql()))
    for i, step in enumerate(query["steps"], start=1):
        ctx = Context(compiled.columns[-1], f"s{i - 1}", resolve)
        label = describe(step["op"], step["params"], names)
        try:
            ctes.append(f"s{i} AS ({compile_step(step['op'], step['params'], ctx)})")
            compiled.columns.append(columns_of(con, compiled.sql()))
        except StepError as exc:
            raise StepError(f"Step {i} ({label}): {exc}") from exc
        except duckdb.Error as exc:
            raise StepError(f"Step {i} ({label}) failed: {str(exc).splitlines()[0]}") from exc
    return compiled


def validate(con, project: dict, query: dict) -> Compiled:
    """Compile a query and check its merges; raises StepError."""
    compiled = compile_query(con, project, query)
    check_merges(con, project, query, compiled)
    return compiled


def failing(con, project: dict) -> dict[str, str]:
    """Query id -> error, for every query that does not pass validation."""
    errors = {}
    for query in state(project)["queries"]:
        try:
            validate(con, project, query)
        except StepError as exc:
            errors[query["id"]] = str(exc)
    return errors


def check_change(con, before: dict, after: dict, changed_query_id: str) -> None:
    """Raise if the change breaks the query it touches or any query that worked before."""
    broken_before = failing(con, before)
    broken_after = failing(con, after)
    if changed_query_id in broken_after:
        raise StepError(broken_after[changed_query_id])
    newly = [qid for qid in broken_after if qid not in broken_before]
    if newly:
        names = query_names(after)
        raise StepError(f"This would break '{names[newly[0]]}': {broken_after[newly[0]]}")


def check_merges(con, project: dict, query: dict, compiled: Compiled) -> None:
    """A merge must not multiply rows: that means the other table's keys repeat."""
    names = query_names(project)
    for i, step in enumerate(query["steps"], start=1):
        if step["op"] != "merge":
            continue
        before = con.execute(f"SELECT count(*) FROM ({compiled.sql(i - 1)})").fetchone()[0]
        after = con.execute(f"SELECT count(*) FROM ({compiled.sql(i)})").fetchone()[0]
        if after > before:
            other = names.get(step["params"].get("query"), "the other table")
            raise StepError(
                f"Step {i}: merging would turn {before} rows into {after} because the key repeats in '{other}'. "
                f"Remove duplicates by the key in '{other}' first."
            )


# Changing steps. Each function changes `project` in place, or raises StepError and leaves it alone.


LOSS_LIMIT = 0.5


def check_effect(con, project: dict, query: dict, index: int) -> None:
    """Refuse a new step that silently destroys data or does nothing.

    A type change may not blank more than half of a column's filled values (usually the wrong
    date order or a mislabelled column), and a replacement must match at least one value.
    """
    step = query["steps"][index]
    if step["op"] not in ("change_type", "replace_values"):
        return
    compiled = compile_query(con, project, query)
    before_sql, after_sql = compiled.sql(index), compiled.sql(index + 1)
    names = [c.name for c in compiled.columns[index + 1]]
    target = next((n for n in names if n.lower() == str(step["params"].get("column", "")).lower()), None)
    if target is None:
        return
    c = quote(target)
    # Both sides are projections of the same rows in the same order, so they line up position by position.
    pairs = f"(SELECT {c} AS v FROM ({before_sql})) b POSITIONAL JOIN (SELECT {c} AS v FROM ({after_sql})) a"
    if step["op"] == "replace_values":
        changed = con.execute(f"SELECT count(*) FROM {pairs} WHERE b.v IS DISTINCT FROM a.v").fetchone()[0]
        if changed == 0:
            raise StepError(f"None of the values to replace exist in {target}, so this step would change nothing.")
        return
    filled, lost = con.execute(
        f"SELECT count(b.v), count(*) FILTER (WHERE b.v IS NOT NULL AND a.v IS NULL) FROM {pairs}"
    ).fetchone()
    if filled and lost / filled > LOSS_LIMIT:
        examples = [str(v) for (v,) in con.execute(
            f"SELECT DISTINCT CAST(b.v AS VARCHAR) FROM {pairs} WHERE b.v IS NOT NULL AND a.v IS NULL LIMIT 3"
        ).fetchall()]
        raise StepError(
            f"Changing {target} to {step['params'].get('to')} would blank {lost} of {filled} values "
            f"(e.g. {', '.join(repr(e) for e in examples)}), so it was refused."
        )


def add_steps(con, project: dict, query_id: str, steps: list[dict], status: str, position: int | None = None):
    """Add steps one by one, keeping those that pass the checks. Returns (added, rejected)."""
    query = find_query(project, query_id)
    snapshot(project)
    added, rejected = [], []
    for proposal in steps:
        step = {
            "id": new_id(),
            "op": proposal.get("op"),
            "params": copy.deepcopy(proposal.get("params") or {}),
            "reason": str(proposal.get("reason") or "").strip(),
            "status": status,
            "added_at": now(),
        }
        before = copy.deepcopy(project)
        at = len(query["steps"]) if position is None else max(0, min(position + len(added), len(query["steps"])))
        query["steps"].insert(at, step)
        try:
            if step["op"] not in OPERATIONS:
                raise StepError(f"Unknown operation '{step['op']}'.")
            check_change(con, before, project, query["id"])
            check_effect(con, project, query, query["steps"].index(step))
        except StepError as exc:
            query["steps"].remove(step)
            rejected.append({"op": step["op"], "params": proposal.get("params"), "reason": step["reason"], "error": str(exc)})
            continue
        added.append(step)
    if not added:
        state(project)["history"].pop()
    return added, rejected


def change(con, project: dict, query_id: str, edit) -> None:
    """Apply `edit(query)`, keeping it only if every check passes."""
    before = copy.deepcopy(project)
    query = find_query(project, query_id)
    edit(query)
    try:
        check_change(con, before, project, query["id"])
    except StepError:
        project.clear()
        project.update(before)
        raise
    push_history(project, before["transform"]["queries"])


def step_index(query: dict, step_id: str) -> int:
    for i, step in enumerate(query["steps"]):
        if step["id"] == step_id:
            return i
    raise StepError("Step not found.")


def remove_step(con, project, query_id, step_id):
    change(con, project, query_id, lambda q: q["steps"].pop(step_index(q, step_id)))


def move_step(con, project, query_id, step_id, to: int):
    def edit(q):
        step = q["steps"].pop(step_index(q, step_id))
        q["steps"].insert(max(0, min(to, len(q["steps"]))), step)
    change(con, project, query_id, edit)


def approve_step(project, query_id, step_id):
    query = find_query(project, query_id)
    step = query["steps"][step_index(query, step_id)]
    if step["status"] != "Approved":
        snapshot(project)
        step["status"] = "Approved"


def approve_all(project, query_id: str | None = None) -> int:
    """Approve every AI step, in one table or all of them, as a single undoable change."""
    queries = [find_query(project, query_id)] if query_id else state(project)["queries"]
    pending = [s for q in queries for s in q["steps"] if s["status"] != "Approved"]
    pending += [q for q in queries if q.get("status") == "AI"]  # lookup tables the AI split off
    if pending:
        snapshot(project)
        for item in pending:
            item["status"] = "Approved"
    return len(pending)


# Lookup tables


def split_table(con, project: dict, from_query: str, columns: list, key: str | None = None, name: str | None = None,
                reason: str = "", status: str = "Approved") -> dict:
    """Split columns of a table off into a lookup table with one row per key (Reference, keep
    columns, remove blank keys, remove duplicates). It starts from the table as it is now, so
    later steps there, such as removing the moved columns, do not change it."""
    base = find_query(project, from_query)
    available = compile_query(con, project, base).columns[-1]
    if isinstance(columns, str):
        columns = [columns]
    if not isinstance(columns, list) or not columns:
        raise StepError("List the columns to move into the lookup table.")

    def actual(ref):
        for column in available:
            if isinstance(ref, str) and column.name.lower() == ref.strip().lower():
                return column.name
        raise StepError(f"'{base['name']}' has no column '{ref}'.")

    picked = list(dict.fromkeys(actual(c) for c in columns))
    key_name = actual(key) if key else picked[0]
    if key_name not in picked:
        picked.insert(0, key_name)
    if len(picked) == len(available):
        raise StepError("A lookup table should take some columns, not all of them.")
    taken = {q["name"] for q in state(project)["queries"]}
    new_name = table_name(name or key_name)
    if new_name in taken:
        raise StepError(f"A table named {new_name} already exists.")
    made = now()
    step = lambda op, params, why: {"id": new_id(), "op": op, "params": params, "reason": why, "status": status, "added_at": made}
    query = {
        "id": new_id(),
        "workspace_id": project["workspace_id"],
        "name": new_name,
        "source_id": None,
        "base_query": base["id"],
        "base_step": base["steps"][-1]["id"] if base["steps"] else None,
        "status": status,
        "reason": str(reason or ""),
        "steps": [
            step("remove_columns", {"columns": [c.name for c in available if c.name not in picked]}, "Keep only the lookup columns."),
            step("filter_rows", {"column": key_name, "operator": "is_not_blank"}, "Every row needs a key."),
            step("remove_duplicates", {"columns": [key_name]}, "One row per key, so it can be the one side of a relationship."),
        ],
        "output": None,
        "error": None,
    }
    before = copy.deepcopy(project)
    snapshot(project)
    state(project)["queries"].append(query)
    try:
        check_change(con, before, project, query["id"])
    except StepError:
        project.clear()
        project.update(before)
        raise
    return query


def remove_table(con, project: dict, query_id: str) -> None:
    """Remove a lookup table. Tables loaded in Get Data are removed there instead."""
    query = find_query(project, query_id)
    if not query.get("base_query"):
        raise StepError("Remove this table in Get Data; only lookup tables are removed here.")
    before = copy.deepcopy(project)
    state(project)["queries"].remove(query)
    broken_before, broken_after = failing(con, before), failing(con, project)
    newly = [qid for qid in broken_after if qid not in broken_before]
    if newly:
        names = query_names(before)
        project.clear()
        project.update(before)
        raise StepError(f"'{names[newly[0]]}' uses this table, so it cannot be removed.")
    push_history(project, before["transform"]["queries"])


def rename_query(con, project, query_id, name: str):
    new = table_name(name)
    if any(q["name"] == new and q["id"] != query_id for q in state(project)["queries"]):
        raise StepError(f"A table named {new} already exists.")
    change(con, project, query_id, lambda q: q.update(name=new))


def undo(project: dict) -> None:
    transform = state(project)
    if not transform["history"]:
        raise StepError("Nothing to undo.")
    sources = {s["id"] for s in project["sources"]}
    current = {q["id"]: q for q in transform["queries"]}
    queries = [
        {**current.get(saved["id"], {"output": None, "error": None}), **saved}
        for saved in transform["history"].pop()
        if saved.get("base_query") or saved["source_id"] in sources
    ]
    restored = {q["id"] for q in queries}
    for query in transform["queries"]:
        # A source loaded after the snapshot keeps its table, without steps; lookup tables made since are dropped.
        if query["id"] not in restored and not query.get("base_query"):
            queries.append({**query, "steps": []})
    transform["queries"] = queries


def drop_source(project: dict, source_id: str) -> None:
    """Forget the query of a removed source. Queries that merged it will show an error."""
    transform = state(project)
    transform["queries"] = [q for q in transform["queries"] if q["source_id"] != source_id]


# Running


def apply_all(con, project: dict) -> None:
    """Materialize every query into the clean schema and profile the result."""
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {OUTPUT_SCHEMA}")
    transform = state(project)
    transform["applied_sources"] = sorted(s["id"] for s in project["sources"])
    queries = transform["queries"]
    keep = set()
    for query in queries:
        try:
            compiled = validate(con, project, query)
            con.execute(f"CREATE OR REPLACE TABLE {OUTPUT_SCHEMA}.{quote(query['name'])} AS {compiled.sql()}")
        except StepError as exc:
            query["error"], query["output"] = str(exc), None
            continue
        keep.add(query["name"])
        query["error"] = None
        query["output"] = {
            "columns": [{"name": c.name, "type": c.type} for c in compiled.columns[-1]],
            "profile": profiling.profile_table(con, query["name"], OUTPUT_SCHEMA),
        }
    existing = con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = ?", [OUTPUT_SCHEMA]
    ).fetchall()
    for (name,) in existing:
        if name not in keep:
            con.execute(f"DROP TABLE {OUTPUT_SCHEMA}.{quote(name)}")


def needs_apply(project: dict) -> bool:
    """True when sources were added or removed since the queries last ran."""
    transform = state(project)
    return transform.get("applied_sources") != sorted(s["id"] for s in project["sources"])


def preview(con, project: dict, query_id: str, step: int, limit: int = 100) -> dict:
    """Rows and impact before and after step `step` (1-based; 0 shows the source)."""
    query = find_query(project, query_id)
    compiled = compile_query(con, project, query)
    step = max(0, min(step, len(query["steps"])))

    def rows(k):
        result = con.execute(f"SELECT * FROM ({compiled.sql(k)}) LIMIT {limit}")
        return {
            "columns": [{"name": c.name, "type": c.type} for c in compiled.columns[k]],
            "rows": [[None if v is None else str(v) for v in row] for row in result.fetchall()],
            "total": con.execute(f"SELECT count(*) FROM ({compiled.sql(k)})").fetchone()[0],
        }

    after = rows(step)
    before = rows(step - 1) if step > 0 else None
    return {"step": step, "before": before, "after": after, "impact": impact(con, compiled, step) if before else None}


def impact(con, compiled: Compiled, step: int) -> dict:
    before_cols = {c.name: c for c in compiled.columns[step - 1]}
    after_cols = {c.name: c for c in compiled.columns[step]}
    shared = [n for n in after_cols if n in before_cols]

    def blanks(k):
        counts = ", ".join(f"count(*) - count({quote(n)})" for n in shared) or "0"
        return con.execute(f"SELECT count(*), {counts} FROM ({compiled.sql(k)})").fetchone()

    b, a = blanks(step - 1), blanks(step)
    return {
        "rows_before": b[0],
        "rows_after": a[0],
        "added_columns": [n for n in after_cols if n not in before_cols],
        "removed_columns": [n for n in before_cols if n not in after_cols],
        "type_changes": [
            {"column": n, "from": before_cols[n].type, "to": after_cols[n].type}
            for n in shared if before_cols[n].type != after_cols[n].type
        ],
        "blank_changes": [
            {"column": n, "before": b[i + 1], "after": a[i + 1]} for i, n in enumerate(shared) if b[i + 1] != a[i + 1]
        ],
    }
