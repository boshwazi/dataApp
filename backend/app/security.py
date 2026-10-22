"""Permissions: row-level security, groups, who can see a dashboard, and an audit log.

Row-level security (RLS) lives on a dataset as roles. A role has fixed filters (Region is North) and/or a
per-person filter: a column whose value must be the viewer's email, so each salesperson sees their own
rows. Its members are people (by email) or groups. As in Power BI:

  - roles restrict viewers; creators and admins see everything, and can view as any role;
  - once a dataset has roles, a viewer in none of them sees no data;
  - a role's filter restricts the table it names and every table that reaches it through relationships
    (a filter on Customers restricts Sales); tables that don't reach it are not filtered, and the role
    editor lists them so that is never a surprise;
  - a viewer belongs to one role per dataset (assigning someone to a role takes them out of the others).

The filters are added where dashboards open their data (dashboards_api.opened), so visuals, slicer values,
Ask, Explain and exports all go through them, and the engine applies them even where page filters are left
out (a KPI's comparison with last year). AI-written insights describe all the data, so they're hidden from
restricted viewers.

Groups and the audit log are kept in the workspace's own record (guidance.PreferenceStore).
"""

import copy
import logging
import re
from contextvars import ContextVar

from . import model, report, workspaces
from .projects import new_id, now

log = logging.getLogger(__name__)

MAX_ROLES = 50
MAX_GROUPS = 100
AUDIT_LIMIT = 500
NO_ACCESS = model.NO_ACCESS

# A creator looking at a dashboard as a role (and, for per-person roles, as one person): {"role", "email"}.
VIEW_AS: ContextVar[dict | None] = ContextVar("view_as", default=None)


class SecurityError(ValueError):
    pass


def roles(dataset: dict) -> list[dict]:
    return (dataset.get("security") or {}).get("roles") or []


# Groups


def groups(preferences) -> list[dict]:
    return preferences.load().get("groups") or [] if preferences else []


def set_groups(preferences, items: list) -> list[dict]:
    if not isinstance(items, list) or len(items) > MAX_GROUPS:
        raise SecurityError(f"Up to {MAX_GROUPS} groups.")
    clean, names = [], set()
    for item in items:
        name = str((item or {}).get("name") or "").strip()
        if not name:
            raise SecurityError("Each group needs a name.")
        if name.lower() in names:
            raise SecurityError(f"There are two groups called {name}.")
        names.add(name.lower())
        clean.append({"id": item.get("id") or new_id(), "name": name[:80], "members": emails(item.get("members"))})
    record = preferences.load()
    record["groups"] = clean
    preferences.save(record)
    return clean


def emails(values) -> list[str]:
    out = []
    for value in values or []:
        email = str(value or "").strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+", email):
            raise SecurityError(f"'{value}' isn't an email address.")
        if email not in out:
            out.append(email)
    return out


def in_refs(email: str, refs: dict, all_groups: list[dict]) -> bool:
    """Whether a person is named in {people, groups} directly or through a group."""
    email = (email or "").lower()
    if email in (refs.get("people") or []):
        return True
    wanted = set(refs.get("groups") or [])
    return any(g["id"] in wanted and email in g["members"] for g in all_groups)


# Roles


def check_roles(con, project: dict, items: list) -> list[dict]:
    """Roles as given, checked against the model's tables and columns."""
    if not isinstance(items, list) or len(items) > MAX_ROLES:
        raise SecurityError(f"Up to {MAX_ROLES} roles.")
    found = model.tables(con, project)
    clean, names, people = [], set(), {}
    for item in items:
        name = str((item or {}).get("name") or "").strip()
        if not name:
            raise SecurityError("Each role needs a name.")
        if name.lower() in names:
            raise SecurityError(f"There are two roles called {name}.")
        names.add(name.lower())
        try:
            filters = report.check_filters(con, project, [{k: v for k, v in f.items() if k != "source"}
                                                          for f in item.get("filters") or []])
        except (report.ReportError, model.ModelError) as exc:
            raise SecurityError(f"{name}: {exc}") from exc
        per_person = item.get("per_person")
        if per_person:
            try:
                table = model.find_table(found, per_person.get("table"))
                column = table.column(per_person.get("column"))
            except model.ModelError as exc:
                raise SecurityError(f"{name}: {exc}") from exc
            if column.type != "text":
                raise SecurityError(f"{name}: {table.name}[{column.name}] must be a text column holding email addresses.")
            per_person = {"table": table.id, "column": column.name}
        if not filters and not per_person:
            raise SecurityError(f"{name}: give the role a filter, or a column matched to each person's email.")
        members = {"people": emails((item.get("members") or {}).get("people")),
                   "groups": [str(g) for g in (item.get("members") or {}).get("groups") or []]}
        for email in members["people"]:
            if email in people:
                raise SecurityError(f"{email} is in both {people[email]} and {name}; a person has one role per dataset.")
            people[email] = name
        clean.append({"id": item.get("id") or new_id(), "name": name[:80], "filters": filters,
                      "per_person": per_person, "members": members})
    return clean


