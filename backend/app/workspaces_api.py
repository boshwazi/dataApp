"""Who am I, workspaces, members and invites."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import workspaces
from .auth import AuthConfig
from .projects import LOCAL_WORKSPACE
from .workspaces import WorkspaceError, WorkspaceStore


class NameBody(BaseModel):
    name: str


class RoleBody(BaseModel):
    role: str


class InviteBody(BaseModel):
    email: str
    role: str = "viewer"


def build_router(store: WorkspaceStore, config: AuthConfig) -> APIRouter:
    router = APIRouter()

    def checked(action):
        try:
            return action()
        except WorkspaceError as exc:
            raise HTTPException(exc.status, str(exc)) from exc

    def invite_view(invite: dict) -> dict:
        # The token is shown to admins as a link to pass on; until email sending exists, that is the delivery.
        return {k: invite[k] for k in ("id", "email", "role", "invited_by", "created_at", "expires_at", "token")}

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
            "workspaces": [{"id": LOCAL_WORKSPACE, "name": store.get(LOCAL_WORKSPACE)["name"], "role": "admin"}] if config.mode == "local" else store.for_user(user),
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

    @router.put("/api/workspaces/current/members/{user_id}")
    def set_role(user_id: str, body: RoleBody):
        wid = workspaces.current().workspace_id
        checked(lambda: store.set_role(wid, user_id, body.role))
        return members()

    @router.delete("/api/workspaces/current/members/{user_id}")
    def remove_member(user_id: str):
        wid = workspaces.current().workspace_id
        checked(lambda: store.remove_member(wid, user_id))
        return members()

    @router.post("/api/workspaces/current/invites")
    def invite(body: InviteBody):
        access = workspaces.current()
        if config.mode == "local":
            raise HTTPException(400, "Inviting people needs sign-in, which the hosted version adds.")
        created = checked(lambda: store.invite(access.workspace_id, body.email, body.role, access.user))
        return {"invite": invite_view(created), **members()}

    @router.delete("/api/workspaces/current/invites/{invite_id}")
    def revoke(invite_id: str):
        wid = workspaces.current().workspace_id
        checked(lambda: store.revoke(wid, invite_id))
        return members()

    @router.get("/api/invites/{token}")
    def show_invite(token: str):
        invite = checked(lambda: store.find_invite(token))
        workspace = checked(lambda: store.get(invite["workspace_id"]))
        return {"workspace": workspace["name"], "email": invite["email"], "role": invite["role"],
                "invited_by": invite["invited_by"]}

    @router.post("/api/invites/{token}/accept")
    def accept(token: str):
        workspace = checked(lambda: store.accept(token, workspaces.current_user()))
        return {**workspace, "role": store.role_of(workspace["id"], workspaces.current_user())}

    return router
