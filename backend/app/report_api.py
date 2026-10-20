"""HTTP routes for the Report stage."""

from typing import Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import ai_report, llm, model, report, transform
from .projects import NotFound, ProjectStore
from .settings import ProviderSettings, SettingsStore


class PageBody(BaseModel):
    title: str
    question: str = ""


class VisualBody(BaseModel):
    type: str
    title: str
    measures: list[str]
    group_by: list[dict] = []
    kpi: dict = {}
    width: str | None = None


def build_router(
    store: ProjectStore,
    settings_store: SettingsStore,
    client_factory: Callable[[ProviderSettings], object],
) -> APIRouter:
    router = APIRouter(prefix="/api/projects/{project_id}/report")

    def respond(project_id: str, change=None, with_data: bool = True) -> dict:
        """Apply `change(project, con)` (if any) as one locked edit and return the report with its data."""
        try:
            with store.edit(project_id) as project, store.connect(project_id) as con:
                if transform.needs_apply(project):
                    transform.apply_all(con, project)
                model.revalidate(con, project)
                report.state(project)
                if change:
                    change(project, con)
                return view(con, project, with_data)
        except NotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except (report.ReportError, model.ModelError) as exc:
            raise HTTPException(400, str(exc)) from exc

    def view(con, project: dict, with_data: bool) -> dict:
        current = report.state(project)
        pages = []
        for page in current["pages"]:
            visuals = []
            for visual in page["visuals"]:
                entry = dict(visual)
                if with_data:
                    try:
                        entry["data"], entry["error"] = report.visual_data(con, project, visual), None
                    except (report.ReportError, model.ModelError) as exc:
                        entry["data"], entry["error"] = None, str(exc)
                    if entry["data"] and visual.get("insight"):
                        # Checked against today's data, so an insight that went stale after a refresh is flagged.
                        text = visual["insight"]["text"]
                        entry["insight"] = {"text": text, "check": report.check_insight(text, entry["data"])}
                visuals.append(entry)
            pages.append({**page, "visuals": visuals})
        measures = {m["name"]: m["format"] for m in model.state(project)["measures"]}
        return {"pages": pages, "formats": measures, "as_of": report.as_of(con, project),
                "can_undo": bool(current["history"])}

    def undoable(action):
        def change(project, con):
            report.snapshot(project)
            action(project, con)
        return change

    @router.get("")
    def get_report(project_id: str):
        return respond(project_id)

    @router.post("/pages")
    def add_page(project_id: str, body: PageBody):
        return respond(project_id, undoable(lambda p, con: report.add_page(p, body.title, body.question)))

    @router.delete("/pages/{page_id}")
    def remove_page(project_id: str, page_id: str):
        return respond(project_id, undoable(lambda p, con: report.state(p)["pages"].remove(report.find_page(p, page_id))))

    @router.post("/pages/{page_id}/visuals")
    def add_visual(project_id: str, page_id: str, body: VisualBody):
        return respond(project_id, undoable(lambda p, con: report.add_visual(con, p, page_id, body.model_dump(), "Approved")))

    @router.delete("/visuals/{visual_id}")
    def remove_visual(project_id: str, visual_id: str):
        def change(p, con):
            page, visual = report.find_visual(p, visual_id)
            page["visuals"].remove(visual)
        return respond(project_id, undoable(change))

    @router.post("/approve")
    def approve(project_id: str, item_id: str | None = None):
        """Approve one page or visual by id, or everything the AI added."""
        def change(p, con):
            for page in report.state(p)["pages"]:
                for entry in [page] + page["visuals"]:
                    if entry.get("status") == "AI" and (item_id is None or entry["id"] == item_id):
                        entry["status"] = "Approved"
        return respond(project_id, undoable(change))

    @router.post("/undo")
    def undo(project_id: str):
        return respond(project_id, lambda p, con: report.undo(p))

    @router.post("/ai")
    def ai_report_pass(project_id: str):
        settings = settings_store.load()
        if not settings.api_key:
            raise HTTPException(400, "Add an API key in Settings to let the AI build the report.")
        respond(project_id, with_data=False)  # bring Transform and Model up to date first
        project = store.get(project_id)
        if not [m for m in model.state(project)["measures"] if not m.get("error")]:
            raise HTTPException(400, "Build the model first: the report needs measures.")
        client = client_factory(settings)
        try:
            with store.connect(project_id) as con:
                proposal = ai_report.design(client, settings.model, settings.capabilities, con, project)
        except llm.AIError as exc:
            raise HTTPException(502, f"AI report design failed: {exc}") from exc

        result = {}

        def add(p, con):
            result.update(ai_report.apply_design(con, p, proposal))

        respond(project_id, undoable(add), with_data=False)
        page_ids = [p["id"] for p in result["pages"]]
        if page_ids:
            # Insights are written from the stored pages' data, then saved onto them.
            project = store.get(project_id)
            pages = [report.find_page(project, pid) for pid in page_ids]
            try:
                with store.connect(project_id) as con:
                    ai_report.write_insights(client, settings.model, settings.capabilities, con, project, pages)
            except llm.AIError as exc:
                result["refused"].append({"kind": "insights", "label": "Insights", "error": f"AI insights failed: {exc}"})
            else:
                written = {p["id"]: p for p in pages}

                def save(p, con):
                    for page in report.state(p)["pages"]:
                        if page["id"] in written:
                            fresh = written[page["id"]]
                            page["summary"] = fresh["summary"]
                            insights = {v["id"]: v.get("insight") for v in fresh["visuals"]}
                            for visual in page["visuals"]:
                                if visual["id"] in insights:
                                    visual["insight"] = insights[visual["id"]]

                respond(project_id, save, with_data=False)
        body = respond(project_id)
        return {"summary": result["summary"], "added": result["added"], "refused": result["refused"],
                "new_pages": page_ids, **body}

    return router
