"""Sign-in: who is calling, checked on every API request.

Two modes:
- local (no SUPABASE_URL): one person on their own machine. Every request is the local user,
  admin of the local workspace. Nothing to sign in to.
- supabase: the browser signs in with Supabase Auth and sends its access token. The token's
  signature, expiry, audience and issuer are verified here; nothing in it is trusted otherwise.

The guard then picks the workspace (the X-Workspace header, else the user's first one),
checks membership and role, and sets them for the request. Role rules live in `allowed`.
"""

import json
import logging
import os
import re
from dataclasses import dataclass, field

import jwt

from . import workspaces
from .projects import LOCAL_WORKSPACE
from .workspaces import LOCAL_USER, Access, User, WorkspaceStore

log = logging.getLogger(__name__)

PUBLIC = {"/api/health", "/api/auth/config"}
# Signed in, but no workspace needed: who am I, create a workspace, accept an invite, AI provider settings.
ACCOUNT = re.compile(r"^/api/(me|workspaces|invites/[^/]+(/accept)?|settings(/test)?)$")
VIEWER_GET = re.compile(r"^/api/(dashboards|dashboards/[0-9a-f]{12}|dashboards/[0-9a-f]{12}/values|workspaces/current)$")
VIEWER_POST = re.compile(r"^/api/dashboards/[0-9a-f]{12}/(ask|export/summary)$")
ADMIN_ONLY = re.compile(r"^/api/workspaces/current/(members|invites)(/.*)?$|^/api/workspaces/current$")


class AuthError(Exception):
    def __init__(self, message: str, status: int = 401):
        super().__init__(message)
        self.status = status


@dataclass
class AuthConfig:
    supabase_url: str = ""
    anon_key: str = ""
    jwt_secret: str = ""
    platform_admins: set[str] = field(default_factory=set)
    _jwks: object = None

    @property
    def mode(self) -> str:
        return "supabase" if self.supabase_url else "local"

    @classmethod
    def from_env(cls) -> "AuthConfig":
        return cls(
            supabase_url=os.getenv("SUPABASE_URL", "").rstrip("/"),
            anon_key=os.getenv("SUPABASE_PUBLISHABLE_KEY") or os.getenv("SUPABASE_ANON_KEY", ""),
            jwt_secret=os.getenv("SUPABASE_JWT_SECRET", ""),
            platform_admins={e.strip().lower() for e in os.getenv("PLATFORM_ADMINS", "").split(",") if e.strip()},
        )

    def public(self) -> dict:
        """What the browser needs to start sign-in. The publishable key is public by design."""
        return {"mode": self.mode, "supabase_url": self.supabase_url or None, "supabase_key": self.anon_key or None}

    def verify(self, token: str) -> User:
        if self.mode == "local":
            return LOCAL_USER
        if not token:
            raise AuthError("Sign in to continue.")
        try:
            if self.jwt_secret:  # projects still on the legacy shared secret
                key, algorithms = self.jwt_secret, ["HS256"]
            else:  # asymmetric signing keys, published by the project
                if self._jwks is None:
                    self._jwks = jwt.PyJWKClient(f"{self.supabase_url}/auth/v1/.well-known/jwks.json", cache_keys=True)
                key, algorithms = self._jwks.get_signing_key_from_jwt(token).key, ["ES256", "RS256"]
            claims = jwt.decode(token, key, algorithms=algorithms, audience="authenticated",
                                issuer=f"{self.supabase_url}/auth/v1", options={"require": ["exp", "sub"]})
        except jwt.ExpiredSignatureError as exc:
            raise AuthError("Your session has expired. Sign in again.") from exc
        except (jwt.PyJWTError, ValueError) as exc:
            raise AuthError("Sign in to continue.") from exc
        email = (claims.get("email") or "").lower()
        if not email:
            raise AuthError("This account has no email address.")
        return User(id=claims["sub"], email=email)

    def is_platform_admin(self, user: User) -> bool:
        return self.mode == "local" or user.email in self.platform_admins


def allowed(role: str, method: str, path: str) -> bool:
    """What each role may call. Viewers read published dashboards and ask about them; admins manage people."""
    if ADMIN_ONLY.match(path):
        return role == "admin" or (method == "GET" and path == "/api/workspaces/current")
    if role in ("creator", "admin"):
        return True
    return (method == "GET" and bool(VIEWER_GET.match(path))) or (method == "POST" and bool(VIEWER_POST.match(path)))


class AuthGuard:
    """ASGI middleware: authenticate, resolve the workspace and role, then let the request through."""

    def __init__(self, app, config: AuthConfig, store: WorkspaceStore):
        self.app = app
        self.config = config
        self.store = store

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/api/") or scope["path"] in PUBLIC:
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        try:
            user, access = self.resolve(scope["method"], scope["path"], headers)
        except (AuthError, workspaces.WorkspaceError) as exc:
            code = getattr(exc, "code", None)
            body = {"detail": {"message": str(exc), "code": code} if code else str(exc)}
            await respond(send, exc.status, body)
            return
        workspaces.set_request(user, access)
        await self.app(scope, receive, send)

    def resolve(self, method: str, path: str, headers: dict) -> tuple[User, Access | None]:
        auth = headers.get("authorization", "")
        user = self.config.verify(auth[7:].strip() if auth.lower().startswith("bearer ") else "")
        if self.config.mode == "local":
            return user, Access(user, LOCAL_WORKSPACE, "admin")
        self.claim_local_workspace(user)
        if ACCOUNT.match(path):
            return user, None
        mine = self.store.for_user(user)
        if not mine:
            raise workspaces.WorkspaceError("You have no workspace yet. Create one, or ask an admin for an invite.", 409,
                                           code="no_workspace")
        wanted = headers.get("x-workspace") or mine[0]["id"]
        role = next((w["role"] for w in mine if w["id"] == wanted), None)
        if role is None:
            raise workspaces.WorkspaceError("You are not a member of this workspace.", 403)
        if not allowed(role, method, path):
            raise workspaces.WorkspaceError(
                "Only admins can do this." if ADMIN_ONLY.match(path) else "Viewers can see published dashboards only.", 403)
        return user, Access(user, wanted, role)

    def claim_local_workspace(self, user: User) -> None:
        """Data built before sign-in existed sits in the local workspace; a platform admin inherits it."""
        if self.config.is_platform_admin(user) and LOCAL_WORKSPACE in self.store.ids() \
                and not self.store.members(LOCAL_WORKSPACE):
            self.store.add_member(LOCAL_WORKSPACE, user, "admin")
            log.info("%s became admin of the local workspace", user.email)


async def respond(send, status: int, body: dict) -> None:
    payload = json.dumps(body).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode())]})
    await send({"type": "http.response.body", "body": payload})
