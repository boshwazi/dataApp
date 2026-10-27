"""Supabase Postgres: workspaces, members, invites and dataset/dashboard records.

Used when DATABASE_URL is set (with sign-in on). Each request's queries run inside one
transaction as the signed-in user: role "authenticated", with their id and email as JWT claims,
so the row-level security in schema.sql applies. Background jobs and the one-time import of
local files run as the service (the table owner), and always name their workspace explicitly.
"""

import json
import logging
import re
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from . import workspaces
from .projects import NotFound, new_id
from .workspaces import EMAIL, INVITE_DAYS, User, WorkspaceError, clean_name, clean_role, token_hash

log = logging.getLogger(__name__)
SCHEMA_FILE = Path(__file__).with_name("schema.sql")
SERVICE_USERS = {"service", "local"}


def acting_user() -> User | None:
    """The signed-in user of this request, or None for background work run as the service."""
    try:
        user = workspaces.current_user()
    except RuntimeError:
        return None
    return None if user.id in SERVICE_USERS else user


class Database:
    def __init__(self, url: str, schema: str = "datawazi", pool_size: int = 8):
        if not re.fullmatch(r"[a-z_][a-z0-9_]{0,40}", schema):
            raise ValueError("Bad schema name.")
        self.schema = schema
        # prepare_threshold=None: prepared statements don't survive Supabase's pooler.
        self.pool = ConnectionPool(url, min_size=1, max_size=pool_size, open=True,
                                   kwargs={"prepare_threshold": None, "row_factory": dict_row})

    def migrate(self) -> None:
        sql = SCHEMA_FILE.read_text(encoding="utf-8").replace("{schema}", self.schema)
        with self.pool.connection() as con:
            con.execute(sql)

    @contextmanager
    def tx(self, user: User | None):
        """One transaction, as `user` (row-level security applies) or as the service when None."""
        with self.pool.connection() as con:
            with con.transaction():
                if user is not None:
                    claims = {"sub": user.id, "email": user.email, "role": "authenticated", "aud": "authenticated"}
                    con.execute("select set_config('request.jwt.claims', %s, true)", [json.dumps(claims)])
                    con.execute("set local role authenticated")
                yield con

    def close(self) -> None:
        self.pool.close()

    def t(self, name: str) -> str:
        return f"{self.schema}.{name}"


