"""HTTP routes for dashboards and their report pages.

A dashboard's pages always read its dataset's published copy, opened read-only.
"""

import copy
import json
import os
import threading
from collections import OrderedDict
from collections.abc import Callable
from contextlib import contextmanager

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from . import (
    activity,
    ai_report,
    ask,
    dashboards,
    digests,
    export,
    guidance,
    llm,
    mail,
    model,
    notices,
    pbi_dax,
    pbi_export,
    publishing,
    report,
    security,
    templates,
    workspaces,
)
from .dashboards import DashboardStore
from .projects import NotFound, ProjectStore
from .settings import ProviderSettings, SettingsStore


class NewDashboardBody(BaseModel):
    name: str
    dataset_id: str


class DigestBody(BaseModel):
    frequency: str  # off, weekly, monthly
    day: int = 0  # weekly: 0 Monday to 6 Sunday
    at: str = "08:00"  # Malaysia time
    people: list[str] = []
    groups: list[str] = []


class SharingBody(BaseModel):
    mode: str  # "workspace" or "people"
    people: list[str] = []
    groups: list[str] = []


class DesignBody(BaseModel):
    template: str | None = None  # a template id (templates.py)
    description: str | None = None  # what the user wants, in their words
    choose: bool = False  # let the AI pick the template that fits the data


class NameBody(BaseModel):
    name: str


class PageBody(BaseModel):
    title: str
    question: str = ""


class VisualPatch(BaseModel):
    type: str | None = None
    title: str | None = None
    measures: list[str] | None = None
    group_by: list[dict] | None = None
    kpi: dict | None = None


class InstructionsBody(BaseModel):
    instructions: str = ""


class LayoutBody(BaseModel):
    positions: dict[str, dict]


class SlicersBody(BaseModel):
    slicers: list[dict]


class FiltersBody(BaseModel):
    filters: list[dict]


class AskBody(BaseModel):
    question: str
    history: list[dict] = []
    side: str = "published"
    page_id: str | None = None
    filters: list[dict] = []


class VisualBody(BaseModel):
    type: str
    title: str
    measures: list[str]
    group_by: list[dict] = []
    kpi: dict = {}
    width: str | None = None
    layout: dict | None = None


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
        "owner": dashboard.get("owner"),
        "sharing": dashboard.get("sharing") or {"mode": "workspace"},
        "preview": preview(dashboard),
        **dashboards.status(dashboard),
    }


def preview(dashboard: dict) -> dict | None:
    """The first page's shape for a card on Home: each visual's kind and place, never its data. The published
    page when there is one (what viewers see), otherwise the draft."""
    pages = (dashboard.get("published") or dashboard["draft"]).get("pages") or []
    if not pages:
        return None
    first = pages[0]
    return {"title": first.get("title", ""), "visuals": [
        {"id": v["id"], "type": v["type"], "layout": v.get("layout"), "width": v.get("width"),
         "insight": bool(v.get("insight"))} for v in first.get("visuals", [])[:24]]}


class ResultCache:
    """Visual results, by what they depend on: the published version's data file (never changed once written),
    the visual's definition and the filters in force. Least recently used results go first.
    (Row-level security, M16, adds who is looking to the key.)"""

    UNUSED = ("insight", "layout", "status", "reason", "by", "added_at", "title", "data", "error", "advice")

    def __init__(self, size: int = 512):
        self.size, self.items, self.lock = size, OrderedDict(), threading.Lock()

    def key(self, dataset: dict, visual: dict, filters: list) -> str:
        spec = {k: v for k, v in visual.items() if k not in self.UNUSED}
        return json.dumps([dataset["workspace_id"], dataset["id"], dataset["published"]["file"], spec, filters],
                          sort_keys=True, default=str)

    def get(self, key: str):
        with self.lock:
            if key in self.items:
                self.items.move_to_end(key)
                return copy.deepcopy(self.items[key])
        return None

    def put(self, key: str, value) -> None:
        with self.lock:
            self.items[key] = copy.deepcopy(value)
            self.items.move_to_end(key)
            while len(self.items) > self.size:
                self.items.popitem(last=False)


