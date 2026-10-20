"""Workspaces (one per company), their members and invites.

Every request runs inside one workspace. The auth guard sets it from the signed-in user's
membership, and the dataset and dashboard stores read it to find their folder:

  <data>/workspaces/<workspace id>/datasets/...
  <data>/workspaces/<workspace id>/dashboards/...

With no workspace set, the stores refuse to work rather than fall back to a shared folder,
so a missed check fails closed. In the hosted phase the records here move to Postgres with
row-level security on workspace_id; the folders move to per-workspace storage.
"""

import json
import re
import secrets
import shutil
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .projects import LOCAL_WORKSPACE, new_id, now

ROLES = ("viewer", "creator", "admin")
INVITE_DAYS = 14
MAX_NAME = 60
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class WorkspaceError(Exception):
    """A request the workspace rules refuse; the message is safe to show."""

    def __init__(self, message: str, status: int = 400, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass(frozen=True)
class User:
    id: str
    email: str


@dataclass(frozen=True)
class Access:
    """Who is asking, in which workspace, with which role."""

    user: User
    workspace_id: str
    role: str

    def at_least(self, role: str) -> bool:
        return ROLES.index(self.role) >= ROLES.index(role)


LOCAL_USER = User(id="local", email="you@local")
_access: ContextVar[Access | None] = ContextVar("access", default=None)
_user: ContextVar[User | None] = ContextVar("user", default=None)


def current() -> Access:
    access = _access.get()
    if access is None:
        raise RuntimeError("No workspace is set for this request.")
    return access


def current_user() -> User:
    user = _user.get()
    if user is None:
        raise RuntimeError("No user is set for this request.")
    return user


def set_request(user: User | None, access: Access | None) -> None:
    _user.set(user)
    _access.set(access)


@contextmanager
def use(workspace_id: str, role: str = "admin", user: User = LOCAL_USER):
    """Run code inside one workspace, for background jobs, startup migrations and tests."""
    tokens = (_user.set(user), _access.set(Access(user, workspace_id, role)))
    try:
        yield
    finally:
        _access.reset(tokens[1])
        _user.reset(tokens[0])


def folder_for(base: Path, kind: str):
    """A store root that follows the current workspace."""
    return lambda: base / current().workspace_id / kind


def clean_name(name: str) -> str:
    name = (name or "").strip()[:MAX_NAME]
    if not name:
        raise WorkspaceError("Give the workspace a name.")
    return name


def clean_role(role: str) -> str:
    if role not in ROLES:
        raise WorkspaceError("The role is admin, creator or viewer.")
    return role


class WorkspaceStore:
    """Workspaces, members and invites in one JSON file (Postgres in the hosted phase)."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()

    def _load(self) -> dict:
        if not self.path.exists():
            return {"workspaces": [], "members": [], "invites": []}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    @contextmanager
    def _edit(self):
        with self._lock:
            data = self._load()
            yield data
            self._save(data)

    # Workspaces

    def ids(self) -> list[str]:
        return [w["id"] for w in self._load()["workspaces"]]

    def get(self, workspace_id: str) -> dict:
        for workspace in self._load()["workspaces"]:
            if workspace["id"] == workspace_id:
                return workspace
        raise WorkspaceError("Workspace not found.", 404)

    def ensure(self, workspace_id: str, name: str) -> None:
        with self._edit() as data:
            if not any(w["id"] == workspace_id for w in data["workspaces"]):
                data["workspaces"].append({"id": workspace_id, "name": name, "created_at": now()})

    def create(self, name: str, owner: User) -> dict:
        workspace = {"id": new_id(), "name": clean_name(name), "created_at": now()}
        with self._edit() as data:
            data["workspaces"].append(workspace)
            data["members"].append(self._member(workspace["id"], owner, "admin"))
        return workspace

    def rename(self, workspace_id: str, name: str) -> dict:
        with self._edit() as data:
            for workspace in data["workspaces"]:
                if workspace["id"] == workspace_id:
                    workspace["name"] = clean_name(name)
                    return workspace
        raise WorkspaceError("Workspace not found.", 404)

    def for_user(self, user: User) -> list[dict]:
        """The user's workspaces with their role, oldest membership first."""
        data = self._load()
        names = {w["id"]: w["name"] for w in data["workspaces"]}
        return [{"id": m["workspace_id"], "name": names.get(m["workspace_id"], "?"), "role": m["role"]}
                for m in data["members"] if m["user_id"] == user.id and m["workspace_id"] in names]

    def role_of(self, workspace_id: str, user: User) -> str | None:
        for m in self._load()["members"]:
            if m["workspace_id"] == workspace_id and m["user_id"] == user.id:
                return m["role"]
        return None

    # Members

    @staticmethod
    def _member(workspace_id: str, user: User, role: str) -> dict:
        return {"workspace_id": workspace_id, "user_id": user.id, "email": user.email.lower(),
                "role": role, "joined_at": now()}

    def add_member(self, workspace_id: str, user: User, role: str) -> None:
        with self._edit() as data:
            if not any(m["workspace_id"] == workspace_id and m["user_id"] == user.id for m in data["members"]):
                data["members"].append(self._member(workspace_id, user, clean_role(role)))

    def members(self, workspace_id: str) -> list[dict]:
        return [m for m in self._load()["members"] if m["workspace_id"] == workspace_id]

    def set_role(self, workspace_id: str, user_id: str, role: str) -> dict:
        clean_role(role)
        with self._edit() as data:
            member = self._find_member(data, workspace_id, user_id)
            if member["role"] == "admin" and role != "admin":
                self._keep_an_admin(data, workspace_id, user_id)
            member["role"] = role
            return member

    def remove_member(self, workspace_id: str, user_id: str) -> None:
        with self._edit() as data:
            member = self._find_member(data, workspace_id, user_id)
            if member["role"] == "admin":
                self._keep_an_admin(data, workspace_id, user_id)
            data["members"].remove(member)

    @staticmethod
    def _find_member(data: dict, workspace_id: str, user_id: str) -> dict:
        for m in data["members"]:
            if m["workspace_id"] == workspace_id and m["user_id"] == user_id:
                return m
        raise WorkspaceError("Member not found.", 404)

    @staticmethod
    def _keep_an_admin(data: dict, workspace_id: str, leaving: str) -> None:
        admins = [m for m in data["members"] if m["workspace_id"] == workspace_id and m["role"] == "admin"
                  and m["user_id"] != leaving]
        if not admins:
            raise WorkspaceError("A workspace needs at least one admin. Make someone else an admin first.", 409)

    # Invites

    def invite(self, workspace_id: str, email: str, role: str, by: User) -> dict:
        email = (email or "").strip().lower()
        if not EMAIL.match(email):
            raise WorkspaceError("Enter a valid email address.")
        clean_role(role)
        with self._edit() as data:
            if any(m["workspace_id"] == workspace_id and m["email"] == email for m in data["members"]):
                raise WorkspaceError(f"{email} is already a member.", 409)
            # A new invite to the same address replaces the old one.
            data["invites"] = [i for i in data["invites"] if not (i["workspace_id"] == workspace_id and i["email"] == email)]
            invite = {"id": new_id(), "workspace_id": workspace_id, "email": email, "role": role,
                      "token": secrets.token_urlsafe(24), "invited_by": by.email, "created_at": now(),
                      "expires_at": (datetime.now(timezone.utc) + timedelta(days=INVITE_DAYS)).isoformat(timespec="seconds")}
            data["invites"].append(invite)
            return invite

    def invites(self, workspace_id: str) -> list[dict]:
        return [i for i in self._load()["invites"] if i["workspace_id"] == workspace_id]

    def revoke(self, workspace_id: str, invite_id: str) -> None:
        with self._edit() as data:
            before = len(data["invites"])
            data["invites"] = [i for i in data["invites"] if not (i["workspace_id"] == workspace_id and i["id"] == invite_id)]
            if len(data["invites"]) == before:
                raise WorkspaceError("Invite not found.", 404)

    def find_invite(self, token: str) -> dict:
        for invite in self._load()["invites"]:
            if secrets.compare_digest(invite["token"], token or ""):
                if datetime.fromisoformat(invite["expires_at"]) < datetime.now(timezone.utc):
                    raise WorkspaceError("This invite has expired. Ask for a new one.", 410)
                return invite
        raise WorkspaceError("This invite link is not valid. It may have been used or withdrawn.", 404)

    def accept(self, token: str, user: User) -> dict:
        with self._edit() as data:
            invite = None
            for candidate in data["invites"]:
                if secrets.compare_digest(candidate["token"], token or ""):
                    invite = candidate
            if invite is None:
                raise WorkspaceError("This invite link is not valid. It may have been used or withdrawn.", 404)
            if datetime.fromisoformat(invite["expires_at"]) < datetime.now(timezone.utc):
                raise WorkspaceError("This invite has expired. Ask for a new one.", 410)
            if invite["email"] != user.email.lower():
                raise WorkspaceError(f"This invite is for {invite['email']}. Sign in with that address to accept it.", 403)
            if not any(m["workspace_id"] == invite["workspace_id"] and m["user_id"] == user.id for m in data["members"]):
                data["members"].append(self._member(invite["workspace_id"], user, invite["role"]))
            data["invites"].remove(invite)
            return next(w for w in data["workspaces"] if w["id"] == invite["workspace_id"])


def move_to_workspaces(data_dir: Path) -> None:
    """Older versions kept one set of datasets and dashboards; they become the local workspace."""
    target = data_dir / "workspaces" / LOCAL_WORKSPACE
    for name in ("projects", "datasets", "dashboards"):
        old = data_dir / name
        if old.exists() and not (target / name).exists():
            target.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old), str(target / name))
