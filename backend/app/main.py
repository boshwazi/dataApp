"""FastAPI entry point.

Run from the backend folder:  uvicorn app.main:app --reload
"""

from pathlib import Path
from typing import Callable

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import ingest, llm
from .projects import ProjectStore
from .projects_api import build_router
from .transform_api import build_router as build_transform_router
from .settings import ProviderSettings, SettingsStore, public_view

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = REPO_ROOT / "data"
FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"
SAMPLES_DIR = REPO_ROOT / "samples"


class SettingsUpdate(BaseModel):
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None


def create_app(
    data_dir: Path = DEFAULT_DATA_DIR,
    client_factory: Callable[[ProviderSettings], object] = llm.make_client,
    fetch_url: Callable[[str], tuple[bytes, str, str]] = ingest.http_get,
) -> FastAPI:
    store = SettingsStore(data_dir / "settings.json")
    app = FastAPI(title="AI Data Analytics")
    projects = ProjectStore(data_dir / "projects")
    app.include_router(build_router(projects, store, client_factory, fetch_url, SAMPLES_DIR))
    app.include_router(build_transform_router(projects, store, client_factory))

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
            return FileResponse(FRONTEND_DIST / "index.html")

    return app


load_dotenv(REPO_ROOT / ".env")
app = create_app()
