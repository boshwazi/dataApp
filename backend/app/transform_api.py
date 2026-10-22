"""HTTP routes for the Transform stage."""

from contextlib import contextmanager
from typing import Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import guidance, activity, ai_transform, llm, transform
from .projects import NotFound, ProjectStore
from .settings import ProviderSettings, SettingsStore
from .transform_ops import StepError, catalogue, describe

PREVIEW_ROWS = 100


class StepBody(BaseModel):
    op: str
    params: dict = {}
    reason: str = ""
    position: int | None = None


class SplitBody(BaseModel):
    from_table: str
    columns: list[str]
    key: str | None = None
    name: str | None = None
    reason: str = ""


class MoveBody(BaseModel):
    to: int


class NameBody(BaseModel):
    name: str


def step_view(step: dict, names: dict[str, str]) -> dict:
    return {**step, "label": describe(step["op"], step["params"], names)}


def query_summary(project: dict, query: dict) -> dict:
    names = transform.query_names(project)
    source = next((s for s in project["sources"] if s["id"] == query["source_id"]), None)
    profile = (query["output"] or {}).get("profile")
    base = next((q for q in transform.state(project)["queries"] if q["id"] == query.get("base_query")), None)
    split_after = None
    if base and query.get("base_step"):
        ids = [s["id"] for s in base["steps"]]
        split_after = ids.index(query["base_step"]) + 1 if query["base_step"] in ids else None
    return {
        "id": query["id"],
        "name": query["name"],
        "kind": "lookup" if query.get("base_query") else "source",
        "status": query.get("status"),
        "reason": query.get("reason", ""),
        "base_table": base["name"] if base else None,
        "split_after_step": split_after,
        "source_table": source["table"] if source else None,
        "steps": [step_view(s, names) for s in query["steps"]],
        "error": query["error"],
        "warnings": query.get("warnings", []),
        "rows": profile["rows"] if profile else None,
        "columns": (query["output"] or {}).get("columns", []),
        "issue_count": (
            sum(len(c["issues"]) for c in profile["columns"]) + bool(profile["duplicate_rows"]) if profile else None
        ),
    }


def view(project: dict) -> dict:
    t = transform.state(project)
    return {
        "queries": [query_summary(project, q) for q in t["queries"]],
        "can_undo": bool(t["history"]),
    }


