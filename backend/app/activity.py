"""The activity log and stale markers.

Every change, by a person or the AI, through a button or the chat, is logged on the
dataset or dashboard it belongs to. Each dataset stage also records a change number:
when an earlier stage changes after a later one, the later stage is marked
"needs re-run" until it changes or someone marks it as reviewed. Dashboards are
marked when their dataset is published again after they were last reviewed.
"""

from .projects import now

STAGES = ("get_data", "transform", "model")
STAGE_NAMES = {"get_data": "Get Data", "transform": "Transform", "model": "Model", "report": "Report"}
LOG_LIMIT = 300


def log(record: dict, stage: str, summary: str, actor: str = "You", via: str = "button") -> None:
    entries = record.setdefault("activity", [])
    entries.append({"at": now(), "stage": stage, "summary": summary, "actor": actor, "via": via})
    del entries[:-LOG_LIMIT]


def changed(project: dict, stage: str, summary: str, actor: str = "You", via: str = "button") -> None:
    """A change to a dataset stage: log it and move that stage's change number forward."""
    marks = project.setdefault("stage_marks", {})
    marks["seq"] = marks.get("seq", 0) + 1
    marks[stage] = marks["seq"]
    log(project, stage, summary, actor, via)


def reviewed(project: dict, stage: str) -> None:
    marks = project.setdefault("stage_marks", {})
    marks["seq"] = marks.get("seq", 0) + 1
    marks[stage] = marks["seq"]
    log(project, stage, f"Marked {STAGE_NAMES[stage]} as reviewed")


def stale(project: dict) -> dict[str, str | None]:
    """For each stage, the earlier stage that changed after it, or None."""
    marks = project.get("stage_marks", {})
    result = {}
    for i, stage in enumerate(STAGES):
        newer = [s for s in STAGES[:i] if marks.get(s, 0) > marks.get(stage, 0)]
        result[stage] = STAGE_NAMES[newer[-1]] if newer else None
    return result


def dashboard_stale(dashboard: dict, dataset: dict | None) -> int | None:
    """The dataset version published since the dashboard was last reviewed, or None."""
    version = ((dataset or {}).get("published") or {}).get("version")
    seen = dashboard.get("reviewed_version")
    if version is None or seen is None or version <= seen:
        return None
    return version


def dashboard_reviewed(dashboard: dict, dataset: dict | None) -> None:
    dashboard["reviewed_version"] = ((dataset or {}).get("published") or {}).get("version")
