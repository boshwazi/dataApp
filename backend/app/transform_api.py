"""HTTP routes for the Transform stage."""

from contextlib import contextmanager
from typing import Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import ai_transform, llm, transform
from .projects import NotFound, ProjectStore
from .settings import ProviderSettings, SettingsStore
from .transform_ops import StepError, catalogue, describe

PREVIEW_ROWS = 100


class StepBody(BaseModel):
    op: str
    params: dict = {}
    reason: str = ""
    position: int | None = None


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
    return {
        "id": query["id"],
        "name": query["name"],
        "source_table": source["table"] if source else None,
        "steps": [step_view(s, names) for s in query["steps"]],
        "error": query["error"],
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
    router = APIRouter(prefix="/api/projects/{project_id}/transform")

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

    @router.post("/queries/{query_id}/steps")
    def add_step(project_id: str, query_id: str, body: StepBody):
        with editing(project_id) as (project, con):
            query = query_of(project, query_id)
            step = {"op": body.op, "params": body.params, "reason": body.reason}
            added, rejected = transform.add_steps(con, project, query["id"], [step], "Approved", body.position)
            if rejected:
                raise StepError(rejected[0]["error"])
        return view(project)

    @router.post("/queries/{query_id}/steps/{step_id}/approve")
    def approve(project_id: str, query_id: str, step_id: str):
        with editing(project_id) as (project, _):
            transform.approve_step(project, query_of(project, query_id)["id"], step_id)
        return view(project)

    @router.delete("/queries/{query_id}/steps/{step_id}")
    def remove(project_id: str, query_id: str, step_id: str):
        with editing(project_id) as (project, con):
            transform.remove_step(con, project, query_of(project, query_id)["id"], step_id)
        return view(project)

    @router.post("/queries/{query_id}/steps/{step_id}/move")
    def move(project_id: str, query_id: str, step_id: str, body: MoveBody):
        with editing(project_id) as (project, con):
            transform.move_step(con, project, query_of(project, query_id)["id"], step_id, body.to)
        return view(project)

    @router.patch("/queries/{query_id}")
    def rename(project_id: str, query_id: str, body: NameBody):
        with editing(project_id) as (project, con):
            transform.rename_query(con, project, query_of(project, query_id)["id"], body.name)
        return view(project)

    @router.post("/undo")
    def undo(project_id: str):
        with editing(project_id) as (project, _):
            transform.undo(project)
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
                    client_factory(settings), settings.model, settings.capabilities, con, project, query
                )
        except llm.AIError as exc:
            raise HTTPException(502, f"AI cleaning failed: {exc}") from exc
        with editing(project_id) as (project, con):
            added, rejected = transform.add_steps(con, project, query["id"], proposal["steps"], "AI")
        names = transform.query_names(project)
        return {
            "summary": proposal["summary"],
            "added": [step_view(s, names) for s in added],
            "rejected": rejected,
            **view(project),
        }

    return router