def build_router(
    store: ProjectStore,
    settings_store: SettingsStore,
    client_factory: Callable[[ProviderSettings], object],
) -> APIRouter:
    router = APIRouter(prefix="/api/datasets/{project_id}/transform")

    @contextmanager
    def editing(project_id: str):
        """Change the project's transform state, then re-run every query. Step errors become HTTP 400."""
        try:
            with store.edit(project_id) as project, store.connect(project_id) as con:
                transform.state(project)
                yield project, con
                transform.apply_all(con, project)
        except NotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except StepError as exc:
            raise HTTPException(400, str(exc)) from exc

    def current(project_id: str) -> dict:
        """The project with its queries up to date with Get Data."""
        try:
            project = store.get(project_id)
        except NotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        if transform.needs_apply(project):
            with editing(project_id) as (project, _):
                pass
        return project

    def query_of(project: dict, query_id: str) -> dict:
        try:
            return transform.find_query(project, query_id)
        except StepError as exc:
            raise HTTPException(404, "Table not found.") from exc

    @router.get("")
    def get_transform(project_id: str):
        return {**view(current(project_id)), "catalogue": catalogue()}

    @router.get("/queries/{query_id}")
    def get_query(project_id: str, query_id: str):
        project = current(project_id)
        query = query_of(project, query_id)
        return {**query_summary(project, query), "profile": (query["output"] or {}).get("profile")}

    @router.get("/queries/{query_id}/preview")
    def preview(project_id: str, query_id: str, step: int = -1):
        project = current(project_id)
        query = query_of(project, query_id)
        if step < 0:
            step = len(query["steps"])
        try:
            with store.connect(project_id) as con:
                return transform.preview(con, project, query["id"], step, PREVIEW_ROWS)
        except StepError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.get("/queries/{query_id}/steps/{step}/removed")
    def removed(project_id: str, query_id: str, step: int):
        """The rows a step dropped: removed duplicates, rows a filter left out, blank keys."""
        project = current(project_id)
        query = query_of(project, query_id)
        try:
            with store.connect(project_id) as con:
                return transform.removed_rows(con, project, query["id"], step, PREVIEW_ROWS)
        except StepError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.post("/queries/{query_id}/steps")
    def add_step(project_id: str, query_id: str, body: StepBody):
        with editing(project_id) as (project, con):
            query = query_of(project, query_id)
            step = {"op": body.op, "params": body.params, "reason": body.reason}
            added, rejected = transform.add_steps(con, project, query["id"], [step], "Approved", body.position)
            if rejected:
                raise StepError(rejected[0]["error"])
            activity.changed(project, "transform", f"{query['name']}: added step {describe(body.op, step['params'], transform.query_names(project))}")
        return view(project)

    @router.post("/queries/{query_id}/steps/{step_id}/approve")
    def approve(project_id: str, query_id: str, step_id: str):
        with editing(project_id) as (project, _):
            query = query_of(project, query_id)
            transform.approve_step(project, query["id"], step_id)
            activity.log(project, "transform", f"{query['name']}: approved a step")
        return view(project)

    @router.post("/split")
    def split(project_id: str, body: SplitBody):
        with editing(project_id) as (project, con):
            made = transform.split_table(con, project, body.from_table, body.columns, body.key, body.name, body.reason)
            activity.changed(project, "transform", f"Split lookup table {made['name']} off {body.from_table}")
        return view(project)

    @router.delete("/queries/{query_id}")
    def remove_table(project_id: str, query_id: str):
        with editing(project_id) as (project, con):
            query = query_of(project, query_id)
            transform.remove_table(con, project, query["id"])
            activity.changed(project, "transform", f"Removed lookup table {query['name']}")
        return view(project)

    @router.post("/approve-all")
    def approve_all(project_id: str, query_id: str | None = None):
        """Approve every AI step; pass query_id to limit it to one table."""
        with editing(project_id) as (project, _):
            count = transform.approve_all(project, query_of(project, query_id)["id"] if query_id else None)
            activity.log(project, "transform", f"Approved {count} AI {'change' if count == 1 else 'changes'}")
        return view(project)

    @router.delete("/queries/{query_id}/steps/{step_id}")
    def remove(project_id: str, query_id: str, step_id: str):
        with editing(project_id) as (project, con):
            query = query_of(project, query_id)
            label = next((describe(s["op"], s["params"], transform.query_names(project)) for s in query["steps"] if s["id"] == step_id), "a step")
            transform.remove_step(con, project, query["id"], step_id)
            activity.changed(project, "transform", f"{query['name']}: removed step {label}")
        return view(project)

    @router.post("/queries/{query_id}/steps/{step_id}/move")
    def move(project_id: str, query_id: str, step_id: str, body: MoveBody):
        with editing(project_id) as (project, con):
            query = query_of(project, query_id)
            transform.move_step(con, project, query["id"], step_id, body.to)
            activity.changed(project, "transform", f"{query['name']}: moved a step to position {body.to + 1}")
        return view(project)

    @router.patch("/queries/{query_id}")
    def rename(project_id: str, query_id: str, body: NameBody):
        with editing(project_id) as (project, con):
            query = query_of(project, query_id)
            old = query["name"]
            transform.rename_query(con, project, query["id"], body.name)
            activity.changed(project, "transform", f"Renamed table {old} to {query['name']}")
        return view(project)

    @router.post("/undo")
    def undo(project_id: str):
        with editing(project_id) as (project, _):
            transform.undo(project)
            activity.changed(project, "transform", "Undid the last Transform change")
        return view(project)

    @router.post("/queries/{query_id}/ai")
    def ai_clean(project_id: str, query_id: str):
        settings = settings_store.load()
        if not settings.api_key:
            raise HTTPException(400, "Add an API key in Settings to let the AI clean your data.")
        project = current(project_id)
        query = query_of(project, query_id)
        if query["error"] or not query["output"]:
            raise HTTPException(400, f"Fix this table first: {query['error']}")
        # The AI call runs without holding the project lock; its steps are checked against the latest state.
        try:
            with store.connect(project_id) as con:
                proposal = ai_transform.propose(
                    guidance.guided(client_factory(settings), guidance.for_project(store, project)), settings.model, settings.capabilities, con, project, query
                )
        except llm.AIError as exc:
            raise HTTPException(502, f"AI cleaning failed: {exc}") from exc
        # Cleaning steps first, then the lookup tables (so they get cleaned values), then removing the
        # columns that moved into them. Each part is checked; together they undo as one change.
        moved = {c.lower() for t in proposal["new_tables"] for c in t.get("columns") or [] if isinstance(c, str)}

        def removes_moved(step):
            cols = (step.get("params") or {}).get("columns")
            cols = [cols] if isinstance(cols, str) else cols or []
            return step.get("op") == "remove_columns" and moved and all(str(c).lower() in moved for c in cols)

        first = [s for s in proposal["steps"] if not removes_moved(s)]
        last = [s for s in proposal["steps"] if removes_moved(s)]
        with editing(project_id) as (project, con):
            before = len(transform.state(project)["history"])
            added, rejected = transform.add_steps(con, project, query["id"], first, "AI")
            tables = []
            for spec in proposal["new_tables"]:
                try:
                    made = transform.split_table(con, project, query["id"], spec.get("columns"), spec.get("key"),
                                                 spec.get("name"), spec.get("reason", ""), "AI")
                    tables.append(made["name"])
                except StepError as exc:
                    rejected.append({"op": "new table", "params": spec, "reason": spec.get("reason", ""),
                                     "error": f"Lookup table '{spec.get('name')}': {exc}"})
            more, refused = transform.add_steps(con, project, query["id"], last, "AI")
            added += more
            rejected += refused
            history = transform.state(project)["history"]
            del history[before + 1:]  # keep only the first snapshot, so one undo reverts the whole run
            if added or tables:
                extra = f" and split off {', '.join(tables)}" if tables else ""
                activity.changed(project, "transform", f"{query['name']}: AI added {len(added)} cleaning steps{extra}", "AI")
        names = transform.query_names(project)
        return {
            "summary": proposal["summary"],
            "added": [step_view(s, names) for s in added],
            "new_tables": tables,
            "rejected": rejected,
            **view(project),
        }

    router.calls = {"ai_clean": ai_clean, "approve_steps": approve_all}  # for Autopilot
    return router