def reach(con, project: dict, role: dict) -> dict:
    """Which tables a role's filters restrict, and which they don't (no relationship leads to them)."""
    found = model.tables(con, project)
    relationships = model.state(project)["relationships"]
    targets = {f["table"] for f in role["filters"]} | ({role["per_person"]["table"]} if role.get("per_person") else set())
    restricted = {t.id for t in found.values() if set(model.paths_from(t.id, relationships)) & targets}
    return {"restricted": sorted(found[t].name for t in restricted if t in found),
            "not_restricted": sorted(t.name for t in found.values() if t.id not in restricted)}


def role_for(dataset: dict, email: str, all_groups: list[dict]) -> dict | None:
    return next((r for r in roles(dataset) if in_refs(email, r["members"], all_groups)), None)


def filters_of(role: dict, person: str) -> list[dict]:
    out = copy.deepcopy(role["filters"])
    if role.get("per_person"):
        out.append({**role["per_person"], "operator": "in", "values": [person or ""]})
    return out


def filters_for(dataset: dict, all_groups: list[dict]) -> list[dict] | None:
    """The row filters for whoever is asking: None when they see everything."""
    view_as = VIEW_AS.get()
    if view_as and workspaces.current().at_least("creator"):
        role = next((r for r in roles(dataset) if r["id"] == view_as.get("role")), None)
        if role is None:
            raise SecurityError("That role doesn't exist on this dataset.")
        return filters_of(role, (view_as.get("email") or "").lower())
    if workspaces.current().at_least("creator") or not roles(dataset):
        return None
    email = workspaces.current_user().email.lower()
    role = role_for(dataset, email, all_groups)
    return filters_of(role, email) if role else [{"deny": True}]


def restrict(frozen: dict, dataset: dict, all_groups: list[dict]) -> dict:
    """The published dataset, with the asker's row filters attached for the engine."""
    filters = filters_for(dataset, all_groups)
    if filters is not None:
        frozen[model.ROLE_FILTERS] = filters
    return frozen


# Who can see a dashboard


def can_see(dashboard: dict, all_groups: list[dict]) -> bool:
    """Creators see every dashboard; viewers see published ones shared with the workspace or with them."""
    if workspaces.current().at_least("creator"):
        return True
    if not dashboard.get("published"):
        return False
    sharing = dashboard.get("sharing") or {"mode": "workspace"}
    return sharing["mode"] == "workspace" or in_refs(workspaces.current_user().email, sharing, all_groups)


def check_sharing(body: dict) -> dict:
    mode = body.get("mode")
    if mode not in ("workspace", "people"):
        raise SecurityError("Share with the whole workspace, or with chosen people and groups.")
    if mode == "workspace":
        return {"mode": "workspace"}
    return {"mode": "people", "people": emails(body.get("people")), "groups": [str(g) for g in body.get("groups") or []]}


# Audit log


def audit(preferences, action: str, detail: str) -> None:
    """Who changed access or published what, newest first, for the workspace's admins."""
    if preferences is None:
        return
    entry = {"at": now(), "by": workspaces.current_user().email, "action": action, "detail": detail}
    try:
        record = preferences.load()
        record["audit"] = ([entry] + (record.get("audit") or []))[:AUDIT_LIMIT]
        preferences.save(record)
    except Exception:  # noqa: BLE001  the log never undoes the change it records
        # Someone who just made themselves a viewer may no longer write the workspace's records.
        log.warning("Audit entry not stored: %s", entry, exc_info=True)


# Power BI roles


DAX_EQUALS = re.compile(r"^\s*(?:'?([^'\[\]]+)'?)?\[([^\]]+)\]\s*=\s*\"([^\"]*)\"\s*$")
DAX_IN = re.compile(r"^\s*(?:'?([^'\[\]]+)'?)?\[([^\]]+)\]\s+IN\s+\{([^}]*)\}\s*$", re.IGNORECASE)
DAX_PERSON = re.compile(r"^\s*(?:'?([^'\[\]]+)'?)?\[([^\]]+)\]\s*=\s*(?:USERPRINCIPALNAME|USERNAME)\s*\(\s*\)\s*$",
                        re.IGNORECASE)


def from_dax(table: str, expression: str) -> dict | None:
    """A Power BI role's table filter as a datawazi filter, for the simple shapes; None when it can't be read."""
    text = (expression or "").strip()
    if match := DAX_PERSON.match(text):
        return {"per_person": {"table": table, "column": match.group(2)}}
    if match := DAX_EQUALS.match(text):
        return {"filter": {"table": table, "column": match.group(2), "operator": "in", "values": [match.group(3)]}}
    if match := DAX_IN.match(text):
        values = [v.strip().strip('"') for v in match.group(3).split(",") if v.strip()]
        return {"filter": {"table": table, "column": match.group(2), "operator": "in", "values": values}}
    return None