class PgWorkspaceStore:
    """The same interface as workspaces.WorkspaceStore, kept in Postgres."""

    def __init__(self, db: Database):
        self.db = db

    def _tx(self, user: User | None = None):
        return self.db.tx(user if user is not None else acting_user())

    # Workspaces

    def ids(self) -> list[str]:
        with self.db.tx(None) as con:
            return [r["id"] for r in con.execute(f"select id from {self.db.t('workspaces')} order by created_at")]

    def get(self, workspace_id: str) -> dict:
        with self._tx() as con:
            row = con.execute(f"select id, name, created_at from {self.db.t('workspaces')} where id = %s",
                              [workspace_id]).fetchone()
        if not row:
            raise WorkspaceError("Workspace not found.", 404)
        return {**row, "created_at": row["created_at"].isoformat(timespec="seconds")}

    def ensure(self, workspace_id: str, name: str) -> None:
        with self.db.tx(None) as con:
            con.execute(f"insert into {self.db.t('workspaces')} (id, name) values (%s, %s) on conflict do nothing",
                        [workspace_id, name])

    def create(self, name: str, owner: User) -> dict:
        workspace_id = new_id()
        with self.db.tx(owner) as con:
            con.execute(f"select {self.db.t('create_workspace')}(%s, %s)", [workspace_id, clean_name(name)])
        return {"id": workspace_id, "name": clean_name(name)}

    def rename(self, workspace_id: str, name: str) -> dict:
        with self._tx() as con:
            row = con.execute(f"update {self.db.t('workspaces')} set name = %s where id = %s returning id, name",
                              [clean_name(name), workspace_id]).fetchone()
        if not row:
            raise WorkspaceError("Only admins can rename the workspace.", 403)
        return row

    def for_user(self, user: User) -> list[dict]:
        with self.db.tx(None) as con:  # filtered by user explicitly; runs before the request has a workspace
            rows = con.execute(
                f"select w.id, w.name, m.role from {self.db.t('members')} m join {self.db.t('workspaces')} w "
                f"on w.id = m.workspace_id where m.user_id = %s order by m.joined_at", [user.id]).fetchall()
        return [dict(r) for r in rows]

    def role_of(self, workspace_id: str, user: User) -> str | None:
        return next((w["role"] for w in self.for_user(user) if w["id"] == workspace_id), None)

    # Members

    def add_member(self, workspace_id: str, user: User, role: str) -> None:
        with self.db.tx(None) as con:
            con.execute(f"insert into {self.db.t('members')} (workspace_id, user_id, email, role) values (%s, %s, %s, %s) "
                        "on conflict do nothing", [workspace_id, user.id, user.email.lower(), clean_role(role)])

    def members(self, workspace_id: str) -> list[dict]:
        with self._tx() as con:
            rows = con.execute(f"select workspace_id, user_id, email, role, joined_at from {self.db.t('members')} "
                               "where workspace_id = %s order by joined_at", [workspace_id]).fetchall()
        return [{**r, "joined_at": r["joined_at"].isoformat(timespec="seconds")} for r in rows]

    def _keep_an_admin(self, con, workspace_id: str, leaving: str) -> None:
        admins = con.execute(f"select user_id from {self.db.t('members')} where workspace_id = %s and role = 'admin' "
                             "and user_id <> %s for update", [workspace_id, leaving]).fetchall()
        if not admins:
            raise WorkspaceError("A workspace needs at least one admin. Make someone else an admin first.", 409)

    def set_role(self, workspace_id: str, user_id: str, role: str) -> dict:
        clean_role(role)
        with self._tx() as con:
            member = con.execute(f"select role from {self.db.t('members')} where workspace_id = %s and user_id = %s",
                                 [workspace_id, user_id]).fetchone()
            if not member:
                raise WorkspaceError("Member not found.", 404)
            if member["role"] == "admin" and role != "admin":
                self._keep_an_admin(con, workspace_id, user_id)
            con.execute(f"update {self.db.t('members')} set role = %s where workspace_id = %s and user_id = %s",
                        [role, workspace_id, user_id])
        return {"user_id": user_id, "role": role}

    def remove_member(self, workspace_id: str, user_id: str) -> None:
        with self._tx() as con:
            member = con.execute(f"select role from {self.db.t('members')} where workspace_id = %s and user_id = %s",
                                 [workspace_id, user_id]).fetchone()
            if not member:
                raise WorkspaceError("Member not found.", 404)
            if member["role"] == "admin":
                self._keep_an_admin(con, workspace_id, user_id)
            gone = con.execute(f"delete from {self.db.t('members')} where workspace_id = %s and user_id = %s",
                               [workspace_id, user_id])
            if gone.rowcount == 0:  # row-level security refused it: say so rather than pretend
                raise WorkspaceError("You can't remove this member.", 403)

    # Invites

    def invite(self, workspace_id: str, email: str, role: str, by: User) -> dict:
        email = (email or "").strip().lower()
        if not EMAIL.match(email):
            raise WorkspaceError("Enter a valid email address.")
        clean_role(role)
        token = workspaces.new_token()
        expires = datetime.now(UTC) + timedelta(days=INVITE_DAYS)
        with self._tx() as con:
            if con.execute(f"select 1 from {self.db.t('members')} where workspace_id = %s and email = %s",
                           [workspace_id, email]).fetchone():
                raise WorkspaceError(f"{email} is already a member.", 409)
            con.execute(f"delete from {self.db.t('invites')} where workspace_id = %s and email = %s", [workspace_id, email])
            row = con.execute(
                f"insert into {self.db.t('invites')} (id, workspace_id, email, role, token_hash, invited_by, expires_at) "
                "values (%s, %s, %s, %s, %s, %s, %s) returning id, email, role, invited_by, created_at, expires_at",
                [new_id(), workspace_id, email, role, token_hash(token), by.email, expires]).fetchone()
        return {**self._invite(row), "token": token}

    @staticmethod
    def _invite(row: dict) -> dict:
        return {**row, "created_at": row["created_at"].isoformat(timespec="seconds"),
                "expires_at": row["expires_at"].isoformat(timespec="seconds")}

    def invites(self, workspace_id: str) -> list[dict]:
        with self._tx() as con:
            rows = con.execute(f"select id, email, role, invited_by, created_at, expires_at from {self.db.t('invites')} "
                               "where workspace_id = %s order by created_at", [workspace_id]).fetchall()
        return [self._invite(r) for r in rows]

    def renew(self, workspace_id: str, invite_id: str) -> dict:
        """A fresh link (and 14 more days) for a pending invite; the old link stops working."""
        token = workspaces.new_token()
        expires = datetime.now(UTC) + timedelta(days=INVITE_DAYS)
        with self._tx() as con:
            row = con.execute(f"update {self.db.t('invites')} set token_hash = %s, expires_at = %s "
                              "where workspace_id = %s and id = %s "
                              "returning id, email, role, invited_by, created_at, expires_at",
                              [token_hash(token), expires, workspace_id, invite_id]).fetchone()
        if not row:
            raise WorkspaceError("Invite not found.", 404)
        return {**self._invite(row), "token": token}

    def revoke(self, workspace_id: str, invite_id: str) -> None:
        with self._tx() as con:
            gone = con.execute(f"delete from {self.db.t('invites')} where workspace_id = %s and id = %s",
                               [workspace_id, invite_id]).rowcount
        if not gone:
            raise WorkspaceError("Invite not found.", 404)

    def find_invite(self, token: str) -> dict:
        with self._tx() as con:
            row = con.execute(f"select * from {self.db.t('invite_info')}(%s)", [token_hash(token)]).fetchone()
        if not row:
            raise WorkspaceError("This invite link is not valid. It may have been used or withdrawn.", 404)
        if row["expires_at"] < datetime.now(UTC):
            raise WorkspaceError("This invite has expired. Ask for a new one.", 410)
        return {"workspace": row["workspace"], "email": row["email"], "role": row["role"], "invited_by": row["invited_by"]}

    def accept(self, token: str, user: User) -> dict:
        try:
            with self.db.tx(user) as con:
                workspace_id = con.execute(f"select {self.db.t('accept_invite')}(%s) as id", [token_hash(token)]).fetchone()["id"]
        except psycopg.Error as exc:
            code = getattr(exc, "sqlstate", None)
            if code == "DW404":
                raise WorkspaceError("This invite link is not valid. It may have been used or withdrawn.", 404) from exc
            if code == "DW410":
                raise WorkspaceError("This invite has expired. Ask for a new one.", 410) from exc
            if code == "DW403":
                invited = str(exc).split("invite is for ", 1)[-1].splitlines()[0]
                raise WorkspaceError(f"This invite is for {invited}. Sign in with that address to accept it.", 403) from exc
            raise
        with self.db.tx(user) as con:
            row = con.execute(f"select id, name from {self.db.t('workspaces')} where id = %s", [workspace_id]).fetchone()
        return dict(row)


