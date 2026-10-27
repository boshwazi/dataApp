"""FastAPI entry point.

Run from the repo root:  uvicorn app.main:default_app --factory --app-dir backend --reload
"""

import functools
import logging
import os
import re
import tempfile
import threading
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.background import BackgroundTask

from . import (
    ai_report,
    billing,
    db,
    digests,
    guidance,
    ingest,
    links,
    llm,
    notices,
    refresh,
    security,
    subscriptions,
    usage,
    workspace_admin,
    workspaces,
)
from .auth import AuthConfig, AuthGuard
from .autopilot import Autopilot
from .autopilot import build_router as build_autopilot_router
from .billing_api import build_router as build_billing_router
from .chat_api import build_router as build_chat_router
from .dashboards import DashboardStore, migrate
from .dashboards_api import build_router as build_dashboards_router
from .db import Database, PgRecords, PgWorkspaceStore
from .guidance import PreferenceStore
from .jobs import Jobs
from .language import LanguageMiddleware
from .model_api import build_router as build_model_router
from .payments import ChipProvider
from .projects import LOCAL_WORKSPACE, NotFound, ProjectStore
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
    """Every minute, start a background job for each scheduled refresh and dashboard digest that is due."""
    stop = threading.Event()

    def loop():
        projects, boards, fetch, members = app.state.refresh_job
        while not stop.wait(SCHEDULER_SECONDS):
            try:  # workspaces whose 7-day grace period has ended
                workspace_admin.purge_due(members, app.state.workspace_root, getattr(projects, "backup", None),
                                          workspace_admin.now())
            except Exception:
                log.exception("Purging deleted workspaces failed")
            try:  # paid plans whose month has ended: charge the saved card, retry, or move to Free
                app.state.subscriptions.renew_due(workspace_admin.now())
            except Exception:
                log.exception("Renewing plans failed")
            closing = {wid for wid, settings in members.all_settings() if settings.get("delete_after")}
            for workspace_id in members.ids():
                if workspace_id in closing:
                    continue
                try:
                    with workspaces.use(workspace_id):
                        for project_id in refresh.due(projects):
                            app.state.jobs.submit(f"refresh:{project_id}", functools.partial(
                                refresh.run_scheduled, projects, boards, project_id, fetch))
                        for dashboard_id in digests.due(boards):
                            app.state.jobs.submit(f"digest:{dashboard_id}", functools.partial(
                                app.state.send_digest, dashboard_id))
                except Exception:  # keep the loop alive; the next tick tries again
                    log.exception("Scheduled refresh failed in workspace %s", workspace_id)

    worker = threading.Thread(target=loop, name="refresh-scheduler", daemon=True)
    worker.start()
    yield
    stop.set()


class InstructionsBody(BaseModel):
    instructions: str = ""


class AIPrivacyBody(BaseModel):
    samples: bool = True


class DeleteWorkspaceBody(BaseModel):
    confirm: str = ""


class GroupsBody(BaseModel):
    groups: list[dict]


