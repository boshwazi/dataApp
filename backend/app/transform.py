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


def compile_query(con, project: dict, query: dict, visiting: frozenset = frozenset()) -> Compiled:
    source = next((s for s in project["sources"] if s["id"] == query["source_id"]), None)
    if source is None:
        raise StepError("Its source table was removed in Get Data.")
    names = query_names(project)
    visiting = visiting | {query["id"]}

    def resolve(ref):
        if not isinstance(ref, str):
            raise StepError("Name another table.")
        other = find_query(project, ref)
        if other["id"] in visiting:
            raise StepError(f"'{other['name']}' already depends on this table.")
        compiled = compile_query(con, project, other, visiting)
        return compiled.sql(), compiled.columns[-1], other["id"]

    ctes = [f"s0 AS (SELECT * FROM {quote(source['table'])})"]
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
    saved = {q["id"]: q for q in transform["history"].pop() if q["source_id"] in sources}
    queries = []
    for query in transform["queries"]:
        if query["id"] in saved:
            queries.append({**query, **saved.pop(query["id"])})
        else:
            queries.append({**query, "steps": []})  # added after the snapshot: its source is new
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
