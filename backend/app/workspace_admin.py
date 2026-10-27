"""A workspace's export, and deleting it after a grace period.

Deleting is two steps on purpose: an admin downloads the export, then asks for the deletion, which happens
GRACE_DAYS later. Until then the workspace is closed to everyone, and an admin can cancel. The scheduler purges
what is due: the data folder, published copies in storage, then the workspace row (its records, members and
invites go with it).
"""

import json
import logging
import shutil
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

GRACE_DAYS = 7
EXPORT_FRESH_HOURS = 24  # the deletion needs an export made this recently

README = """This is an export of the datawazi workspace "{name}", made {at}.

workspace.json      the workspace, its settings and its members
preferences.json    AI instructions, groups, notifications and the audit log
datasets/<id>.json  each dataset: sources, cleaning steps, model, measures, security roles
dashboards/<id>.json each dashboard: pages, visuals, sharing, digests
files/              the data itself: each dataset's DuckDB database and published versions
                    (open a .duckdb file with DuckDB, or export its tables to CSV or Parquet)
"""


def now() -> datetime:
    return datetime.now(UTC)


def stamp(at: datetime) -> str:
    return at.isoformat(timespec="seconds")


def write_export(target: Path, workspace: dict, members: list[dict], preferences: dict,
                 datasets: list[dict], dashboards: list[dict], folder: Path, at: datetime) -> None:
    """Everything the workspace holds, as one zip."""
    def dump(value) -> str:
        return json.dumps(value, indent=2, ensure_ascii=False, default=str)

    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("README.txt", README.format(name=workspace["name"], at=stamp(at)))
        z.writestr("workspace.json", dump({**workspace, "members": members}))
        z.writestr("preferences.json", dump(preferences))
        for dataset in datasets:
            z.writestr(f"datasets/{dataset['id']}.json", dump(dataset))
        for dashboard in dashboards:
            z.writestr(f"dashboards/{dashboard['id']}.json", dump(dashboard))
        if folder.exists():
            for path in sorted(folder.rglob("*")):
                if path.is_file():
                    z.write(path, f"files/{path.relative_to(folder).as_posix()}")


def export_is_fresh(settings: dict, at: datetime) -> bool:
    exported = settings.get("exported_at")
    return bool(exported) and at - datetime.fromisoformat(exported) <= timedelta(hours=EXPORT_FRESH_HOURS)


def purge_due(store, root: Path, backup, at: datetime, before=None) -> list[str]:
    """Delete every workspace whose grace period has ended. Returns their ids. `before` runs first for each
    (removing a saved card at the payment provider)."""
    purged = []
    for workspace_id, settings in store.all_settings():
        due = settings.get("delete_after")
        if not due or datetime.fromisoformat(due) > at:
            continue
        if before is not None:
            try:
                before(workspace_id)
            except Exception:  # noqa: BLE001 — the deletion goes ahead; the card is logged for a hand check
                log.exception("Couldn't tidy billing for workspace %s before deleting it", workspace_id)
        shutil.rmtree(root / workspace_id, ignore_errors=True)
        if backup is not None:
            backup.forget_workspace(workspace_id)
        store.delete_workspace(workspace_id)
        log.info("Deleted workspace %s after its grace period", workspace_id)
        purged.append(workspace_id)
    return purged
