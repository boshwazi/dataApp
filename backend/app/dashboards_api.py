"""HTTP routes for dashboards and their report pages.

A dashboard's pages always read its dataset's published copy, opened read-only.
"""

from contextlib import contextmanager
from typing import Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import activity, ai_report, dashboards, llm, model, publishing, report
from .dashboards import DashboardStore
from .projects import NotFound, ProjectStore
from .settings import ProviderSettings, SettingsStore


class NewDashboardBody(BaseModel):
    name: str
    dataset_id: str


class NameBody(BaseModel):
    name: str


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


def summary(dashboard: dict, datasets: ProjectStore) -> dict:
    try:
        dataset = datasets.get(dashboard["dataset_id"])
        data = {"id": dataset["id"], "name": dataset["name"], **publishing.draft_status(dataset)}
    except NotFound:
        data = None
    return {
        "id": dashboard["id"],
        "name": dashboard["name"],
        "updated_at": dashboard["updated_at"],
        "pages": len(dashboard["draft"]["pages"]),
        "dataset": data,
        **dashboards.status(dashboard),
    }


def build_router(
    datasets: ProjectStore,
    boards: DashboardStore,
    settings_store: SettingsStore,
    client_factory: Callable[[ProviderSettings], object],
) -> APIRouter:
    router = APIRouter(prefix="/api/dashboards")

    @contextmanager
    def opened(dashboard: dict):
        """The dashboard's dataset as published, and a read-only connection to its data."""
        try:
            dataset = datasets.get(dashboard["dataset_id"])
        except NotFound as exc:
            raise HTTPException(409, "This dashboard's dataset was removed.") from exc
        frozen = publishing.published_project(dataset)
        if frozen is None:
            yield dataset, None, None
            return
        with datasets.connect_published(dataset["id"], dataset["published"]["file"]) as con:
            yield dataset, frozen, con

    def respond(dashboard_id: str, change=None, side: str = "draft", with_data: bool = True,
                summary: str | None = None, actor: str = "You") -> dict:
        """Apply `change(project, con)` to the draft (if any) as one locked edit, log it, then return the dashboard."""
        try:
            with boards.edit(dashboard_id) as dash, opened(dash) as (dataset, frozen, con):
                if dash.get("reviewed_version") is None:
                    activity.dashboard_reviewed(dash, dataset)  # first sight of the data counts as reviewed
                if change:
                    if frozen is None:
                        raise report.ReportError("Publish the dataset first: dashboards read its published version.")
                    change(publishing.bind(frozen, dash["draft"]), con)
                    activity.dashboard_reviewed(dash, dataset)
                    if summary:
                        activity.log(dash, "report", summary, actor)
                return view(dash, dataset, frozen, con, side, with_data)
        except NotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except (report.ReportError, model.ModelError) as exc:
            raise HTTPException(400, str(exc)) from exc

    def view(dash: dict, dataset: dict, frozen: dict | None, con, side: str, with_data: bool) -> dict:
        source = dash["draft"] if side == "draft" else (dash["published"] or {"pages": []})
        pages = []
        if frozen is not None:
            project = publishing.bind(frozen, {"pages": source["pages"], "history": []})
            for page in source["pages"]:
                visuals = []
                for visual in page["visuals"]:
                    entry = dict(visual)
                    if with_data:
                        try:
                            entry["data"], entry["error"] = report.visual_data(con, project, visual), None
                        except (report.ReportError, model.ModelError) as exc:
                            entry["data"], entry["error"] = None, str(exc)
                        if entry["data"] and visual.get("insight"):
                            # Checked against today's data, so an insight that went stale is flagged.
                            text = visual["insight"]["text"]
                            entry["insight"] = {"text": text, "check": report.check_insight(text, entry["data"])}
                    visuals.append(entry)
                pages.append({**page, "visuals": visuals})
        formats = {m["name"]: m["format"] for m in model.state(frozen)["measures"]} if frozen else {}
        return {
            **summary(dash, datasets),
            "dataset_updated_to": activity.dashboard_stale(dash, dataset),
            "side": side,
            "pages": pages,
            "formats": formats,
            "as_of": report.as_of(con, frozen) if frozen else None,
            "can_undo": bool(dash["draft"]["history"]),
        }

    def undoable(action):
        def change(project, con):
            report.snapshot(project)
            action(project, con)
        return change

    # Dashboards

    @router.get("")
    def list_dashboards():
        return [summary(d, datasets) for d in boards.all()]

    @router.post("")
    def create(body: NewDashboardBody):
        try:
            datasets.get(body.dataset_id)
        except NotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        return summary(boards.create(body.name, body.dataset_id), datasets)

    @router.get("/{dashboard_id}")
    def get_dashboard(dashboard_id: str, side: str = "draft"):
        if side not in ("draft", "published"):
            raise HTTPException(400, "side is draft or published.")
        return respond(dashboard_id, side=side)

    @router.patch("/{dashboard_id}")
    def rename(dashboard_id: str, body: NameBody):
        try:
            boards.rename(dashboard_id, body.name)
        except NotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        return respond(dashboard_id)

    @router.delete("/{dashboard_id}")
    def delete(dashboard_id: str):
        try:
            boards.delete(dashboard_id)
        except NotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"deleted": dashboard_id}

    @router.post("/{dashboard_id}/publish")
    def publish(dashboard_id: str):
        """Make the draft pages what viewers see. Refused while any visual is broken."""
        def check_and_publish(project, con):
            broken = [
                f"{page['title']} / {visual['title']}: {exc}"
                for page in report.state(project)["pages"]
                for visual in page["visuals"]
                for exc in [visual_error(con, project, visual)]
                if exc
            ]
            if broken:
                raise report.ReportError("Fix or remove these visuals before publishing: " + "; ".join(broken))

        try:
            with boards.edit(dashboard_id) as dash, opened(dash) as (dataset, frozen, con):
                if frozen is None:
                    raise report.ReportError("Publish the dataset first.")
                if not dash["draft"]["pages"]:
                    raise report.ReportError("Add at least one page before publishing.")
                check_and_publish(publishing.bind(frozen, dash["draft"]), con)
                dashboards.publish(dash, dataset)
                activity.log(dash, "publish", "Published the dashboard")
        except NotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except (report.ReportError, model.ModelError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return respond(dashboard_id)

    def visual_error(con, project, visual) -> str | None:
        try:
            report.visual_data(con, project, visual)
        except (report.ReportError, model.ModelError) as exc:
            return str(exc)
        return None

    @router.get("/{dashboard_id}/activity")
    def get_activity(dashboard_id: str):
        try:
            return {"entries": list(reversed(boards.get(dashboard_id).get("activity", [])))}
        except NotFound as exc:
            raise HTTPException(404, str(exc)) from exc

    @router.post("/{dashboard_id}/reviewed")
    def mark_reviewed(dashboard_id: str):
        """The creator has checked the report against the newly published dataset."""
        def change(p, con):
            pass
        return respond(dashboard_id, change, summary="Reviewed the report against the updated dataset")

    # Report pages (the draft)

    @router.post("/{dashboard_id}/pages")
    def add_page(dashboard_id: str, body: PageBody):
        return respond(dashboard_id, undoable(lambda p, con: report.add_page(p, body.title, body.question)),
                       summary=f"Added page '{body.title}'")

    @router.delete("/{dashboard_id}/pages/{page_id}")
    def remove_page(dashboard_id: str, page_id: str):
        return respond(dashboard_id, undoable(lambda p, con: report.state(p)["pages"].remove(report.find_page(p, page_id))),
                       summary="Removed a page")

    @router.post("/{dashboard_id}/pages/{page_id}/visuals")
    def add_visual(dashboard_id: str, page_id: str, body: VisualBody):
        return respond(dashboard_id, undoable(lambda p, con: report.add_visual(con, p, page_id, body.model_dump(), "Approved")),
                       summary=f"Added {body.type} '{body.title}'")

    @router.delete("/{dashboard_id}/visuals/{visual_id}")
    def remove_visual(dashboard_id: str, visual_id: str):
        def change(p, con):
            page, visual = report.find_visual(p, visual_id)
            page["visuals"].remove(visual)
        return respond(dashboard_id, undoable(change), summary="Removed a visual")

    @router.post("/{dashboard_id}/approve")
    def approve(dashboard_id: str, item_id: str | None = None):
        """Approve one page or visual by id, or everything the AI added."""
        def change(p, con):
            for page in report.state(p)["pages"]:
                for entry in [page] + page["visuals"]:
                    if entry.get("status") == "AI" and (item_id is None or entry["id"] == item_id):
                        entry["status"] = "Approved"
        return respond(dashboard_id, undoable(change), summary="Approved AI changes")

    @router.post("/{dashboard_id}/undo")
    def undo(dashboard_id: str):
        return respond(dashboard_id, lambda p, con: report.undo(p), summary="Undid the last change")

    @router.post("/{dashboard_id}/ai")
    def ai_report_pass(dashboard_id: str):
        settings = settings_store.load()
        if not settings.api_key:
            raise HTTPException(400, "Add an API key in Settings to let the AI build the report.")
        try:
            dash = boards.get(dashboard_id)
        except NotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        client = client_factory(settings)
        # The AI calls run without holding the dashboard lock; their results are applied in locked edits.
        with opened(dash) as (dataset, frozen, con):
            if frozen is None:
                raise HTTPException(400, "Publish the dataset first: dashboards read its published version.")
            if not [m for m in model.state(frozen)["measures"] if not m.get("error")]:
                raise HTTPException(400, "Build the model first: the report needs measures.")
            try:
                proposal = ai_report.design(client, settings.model, settings.capabilities, con,
                                            publishing.bind(frozen, dash["draft"]))
            except llm.AIError as exc:
                raise HTTPException(502, f"AI report design failed: {exc}") from exc

        result = {}

        def add(p, con):
            result.update(ai_report.apply_design(con, p, proposal))

        respond(dashboard_id, undoable(add), with_data=False, summary="AI designed report pages", actor="AI")
        page_ids = [p["id"] for p in result["pages"]]
        if page_ids:
            dash = boards.get(dashboard_id)
            with opened(dash) as (dataset, frozen, con):
                project = publishing.bind(frozen, dash["draft"])
                pages = [report.find_page(project, pid) for pid in page_ids]
                try:
                    ai_report.write_insights(client, settings.model, settings.capabilities, con, project, pages)
                except llm.AIError as exc:
                    result["refused"].append({"kind": "insights", "label": "Insights", "error": f"AI insights failed: {exc}"})
                    pages = []
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

            if written:
                respond(dashboard_id, save, with_data=False)
        body = respond(dashboard_id)
        return {"summary": result["summary"], "added": result["added"], "refused": result["refused"],
                "new_pages": page_ids, **body}

    return router