class PgRecords:
    """JSON records of one kind (datasets or dashboards) for FolderStore, kept in Postgres."""

    def __init__(self, db: Database, kind: str):
        self.db, self.kind = db, kind

    def all(self, workspace_id: str) -> list[dict]:
        with self.db.tx(acting_user()) as con:
            rows = con.execute(f"select record from {self.db.t('records')} where kind = %s and workspace_id = %s "
                               "order by updated_at desc", [self.kind, workspace_id]).fetchall()
        return [r["record"] for r in rows]

    def get(self, workspace_id: str, record_id: str) -> dict | None:
        with self.db.tx(acting_user()) as con:
            row = con.execute(f"select record from {self.db.t('records')} where kind = %s and id = %s and workspace_id = %s",
                              [self.kind, record_id, workspace_id]).fetchone()
        return row["record"] if row else None

    def save(self, workspace_id: str, record: dict) -> None:
        if record.get("workspace_id", workspace_id) != workspace_id:
            raise NotFound("Record belongs to another workspace.")
        with self.db.tx(acting_user()) as con:
            con.execute(
                f"insert into {self.db.t('records')} (kind, id, workspace_id, record, updated_at) values (%s, %s, %s, %s, now()) "
                "on conflict (kind, id) do update set record = excluded.record, updated_at = now() "
                f"where {self.db.t('records')}.workspace_id = excluded.workspace_id",
                [self.kind, record["id"], workspace_id, json.dumps(record)])

    def delete(self, workspace_id: str, record_id: str) -> bool:
        with self.db.tx(acting_user()) as con:
            return bool(con.execute(f"delete from {self.db.t('records')} where kind = %s and id = %s and workspace_id = %s",
                                    [self.kind, record_id, workspace_id]).rowcount)

    def count(self) -> int:
        with self.db.tx(None) as con:
            return con.execute(f"select count(*) as n from {self.db.t('records')} where kind = %s", [self.kind]).fetchone()["n"]


def import_local(data_dir: Path, db: Database, members: "PgWorkspaceStore") -> None:
    """Copy workspaces, members and records from the local JSON files, once, when Postgres is still empty."""
    with db.tx(None) as con:
        if con.execute(f"select count(*) as n from {db.t('records')}").fetchone()["n"]:
            return
    local = data_dir / "workspaces.json"
    data = json.loads(local.read_text(encoding="utf-8")) if local.exists() else {"workspaces": [], "members": []}
    with db.tx(None) as con:
        for w in data["workspaces"]:
            con.execute(f"insert into {db.t('workspaces')} (id, name) values (%s, %s) on conflict do nothing", [w["id"], w["name"]])
        for m in data["members"]:
            con.execute(f"insert into {db.t('members')} (workspace_id, user_id, email, role) values (%s, %s, %s, %s) "
                        "on conflict do nothing", [m["workspace_id"], m["user_id"], m["email"], m["role"]])
        moved = 0
        for kind, folder, filename in (("dataset", "datasets", "project.json"), ("dashboard", "dashboards", "dashboard.json")):
            for path in (data_dir / "workspaces").glob(f"*/{folder}/*/{filename}"):
                record = json.loads(path.read_text(encoding="utf-8"))
                workspace_id = path.parents[2].name
                con.execute(f"insert into {db.t('workspaces')} (id, name) values (%s, %s) on conflict do nothing",
                            [workspace_id, "Local workspace" if workspace_id == "local" else workspace_id])
                con.execute(f"insert into {db.t('records')} (kind, id, workspace_id, record) values (%s, %s, %s, %s) "
                            "on conflict do nothing", [kind, record["id"], workspace_id, json.dumps(record)])
                moved += 1
    log.info("Imported %d local records into Postgres", moved)
