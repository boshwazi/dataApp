"""Publishing datasets: frozen snapshots that dashboards read.

A dataset's draft keeps changing while creators work on it. Publishing freezes what
dashboards see: a copy of the data file (raw, clean and model schemas) plus a copy of
the transform and model definitions. Dashboards evaluate measures against that copy
only, so a half-finished change never reaches a live dashboard.

Before publishing, every dashboard built on the dataset is checked against the new
version, and visuals that would stop working are listed.
"""

import copy
import hashlib
import json

from . import model, report, transform
from .projects import now


def content_hash(project: dict) -> str:
    """What dashboards depend on: sources, steps, relationships, measures. Approvals and checks are left out."""
    t = transform.state(project)
    m = model.state(project)
    content = {
        "sources": [(s["id"], s["table"]) for s in project["sources"]],
        "queries": [
            (q["id"], q["name"], q.get("source_id"), q.get("base_query"), q.get("base_step"),
             [(s["op"], s["params"]) for s in q["steps"]])
            for q in t["queries"]
        ],
        "relationships": [(r["from_table"], r["from_column"], r["to_table"], r["to_column"], r.get("active"))
                          for r in m["relationships"]],
        "measures": [(x["name"], x["kind"], x.get("table"), x.get("aggregation"), x.get("expression"),
                      x.get("filters"), x.get("format")) for x in m["measures"]],
        "date_table": None if not m["date_table"] else (m["date_table"].get("start_year"), m["date_table"].get("end_year")),
        "hidden": m["hidden"],
    }
    return hashlib.sha256(json.dumps(content, sort_keys=True, default=str).encode()).hexdigest()[:16]


def frozen(project: dict) -> dict:
    """The parts of a dataset a dashboard needs, without undo histories."""
    t = transform.state(project)
    m = model.state(project)
    return {
        "sources": copy.deepcopy(project["sources"]),
        "transform": {"queries": copy.deepcopy(t["queries"]), "history": [],
                      "applied_sources": copy.deepcopy(t.get("applied_sources"))},
        "model": {**{k: copy.deepcopy(v) for k, v in m.items() if k != "history"}, "history": []},
    }


def published_project(project: dict) -> dict | None:
    """A read-only stand-in for the dataset as published, shaped like a project, or None."""
    published = project.get("published")
    if not published:
        return None
    return {"id": project["id"], "workspace_id": project["workspace_id"], "name": project["name"],
            **copy.deepcopy(published["snapshot"])}


def draft_status(project: dict) -> dict:
    published = project.get("published")
    return {
        "published": bool(published),
        "version": published["version"] if published else None,
        "published_at": published["published_at"] if published else None,
        "unpublished_changes": (not published) or published["hash"] != content_hash(project),
    }


def bind(dataset: dict, report_state: dict) -> dict:
    """A dataset with a dashboard's pages attached, the shape the report functions work on."""
    return {**dataset, "report": report_state}


def broken_visuals(con, dataset: dict, dashboards: list[dict]) -> list[dict]:
    """Visuals in these dashboards (draft and published) that would fail against `dataset`."""
    problems = []
    for dash in dashboards:
        for side in ("draft", "published"):
            pages = (dash.get(side) or {}).get("pages") or []
            for page in pages:
                for visual in page["visuals"]:
                    try:
                        report.visual_data(con, bind(dataset, {"pages": pages, "history": []}), visual)
                    except (report.ReportError, model.ModelError) as exc:
                        problems.append({"dashboard": dash["name"], "dashboard_id": dash["id"], "version": side,
                                         "page": page["title"], "visual": visual["title"], "error": str(exc)})
    return problems


def publish(store, project: dict) -> dict:
    """Freeze the dataset. The caller holds the dataset's edit lock, has re-run Transform and Model,
    and has closed its own connection (the data file is copied as a whole)."""
    version = (project.get("published") or {}).get("version", 0) + 1
    snapshot = frozen(project)
    filename = store.copy_for_publishing(project["id"], version)
    project["published"] = {"version": version, "file": filename, "published_at": now(),
                            "hash": content_hash(project), "snapshot": snapshot}
    return project["published"]
