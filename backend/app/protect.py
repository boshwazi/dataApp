"""Protections around every request: browser security headers, request rate limits, and the rule that a copy
running without sign-in only answers the computer it runs on.

The app holds companies' private data, so these are on everywhere, not only in production.
"""

import ipaddress
import socket
import threading
import time
import urllib.parse
from collections import defaultdict, deque

from fastapi import HTTPException

LOOPBACK = {"127.0.0.1", "::1", "localhost", "testclient"}  # testclient: Starlette's in-process test client


def is_loopback(scope) -> bool:
    client = scope.get("client")
    return client is None or client[0] in LOOPBACK


class SecurityHeaders:
    """Headers that tell browsers to refuse what this app never does: loading scripts from elsewhere, being framed by
    another site (clickjacking), guessing file types, or sending the page's address to other sites."""

    def __init__(self, app, supabase_url: str = ""):
        self.app = app
        connect = ["'self'"]
        if supabase_url:
            connect += [supabase_url, supabase_url.replace("https://", "wss://", 1)]
        self.csp = "; ".join([
            "default-src 'self'",
            "script-src 'self'",
            # React sets styles from code, and the charts draw SVG with style attributes.
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data: blob:",
            "font-src 'self' data:",  # Vite inlines the smallest font files
            "connect-src " + " ".join(connect),
            "frame-ancestors 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "object-src 'none'",
        ])

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        https = scope.get("scheme") == "https" or dict(scope["headers"]).get(b"x-forwarded-proto") == b"https"

        async def with_headers(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                names = {k.lower() for k, _ in headers}
                extra = {
                    b"content-security-policy": self.csp.encode(),
                    b"x-content-type-options": b"nosniff",
                    b"x-frame-options": b"DENY",
                    b"referrer-policy": b"strict-origin-when-cross-origin",
                    b"permissions-policy": b"camera=(), microphone=(), geolocation=(), payment=()",
                    b"cross-origin-opener-policy": b"same-origin",
                }
                if https:
                    extra[b"strict-transport-security"] = b"max-age=31536000; includeSubDomains"
                if scope["path"].startswith("/api/"):
                    extra[b"cache-control"] = b"no-store"  # company data never sits in a shared or browser cache
                headers += [(k, v) for k, v in extra.items() if k not in names]
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, with_headers)


class RateLimit:
    """At most `limit` events per `window` seconds for each key (a user). Counts in memory, per server process: a
    brake on runaway scripts and loops, not a quota (credits are the quota)."""

    def __init__(self, limit: int, window: float, message: str):
        self.limit, self.window, self.message = limit, window, message
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                raise HTTPException(429, {"message": self.message, "code": "slow_down"})
            hits.append(now)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


# Changes (anything but reading) per person per minute, and AI calls per person per minute. Generous for people,
# tight for a script: Autopilot makes a few dozen AI calls in a run.
WRITES = RateLimit(300, 60, "That's a lot of changes in a minute. Wait a moment and try again.")
AI_CALLS = RateLimit(120, 60, "The AI is being asked too often. Wait a minute and try again.")


def public_https_url(url: str) -> None:
    """Refuse links that would make the server fetch something private: anything but https, or a host that resolves
    to this machine, the local network or a cloud metadata address (SSRF)."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Only https links can be fetched.")
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(parsed.hostname, parsed.port or 443)}
    except OSError as exc:
        raise ValueError(f"Could not find {parsed.hostname}.") from exc
    for address in addresses:
        if not ipaddress.ip_address(address.split("%")[0]).is_global:
            raise ValueError(f"{parsed.hostname} points to a private address, so it can't be fetched.")