def create_app(
    data_dir: Path = DEFAULT_DATA_DIR,
    run_scheduler: bool = False,
    client_factory: Callable[[ProviderSettings], object] = llm.make_client,
    fetch_url: Callable[[str], tuple[bytes, str, str]] = ingest.http_get,
    auth: AuthConfig | None = None,
    database: "Database | None" = None,
    admin: SupabaseAdmin | None = None,
    payments_provider=None,
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
    # Every AI call is recorded against its workspace (tokens, model, feature, person).
    ledger = usage.PgLedger(database) if database is not None else usage.FileLedger(root)
    app.state.usage = ledger
    # Plans and limits: only enforced on a hosted server with BILLING_ENFORCED on (billing switches on at launch).
    accounts = billing.PgAccounts(database) if database is not None else billing.FileAccounts(root)
    app.state.billing = plans = billing.Billing(accounts, ledger, billing.enforced_here(auth.mode), preferences)
    projects.billing = plans
    if payments_provider is None and os.getenv("CHIP_SECRET_KEY") and os.getenv("CHIP_BRAND_ID"):
        payments_provider = ChipProvider(os.environ["CHIP_SECRET_KEY"], os.environ["CHIP_BRAND_ID"],
                                         os.getenv("CHIP_BASE_URL", "https://gate.chip-in.asia/api/v1"))

    def workspace_name(workspace_id: str) -> str:
        try:
            return members.get(workspace_id)["name"]
        except workspaces.WorkspaceError:
            return workspace_id

    ledger_of_payments = subscriptions.PgPayments(database) if database is not None else subscriptions.FilePayments(root)
    app.state.subscriptions = subscriptions.Subscriptions(plans, ledger_of_payments, payments_provider, workspace_name,
                                                          preferences)
    client_factory = usage.metered(client_factory, ledger, before=plans.check_credits)
    if admin is not None:
        try:
            admin.ensure_bucket()
            projects.backup = PublishedBackup(admin)
        except SupabaseError:
            log.exception("Storage is not available; published data stays on this server only")

    app.state.refresh_job = (projects, boards, fetch_url, members)
    app.state.workspace_root = root  # the purge removes a deleted workspace's folder

    def member_emails() -> list[str] | None:
        """Who digests may go to: the workspace's members (anyone, on a local single-user install)."""
        if auth.mode == "local":
            return None
        return [m["email"] for m in members.members(workspaces.current().workspace_id)]
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
    app.add_middleware(LanguageMiddleware)
    app.add_middleware(usage.PathMiddleware)
    app.include_router(build_workspaces_router(members, auth, admin,
                                               audit=lambda action, detail: security.audit(preferences, action, detail),
                                               plans=plans))
    routers = [
        build_router(projects, boards, store, client_factory, fetch_url, SAMPLES_DIR),
        build_transform_router(projects, store, client_factory),
        build_model_router(projects, store, client_factory),
        build_dashboards_router(projects, boards, store, client_factory, member_emails=member_emails),
    ]
    for router in routers:
        app.include_router(router)
    # Autopilot runs the same endpoints in order, in the background.
    calls = {name: fn for router in routers for name, fn in router.calls.items()}
    app.state.send_digest = calls["send_digest"]
    app.include_router(build_autopilot_router(Autopilot(projects, boards, store, jobs, calls)))
    app.include_router(build_semantic_router(projects, project_view))
    app.include_router(build_templates_router())
    app.include_router(build_billing_router(plans, app.state.subscriptions, auth.mode))
    app.include_router(build_chat_router(projects, boards, store, client_factory, fetch_url))

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/workspaces/current/groups")
    def get_groups():
        return {"groups": security.groups(preferences)}

    @app.put("/api/workspaces/current/groups")
    def put_groups(body: GroupsBody):
        if not workspaces.current().at_least("admin"):
            raise HTTPException(403, "Only workspace admins can change groups.")
        try:
            saved = security.set_groups(preferences, body.groups)
        except security.SecurityError as exc:
            raise HTTPException(400, str(exc)) from exc
        security.audit(preferences, "groups", "Groups are now " + (", ".join(g["name"] for g in saved) or "none"))
        return {"groups": saved}

    @app.get("/api/workspaces/current/audit")
    def get_audit():
        """Who changed members, groups, roles and sharing, and who published, newest first."""
        if not workspaces.current().at_least("admin"):
            raise HTTPException(403, "Only workspace admins can read the audit log.")
        return {"entries": preferences.load().get("audit") or []}

    @app.get("/api/notifications")
    def notifications():
        """What happened that the asker should know about, newest first; the browser keeps what was seen."""
        def can_see(dashboard_id):
            try:
                return calls["can_see"](dashboard_id)
            except NotFound:
                return False
        return {"notifications": notices.for_me(preferences, can_see)}

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

    def admin_only(what: str) -> str:
        access = workspaces.current()
        if not access.at_least("admin"):
            raise HTTPException(403, f"Only workspace admins can {what}.")
        if auth.mode == "local":
            raise HTTPException(400, "Deleting a workspace needs the hosted version, with sign-in.")
        return access.workspace_id

    @app.get("/api/workspaces/current/usage")
    def get_usage(month: str | None = None):
        """AI usage this month (or `month`, YYYY-MM): totals and by person, feature and day. Admins only."""
        access = workspaces.current()
        if not access.at_least("admin"):
            raise HTTPException(403, "Only workspace admins can see AI usage.")
        month = month or workspace_admin.now().strftime("%Y-%m")
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
            raise HTTPException(400, "Month must look like 2026-10.")
        return {"month": month, **usage.summary(ledger.month(access.workspace_id, month))}

    @app.get("/api/workspaces/current/export")
    def export_workspace():
        """Everything in the workspace as a zip: records, settings, members and the data files."""
        wid = admin_only("export the workspace")
        workspace = members.get(wid)
        at = workspace_admin.now()
        handle = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
        handle.close()
        target = Path(handle.name)
        workspace_admin.write_export(target, workspace, members.members(wid), preferences.load(), projects.all(),
                                     boards.all(), root / wid, at)
        members.update_settings(wid, {"exported_at": workspace_admin.stamp(at)})
        security.audit(preferences, "workspace", "Exported the workspace")
        name = "".join(c if c.isalnum() else "-" for c in workspace["name"]).strip("-") or "workspace"
        return FileResponse(target, media_type="application/zip", filename=f"datawazi-{name}-{at:%Y-%m-%d}.zip",
                            background=BackgroundTask(target.unlink, missing_ok=True))

    @app.post("/api/workspaces/current/delete")
    def delete_workspace(body: DeleteWorkspaceBody):
        """Close the workspace now and delete it after the grace period. Needs a fresh export and the name typed."""
        wid = admin_only("delete the workspace")
        workspace = members.get(wid)
        at = workspace_admin.now()
        if body.confirm.strip() != workspace["name"]:
            raise HTTPException(400, f'Type the workspace name, "{workspace["name"]}", to confirm.')
        if not workspace_admin.export_is_fresh(workspace.get("settings") or {}, at):
            raise HTTPException(409, f"Download the export first (within the last {workspace_admin.EXPORT_FRESH_HOURS} "
                                     "hours), so nothing is lost by accident.")
        after = workspace_admin.stamp(at + workspace_admin.timedelta(days=workspace_admin.GRACE_DAYS))
        settings = members.update_settings(wid, {"delete_after": after})
        notices.notify(preferences, "workspace", f"{workspace['name']} will be deleted on {after[:10]}",
                       text="An admin asked for it. Until then the workspace is closed; an admin can cancel.",
                       to=notices.EVERYONE)
        security.audit(preferences, "workspace", f"Asked to delete the workspace on {after[:10]}")
        return {"delete_after": settings["delete_after"]}

    @app.post("/api/workspaces/current/restore")
    def restore_workspace():
        wid = admin_only("cancel the deletion")
        members.update_settings(wid, {"delete_after": None})
        notices.notify(preferences, "workspace", f"{members.get(wid)['name']} won't be deleted",
                       text="An admin cancelled the deletion. Everything is as it was.", to=notices.EVERYONE)
        security.audit(preferences, "workspace", "Cancelled the workspace deletion")
        return {"delete_after": None}

    @app.get("/api/workspaces/current/ai-privacy")
    def get_ai_privacy():
        return {"samples": preferences.load().get("ai_samples", True)}

    @app.put("/api/workspaces/current/ai-privacy")
    def put_ai_privacy(body: AIPrivacyBody):
        if not workspaces.current().at_least("admin"):
            raise HTTPException(403, "Only workspace admins can change what the AI may see.")
        record = preferences.set_ai_samples(body.samples)
        security.audit(preferences, "privacy", "Sample rows for the AI are now " + ("on" if body.samples else "off"))
        return {"samples": record["ai_samples"]}

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
