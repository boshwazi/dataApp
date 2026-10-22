"""HTTP routes for projects and the Get Data stage."""

from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, HTTPException, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from . import (
    activity,
    ai_profile,
    extend,
    guidance,
    ingest,
    lineage,
    links,
    llm,
    model,
    pbi_dax,
    pbi_export,
    pbi_import,
    pbi_model,
    publishing,
    refresh,
    security,
    semantic,
    transform,
)
from .dashboards import DashboardStore
from .projects import NotFound, ProjectStore
from .settings import ProviderSettings, SettingsStore

MAX_UPLOAD_BYTES = 200 * 1024 * 1024
PREVIEW_LIMIT = 500


class NameBody(BaseModel):
    name: str


class RefreshSettingsBody(BaseModel):
    schedule: str | None = None
    auto_publish: bool | None = None
    at: str | None = None  # HH:MM, Malaysia time, for daily, weekday and weekly schedules
    day: int | None = None  # 0 Monday to 6 Sunday, for weekly schedules
    follow_links: bool | None = None


class SecurityBody(BaseModel):
    roles: list[dict]


class LoadBody(BaseModel):
    mode: str
    key: str | None = None


class LinkBody(BaseModel):
    dataset_id: str
    table: str


class InstructionsBody(BaseModel):
    instructions: str = ""


class CertifyBody(BaseModel):
    certified: bool


class SheetBody(BaseModel):
    url: str
    name: str = ""


HIDDEN_KEYS = ("sources", "transform", "model", "published", "published_history", "security", "report", "activity", "stage_marks", "chats", "powerbi")


def project_view(project: dict, dashboards: list[dict] | None = None, versions: dict | None = None,
                 linked_by: list[dict] | None = None) -> dict:
    """A dataset as the browser sees it: sources without their full profiles, publish status, users."""
    return {
        **{k: v for k, v in project.items() if k not in HIDDEN_KEYS},
        "certified": bool(project.get("certified")),
        "linked_by": linked_by or [],
        "sources": [{**source_summary(s), "update_available": links.update_available(s, versions or {})}
                    for s in project["sources"]],
        "status": publishing.draft_status(project),
        "stale": activity.stale(project),
        "dashboards": [{"id": d["id"], "name": d["name"]} for d in dashboards or []],
        "progress": progress(project),
        # The last Power BI import: its report, and which tables still need data (the stored model stays hidden).
        "powerbi": {k: v for k, v in project["powerbi"].items() if k in ("file", "kind", "imported_at", "report", "waiting_for")}
                   if project.get("powerbi") else None,
    }


