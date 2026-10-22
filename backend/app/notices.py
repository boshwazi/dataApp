"""In-app notifications: things worth knowing that happened while you weren't looking.

Each workspace keeps its latest notifications in its own record (guidance.PreferenceStore). A notification
is addressed to people by email, to the workspace's creators, or to everyone who can see a dashboard
(checked when it is read, so sharing and row-level security still hold). What each person has already
seen is kept by their browser, so reading never writes anything (viewers can't write workspace records).

Sent when: a scheduled or linked refresh fails or warns, Autopilot finishes, a dashboard is shared with
you, a dashboard you can see is published again, and a digest goes out.
"""

import logging

from . import workspaces
from .projects import new_id, now

log = logging.getLogger(__name__)
LIMIT = 300
SHOWN = 50
EVERYONE, CREATORS = "everyone", "creators"
SYSTEM = "datawazi"  # sent by the app itself (a scheduled refresh), not by whoever happens to be signed in


def notify(preferences, kind: str, title: str, text: str = "", link: str = "", to=CREATORS,
           dashboard_id: str | None = None, by: str | None = None) -> None:
    """Add a notification. `to`: a list of emails, CREATORS, or EVERYONE (with a dashboard: those who can see it).
    Never fails the action it reports."""
    if preferences is None:
        return
    entry = {"id": new_id(), "at": now(), "kind": kind, "title": title, "text": text, "link": link,
             "to": [e.lower() for e in to] if isinstance(to, list) else to, "dashboard_id": dashboard_id,
             "by": by or workspaces.current_user().email}
    try:
        record = preferences.load()
        record["notifications"] = ([entry] + (record.get("notifications") or []))[:LIMIT]
        preferences.save(record)
    except Exception:  # noqa: BLE001
        log.warning("Notification not stored: %s", entry, exc_info=True)


def for_me(preferences, can_see_dashboard) -> list[dict]:
    """The asker's notifications, newest first. `can_see_dashboard(id)` checks dashboard visibility."""
    if preferences is None:
        return []
    email = workspaces.current_user().email.lower()
    creator = workspaces.current().at_least("creator")
    out = []
    for entry in preferences.load().get("notifications") or []:
        to = entry["to"]
        addressed = (to == EVERYONE or (to == CREATORS and creator) or (isinstance(to, list) and email in to))
        if not addressed or entry.get("by", "").lower() == email and to != [email]:
            continue  # nobody needs telling about what they did themselves
        if entry.get("dashboard_id") and not can_see_dashboard(entry["dashboard_id"]):
            continue
        out.append({k: v for k, v in entry.items() if k not in ("to",)})
        if len(out) >= SHOWN:
            break
    return out
