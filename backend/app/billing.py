"""Plans, limits and AI credits.

Billing is built before the beta and switched on at launch: limits are only enforced when BILLING_ENFORCED is set
on a hosted server, never on this computer. Until then the numbers are shown (usage against the plan) but nothing
is blocked. The plan numbers are placeholders until real AI cost per action has been measured (usage.py).

A workspace's billing account is kept apart from its settings: settings travel to every member with /api/me,
while the account holds payment details that only admins see and a saved-card token nobody sees.
"""

import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path

from fastapi import HTTPException

from . import notices, usage, workspaces

# Placeholders: final numbers come from measured AI cost (PHASE2_PLAN E). None means unlimited; creators on paid
# plans are the seats paid for.
PLANS = {
    "free": {"name": "Free", "creators": 1, "viewers": 3, "datasets": 2, "refresh": "manual", "credits": 300,
             "extras": []},
    "pro": {"name": "Pro", "creators": "seats", "viewers": 10, "datasets": 20, "refresh": "daily", "credits": 3000,
            "extras": ["Share links", "PDF exports"]},
    "team": {"name": "Team", "creators": "seats", "viewers": None, "datasets": None, "refresh": "hourly",
             "credits": 15000, "extras": ["Row-level security", "Single sign-on", "Audit log export"]},
}
# Which refresh schedules each plan's refresh allows.
REFRESH = {
    "manual": {"off"},
    "daily": {"off", "daily", "weekdays", "weekly"},
    "hourly": {"off", "daily", "weekdays", "weekly", "hourly", "every_6_hours"},
}
WARN_AT = 0.8
SECRET_KEYS = {"recurring_token"}  # never leaves the server


def default_account() -> dict:
    return {"plan": "free", "status": "active", "seats": 1, "period_end": None, "cancel_at_period_end": False,
            "payment_method": None, "topup_credits": {}, "warned": None}


def this_month(at: datetime | None = None) -> str:
    return (at or datetime.now(UTC)).strftime("%Y-%m")


class FileAccounts:
    """Locally: <workspace>/billing/account.json."""

    def __init__(self, root: Path):
        self.root = root
        self.lock = threading.Lock()

    def _path(self, workspace_id: str) -> Path:
        return self.root / workspace_id / "billing" / "account.json"

    def get(self, workspace_id: str) -> dict:
        path = self._path(workspace_id)
        with self.lock:
            saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        return {**default_account(), **saved}

    def save(self, workspace_id: str, account: dict) -> None:
        path = self._path(workspace_id)
        with self.lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(account, indent=2), encoding="utf-8")


class PgAccounts:
    """Hosted: billing_accounts. Written by the server as the service; admins can read their own under RLS."""

    def __init__(self, db):
        self.db = db

    def get(self, workspace_id: str) -> dict:
        with self.db.tx(None) as con:
            row = con.execute(f"select state from {self.db.t('billing_accounts')} where workspace_id = %s",
                              [workspace_id]).fetchone()
        return {**default_account(), **(row["state"] if row else {})}

    def save(self, workspace_id: str, account: dict) -> None:
        with self.db.tx(None) as con:
            con.execute(f"insert into {self.db.t('billing_accounts')} (workspace_id, state) values (%s, %s::jsonb) "
                        "on conflict (workspace_id) do update set state = excluded.state, updated_at = now()",
                        [workspace_id, json.dumps(account)])


def limit_error(message: str, code: str = "plan_limit") -> HTTPException:
    return HTTPException(402, {"message": message, "code": code})