def progress(project: dict) -> dict:
    """How far the dataset is along the pipeline, for the stepper. Reads only; never creates state."""
    queries = (project.get("transform") or {}).get("queries", [])
    model = project.get("model") or {}
    return {
        "sources": len(project["sources"]),
        "steps": sum(len(q["steps"]) for q in queries),
        "relationships": len(model.get("relationships", [])),
        "measures": len(model.get("measures", [])),
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

    def full_view(project: dict) -> dict:
        return project_view(project, boards.using(project["id"]), links.latest_versions(store),
                            links.linked_by(store, project["id"]))

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
            where = {**origin, "label": table.name, **({"sheet": table.sheet} if table.sheet else {})}
            added.append(store.add_source(project_id, table.frame, table.name, where, table.warnings))
        if added:
            with store.edit(project_id) as project:
                activity.changed(project, "get_data", "Added " + ", ".join(s["table"] for s in added))
        return added

    # Projects

    @router.get("")
    def list_projects():
        boards_now = boards.all()
        versions = links.latest_versions(store)
        return [project_view(p, [d for d in boards_now if d["dataset_id"] == p["id"]], versions) for p in store.all()]

    @router.post("")
    def create_project(body: NameBody):
        return project_view(store.create(body.name))

    @router.get("/{project_id}")
    def get_project(project_id: str):
        return full_view(load(project_id))

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

    @router.get("/{project_id}/activity")
    def get_activity(project_id: str):
        project = load(project_id)
        return {"entries": list(reversed(project.get("activity", []))), "stale": activity.stale(project)}

    @router.post("/{project_id}/stages/{stage}/reviewed")
    def mark_reviewed(project_id: str, stage: str):
        if stage not in activity.STAGES:
            raise HTTPException(404, "Unknown stage.")
        load(project_id)
        with store.edit(project_id) as project:
            activity.reviewed(project, stage)
        return full_view(project)

    # Refresh

    def refreshed(project_id: str, entry: dict) -> dict:
        return {"entry": entry, **refresh.history_view(load(project_id))}

    @router.get("/{project_id}/refresh")
    def refresh_state(project_id: str):
        return refresh.history_view(load(project_id))

    @router.put("/{project_id}/refresh")
    def refresh_settings(project_id: str, body: RefreshSettingsBody):
        load(project_id)
        try:
            with store.edit(project_id) as project:
                refresh.configure(project, body.schedule, body.auto_publish, body.at, body.day, body.follow_links)
                activity.log(project, "refresh", "Changed refresh settings")
        except refresh.RefreshError as exc:
            raise HTTPException(400, str(exc)) from exc
        return refresh.history_view(load(project_id))

    @router.post("/{project_id}/refresh/alert/dismiss")
    def dismiss_alert(project_id: str):
        load(project_id)
        with store.edit(project_id) as project:
            project.pop("refresh_alert", None)
        return refresh.history_view(load(project_id))

    @router.put("/{project_id}/sources/{source_id}/load")
    def set_load(project_id: str, source_id: str, body: LoadBody):
        """Replace a table's rows on each refresh, or append them (skipping rows whose key is already loaded)."""
        find_source(load(project_id), source_id)
        try:
            with store.edit(project_id) as project:
                source = refresh.set_load(project, source_id, body.mode, body.key)
                key = f" (skipping rows whose {body.key} is already loaded)" if source["load"]["key"] else ""
                activity.log(project, "refresh", f"{source['table']}: refreshes {source['load']['mode']} rows{key}")
        except refresh.RefreshError as exc:
            raise HTTPException(400, str(exc)) from exc
        return refresh.history_view(load(project_id))

    @router.post("/{project_id}/refresh")
    def refresh_now(project_id: str):
        """Pull every Google Sheet again, re-run the steps and model, and check the result."""
        load(project_id)
        try:
            return refreshed(project_id, refresh.pull_sheets(store, boards, project_id, fetch_url))
        except refresh.RefreshError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.post("/{project_id}/sources/{source_id}/pull")
    def pull_one(project_id: str, source_id: str):
        find_source(load(project_id), source_id)
        try:
            return refreshed(project_id, refresh.pull_sheets(store, boards, project_id, fetch_url, only=source_id))
        except refresh.RefreshError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.post("/{project_id}/sources/{source_id}/replace")
    async def replace(project_id: str, source_id: str, file: UploadFile):
        """New rows for a loaded table from a newer file. Steps, model and dashboards stay attached."""
        find_source(load(project_id), source_id)
        data = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(400, "The file is larger than 200 MB.")
        try:
            entry = await run_in_threadpool(refresh.replace_file, store, boards, project_id, source_id,
                                            file.filename or "upload", data)
        except (refresh.RefreshError, ingest.IngestError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return refreshed(project_id, entry)

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
            activity.log(project, "publish", f"Published version {published['version']}")
        store.remove_old_copies(project_id, keep=publishing.kept_files(project))
        store.published(project_id)
        security.audit(getattr(store, "preferences", None), "publish", f"{project['name']}: published version {published['version']}")
        return {**full_view(project), "broken_visuals": problems}

    # Row-level security

    def security_view(project: dict) -> dict:
        project_id = project["id"]
        with store.connect(project_id) as con:
            transform.apply_all(con, project)
            roles = [{**role, "reach": security.reach(con, project, role)} for role in security.roles(project)]
            fields = [{"id": t.id, "name": t.name, "columns": [{"name": c.name, "type": c.type} for c in t.columns]}
                      for t in model.tables(con, project).values()]
        return {"roles": roles, "fields": fields, "groups": security.groups(getattr(store, "preferences", None)),
                "dataset_id": project_id}

    @router.get("/{project_id}/security")
    def get_security(project_id: str):
        return security_view(load(project_id))

    @router.put("/{project_id}/security")
    def set_security(project_id: str, body: SecurityBody):
        """The dataset's roles: who sees which rows on its dashboards. Checked against the model."""
        load(project_id)
        try:
            with store.edit(project_id) as project, store.connect(project_id) as con:
                transform.apply_all(con, project)
                project["security"] = {"roles": security.check_roles(con, project, body.roles)}
        except security.SecurityError as exc:
            raise HTTPException(400, str(exc)) from exc
        names = ", ".join(r["name"] for r in project["security"]["roles"]) or "none"
        security.audit(getattr(store, "preferences", None), "roles", f"{project['name']}: roles are now {names}")
        return security_view(project)

    @router.post("/{project_id}/extend")
    def extend_dataset(project_id: str, body: NameBody):
        """A new dataset on top of this published one: its tables and model, inherited and kept in step."""
        load(project_id)
        try:
            created = extend.create(store, project_id, body.name)
        except semantic.SemanticError as exc:
            raise HTTPException(400, str(exc)) from exc
        return project_view(created)

    @router.get("/{project_id}/versions")
    def versions(project_id: str):
        return {"versions": publishing.versions(load(project_id)), "keep": publishing.KEEP_VERSIONS}

    @router.post("/{project_id}/versions/{version}/roll-back")
    def roll_back(project_id: str, version: int, confirm: bool = False):
        """Publish an earlier version again. Dashboards built since then are checked against it first."""
        load(project_id)
        with store.edit(project_id) as project:
            try:
                old = publishing.earlier(project, version)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            then = {"id": project["id"], "workspace_id": project["workspace_id"], "name": project["name"],
                    **old["snapshot"]}
            with store.connect_published(project_id, old["file"]) as con:
                problems = publishing.broken_visuals(con, then, boards.using(project_id))
            if problems and not confirm:
                raise HTTPException(409, {"message": f"Version {version} would break dashboard visuals.", "problems": problems})
            published = publishing.roll_back(store, project, version)
            activity.log(project, "publish", f"Rolled back to version {version} (published as version {published['version']})")
        security.audit(getattr(store, "preferences", None), "publish",
                       f"{project['name']}: rolled back to version {version}")
        store.remove_old_copies(project_id, keep=publishing.kept_files(project))
        store.published(project_id)
        return {**full_view(project), "versions": publishing.versions(project), "broken_visuals": problems}

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

    @router.post("/{project_id}/sources/shared-link")
    def add_shared_link(project_id: str, body: SheetBody):
        """A file from a shared link: OneDrive, SharePoint, Google Drive (or a Google Sheet). It can be pulled again."""
        load(project_id)
        try:
            provider, _ = ingest.shared_file_url(body.url)
            tables, filename = ingest.read_shared_file(body.url, fetch_url, body.name.strip())
        except ingest.IngestError as exc:
            raise HTTPException(400, str(exc)) from exc
        origin = ({"kind": "gsheet", "url": body.url.strip()} if provider == "gsheet" else
                  {"kind": "link", "url": body.url.strip(), "provider": provider, "file": filename})
        added = add_tables(project_id, tables, origin)
        return {"added": [source_summary(s) for s in added], "errors": []}

    # Get Data: from Power BI

    def import_powerbi(project_id: str, pbi: "pbi_model.PbiModel") -> dict:
        """Load the model's tables (with data from .pbix), then add the date table, relationships and measures."""
        overview = pbi_import.plan(pbi)
        names, added = {}, []
        for table in pbi.tables:
            role = next(t["role"] for t in overview["tables"] if t["name"] == table.name)
            if role != "load":
                continue
            frame, types = pbi_import.frame_and_types(table)
            notes = ["Calculated in Power BI; imported as values."] if table.calculated else []
            source = store.add_source(project_id, frame, table.name, pbi_import.origin_for(table, pbi.name), notes, types)
            names[table.name] = source["table"]
            added.append(source)
        waiting = [t["name"] for t in overview["tables"] if t["role"] == "needs_data"]
        with store.edit(project_id) as project, store.connect(project_id) as con:
            transform.state(project)
            report = pbi_import.apply_model(con, project, pbi, names) if names else None
            if report:
                note = pbi_import.snapshot_note(pbi, list(names))
                if note:
                    report["notes"].insert(0, note)
                direct = [t["name"] for t in overview["tables"] if t["mode"] == "directquery"]
                if direct:
                    report["notes"].append(f"{', '.join(direct)} use DirectQuery in Power BI, so the file holds no "
                                           "data for them. Load their data another way, then Finish import.")
            pbi_import.record(project, pbi, report, waiting)
            project["powerbi"]["names"] = names
            activity.changed(project, "get_data", f"Imported {pbi.name} from Power BI"
                             + (f" ({len(added)} tables)" if added else " (model only; its tables need data)"))
            if report:
                activity.changed(project, "model", f"Imported {len(report['measures'])} measures and "
                                 f"{sum(r['added'] for r in report['relationships'])} relationships from {pbi.name}")
        return {"plan": overview, "report": report, "waiting_for": waiting,
                "added": [source_summary(s) for s in added]}

    @router.post("/{project_id}/sources/powerbi")
    async def upload_powerbi(project_id: str, file: UploadFile):
        """A .pbix (model and data) or a .pbip project as a .zip (model; its tables' data is loaded after)."""
        load(project_id)
        data = await file.read(MAX_UPLOAD_BYTES + 1)
        name = file.filename or "model"
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(400, "The file is larger than 200 MB.")
        stem = Path(name).stem
        try:
            if name.lower().endswith(".pbix"):
                pbi = await run_in_threadpool(pbi_model.read_pbix, data, stem)
            elif name.lower().endswith(".zip"):
                pbi = await run_in_threadpool(pbi_model.read_pbip, data, stem)
            elif name.lower().endswith(".pbip"):
                raise pbi_model.PbiError("A .pbip file only points to its folders. Zip the whole project folder "
                                         "(the .pbip file and its .SemanticModel and .Report folders) and upload the zip.")
            else:
                raise pbi_model.PbiError("Upload a .pbix file, or a .pbip project as a .zip.")
        except pbi_model.PbiError as exc:
            raise HTTPException(400, str(exc)) from exc
        try:
            return await run_in_threadpool(import_powerbi, project_id, pbi)
        except (model.ModelError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.post("/{project_id}/powerbi/refresh")
    async def refresh_powerbi(project_id: str, file: UploadFile):
        """Refresh tables imported from Power BI with a newer .pbix (refreshed in Power BI Desktop first)."""
        load(project_id)
        data = await file.read(MAX_UPLOAD_BYTES + 1)
        name = file.filename or "model.pbix"
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(400, "The file is larger than 200 MB.")
        if not name.lower().endswith(".pbix"):
            raise HTTPException(400, "Refresh needs a .pbix file: a Power BI project (.pbip) holds no data.")
        try:
            pbi = await run_in_threadpool(pbi_model.read_pbix, data, Path(name).stem)
            return await run_in_threadpool(pbi_import.refresh_from, store, boards, project_id, pbi)
        except pbi_model.PbiError as exc:
            raise HTTPException(400, str(exc)) from exc
        except model.ModelError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.post("/{project_id}/powerbi/apply")
    def finish_powerbi(project_id: str):
        """After loading data for a .pbip model's tables: add its relationships and measures."""
        project = load(project_id)
        pending = (project.get("powerbi") or {}).get("pending")
        if not pending:
            raise HTTPException(400, "There is no Power BI model waiting for data in this dataset.")
        pbi = pbi_import.from_storage(pending)
        by_name = {s["table"]: s["table"] for s in project["sources"]}
        names = {t.name: pbi_import.dataset_name(t.name) for t in pbi.tables
                 if pbi_import.dataset_name(t.name) in by_name}
        waiting = [t["name"] for t in pbi_import.plan(pbi)["tables"]
                   if t["role"] == "needs_data" and t["name"] not in names]
        with store.edit(project_id) as project, store.connect(project_id) as con:
            transform.state(project)
            report = pbi_import.apply_model(con, project, pbi, names)
            pbi_import.record(project, pbi, report, waiting)
            activity.changed(project, "model", f"Added the {pbi.name} model from Power BI: "
                             f"{len(report['measures'])} measures, {sum(r['added'] for r in report['relationships'])} relationships")
        return {"report": report, "waiting_for": waiting}

    @router.get("/{project_id}/powerbi/export")
    def export_powerbi(project_id: str):
        """The dataset as a Power BI project (.pbip) in a zip: TMDL model, DAX measures and Parquet data."""
        load(project_id)
        try:
            with store.connect(project_id) as con:
                project = load(project_id)
                name, files = pbi_export.build(con, project)
        except (model.ModelError, pbi_dax.Untranslatable) as exc:
            raise HTTPException(400, str(exc)) from exc
        data = pbi_export.zip_project(name, files)
        return Response(data, media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{name}.zip"'})

    @router.get("/{project_id}/lineage")
    def get_lineage(project_id: str):
        """Sources → tables → measures → dashboard visuals, for this dataset's draft."""
        return lineage.build(load(project_id), boards.using(project_id))

    @router.get("/{project_id}/link-catalogue")
    def link_catalogue(project_id: str):
        """Clean tables of other published datasets that can be linked in, certified ones first."""
        load(project_id)
        return links.catalogue(store, project_id)

    @router.post("/{project_id}/sources/link")
    def link_table(project_id: str, body: LinkBody):
        load(project_id)
        if body.dataset_id == project_id:
            raise HTTPException(400, "A dataset can't link its own tables.")
        try:
            table, origin = links.read(store, body.dataset_id, body.table)
        except links.LinkError as exc:
            raise HTTPException(400, str(exc)) from exc
        source = store.add_source(project_id, table.frame, table.name, origin, [], table.types)
        with store.edit(project_id) as project:
            activity.changed(project, "get_data", f"Linked {origin['label']} (v{origin['version']})")
        return {"added": [source_summary(source)], "errors": []}

    @router.put("/{project_id}/instructions")
    def set_instructions(project_id: str, body: InstructionsBody):
        """What every AI working on this dataset should know or follow (added to its system prompt)."""
        load(project_id)
        try:
            text = guidance.clean(body.instructions)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        with store.edit(project_id) as project:
            project["instructions"] = text
            activity.log(project, "publish", "Changed the AI instructions")
        return full_view(load(project_id))

    @router.post("/{project_id}/certify")
    def certify(project_id: str, body: CertifyBody):
        """Mark a dataset as a trusted, shared source (admins only, once roles exist)."""
        load(project_id)
        with store.edit(project_id) as project:
            project["certified"] = body.certified
            project["certified_at"] = publishing.now() if body.certified else None
            activity.log(project, "publish", "Certified the dataset" if body.certified else "Removed certification")
        return full_view(load(project_id))

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
        old = find_source(load(project_id), source_id)["table"]
        try:
            source = store.rename_source(project_id, source_id, body.name)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        with store.edit(project_id) as project:
            activity.changed(project, "get_data", f"Renamed {old} to {source['table']}")
        return source

    @router.delete("/{project_id}/sources/{source_id}")
    def delete_source(project_id: str, source_id: str):
        source = find_source(load(project_id), source_id)
        if source.get("inherited"):
            raise HTTPException(400, f"{source['table']} comes from the dataset this one extends; it stays while it extends it.")
        name = source["table"]
        store.delete_source(project_id, source_id)
        with store.edit(project_id) as project, store.connect(project_id) as con:
            transform.drop_source(project, source_id)
            transform.apply_all(con, project)
            activity.changed(project, "get_data", f"Removed {name}")
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
                guidance.guided(client_factory(settings), guidance.for_project(store, project)), settings.model,
                settings.capabilities, source["table"], source["profile"], samples
            )
        except llm.AIError as exc:
            raise HTTPException(502, f"AI profiling failed: {exc}") from exc
        ai["model"] = settings.model
        return store.set_ai(project_id, source_id, ai)

    router.calls = {"describe": describe, "publish": publish}  # for Autopilot
    return router
