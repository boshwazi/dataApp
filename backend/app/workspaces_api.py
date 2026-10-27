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


class DomainsBody(BaseModel):
    domains: list[str] = []


class SignInRulesBody(BaseModel):
    require_mfa: bool = False


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

    @router.delete("/api/me")
    def delete_account():
        """Delete the signed-in person's account: their memberships, then their sign-in. A workspace never
        loses its last admin this way; they hand it over first."""
        if config.mode == "local":
            raise HTTPException(400, "There's no account to delete on this computer.")
        user = workspaces.current_user()
        mine = store.for_user(user)
        stuck = [w["name"] for w in mine if w["role"] == "admin"
                 and not any(m["role"] == "admin" and m["user_id"] != user.id for m in store.members(w["id"]))]
        if stuck:
            names = ", ".join(stuck)
            raise HTTPException(409, f"You're the only admin of {names}. Make someone else an admin in Members first, "
                                     "so the workspace isn't left without one.")
        if mailer is None:
            raise HTTPException(503, "Deleting accounts needs the Supabase secret key on the server.")
        for w in mine:
            checked(lambda w=w: store.remove_member(w["id"], user.id))
        try:
            mailer.delete_user(user.id)
        except SupabaseError as exc:
            raise HTTPException(502, f"Your workspaces were left, but the sign-in account couldn't be deleted: {exc}") from exc
        return {"deleted": True}

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
        if not access.at_least("admin"):
            raise HTTPException(403, "Only workspace admins can rename the workspace.")
        return {**checked(lambda: store.rename(access.workspace_id, body.name)), "role": access.role}

    @router.put("/api/workspaces/current/sign-in")
    def set_sign_in(body: SignInRulesBody):
        """Require two-step sign-in for everyone in the workspace. The admin turning it on must already use it,
        so nobody locks themselves out."""
        access = workspaces.current()
        if not access.at_least("admin"):
            raise HTTPException(403, "Only workspace admins can change how people sign in.")
        if config.mode == "local":
            raise HTTPException(400, "Sign-in rules need the hosted version.")
        if body.require_mfa and access.user.aal != "aal2":
            raise HTTPException(409, "Turn on two-step sign-in for your own account first (in Your account), sign in "
                                     "again with your code, then require it for everyone.")
        settings = checked(lambda: store.update_settings(access.workspace_id, {"require_mfa": body.require_mfa or None}))
        note("sign-in", "Two-step sign-in is now " + ("required" if body.require_mfa else "optional"))
        return {"require_mfa": bool(settings.get("require_mfa"))}

    @router.put("/api/workspaces/current/domains")
    def set_domains(body: DomainsBody):
        """Email domains whose people may ask to join. An admin can only add the domain of their own email."""
        access = workspaces.current()
        if not access.at_least("admin"):
            raise HTTPException(403, "Only workspace admins can change who may join.")
        if config.mode == "local":
            raise HTTPException(400, "Joining by email domain needs the hosted version.")
        mine = workspaces.email_domain(access.user.email)
        domains = sorted({d.strip().lower().lstrip("@") for d in body.domains if d.strip()})
        for domain in domains:
            if domain in workspaces.FREE_MAIL:
                raise HTTPException(400, f"{domain} is a free email service, so it can't stand for your company.")
            if domain != mine:
                raise HTTPException(400, f"You can only add your own email's domain ({mine}).")
        checked(lambda: store.update_settings(access.workspace_id, {"domains": domains or None}))
        note("domains", "People at " + (", ".join(domains) if domains else "no domain") + " may ask to join")
        return {"domains": domains}

    def joinable_for(user: workspaces.User) -> list[dict]:
        if config.mode == "local" or user.provider not in workspaces.VERIFIED_PROVIDERS:
            return []
        domain = workspaces.email_domain(user.email)
        if domain in workspaces.FREE_MAIL:
            return []
        member_of = {w["id"] for w in store.for_user(user)}
        return [w for w in store.by_domain(domain) if w["id"] not in member_of]

    @router.get("/api/workspaces/joinable")
    def joinable():
        """Workspaces this person may join because of their email's domain (offered, never joined silently)."""
        return {"workspaces": joinable_for(workspaces.current_user())}

    @router.post("/api/workspaces/{workspace_id}/join")
    def join(workspace_id: str):
        user = workspaces.current_user()
        if not any(w["id"] == workspace_id for w in joinable_for(user)):
            raise HTTPException(403, "This workspace isn't open to your email's domain.")
        checked(lambda: store.add_member(workspace_id, user, "viewer"))
        log.info("%s joined workspace %s by email domain", user.email, workspace_id)
        return {**checked(lambda: store.get(workspace_id)), "role": "viewer"}

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
