"""HTTP routes for projects and the Get Data stage."""

from pathlib import Path
from typing import Callable

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from . import ai_profile, ingest, llm, model, publishing, transform
from .dashboards import DashboardStore
from .projects import NotFound, ProjectStore
from .settings import ProviderSettings, SettingsStore

MAX_UPLOAD_BYTES = 200 * 1024 * 1024
PREVIEW_LIMIT = 500


class NameBody(BaseModel):
    name: str


class SheetBody(BaseModel):
    url: str
    name: str = ""


HIDDEN_KEYS = ("sources", "transform", "model", "published", "report")


def project_view(project: dict, dashboards: list[dict] | None = None) -> dict:
    """A dataset as the browser sees it: sources without their full profiles, publish status, users."""
    return {
        **{k: v for k, v in project.items() if k not in HIDDEN_KEYS},
        "sources": [source_summary(s) for s in project["sources"]],
        "status": publishing.draft_status(project),
        "dashboards": [{"id": d["id"], "name": d["name"]} for d in dashboards or []],
    }


def source_summary(source: dict) -> dict:
    return {
        "id": source["id"],
        "table": source["table"],
        "origin": source["origin"],
        "added_at": source["added_at"],
        "rows": source["profile"]["rows"],
        "columns": len(source["profile"]["columns"]),
        "issue_count": sum(len(c["issues"]) for c in source["profile"]["columns"]) + bool(source["profile"]["duplicate_rows"]),
        "has_ai": source["ai"] is not None,
    }


