"""Autopilot: from data straight to a dashboard, for people who'd rather not do each step.

The same pipeline as the step-by-step flow, run in order by a background job:

  1. Read your data      the AI describes each table
  2. Clean the tables    the Transform AI proposes steps for each table; they are accepted
  3. Build the model     the Model AI proposes relationships, a date table and measures; accepted
  4. Publish             the dataset is published so dashboards can use it
  5. Design a dashboard  a new dashboard; the Report AI designs its pages; accepted, left as a draft

Everything the AI proposes still goes through the engine's checks, which refuse what doesn't hold.
What Autopilot adds is marked by="autopilot", so the editors can show it and it can be reviewed,
undone or changed in each stage's chat. The dashboard stays a draft: viewers see nothing until its
owner publishes it. Progress is kept on the dataset (project["autopilot"]) for the progress screen.
"""

import logging

from fastapi import HTTPException

from fastapi import APIRouter
from pydantic import BaseModel

from . import model, transform
from .projects import NotFound, new_id, now

log = logging.getLogger(__name__)

STEPS = [
    ("read", "Read your data"),
    ("clean", "Clean the tables"),
    ("model", "Build the model"),
    ("publish", "Publish the dataset"),
    ("dashboard", "Design a dashboard"),
]
FIX_AT = {"read": "get-data", "clean": "transform", "model": "model", "publish": "model", "dashboard": None}


class Stop(Exception):
    """A step that can't go on; the run stops with this message."""


def message_of(exc: Exception) -> str:
    if isinstance(exc, HTTPException):
        detail = exc.detail
        return detail.get("message", str(detail)) if isinstance(detail, dict) else str(detail)
    return str(exc)


def plural(n: int, one: str, many: str) -> str:
    return f"{n:,} {one if n == 1 else many}"


def left_out(refused: list[dict], shown: int = 2) -> str:
    """What the engine refused from the AI's proposal, briefly, so it can be looked at."""
    if not refused:
        return ""
    names = [str(r["label"]) for r in refused[:shown] if r.get("label")]
    if len(names) < min(len(refused), shown):
        return f"; {plural(len(refused), 'suggestion', 'suggestions')} didn't pass the checks"
    more = f" and {len(refused) - shown} more" if len(refused) > shown else ""
    return f"; left out {', '.join(names)}{more} (didn't pass the checks)"