def build_router(
    datasets: ProjectStore,
    boards: DashboardStore,
    settings_store: SettingsStore,
    client_factory: Callable[[ProviderSettings], object],
    member_emails: Callable[[], list[str] | None] = lambda: None,
    mailer_factory: Callable[[], "mail.Mailer | None"] = lambda: mail.Mailer.from_env(),
) -> APIRouter:
    router = APIRouter(prefix="/api/dashboards")
    results = ResultCache()

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
        try:
            # Row-level security: whoever is asking sees only their rows, on every path through here.
            security.restrict(frozen, dataset, groups())
        except security.SecurityError as exc:
            raise HTTPException(400, str(exc)) from exc
        with datasets.connect_published(dataset["id"], dataset["published"]["file"]) as con:
            yield dataset, frozen, con

    def groups() -> list[dict]:
        return security.groups(getattr(datasets, "preferences", None))

    def visible(dashboard_id: str) -> dict:
        """The dashboard, if the asker may see it (viewers: published and shared with them)."""
        try:
            dash = boards.get(dashboard_id)
        except NotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        if not security.can_see(dash, groups()):
            raise HTTPException(404, "Dashboard not found.")
        return dash

    def view_as(as_role: str | None, as_email: str | None) -> None:
        """A creator checking what a role sees: ?as_role=<role id>&as_email=<person, for per-person roles>."""
        if as_role:
            if not workspaces.current().at_least("creator"):
                raise HTTPException(403, "Only creators can view as a role.")
            security.VIEW_AS.set({"role": as_role, "email": as_email})

    def respond(dashboard_id: str, change=None, side: str = "draft", with_data: bool = True,
                summary: str | None = None, actor: str = "You", selection: tuple | None = None,
                drill: dict | None = None) -> dict:
        """Apply `change(project, con)` to the draft (if any) as one locked edit, log it, then return the dashboard.

        Without a change it only reads, so viewers never write: only a creator's first look is recorded."""
        try:
            if change is None:
                dash = boards.get(dashboard_id)
                if dash.get("reviewed_version") is not None or not workspaces.current().at_least("creator"):
                    with opened(dash) as (dataset, frozen, con):
                        return view(dash, dataset, frozen, con, side, with_data, selection, drill)
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
                return view(dash, dataset, frozen, con, side, with_data, selection, drill)
        except NotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except (report.ReportError, model.ModelError) as exc:
            raise HTTPException(400, str(exc)) from exc

    def view(dash: dict, dataset: dict, frozen: dict | None, con, side: str, with_data: bool,
             selection: tuple | None = None, drill: dict | None = None) -> dict:
        source = dash["draft"] if side == "draft" else (dash["published"] or {"pages": []})
        pages = []
        # Insights and summaries were written about all the data: not for someone who sees only some of it.
        restricted = frozen is not None and frozen.get(model.ROLE_FILTERS) is not None
        if frozen is not None:
            project = publishing.bind(frozen, {"pages": source["pages"], "history": []})
            for page in source["pages"]:
                # The page's own filters for everyone; a viewer's slicer choices on the page they are looking at.
                chosen = selection[1] if selection and selection[0] == page["id"] else []
                visuals = []
                for visual in page["visuals"]:
                    # A choice made by clicking a visual filters the rest of the page, not that visual itself.
                    project[model.PAGE_FILTERS] = list(page.get("filters") or []) + [
                        f for f in chosen if f.get("source") != visual["id"]]
                    shown = visual
                    if drill and visual["id"] in drill:
                        shown = report.drilled(visual, drill[visual["id"]])
                        project[model.PAGE_FILTERS] += drill[visual["id"]]["filters"]
                    entry = {**visual, "group_by": shown["group_by"], "drill": (drill or {}).get(visual["id"]),
                             "drill_next": report.next_level(shown)}
                    if with_data:
                        try:
                            key = results.key(dataset, shown, [project[model.PAGE_FILTERS], frozen.get(model.ROLE_FILTERS)])
                            data = results.get(key)
                            if data is None:
                                data = report.visual_data(con, project, shown)
                                results.put(key, data)
                            entry["data"], entry["error"] = data, None
                            entry["advice"] = report.advice(visual, entry["data"])
                        except (report.ReportError, model.ModelError) as exc:
                            entry["data"], entry["error"] = None, str(exc)
                        if chosen or entry["drill"] or restricted:
                            entry["insight"] = None  # written about the whole page, not this selection
                        elif entry["data"] and visual.get("insight"):
                            # Checked against today's data, so an insight that went stale is flagged.
                            text = visual["insight"]["text"]
                            entry["insight"] = {"text": text, "check": report.check_insight(text, entry["data"]),
                                                "stale": bool(visual["insight"].get("stale"))}  # new data changed it
                    visuals.append(entry)
                pages.append({**page, "slicers": page.get("slicers", []), "filters": page.get("filters", []),
                              "selected": chosen, "visuals": visuals, **({"summary": ""} if restricted else {})})
            project.pop(model.PAGE_FILTERS, None)
        formats = {m["name"]: m["format"] for m in model.state(frozen)["measures"]} if frozen else {}
        fields = report.fields(con, frozen) if frozen and side == "draft" and not viewer() else []
        return {
            **summary(dash, datasets),
            "dataset_updated_to": activity.dashboard_stale(dash, dataset),
            "side": side,
            "pages": pages,
            "formats": formats,
            "fields": fields,
            "as_of": report.as_of(con, frozen) if frozen else None,
            "can_undo": bool(dash["draft"]["history"]),
            "instructions": dash.get("instructions", ""),
            "restricted": restricted,
            "viewing_as": security.VIEW_AS.get(),
        }

    def undoable(action):
        def change(project, con):
            report.snapshot(project)
            action(project, con)
        return change

    # Dashboards

    def viewer() -> bool:
        return not workspaces.current().at_least("creator")

    @router.get("")
    def list_dashboards():
        # Viewers only ever see published dashboards shared with them.
        everyone = groups()
        return [summary(d, datasets) for d in boards.all() if security.can_see(d, everyone)]

    @router.post("")
    def create(body: NewDashboardBody):
        try:
            datasets.get(body.dataset_id)
        except NotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        with boards.edit(boards.create(body.name, body.dataset_id)["id"]) as dash:
            dash["owner"] = workspaces.current_user().email
        return summary(dash, datasets)

    @router.put("/{dashboard_id}/sharing")
    def set_sharing(dashboard_id: str, body: SharingBody):
        """Who sees the published dashboard: the whole workspace (the default), or chosen people and groups."""
        try:
            sharing = security.check_sharing(body.model_dump())
            with boards.edit(dashboard_id) as dash:
                dash["sharing"] = sharing
        except security.SecurityError as exc:
            raise HTTPException(400, str(exc)) from exc
        except NotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        who = "the whole workspace" if sharing["mode"] == "workspace" else \
            ", ".join(sharing["people"] + [g["name"] for g in groups() if g["id"] in sharing["groups"]]) or "nobody yet"
        preferences = getattr(datasets, "preferences", None)
        security.audit(preferences, "sharing", f"{dash['name']}: shared with {who}")
        if sharing["mode"] == "people":
            people = list(sharing["people"])
            for group in groups():
                if group["id"] in sharing["groups"]:
                    people += [m for m in group["members"] if m not in people]
            notices.notify(preferences, "shared", f"{dash['name']} was shared with you",
                           link=f"/dashboards/{dashboard_id}", to=people)
        return summary(dash, datasets)

    @router.get("/{dashboard_id}/powerbi")
    def export_powerbi(dashboard_id: str):
        """The dashboard as a Power BI project: its dataset's published model and data, and its pages as report pages."""
        if viewer():
            raise HTTPException(403, "Only creators can download a dashboard for Power BI.")
        dash = visible(dashboard_id)
        with opened(dash) as (dataset, frozen, con):
            if frozen is None:
                raise HTTPException(400, "Publish the dataset first.")
            pages = (dash.get("published") or dash["draft"])["pages"]
            try:
                name, files = pbi_export.build(con, frozen, pages, title=dash["name"])
            except (model.ModelError, pbi_dax.Untranslatable) as exc:
                raise HTTPException(400, str(exc)) from exc
        return Response(pbi_export.zip_project(name, files), media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{name}.zip"'})

    # Digests

    @router.get("/{dashboard_id}/digest")
    def get_digest(dashboard_id: str):
        dash = visible(dashboard_id)
        return {**digests.settings(dash), "email": mailer_factory() is not None,
                "recipients": digests.recipients(dash, groups(), member_emails())}

    @router.put("/{dashboard_id}/digest")
    def set_digest(dashboard_id: str, body: DigestBody):
        """Email (and notify) chosen people with the dashboard's headline numbers, weekly or monthly."""
        try:
            with boards.edit(dashboard_id) as dash:
                digests.configure(dash, body.model_dump(), groups())
        except digests.DigestError as exc:
            raise HTTPException(400, str(exc)) from exc
        except NotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        return get_digest(dashboard_id)

    @router.post("/{dashboard_id}/digest/send")
    def send_digest_now(dashboard_id: str, request: Request):
        """Send the digest now (to check it), without moving the schedule."""
        return send_digest(dashboard_id, str(request.base_url).rstrip("/"), reschedule=False)

    def send_digest(dashboard_id: str, site: str | None = None, reschedule: bool = True) -> dict:
        dash = visible(dashboard_id)
        if not dash.get("published"):
            raise HTTPException(400, "Publish the dashboard first.")
        to = digests.recipients(dash, groups(), member_emails())
        if not to:
            raise HTTPException(400, "Nobody to send it to: choose people the dashboard is shared with.")
        site = (os.getenv("PUBLIC_URL", "").rstrip("/") or site or "http://localhost:8000")
        link = f"{site}/dashboards/{dashboard_id}"
        with opened(dash) as (dataset, frozen, con):
            if frozen is None:
                raise HTTPException(400, "This dashboard's data isn't published yet.")
            content = digests.compose(con, publishing.bind(frozen, dash["published"]), dash, dataset, link)
        mailer, sent, failed = mailer_factory(), [], []
        for person in to:
            if mailer is None:
                continue
            try:
                mailer.send(person, content["subject"], content["text"], content["html"])
                sent.append(person)
            except mail.MailError as exc:
                failed.append(str(exc))
        notices.notify(getattr(datasets, "preferences", None), "digest", content["subject"],
                       text=content["text"].split("\n\nOpen it:")[0].split("\n\n", 1)[-1], link=f"/dashboards/{dashboard_id}", to=to)
        if reschedule:
            with boards.edit(dashboard_id) as fresh:
                digests.scheduled(fresh)
        return {"to": to, "emailed": sent, "failed": failed, "email": mailer is not None, **content}

    @router.get("/{dashboard_id}")
    def get_dashboard(dashboard_id: str, side: str = "draft", page: str | None = None, filters: str | None = None,
                      drill: str | None = None, as_role: str | None = None, as_email: str | None = None):
        if side not in ("draft", "published"):
            raise HTTPException(400, "side is draft or published.")
        visible(dashboard_id)
        view_as(as_role, as_email)
        if viewer():
            side = "published"
        selection = None
        if page and filters:
            try:
                chosen = json.loads(filters)
            except ValueError as exc:
                raise HTTPException(400, "filters must be JSON.") from exc
            selection = (page, checked_filters(dashboard_id, side, chosen))
        drills = None
        if drill:
            try:
                wanted = json.loads(drill)
            except ValueError as exc:
                raise HTTPException(400, "drill must be JSON.") from exc
            if not isinstance(wanted, dict):
                raise HTTPException(400, "drill maps visual ids to a level and filters.")
            drills = {vid: {"column": d.get("column"), "filters": checked_filters(dashboard_id, side, d.get("filters") or [])}
                      for vid, d in wanted.items() if isinstance(d, dict)}
        return respond(dashboard_id, side=side, selection=selection, drill=drills)

    def checked_filters(dashboard_id: str, side: str, chosen) -> list[dict]:
        """A viewer's slicer choices, checked against the dataset; nothing is saved."""
        try:
            dash = boards.get(dashboard_id)
        except NotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        with opened(dash) as (dataset, frozen, con):
            if frozen is None:
                return []
            try:
                return report.check_filters(con, frozen, chosen)
            except (report.ReportError, model.ModelError) as exc:
                raise HTTPException(400, str(exc)) from exc

    @router.get("/{dashboard_id}/values")
    def slicer_values(dashboard_id: str, table: str, column: str, as_role: str | None = None, as_email: str | None = None):
        """The values a slicer offers, from the dataset's published version (and the asker's rows)."""
        dash = visible(dashboard_id)
        view_as(as_role, as_email)
        with opened(dash) as (dataset, frozen, con):
            if frozen is None:
                raise HTTPException(400, "This dashboard's data isn't published yet.")
            try:
                return report.slicer_values(con, frozen, table, column)
            except (report.ReportError, model.ModelError) as exc:
                raise HTTPException(400, str(exc)) from exc

    @router.put("/{dashboard_id}/instructions")
    def set_instructions(dashboard_id: str, body: InstructionsBody):
        """What every AI working on this dashboard should know or follow (added to its system prompt)."""
        try:
            text = guidance.clean(body.instructions)
            with boards.edit(dashboard_id) as dash:
                dash["instructions"] = text
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except NotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        return respond(dashboard_id)

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
                again = bool(dash.get("published"))
                dashboards.publish(dash, dataset)
                activity.log(dash, "publish", "Published the dashboard")
            notices.notify(getattr(datasets, "preferences", None), "published",
                           f"{dash['name']} was {'updated' if again else 'published'}",
                           link=f"/dashboards/{dashboard_id}", to=notices.EVERYONE, dashboard_id=dashboard_id)
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

    @router.patch("/{dashboard_id}/pages/{page_id}")
    def rename_page(dashboard_id: str, page_id: str, body: PageBody):
        return respond(dashboard_id, undoable(lambda p, con: report.rename_page(p, page_id, body.title)),
                       summary=f"Renamed a page to '{body.title}'")

    @router.put("/{dashboard_id}/pages/{page_id}/slicers")
    def set_slicers(dashboard_id: str, page_id: str, body: SlicersBody):
        return respond(dashboard_id, undoable(lambda p, con: report.set_slicers(con, p, page_id, body.slicers)),
                       summary="Changed the page's slicers")

    @router.put("/{dashboard_id}/pages/{page_id}/filters")
    def set_filters(dashboard_id: str, page_id: str, body: FiltersBody):
        return respond(dashboard_id, undoable(lambda p, con: report.set_page_filters(con, p, page_id, body.filters)),
                       summary="Changed the page's filters")

    @router.put("/{dashboard_id}/pages/{page_id}/layout")
    def set_layout(dashboard_id: str, page_id: str, body: LayoutBody):
        return respond(dashboard_id, undoable(lambda p, con: report.set_layout(p, page_id, body.positions)))

    @router.patch("/{dashboard_id}/visuals/{visual_id}")
    def update_visual(dashboard_id: str, visual_id: str, body: VisualPatch):
        return respond(dashboard_id, undoable(lambda p, con: report.update_visual(con, p, visual_id, body.model_dump())),
                       summary=f"Changed visual '{body.title}'" if body.title else "Changed a visual")

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
    def ai_report_pass(dashboard_id: str, body: DesignBody | None = None):
        body = body or DesignBody()
        try:
            brief = templates.brief(body.template, body.description, body.choose)
        except templates.TemplateError as exc:
            raise HTTPException(400, str(exc)) from exc
        settings = settings_store.load()
        if not settings.api_key:
            raise HTTPException(400, "Add an API key in Settings to let the AI build the report.")
        try:
            dash = boards.get(dashboard_id)
        except NotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        client = guidance.guided(client_factory(settings), guidance.for_dashboard(datasets, dash, None))
        # The AI calls run without holding the dashboard lock; their results are applied in locked edits.
        with opened(dash) as (dataset, frozen, con):
            if frozen is None:
                raise HTTPException(400, "Publish the dataset first: dashboards read its published version.")
            if not [m for m in model.state(frozen)["measures"] if not m.get("error")]:
                raise HTTPException(400, "Build the model first: the report needs measures.")
            try:
                proposal = ai_report.design(client, settings.model, settings.capabilities, con,
                                            publishing.bind(frozen, dash["draft"]), brief)
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
        chosen = body.template or (result["template"] if body.choose and result["template"] in templates.BY_ID else None)
        return {"summary": result["summary"], "added": result["added"], "refused": result["refused"],
                "new_pages": page_ids, "template": chosen, **respond(dashboard_id)}

    @router.post("/{dashboard_id}/export/summary")
    def export_summary(dashboard_id: str):
        """An AI executive summary of the published dashboard, with every number checked against its data."""
        settings = settings_store.load()
        if not settings.api_key:
            raise HTTPException(400, "The AI is not set up yet. Ask an admin to add an API key in Settings.")
        dash = visible(dashboard_id)
        pages = (dash.get("published") or {}).get("pages") or []
        if not pages:
            raise HTTPException(400, "Publish the dashboard first.")
        with opened(dash) as (dataset, frozen, con):
            if frozen is None:
                raise HTTPException(400, "This dashboard's data isn't published yet.")
            project = publishing.bind(frozen, {"pages": pages, "history": []})
            data = {}
            for page in pages:
                for visual in page["visuals"]:
                    try:
                        data[visual["id"]] = report.visual_data(con, project, visual)
                    except (report.ReportError, model.ModelError):
                        continue
            try:
                bullets = export.summarise(guidance.guided(client_factory(settings), guidance.for_dashboard(datasets, dash, dataset)),
                                           settings.model, settings.capabilities, pages, data)
            except llm.AIError as exc:
                raise HTTPException(502, f"The AI did not answer: {exc}") from exc
        return {"bullets": bullets}

    @router.post("/{dashboard_id}/ask")
    def ask_question(dashboard_id: str, body: AskBody, as_role: str | None = None, as_email: str | None = None):
        """Answer a question about the dashboard's data. Read-only; anyone who can see the dashboard may ask.
        The conversation is kept by the browser, not stored, so each viewer's questions stay their own."""
        settings = settings_store.load()
        if not settings.api_key:
            raise HTTPException(400, "The AI is not set up yet. Ask an admin to add an API key in Settings.")
        if viewer() and body.side != "published":
            raise HTTPException(403, "Viewers can ask about published dashboards only.")
        question = body.question.strip()
        if not question:
            raise HTTPException(400, "Type a question first.")
        if len(question) > 1000:
            raise HTTPException(400, "Keep questions under 1,000 characters.")
        dash = visible(dashboard_id)
        view_as(as_role, as_email)
        pages = ((dash.get("published") or {}) if body.side == "published" else dash["draft"]).get("pages") or []
        with opened(dash) as (dataset, frozen, con):
            if frozen is None:
                raise HTTPException(400, "This dashboard's data isn't published yet.")
            project = publishing.bind(frozen, {"pages": pages, "history": []})
            page = next((p for p in pages if p["id"] == body.page_id), None)
            try:
                chosen = report.check_filters(con, frozen, body.filters) if body.filters else []
            except (report.ReportError, model.ModelError) as exc:
                raise HTTPException(400, str(exc)) from exc
            project[model.PAGE_FILTERS] = list((page or {}).get("filters") or []) + chosen
            try:
                result = ask.ask(guidance.guided(client_factory(settings), guidance.for_dashboard(datasets, dash, dataset)),
                                 settings.model, settings.capabilities, con,
                                 project, pages, question, body.history)
            except llm.AIError as exc:
                raise HTTPException(502, f"The AI did not answer: {exc}") from exc
            formats = {m["name"]: m["format"] for m in model.state(frozen)["measures"]}
            return {**result, "formats": formats, "dataset_version": dataset["published"]["version"],
                    "as_of": report.as_of(con, frozen),
                    "scope": report.describe_filters(con, frozen, project[model.PAGE_FILTERS])}

    router.calls = {  # for Autopilot
        "create_dashboard": lambda name, dataset_id: create(NewDashboardBody(name=name, dataset_id=dataset_id)),
        "ai_report": ai_report_pass, "approve_report": approve, "send_digest": send_digest,
        "can_see": lambda dashboard_id: security.can_see(boards.get(dashboard_id), groups()),
    }
    return router