class Billing:
    def __init__(self, accounts, ledger, enforced: bool, preferences=None):
        self.accounts = accounts
        self.ledger = ledger
        self.enforced = enforced
        self.preferences = preferences

    def account(self, workspace_id: str) -> dict:
        return self.accounts.get(workspace_id)

    def plan(self, workspace_id: str) -> dict:
        account = self.account(workspace_id)
        return {"id": account["plan"], **PLANS.get(account["plan"], PLANS["free"])}

    def allowance(self, account: dict, month: str) -> float:
        plan = PLANS.get(account["plan"], PLANS["free"])
        return plan["credits"] + float((account.get("topup_credits") or {}).get(month, 0))

    def used(self, workspace_id: str, month: str) -> float:
        return sum(usage.credits(e) for e in self.ledger.month(workspace_id, month))

    def credits(self, workspace_id: str) -> dict:
        month = this_month()
        account = self.account(workspace_id)
        allowance = self.allowance(account, month)
        used = round(self.used(workspace_id, month), 1)
        return {"month": month, "used": used, "allowance": allowance,
                "percent": round(100 * used / allowance) if allowance else 100}

    # Checks: each does nothing until billing is switched on.

    def check_credits(self, workspace_id: str) -> None:
        """Before an AI call. At 100% AI steps pause with a clear message; manual work carries on."""
        if not self.enforced:
            return
        month = this_month()
        account = self.account(workspace_id)
        allowance = self.allowance(account, month)
        used = self.used(workspace_id, month)
        if used >= allowance:
            raise limit_error("This workspace has used this month's AI credits. AI steps are paused until next month "
                              "or until an admin adds credits in Billing; everything else still works.",
                              "credits_used_up")
        if used >= WARN_AT * allowance and account.get("warned") != month:
            account["warned"] = month  # once a month
            self.accounts.save(workspace_id, account)
            notices.notify(self.preferences, "credits", "AI credits are 80% used",
                           f"{round(used)} of {round(allowance)} credits used this month. Add credits or change plan "
                           "in Billing before AI steps pause.", link="/billing", by=notices.SYSTEM)

    def check_datasets(self, count: int) -> None:
        if not self.enforced:
            return
        plan = self.plan(workspaces.current().workspace_id)
        if plan["datasets"] is not None and count >= plan["datasets"]:
            raise limit_error(f"The {plan['name']} plan has room for {plan['datasets']} datasets. Remove one, or "
                              "change plan in Billing.")

    def check_schedule(self, schedule: str | None) -> None:
        if not self.enforced or schedule is None:
            return
        plan = self.plan(workspaces.current().workspace_id)
        if schedule not in REFRESH[plan["refresh"]]:
            allowed = {"manual": "refreshing by hand", "daily": "daily refreshes", "hourly": "hourly refreshes"}
            raise limit_error(f"The {plan['name']} plan includes {allowed[plan['refresh']]}. Change plan in Billing "
                              "for more frequent ones.")

    def check_member(self, role: str, members: list[dict], invites: list[dict]) -> None:
        """Before inviting someone or changing a role: room for one more creator (admins count) or viewer."""
        if not self.enforced:
            return
        workspace_id = workspaces.current().workspace_id
        account = self.account(workspace_id)
        plan = PLANS.get(account["plan"], PLANS["free"])
        people = members + invites
        if role == "viewer":
            room = plan["viewers"]
            taken = sum(1 for p in people if p["role"] == "viewer")
            kind = "viewers"
        else:
            room = account.get("seats", 1) if plan["creators"] == "seats" else plan["creators"]
            taken = sum(1 for p in people if p["role"] in ("creator", "admin"))
            kind = "creators and admins"
        if room is not None and taken >= room:
            raise limit_error(f"The {plan['name']} plan has room for {room} {kind}, and all are taken. Change plan or "
                              "add seats in Billing.")

    def view(self, workspace_id: str) -> dict:
        """What the Billing page shows an admin. The saved-card token never leaves the server."""
        account = {k: v for k, v in self.account(workspace_id).items() if k not in SECRET_KEYS}
        return {"enforced": self.enforced, "account": account, "plan": self.plan(workspace_id),
                "plans": [{"id": k, **v} for k, v in PLANS.items()], "credits": self.credits(workspace_id)}


def enforced_here(mode: str) -> bool:
    """Limits apply only on a hosted server with BILLING_ENFORCED switched on."""
    return mode != "local" and os.getenv("BILLING_ENFORCED", "").lower() in ("1", "true", "yes")