class Autopilot:
    def __init__(self, store, boards, settings_store, jobs, calls: dict):
        self.store, self.boards, self.settings_store, self.jobs, self.calls = store, boards, settings_store, jobs, calls

    # Starting and reading

    def start(self, project_id: str, dashboard_name: str | None = None) -> dict:
        try:
            project = self.store.get(project_id)
        except NotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        if not self.settings_store.load().api_key:
            raise HTTPException(400, "The AI isn't set up yet. Ask an admin to add an API key in Settings.")
        if not project["sources"]:
            raise HTTPException(400, "Add some data first: upload a file or connect a sheet.")
        if self.jobs.busy(f"autopilot:{project_id}"):
            raise HTTPException(409, "Autopilot is already working on this dataset.")
        run = {
            "id": new_id(), "status": "running", "started_at": now(), "finished_at": None, "error": None,
            "fix_at": None, "dashboard_id": None, "dashboard_name": dashboard_name or f"{project['name']} dashboard",
            "steps": [{"key": key, "label": label, "status": "pending", "detail": ""} for key, label in STEPS],
        }
        with self.store.edit(project_id) as fresh:
            fresh["autopilot"] = run
        self.jobs.submit(f"autopilot:{project_id}", lambda: self.run(project_id))
        return run

    def view(self, project_id: str) -> dict | None:
        try:
            run = self.store.get(project_id).get("autopilot")
        except NotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        if run and run["status"] == "running" and not self.jobs.busy(f"autopilot:{project_id}"):
            # The server restarted mid-run: say so rather than spin for ever.
            run = {**run, "status": "stopped", "error": "Autopilot was interrupted (the app restarted). Start it again."}
        return run

    # The run

    def run(self, project_id: str) -> None:
        for key, _ in STEPS:
            self._step(project_id, key, "running")
            try:
                detail = getattr(self, f"_{key}")(project_id)
            except Exception as exc:  # noqa: BLE001  every failure ends the run with a plain message
                if not isinstance(exc, (Stop, HTTPException)):
                    log.exception("Autopilot step %s failed", key)
                text = message_of(exc)
                self._step(project_id, key, "failed", text)
                self._finish(project_id, "failed", error=text, fix_at=FIX_AT[key])
                return
            self._step(project_id, key, "done", detail)
        self._finish(project_id, "done")

    def _step(self, project_id: str, key: str, status: str, detail: str = "") -> None:
        with self.store.edit(project_id) as project:
            for step in project["autopilot"]["steps"]:
                if step["key"] == key:
                    step["status"], step["detail"] = status, detail or step["detail"]

    def _finish(self, project_id: str, status: str, **extra) -> None:
        with self.store.edit(project_id) as project:
            project["autopilot"].update(status=status, finished_at=now(), **extra)

    def _set(self, project_id: str, **values) -> None:
        with self.store.edit(project_id) as project:
            project["autopilot"].update(values)

    # The steps

    def _read(self, project_id: str) -> str:
        project = self.store.get(project_id)
        described, missed = 0, []
        for source in project["sources"]:
            if source.get("ai"):
                continue
            try:
                self.calls["describe"](project_id, source["id"])
                described += 1
            except HTTPException as exc:  # a description helps but isn't needed to go on
                missed.append(f"{source['table']} ({message_of(exc)})")
        tables = len(project["sources"])
        text = f"Looked at {plural(tables, 'table', 'tables')}"
        return text + (f"; couldn't describe {', '.join(missed)}" if missed else "")

    def _ids(self, project_id: str) -> dict:
        project = self.store.get(project_id)
        current = model.state(project)
        return {
            "steps": {s["id"] for q in transform.state(project)["queries"] for s in q["steps"]},
            "relationships": {r["id"] for r in current["relationships"]},
            "measures": {m["id"] for m in current["measures"]},
        }

    def _clean(self, project_id: str) -> str:
        with self.store.edit(project_id) as project:  # a table's query is made when first needed; save them first
            queries = [dict(q) for q in transform.state(project)["queries"] if not q.get("error")]
        before = self._ids(project_id)["steps"]
        if not queries:
            raise Stop("None of the tables could be read. Check the files in Get Data.")
        notes, refused = [], []
        for query in queries:
            try:
                refused += self.calls["ai_clean"](project_id, query["id"]).get("rejected") or []
            except HTTPException as exc:
                notes.append(f"{query['name']}: {message_of(exc)}")
        self.calls["approve_steps"](project_id)
        added = self._mark_steps(project_id, before)
        tables = len({qid for qid, _ in added})
        text = f"Added {plural(len(added), 'step', 'steps')} to {plural(tables, 'table', 'tables')}" if added else \
            "The tables were already clean"
        text += left_out(refused)
        if notes:
            text += "; " + "; ".join(notes)
        return text

    def _mark_steps(self, project_id: str, before: set) -> list[tuple[str, str]]:
        added = []
        with self.store.edit(project_id) as project:
            for query in transform.state(project)["queries"]:
                for step in query["steps"]:
                    if step["id"] not in before:
                        step["by"] = "autopilot"
                        added.append((query["id"], step["id"]))
                if query.get("base_query") and query.get("status") and not query.get("by"):
                    query["by"] = "autopilot"
        return added

    def _model(self, project_id: str) -> str:
        before = self._ids(project_id)
        refused = self.calls["ai_model"](project_id).get("refused") or []
        self.calls["approve_model"](project_id)
        with self.store.edit(project_id) as project:
            current = model.state(project)
            relationships = [r for r in current["relationships"] if r["id"] not in before["relationships"]]
            measures = [m for m in current["measures"] if m["id"] not in before["measures"]]
            for item in relationships + measures:
                item["by"] = "autopilot"
            if current.get("date_table") and current["date_table"].get("reason") is not None:
                current["date_table"].setdefault("by", "autopilot")
            usable = [m for m in current["measures"] if not m.get("error")]
        if not usable:
            raise Stop("The AI couldn't find anything to measure in this data. Add a measure in the Model, then try again.")
        text = f"{plural(len(relationships), 'relationship', 'relationships')}, {plural(len(measures), 'measure', 'measures')}"
        return text + left_out(refused)

    def _publish(self, project_id: str) -> str:
        self.calls["publish"](project_id, True)
        version = (self.store.get(project_id).get("published") or {}).get("version")
        return f"Version {version} is live for dashboards" if version else "Published for dashboards"

    def _dashboard(self, project_id: str) -> str:
        run = self.store.get(project_id)["autopilot"]
        created = self.calls["create_dashboard"](run["dashboard_name"], project_id)
        dashboard_id = created["id"]
        self._set(project_id, dashboard_id=dashboard_id)
        self.calls["ai_report"](dashboard_id)
        self.calls["approve_report"](dashboard_id)
        with self.boards.edit(dashboard_id) as dash:
            pages = dash["draft"]["pages"]
            visuals = 0
            for page in pages:
                page["by"] = "autopilot"
                for visual in page["visuals"]:
                    visual["by"] = "autopilot"
                    visuals += 1
        if not pages:
            raise Stop("The AI couldn't design any pages from this model. Open the dashboard and add visuals by hand.")
        return f"{plural(len(pages), 'page', 'pages')} with {plural(visuals, 'visual', 'visuals')}, kept as a draft"


class StartBody(BaseModel):
    dashboard_name: str | None = None


def build_router(pilot: Autopilot) -> APIRouter:
    router = APIRouter(prefix="/api/datasets/{project_id}/autopilot")

    @router.post("")
    def start(project_id: str, body: StartBody | None = None):
        return pilot.start(project_id, body.dashboard_name if body else None)

    @router.get("")
    def view(project_id: str):
        return {"run": pilot.view(project_id)}

    return router
