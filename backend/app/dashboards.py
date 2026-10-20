"""Dashboards: report pages built on one dataset's published version.

  <root>/<id>/dashboard.json   name, dataset id, draft pages (with undo history), published pages

Creators edit the draft. Publishing copies the draft pages to the published version,
which is what viewers see. Both read the dataset's latest published copy.
"""

import copy
import json
import logging
from pathlib import Path

from . import model, publishing, transform
from .projects import LOCAL_WORKSPACE, FolderStore, NotFound, ProjectStore, new_id, now

log = logging.getLogger(__name__)


class DashboardStore(FolderStore):
    filename = "dashboard.json"
    label = "Dashboard"

    def create(self, name: str, dataset_id: str, pages: list | None = None) -> dict:
        dashboard = {
            "id": new_id(),
            "workspace_id": LOCAL_WORKSPACE,
            "name": name.strip() or "Untitled dashboard",
            "dataset_id": dataset_id,
            "created_at": now(),
            "updated_at": now(),
            "draft": {"pages": pages or [], "history": []},
            "published": None,
        }
        self.save(dashboard)
        return dashboard

    def using(self, dataset_id: str) -> list[dict]:
        return [d for d in self.all() if d["dataset_id"] == dataset_id]


def pages_signature(pages: list[dict]) -> str:
    """What viewers would see, for comparing the draft with the published version."""
    keep = ("type", "title", "measures", "group_by", "kpi", "width", "insight")
    return json.dumps([(p["title"], p.get("summary"), [{k: v.get(k) for k in keep} for v in p["visuals"]])
                       for p in pages], sort_keys=True, default=str)


def status(dashboard: dict) -> dict:
    published = dashboard.get("published")
    return {
        "published": bool(published),
        "published_at": published["published_at"] if published else None,
        "unpublished_changes": (not published) or
                               pages_signature(published["pages"]) != pages_signature(dashboard["draft"]["pages"]),
    }


def publish(dashboard: dict, dataset: dict) -> None:
    dashboard["published"] = {
        "pages": copy.deepcopy(dashboard["draft"]["pages"]),
        "published_at": now(),
        "dataset_version": (dataset.get("published") or {}).get("version"),
    }


def migrate(data_dir: Path, datasets: ProjectStore, dashboards: DashboardStore) -> None:
    """Move from one-project-does-everything to datasets plus dashboards.

    data/projects becomes data/datasets. A project with report pages becomes a published
    dataset plus a dashboard holding those pages, so nothing the user built is lost.
    """
    old, new = data_dir / "projects", data_dir / "datasets"
    if old.exists() and not new.exists():
        old.rename(new)
    for project in datasets.all():
        if "report" not in project:
            continue
        pages = project["report"].get("pages") or []
        with datasets.edit(project["id"]) as p:
            p.pop("report", None)
            p.setdefault("published", None)
            if pages:
                with datasets.connect(p["id"]) as con:
                    transform.apply_all(con, p)
                    model.revalidate(con, p)
                publishing.publish(datasets, p)
        if pages:
            dashboards.create(project["name"], project["id"], copy.deepcopy(pages))
            log.info("Moved the report of %s into a dashboard", project["name"])


def missing_dataset(dashboard_id: str) -> NotFound:
    return NotFound(f"The dataset of dashboard {dashboard_id} was removed.")
