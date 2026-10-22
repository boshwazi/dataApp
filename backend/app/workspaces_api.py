"""Who am I, workspaces, members and invites."""

import logging
import os
from collections.abc import Callable

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from . import workspaces
from .auth import AuthConfig
from .projects import LOCAL_WORKSPACE
from .supabase_admin import SupabaseAdmin, SupabaseError
from .workspaces import WorkspaceError, WorkspaceStore

log = logging.getLogger(__name__)


class NameBody(BaseModel):
    name: str


class RoleBody(BaseModel):
    role: str


class InviteBody(BaseModel):
    email: str
    role: str = "viewer"


def build_router(store: WorkspaceStore, config: AuthConfig, mailer: SupabaseAdmin | None = None,
                 audit: Callable[[str, str], None] | None = None) -> APIRouter:
    router = APIRouter()

    def checked(action):
        try:
            return action()
        except WorkspaceError as exc:
            raise HTTPException(exc.status, str(exc)) from exc

    def invite_view(invite: dict) -> dict:
        # Only a hash of the token is stored, so a link is shown once: when the invite is made or sent again.
        return {k: invite[k] for k in ("id", "email", "role", "invited_by", "created_at", "expires_at")}

    def deliver(request: Request, invite: dict) -> dict:
        """Email the invite when Supabase is set up; the link is returned either way, to copy and send by hand."""
        site = os.getenv("PUBLIC_URL", "").rstrip("/") or str(request.base_url).rstrip("/")
        link = f"{site}/invite/{invite['token']}"
        emailed, problem = False, None
        if mailer is not None:
            try:
                mailer.send_invite(invite["email"], link)
                emailed = True
            except SupabaseError as exc:
                log.warning("Invite email to %s failed: %s", invite["email"], exc)
                problem = str(exc)
        return {"invite": invite_view(invite), "link": link, "emailed": emailed, "email_problem": problem}

    @router.get("/api/auth/config")
    def auth_config():
        return config.public()

    @router.get("/api/me")
    def me():
        user = workspaces.current_user()
        return {
            "mode": config.mode,
            "user": {"id": user.id, "email": user.email},
            "platform_admin": config.is_platform_admin(user),
            # Locally there is one person and one workspace, so no membership is recorded.
            "workspaces": [{"id": LOCAL_WORKSPACE, "name": store.get(LOCAL_WORKSPACE)["name"], "role": "admin"}]
            if config.mode == "local" else store.for_user(user),
        }

    @router.post("/api/workspaces")
    def create_workspace(body: NameBody):
        if config.mode == "local":
            raise HTTPException(400, "The local version has one workspace. Sign-in adds more.")
        workspace = checked(lambda: store.create(body.name, workspaces.current_user()))
        return {**workspace, "role": "admin"}

    @router.get("/api/workspaces/current")
    def current_workspace():
        access = workspaces.current()
        return {**checked(lambda: store.get(access.workspace_id)), "role": access.role}

    @router.patch("/api/workspaces/current")
    def rename_workspace(body: NameBody):
        access = workspaces.current()
        return {**checked(lambda: store.rename(access.workspace_id, body.name)), "role": access.role}

    @router.get("/api/workspaces/current/members")
    def members():
        wid = workspaces.current().workspace_id
        return {"members": store.members(wid), "invites": [invite_view(i) for i in store.invites(wid)]}

    def note(action: str, detail: str) -> None:
        if audit is not None:
            audit(action, detail)

    def email_of(wid: str, user_id: str) -> str:
        return next((m["email"] for m in store.members(wid) if m["user_id"] == user_id), user_id)

    @router.put("/api/workspaces/current/members/{user_id}")
    def set_role(user_id: str, body: RoleBody):
        wid = workspaces.current().workspace_id
        checked(lambda: store.set_role(wid, user_id, body.role))
        note("members", f"{email_of(wid, user_id)} is now {body.role}")
        return members()

    @router.delete("/api/workspaces/current/members/{user_id}")
    def remove_member(user_id: str):
        wid = workspaces.current().workspace_id
        who = email_of(wid, user_id)
        checked(lambda: store.remove_member(wid, user_id))
        note("members", f"{who} was removed")
        return members()

    @router.post("/api/workspaces/current/invites")
    def invite(body: InviteBody, request: Request):
        access = workspaces.current()
        if config.mode == "local":
            raise HTTPException(400, "Inviting people needs sign-in, which the hosted version adds.")
        created = checked(lambda: store.invite(access.workspace_id, body.email, body.role, access.user))
        note("members", f"{body.email.strip().lower()} was invited as {body.role}")
        return {**deliver(request, created), **members()}

    @router.post("/api/workspaces/current/invites/{invite_id}/resend")
    def resend(invite_id: str, request: Request):
        """A new link (the old one stops working) and, when email is set up, a new email."""
        wid = workspaces.current().workspace_id
        renewed = checked(lambda: store.renew(wid, invite_id))
        return {**deliver(request, renewed), **members()}

    @router.delete("/api/workspaces/current/invites/{invite_id}")
    def revoke(invite_id: str):
        wid = workspaces.current().workspace_id
        checked(lambda: store.revoke(wid, invite_id))
        return members()

    @router.get("/api/invites/{token}")
    def show_invite(token: str):
        return checked(lambda: store.find_invite(token))

    @router.post("/api/invites/{token}/accept")
    def accept(token: str):
        workspace = checked(lambda: store.accept(token, workspaces.current_user()))
        return {**workspace, "role": store.role_of(workspace["id"], workspaces.current_user())}

    return router
