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

from . import ingest, llm, refresh
from .projects import ProjectStore
from .projects_api import build_router
from .transform_api import build_router as build_transform_router
from .model_api import build_router as build_model_router
from .dashboards import DashboardStore, migrate
from .dashboards_api import build_router as build_dashboards_router
from .chat_api import build_router as build_chat_router
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
        projects, boards, fetch = app.state.refresh_job
        while not stop.wait(SCHEDULER_SECONDS):
            try:
                refresh.run_due(projects, boards, fetch)
            except Exception:  # keep the loop alive; the next tick tries again
                log.exception("Scheduled refresh failed")

    worker = threading.Thread(target=loop, name="refresh-scheduler", daemon=True)
    worker.start()
    yield
    stop.set()


def create_app(
    data_dir: Path = DEFAULT_DATA_DIR,
    run_scheduler: bool = False,
    client_factory: Callable[[ProviderSettings], object] = llm.make_client,
    fetch_url: Callable[[str], tuple[bytes, str, str]] = ingest.http_get,
) -> FastAPI:
    store = SettingsStore(data_dir / "settings.json")
    app = FastAPI(title="datalemon", lifespan=scheduler_lifespan if run_scheduler else None)
    boards = DashboardStore(data_dir / "dashboards")
    projects = ProjectStore(data_dir / "datasets")
    migrate(data_dir, projects, boards)
    app.state.refresh_job = (projects, boards, fetch_url)
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

    @app.put("/api/settings")
    def put_settings(update: SettingsUpdate):
        return public_view(store.update(update.base_url, update.model, update.api_key))

    @app.post("/api/settings/test")
    def test_settings():
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
app = create_app(run_scheduler=True)
