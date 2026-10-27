"""AI usage: every AI call is recorded against its workspace, as raw facts (tokens, model, time).

Credits are worked out when usage is read, from RATES, so prices can be set later without rewriting the ledger.
The ledger is append-only, one entry per call: jobs make AI calls from several threads at once, and adding a line
never loses another thread's count the way read-change-write of a total would.

Every AI call goes through one client (llm.make_client, or a test's stand-in); `metered` wraps it.
"""

import json
import logging
import threading
import time
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path

from . import workspaces

log = logging.getLogger(__name__)

# Credits per 1,000 tokens. Provisional: the real numbers come from measured cost per action (M17). Output
# tokens cost providers several times what input tokens do, so they weigh more.
RATES = {"prompt": 1.0, "completion": 4.0}

# Which part of the app made the call, from the request path. Background work keeps the path of the request
# that started it (jobs copy the context); scheduled work has none.
REQUEST_PATH: ContextVar[str] = ContextVar("request_path", default="")
FEATURES = (
    ("/autopilot", "Autopilot"),
    ("/chat", "Chat"),
    ("/ask", "Ask"),
    ("/explain", "Ask"),
    ("/transform", "Cleaning"),
    ("/model", "Model"),
    ("/describe", "Describing tables"),
    ("/export", "Exports"),
    ("/dashboards", "Dashboards"),
    ("/settings", "Connection test"),
)


class PathMiddleware:
    """Notes the request's path, so an AI call can say which part of the app made it."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        token = REQUEST_PATH.set(scope.get("path", ""))
        try:
            await self.app(scope, receive, send)
        finally:
            REQUEST_PATH.reset(token)


def feature(path: str) -> str:
    if not path:
        return "Scheduled"
    for part, name in FEATURES:
        if part in path:
            return name
    return "Other"


def credits(entry: dict) -> float:
    return (entry.get("prompt_tokens", 0) * RATES["prompt"] + entry.get("completion_tokens", 0) * RATES["completion"]) / 1000


def month_of(at: str) -> str:
    return at[:7]


class FileLedger:
    """Locally: one JSON line per call in <workspace>/usage/YYYY-MM.jsonl."""

    def __init__(self, root: Path):
        self.root = root
        self.lock = threading.Lock()

    def add(self, entry: dict) -> None:
        folder = self.root / entry["workspace_id"] / "usage"
        with self.lock:
            folder.mkdir(parents=True, exist_ok=True)
            with open(folder / f"{month_of(entry['at'])}.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def month(self, workspace_id: str, month: str) -> list[dict]:
        path = self.root / workspace_id / "usage" / f"{month}.jsonl"
        if not path.exists():
            return []
        with self.lock:
            lines = path.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines if line.strip()]


class PgLedger:
    """Hosted: rows in usage_events. Written as the service (people never write usage); admins read it."""

    COLUMNS = ("workspace_id", "user_id", "email", "at", "feature", "model", "prompt_tokens", "completion_tokens",
               "cached_tokens", "ms")

    def __init__(self, db):
        self.db = db

    def add(self, entry: dict) -> None:
        with self.db.tx(None) as con:
            con.execute(f"insert into {self.db.t('usage_events')} ({', '.join(self.COLUMNS)}) "
                        f"values ({', '.join(['%s'] * len(self.COLUMNS))})", [entry.get(c) for c in self.COLUMNS])

    def month(self, workspace_id: str, month: str) -> list[dict]:
        start = datetime.fromisoformat(f"{month}-01T00:00:00+00:00")
        end = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
        with self.db.tx(workspaces.current_user()) as con:
            rows = con.execute(f"select {', '.join(self.COLUMNS)} from {self.db.t('usage_events')} "
                               "where workspace_id = %s and at >= %s and at < %s order by at",
                               [workspace_id, start, end]).fetchall()
        return [{**r, "at": r["at"].isoformat(timespec="seconds")} for r in rows]


def _tokens(response) -> tuple[int, int, int]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0, 0, 0
    details = getattr(usage, "prompt_tokens_details", None)
    cached = getattr(details, "cached_tokens", 0) if details is not None else 0
    return int(getattr(usage, "prompt_tokens", 0) or 0), int(getattr(usage, "completion_tokens", 0) or 0), int(cached or 0)


class _Completions:
    def __init__(self, metered: "MeteredClient"):
        self.metered = metered

    def create(self, *args, **kwargs):
        return self.metered._create(*args, **kwargs)


class _Chat:
    def __init__(self, metered: "MeteredClient"):
        self.completions = _Completions(metered)


class MeteredClient:
    """The AI client with every chat completion recorded. Anything else passes through to the real client, and
    attributes set on it (guidance's instructions) stay on this wrapper."""

    def __init__(self, client, ledger, before=None):
        self._client = client
        self._ledger = ledger
        self._before = before  # a check run before each call (the credit limit, once billing is on)
        self.chat = _Chat(self)

    def __getattr__(self, name):
        return getattr(self._client, name)

    def _create(self, *args, **kwargs):
        access = workspaces._access.get()
        if access is not None and self._before is not None:
            self._before(access.workspace_id)
        started = time.perf_counter()
        response = self._client.chat.completions.create(*args, **kwargs)
        if access is not None:  # the platform's connection test belongs to no workspace
            prompt, completion, cached = _tokens(response)
            user = access.user
            try:
                self._ledger.add({
                    "workspace_id": access.workspace_id, "user_id": user.id, "email": user.email,
                    "at": datetime.now(UTC).isoformat(timespec="seconds"),
                    "feature": feature(REQUEST_PATH.get()), "model": kwargs.get("model") or getattr(response, "model", None),
                    "prompt_tokens": prompt, "completion_tokens": completion, "cached_tokens": cached,
                    "ms": round((time.perf_counter() - started) * 1000),
                })
            except Exception:  # noqa: BLE001 — never fail the person's AI step because the ledger couldn't be written
                log.exception("Couldn't record AI usage")
        return response


def metered(client_factory, ledger, before=None):
    """Wrap a client factory so every client it makes is metered. A factory that gives None (no AI) still does."""
    def make(settings):
        client = client_factory(settings)
        return None if client is None else MeteredClient(client, ledger, before)
    return make


def summary(entries: list[dict]) -> dict:
    """A month's usage: totals, and by person, feature and day."""
    def add(bucket: dict, key: str, e: dict):
        row = bucket.setdefault(key, {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "credits": 0.0})
        row["calls"] += 1
        row["prompt_tokens"] += e.get("prompt_tokens", 0)
        row["completion_tokens"] += e.get("completion_tokens", 0)
        row["credits"] += credits(e)

    total: dict = {}
    people: dict = {}
    features: dict = {}
    days: dict = {}
    for e in entries:
        add(total, "all", e)
        add(people, e.get("email") or e.get("user_id") or "unknown", e)
        add(features, e.get("feature") or "Other", e)
        add(days, e["at"][:10], e)

    def rows(bucket: dict, label: str) -> list[dict]:
        out = [{label: k, **v, "credits": round(v["credits"], 1)} for k, v in bucket.items()]
        return sorted(out, key=lambda r: -r["credits"])

    all_ = total.get("all", {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "credits": 0.0})
    return {
        "total": {**all_, "credits": round(all_["credits"], 1)},
        "by_person": rows(people, "person"),
        "by_feature": rows(features, "feature"),
        "by_day": sorted(rows(days, "day"), key=lambda r: r["day"]),
        "rates": RATES,
    }
