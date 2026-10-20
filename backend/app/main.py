"""FastAPI entry point.

Run from the backend folder:  uvicorn app.main:app --reload
"""

import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import os

from . import db, ingest, llm, refresh, workspaces
from .db import Database, PgRecords, PgWorkspaceStore
from .supabase_admin import PublishedBackup, SupabaseAdmin, SupabaseError
from .auth import AuthConfig, AuthGuard
from .projects import LOCAL_WORKSPACE, ProjectStore
from .projects_api import build_router
from .transform_api import build_router as build_transform_router
from .model_api import build_router as build_model_router
from .dashboards import DashboardStore, migrate
from .dashboards_api import build_router as build_dashboards_router
from .chat_api import build_router as build_chat_router
from .workspaces_api import build_router as build_workspaces_router
from .settings import ProviderSettings, SettingsStore, public_view

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = REPO_ROOT / "data"
FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"
SAMPLES_DIR = REPO_ROOT / "samples"
SCHEDULER_SECONDS = 60
log = logging.getLogger(__name__)


class SettingsUpdate(BaseModel):
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None


@asynccontextmanager
async def scheduler_lifespan(app: FastAPI):
    """Pull scheduled Google Sheets in the background. In the hosted version this moves to a job queue."""
    stop = threading.Event()

    def loop():
        projects, boards, fetch, members = app.state.refresh_job
        while not stop.wait(SCHEDULER_SECONDS):
            for workspace_id in members.ids():
                try:
                    with workspaces.use(workspace_id):
                        refresh.run_due(projects, boards, fetch)
                except Exception:  # keep the loop alive; the next tick tries again
                    log.exception("Scheduled refresh failed in workspace %s", workspace_id)

    worker = threading.Thread(target=loop, name="refresh-scheduler", daemon=True)
    worker.start()
    yield
    stop.set()


def create_app(
    data_dir: Path = DEFAULT_DATA_DIR,
    run_scheduler: bool = False,
    client_factory: Callable[[ProviderSettings], object] = llm.make_client,
    fetch_url: Callable[[str], tuple[bytes, str, str]] = ingest.http_get,
    auth: AuthConfig | None = None,
    database: "Database | None" = None,
    admin: SupabaseAdmin | None = None,
) -> FastAPI:
    auth = auth or AuthConfig()  # local mode unless sign-in is configured
    store = SettingsStore(data_dir / "settings.json")
    app = FastAPI(title="datawazi", lifespan=scheduler_lifespan if run_scheduler else None)

    # Each workspace keeps its own folders; the stores follow the workspace of the current request.
    members = workspaces.WorkspaceStore(data_dir / "workspaces.json")
    root = data_dir / "workspaces"
    workspaces.move_to_workspaces(data_dir)
    members.ensure(LOCAL_WORKSPACE, "Local workspace")
    current = lambda: workspaces.current().workspace_id  # noqa: E731
    boards = DashboardStore(workspaces.folder_for(root, "dashboards"), current)
    projects = ProjectStore(workspaces.folder_for(root, "datasets"), current)
    for workspace_id in members.ids():
        with workspaces.use(workspace_id):
            migrate(root / workspace_id, projects, boards)

    if database is not None:
        # Hosted: workspaces, members, invites and records live in Postgres with row-level security.
        # Data files stay in the workspace folders; published versions are also kept in storage.
        database.migrate()
        members = PgWorkspaceStore(database)
        db.import_local(data_dir, database, members)
        members.ensure(LOCAL_WORKSPACE, "Local workspace")
        boards = DashboardStore(workspaces.folder_for(root, "dashboards"), current, records=PgRecords(database, "dashboard"))
        projects = ProjectStore(workspaces.folder_for(root, "datasets"), current, records=PgRecords(database, "dataset"))
    if admin is not None:
        try:
            admin.ensure_bucket()
            projects.backup = PublishedBackup(admin)
        except SupabaseError:
            log.exception("Storage is not available; published data stays on this server only")

    app.state.refresh_job = (projects, boards, fetch_url, members)
    app.add_middleware(AuthGuard, config=auth, store=members)
    app.include_router(build_workspaces_router(members, auth, admin))
    app.include_router(build_router(projects, boards, store, client_factory, fetch_url, SAMPLES_DIR))
    app.include_router(build_transform_router(projects, store, client_factory))
    app.include_router(build_model_router(projects, store, client_factory))
    app.include_router(build_dashboards_router(projects, boards, store, client_factory))
    app.include_router(build_chat_router(projects, boards, store, client_factory, fetch_url))

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/settings")
    def get_settings():
        return public_view(store.load())

    def platform_admin():
        # The AI provider is set up once for the whole service, by its operators.
        if not auth.is_platform_admin(workspaces.current_user()):
            raise HTTPException(403, "Only the people running this service can change the AI provider.")

    @app.put("/api/settings")
    def put_settings(update: SettingsUpdate):
        platform_admin()
        return public_view(store.update(update.base_url, update.model, update.api_key))

    @app.post("/api/settings/test")
    def test_settings():
        platform_admin()
        settings = store.load()
        if not settings.api_key:
            raise HTTPException(400, "Add an API key first.")
        report = llm.test_connection(client_factory(settings), settings.model)
        if report.ok:
            settings.capabilities = {
                "tools": report.supports_tools,
                "json_mode": report.supports_json_mode,
            }
            store.save(settings)
        return report.as_dict()

    if FRONTEND_DIST.exists():
        app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

        # Client-side routes (/settings, /projects/...) all load the single-page app.
        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith("api/"):
                raise HTTPException(404)
            file = (FRONTEND_DIST / path).resolve()
            if path and file.is_file() and FRONTEND_DIST in file.parents:
                return FileResponse(file)
            # Always revalidate the page itself so a new build shows at once; its assets are fingerprinted.
            return FileResponse(FRONTEND_DIST / "index.html", headers={"Cache-Control": "no-cache"})

    return app


load_dotenv(REPO_ROOT / ".env")
def hosted_services(auth: AuthConfig) -> dict:
    """Postgres and Supabase admin services, when sign-in is on and their settings are in .env."""
    if auth.mode != "supabase":
        return {}
    services = {}
    if os.getenv("DATABASE_URL"):
        services["database"] = Database(os.environ["DATABASE_URL"])
    if os.getenv("SUPABASE_SECRET_KEY"):
        services["admin"] = SupabaseAdmin(auth.supabase_url, os.environ["SUPABASE_SECRET_KEY"])
    return services


_auth = AuthConfig.from_env()
app = create_app(run_scheduler=True, auth=_auth, **hosted_services(_auth))
