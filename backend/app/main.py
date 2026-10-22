"""FastAPI entry point.

Run from the repo root:  uvicorn app.main:default_app --factory --app-dir backend --reload
"""

import functools
import logging
import os
import threading
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import ai_report, db, guidance, ingest, links, llm, refresh, workspaces
from .auth import AuthConfig, AuthGuard
from .autopilot import Autopilot
from .autopilot import build_router as build_autopilot_router
from .chat_api import build_router as build_chat_router
from .dashboards import DashboardStore, migrate
from .dashboards_api import build_router as build_dashboards_router
from .db import Database, PgRecords, PgWorkspaceStore
from .guidance import PreferenceStore
from .jobs import Jobs
from .model_api import build_router as build_model_router
from .projects import LOCAL_WORKSPACE, ProjectStore
from .projects_api import build_router, project_view
from .semantic import build_router as build_semantic_router
from .settings import ProviderSettings, SettingsStore, public_view
from .supabase_admin import PublishedBackup, SupabaseAdmin, SupabaseError
from .templates import build_router as build_templates_router
from .transform_api import build_router as build_transform_router
from .workspaces_api import build_router as build_workspaces_router

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
    """Every minute, start a background job for each dataset whose scheduled refresh is due."""
    stop = threading.Event()

    def loop():
        projects, boards, fetch, members = app.state.refresh_job
        while not stop.wait(SCHEDULER_SECONDS):
            for workspace_id in members.ids():
                try:
                    with workspaces.use(workspace_id):
                        for project_id in refresh.due(projects):
                            app.state.jobs.submit(f"refresh:{project_id}", functools.partial(
                                refresh.run_scheduled, projects, boards, project_id, fetch))
                except Exception:  # keep the loop alive; the next tick tries again
                    log.exception("Scheduled refresh failed in workspace %s", workspace_id)

    worker = threading.Thread(target=loop, name="refresh-scheduler", daemon=True)
    worker.start()
    yield
    stop.set()


class InstructionsBody(BaseModel):
    instructions: str = ""


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
    preferences = PreferenceStore(workspaces.folder_for(root, "preferences"), current)
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
        preferences = PreferenceStore(workspaces.folder_for(root, "preferences"), current, records=PgRecords(database, "preferences"))
    projects.preferences = preferences  # custom AI instructions, found by every AI call through the datasets store
    if admin is not None:
        try:
            admin.ensure_bucket()
            projects.backup = PublishedBackup(admin)
        except SupabaseError:
            log.exception("Storage is not available; published data stays on this server only")

    app.state.refresh_job = (projects, boards, fetch_url, members)
    app.state.jobs = jobs = Jobs()

    def rewrite_insights(con, project, pages, dashboard):
        settings = store.load()
        if not settings.api_key:
            return
        client = guidance.guided(client_factory(settings), guidance.for_dashboard(projects, dashboard, None))
        ai_report.write_insights(client, settings.model, settings.capabilities, con, project, pages)

    def after_publish(dataset_id: str):
        """A dataset published: re-check its dashboards' insights, and refresh datasets that link its tables."""
        jobs.submit(f"insights:{dataset_id}", functools.partial(
            refresh.recheck_insights, projects, boards, dataset_id, rewrite_insights), queue=True)
        chain = refresh.CHAIN.get()
        token = refresh.CHAIN.set(chain + (dataset_id,))
        try:
            for user in links.linked_by(projects, dataset_id):
                if user["id"] not in chain and user["id"] != dataset_id:
                    jobs.submit(f"refresh:{user['id']}", functools.partial(
                        refresh.pull_linked, projects, boards, user["id"], dataset_id, fetch_url), queue=True)
        finally:
            refresh.CHAIN.reset(token)

    projects.on_publish = after_publish
    app.add_middleware(AuthGuard, config=auth, store=members)
    app.include_router(build_workspaces_router(members, auth, admin))
    routers = [
        build_router(projects, boards, store, client_factory, fetch_url, SAMPLES_DIR),
        build_transform_router(projects, store, client_factory),
        build_model_router(projects, store, client_factory),
        build_dashboards_router(projects, boards, store, client_factory),
    ]
    for router in routers:
        app.include_router(router)
    # Autopilot runs the same endpoints in order, in the background.
    calls = {name: fn for router in routers for name, fn in router.calls.items()}
    app.include_router(build_autopilot_router(Autopilot(projects, boards, store, jobs, calls)))
    app.include_router(build_semantic_router(projects, project_view))
    app.include_router(build_templates_router())
    app.include_router(build_chat_router(projects, boards, store, client_factory, fetch_url))

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/refresh-alerts")
    def refresh_alerts():
        """Unattended refreshes (scheduled, or after a linked table's dataset published) that failed or warned."""
        return [{"dataset_id": p["id"], "dataset_name": p["name"], **p["refresh_alert"]}
                for p in projects.all() if p.get("refresh_alert")]

    @app.get("/api/workspaces/current/instructions")
    def get_instructions():
        return {"instructions": preferences.load()["instructions"]}

    @app.put("/api/workspaces/current/instructions")
    def put_instructions(body: InstructionsBody):
        if not workspaces.current().at_least("admin"):
            raise HTTPException(403, "Only workspace admins can change the instructions for the whole workspace.")
        try:
            return {"instructions": preferences.set_instructions(body.instructions)["instructions"]}
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

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


def default_app() -> FastAPI:
    """The server as run by uvicorn (--factory): settings from .env, with Postgres and Supabase when set.

    A factory rather than a module-level app, so importing this module (as the tests do) never
    connects to a real database."""
    load_dotenv(REPO_ROOT / ".env")
    auth = AuthConfig.from_env()
    return create_app(run_scheduler=True, auth=auth, **hosted_services(auth))