def build_router(
    store: ProjectStore,
    boards: DashboardStore,
    settings_store: SettingsStore,
    client_factory: Callable[[ProviderSettings], object],
    fetch_url: Callable[[str], tuple[bytes, str, str]],
    samples_dir: Path,
) -> APIRouter:
    router = APIRouter(prefix="/api/datasets")

    def load(project_id: str) -> dict:
        try:
            return store.get(project_id)
        except NotFound as exc:
            raise HTTPException(404, str(exc)) from exc

    def find_source(project: dict, source_id: str) -> dict:
        try:
            return store.source(project, source_id)
        except NotFound as exc:
            raise HTTPException(404, str(exc)) from exc

    def add_tables(project_id: str, tables: list[ingest.LoadedTable], origin: dict) -> list[dict]:
        added = []
        for table in tables:
            where = {**origin, "label": table.name}
            added.append(store.add_source(project_id, table.frame, table.name, where, table.warnings))
        return added

    # Projects

    @router.get("")
    def list_projects():
        boards_now = boards.all()
        return [project_view(p, [d for d in boards_now if d["dataset_id"] == p["id"]]) for p in store.all()]

    @router.post("")
    def create_project(body: NameBody):
        return project_view(store.create(body.name))

    @router.get("/{project_id}")
    def get_project(project_id: str):
        return project_view(load(project_id), boards.using(project_id))

    @router.patch("/{project_id}")
    def rename_project(project_id: str, body: NameBody):
        load(project_id)
        return project_view(store.rename(project_id, body.name))

    @router.delete("/{project_id}")
    def delete_project(project_id: str):
        load(project_id)
        users = boards.using(project_id)
        if users:
            names = ", ".join(d["name"] for d in users)
            raise HTTPException(409, f"Dashboards use this dataset: {names}. Delete them or move them first.")
        store.delete(project_id)
        return {"deleted": project_id}

    @router.post("/{project_id}/publish")
    def publish(project_id: str, confirm: bool = False):
        """Freeze the dataset for its dashboards. Lists dashboard visuals that would break unless confirmed."""
        if not load(project_id)["sources"]:
            raise HTTPException(400, "Load some data before publishing.")
        with store.edit(project_id) as project:
            with store.connect(project_id) as con:
                transform.apply_all(con, project)
                model.revalidate(con, project)
                broken = [q["name"] for q in transform.state(project)["queries"] if q["error"]]
                current = model.state(project)
                broken += [f"{m['name']} (measure)" for m in current["measures"] if m.get("error")]
                if broken:
                    raise HTTPException(400, "Fix these before publishing: " + ", ".join(broken))
                problems = publishing.broken_visuals(con, project, boards.using(project_id))
            if problems and not confirm:
                raise HTTPException(409, {"message": "Publishing would break dashboard visuals.", "problems": problems})
            published = publishing.publish(store, project)
        store.remove_old_copies(project_id, keep=published["file"])
        return {**project_view(project, boards.using(project_id)), "broken_visuals": problems}

    # Get Data: adding sources

    @router.post("/{project_id}/sources/upload")
    async def upload(project_id: str, files: list[UploadFile]):
        load(project_id)
        added, errors = [], []
        for file in files:
            data = await file.read(MAX_UPLOAD_BYTES + 1)
            name = file.filename or "upload"
            if len(data) > MAX_UPLOAD_BYTES:
                errors.append(f"{name}: larger than 200 MB.")
                continue
            try:
                tables = await run_in_threadpool(ingest.read_file, name, data)
            except ingest.IngestError as exc:
                errors.append(str(exc))
                continue
            kind = "excel" if name.lower().endswith(ingest.EXCEL_SUFFIXES) else "csv"
            added += await run_in_threadpool(add_tables, project_id, tables, {"kind": kind, "file": name})
        if not added and errors:
            raise HTTPException(400, " ".join(errors))
        return {"added": [source_summary(s) for s in added], "errors": errors}

    @router.post("/{project_id}/sources/gsheet")
    def add_gsheet(project_id: str, body: SheetBody):
        load(project_id)
        try:
            table = ingest.read_gsheet(body.url, fetch_url, body.name.strip())
        except ingest.IngestError as exc:
            raise HTTPException(400, str(exc)) from exc
        added = add_tables(project_id, [table], {"kind": "gsheet", "url": body.url.strip()})
        return {"added": [source_summary(s) for s in added], "errors": []}

    @router.post("/{project_id}/sources/sample")
    def add_sample(project_id: str):
        load(project_id)
        added = []
        for path in sorted(samples_dir.glob("*")):
            if path.suffix.lower() in ingest.CSV_SUFFIXES + ingest.EXCEL_SUFFIXES:
                kind = "excel" if path.suffix.lower() in ingest.EXCEL_SUFFIXES else "csv"
                tables = ingest.read_file(path.name, path.read_bytes())
                added += add_tables(project_id, tables, {"kind": kind, "file": path.name, "sample": True})
        return {"added": [source_summary(s) for s in added], "errors": []}

    # Get Data: working with a source

    @router.get("/{project_id}/sources/{source_id}")
    def get_source(project_id: str, source_id: str):
        return find_source(load(project_id), source_id)

    @router.get("/{project_id}/sources/{source_id}/preview")
    def preview(project_id: str, source_id: str, limit: int = 100, offset: int = 0):
        project = load(project_id)
        find_source(project, source_id)
        return store.preview(project, source_id, max(1, min(limit, PREVIEW_LIMIT)), max(0, offset))

    @router.patch("/{project_id}/sources/{source_id}")
    def rename_source(project_id: str, source_id: str, body: NameBody):
        find_source(load(project_id), source_id)
        try:
            return store.rename_source(project_id, source_id, body.name)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.delete("/{project_id}/sources/{source_id}")
    def delete_source(project_id: str, source_id: str):
        find_source(load(project_id), source_id)
        store.delete_source(project_id, source_id)
        with store.edit(project_id) as project, store.connect(project_id) as con:
            transform.drop_source(project, source_id)
            transform.apply_all(con, project)
        return {"deleted": source_id}

    @router.post("/{project_id}/sources/{source_id}/profile")
    def reprofile(project_id: str, source_id: str):
        find_source(load(project_id), source_id)
        return store.reprofile(project_id, source_id)

    @router.post("/{project_id}/sources/{source_id}/describe")
    def describe(project_id: str, source_id: str):
        project = load(project_id)
        source = find_source(project, source_id)
        settings = settings_store.load()
        if not settings.api_key:
            raise HTTPException(400, "Add an API key in Settings to let the AI profile your data.")
        samples = store.sample_rows(project, source_id, ai_profile.SAMPLE_ROWS)
        try:
            ai = ai_profile.describe(
                client_factory(settings), settings.model, settings.capabilities, source["table"], source["profile"], samples
            )
        except llm.AIError as exc:
            raise HTTPException(502, f"AI profiling failed: {exc}") from exc
        ai["model"] = settings.model
        return store.set_ai(project_id, source_id, ai)

    return router
