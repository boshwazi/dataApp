"""Paying for a plan: checkout, settling a paid purchase once, numbered receipts, cancelling, and renewals.

The flow: an admin starts a checkout, pays on CHIP's page, and comes back. The purchase is settled either by
CHIP's signed callback or by the page asking after the redirect, whichever comes first. Both ask CHIP for the
purchase itself (never trusting what the browser or callback says about it), and a payments ledger makes sure
each purchase is applied once, with one receipt number.

Prices are placeholders until launch: None means not on sale, and checkout says so.
"""

import json
import logging
import os
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import HTTPException

from . import notices, workspaces
from .billing import PLANS, default_account
from .payments import PaymentError, card_of

log = logging.getLogger(__name__)

# Monthly prices in sen (RM 1 = 100), per seat for Pro and Team. None: not on sale yet (set at launch).
PRICES: dict[str, int | None] = {"pro": None, "team": None}
TOPUP = {"credits": 1000, "price": None}  # a pack of extra AI credits for this month
RETRY_DAYS = (2, 3, 2)  # a failed renewal is tried again after 2, 3 and 2 more days (7 in all), then Free


def tax_rate() -> float:
    """Service tax: 8% once registered (above RM 500,000 a year). Off until then; the lines are always built."""
    try:
        return float(os.getenv("SST_RATE", "0"))
    except ValueError:
        return 0.0


def add_month(at: datetime) -> datetime:
    month = at.month % 12 + 1
    year = at.year + (at.month == 12)
    for day in (at.day, 30, 29, 28):  # 31 January → 28 or 29 February
        try:
            return at.replace(year=year, month=month, day=day)
        except ValueError:
            continue
    raise ValueError(at)


def stamp(at: datetime) -> str:
    return at.isoformat(timespec="seconds")


class FilePayments:
    """Locally: one ledger for the whole server in <root>/_billing (kept when a workspace is deleted, as receipts
    must be)."""

    def __init__(self, root: Path):
        self.folder = root / "_billing"
        self.lock = threading.Lock()

    def _state(self) -> dict:
        path = self.folder / "payments.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"next": 1, "done": {}}

    def record(self, purchase_id: str, workspace_id: str, receipt: dict) -> dict | None:
        """Number and keep the receipt, once per purchase. None when the purchase was already settled."""
        with self.lock:
            state = self._state()
            if purchase_id in state["done"]:
                return None
            receipt = {**receipt, "number": f"DW-{receipt['date'][:4]}-{state['next']:06d}"}
            state["next"] += 1
            state["done"][purchase_id] = receipt["number"]
            self.folder.mkdir(parents=True, exist_ok=True)
            with open(self.folder / "receipts.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps({"workspace_id": workspace_id, "purchase_id": purchase_id, **receipt}) + "\n")
            (self.folder / "payments.json").write_text(json.dumps(state), encoding="utf-8")
            return receipt

    def receipts(self, workspace_id: str) -> list[dict]:
        path = self.folder / "receipts.jsonl"
        if not path.exists():
            return []
        with self.lock:
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        return [r for r in rows if r["workspace_id"] == workspace_id][::-1]


class PgPayments:
    """Hosted: billing_payments, numbered from one counter in the same transaction, so numbers have no gaps."""

    def __init__(self, db):
        self.db = db

    def record(self, purchase_id: str, workspace_id: str, receipt: dict) -> dict | None:
        t = self.db.t
        with self.db.tx(None) as con:
            con.execute("select pg_advisory_xact_lock(hashtext(%s))", [purchase_id])  # one settle per purchase
            if con.execute(f"select 1 from {t('billing_payments')} where purchase_id = %s", [purchase_id]).fetchone():
                return None
            n = con.execute(f"insert into {t('billing_counters')} (name, n) values ('receipt', 1) on conflict (name) "
                            f"do update set n = {t('billing_counters')}.n + 1 returning n").fetchone()["n"]
            receipt = {**receipt, "number": f"DW-{receipt['date'][:4]}-{n:06d}"}
            con.execute(f"insert into {t('billing_payments')} (purchase_id, workspace_id, number, receipt) "
                        "values (%s, %s, %s, %s::jsonb)", [purchase_id, workspace_id, receipt["number"], json.dumps(receipt)])
            return receipt

    def receipts(self, workspace_id: str) -> list[dict]:
        with self.db.tx(None) as con:
            rows = con.execute(f"select receipt from {self.db.t('billing_payments')} where workspace_id = %s "
                               "order by created_at desc", [workspace_id]).fetchall()
        return [r["receipt"] for r in rows]


def unavailable(message: str, code: str = "billing_unavailable") -> HTTPException:
    return HTTPException(409, {"message": message, "code": code})


class Subscriptions:
    def __init__(self, billing, payments, provider, names, preferences=None):
        self.billing = billing  # plans, accounts and limits (billing.Billing)
        self.payments = payments
        self.provider = provider  # None until CHIP is configured
        self.names = names  # workspace id → name, for receipts
        self.preferences = preferences

    # Starting a payment

    def lines_for(self, account: dict, kind: str, plan: str | None, seats: int, now: datetime) -> list[dict]:
        if kind == "topup":
            if TOPUP["price"] is None:
                raise unavailable("Extra AI credits go on sale at launch.")
            return [{"name": f"{TOPUP['credits']:,} extra AI credits for {now:%B %Y}", "price": TOPUP["price"], "quantity": 1}]
        if plan not in PRICES or PLANS[plan]["creators"] != "seats":
            raise HTTPException(400, "Choose Pro or Team.")
        price = PRICES[plan]
        if price is None:
            raise unavailable("Paid plans open at launch, priced from what the AI really costs to run.")
        line = {"name": f"datawazi {PLANS[plan]['name']}, one month", "price": price, "quantity": seats}
        # Changing plan mid-month: the unused part of what was paid comes off the new plan.
        end = account.get("period_end")
        old = PRICES.get(account["plan"])
        if end and old and account.get("period_start"):
            start_at, end_at = datetime.fromisoformat(account["period_start"]), datetime.fromisoformat(end)
            left = max(0.0, (end_at - now) / (end_at - start_at))
            line["discount"] = min(round(old * account.get("seats", 1) * left), price * seats)
        return [line]

    def with_tax(self, lines: list[dict]) -> list[dict]:
        rate = tax_rate()
        if not rate:
            return lines
        net = sum(line["price"] * line.get("quantity", 1) - line.get("discount", 0) for line in lines)
        return lines + [{"name": f"Service tax ({rate:.0%})", "price": round(net * rate), "quantity": 1, "tax": True}]

    def checkout(self, workspace_id: str, email: str, kind: str, plan: str | None, seats: int, site: str) -> dict:
        if self.provider is None:
            raise unavailable("Payments aren't set up on this server yet.")
        account = self.billing.account(workspace_id)
        now = datetime.now(UTC)
        seats = max(1, min(int(seats or 1), 500))
        lines = self.with_tax(self.lines_for(account, kind, plan, seats, now))
        reference = f"{workspace_id}:{kind}:{plan or ''}:{seats}"
        try:
            purchase = self.provider.create(email, lines, reference, f"{site}/billing?paid=1", f"{site}/billing?failed=1",
                                            f"{site}/api/billing/callback", save_card=kind == "plan")
        except PaymentError as exc:
            raise HTTPException(502, str(exc)) from exc
        return {"id": purchase["id"], "checkout_url": purchase["checkout_url"]}

    # Settling a paid purchase

    def settle(self, purchase_id: str, expect_workspace: str | None = None) -> str:
        """Ask the provider about the purchase and, when it's paid, apply it once. Returns its status."""
        if self.provider is None:
            raise unavailable("Payments aren't set up on this server yet.")
        try:
            purchase = self.provider.get(purchase_id)
        except PaymentError as exc:
            raise HTTPException(502, str(exc)) from exc
        try:
            workspace_id, kind, plan, seats = (purchase.get("reference") or "").split(":")
        except ValueError:
            raise HTTPException(404, "Not a datawazi purchase.") from None
        if expect_workspace and workspace_id != expect_workspace:
            raise HTTPException(404, "Not a purchase for this workspace.")
        if purchase.get("status") != "paid":
            return purchase.get("status") or "unknown"
        self.apply(purchase, workspace_id, kind, plan, int(seats or 1))
        return "paid"

    def apply(self, purchase: dict, workspace_id: str, kind: str, plan: str, seats: int) -> None:
        now = datetime.now(UTC)
        lines = (purchase.get("purchase") or {}).get("products") or []
        receipt = self.receipt(purchase, workspace_id, lines, now)
        if self.payments.record(purchase["id"], workspace_id, receipt) is None:
            return  # already settled (the callback and the page's check both arrive)
        with self.billing.lock:
            account = self.billing.account(workspace_id)
            if kind == "topup":
                month = now.strftime("%Y-%m")
                topups = account.setdefault("topup_credits", {})
                topups[month] = topups.get(month, 0) + TOPUP["credits"]
            elif kind in ("plan", "renewal"):
                start = now if kind == "plan" else datetime.fromisoformat(account["period_end"])
                account.update({"plan": plan, "seats": seats, "status": "active", "period_start": stamp(start),
                                "period_end": stamp(add_month(start)), "retries": 0, "next_try": None})
                if kind == "plan":
                    account["cancel_at_period_end"] = False
                    if purchase.get("is_recurring_token"):
                        account["recurring_token"] = purchase["id"]
                    account["payment_method"] = card_of(purchase) or account.get("payment_method")
                    account["payer"] = (purchase.get("client") or {}).get("email")
            self.billing.accounts.save(workspace_id, account)

    def receipt(self, purchase: dict, workspace_id: str, lines: list[dict], now: datetime) -> dict:
        rows = []
        for line in lines:
            qty = int(line.get("quantity", 1))
            amount = int(line["price"]) * qty - int(line.get("discount", 0) or 0)
            rows.append({"description": line["name"], "quantity": qty, "unit_price": int(line["price"]),
                         "discount": int(line.get("discount", 0) or 0), "amount": amount, "tax": bool(line.get("tax"))})
        subtotal = sum(r["amount"] for r in rows if not r["tax"])
        tax = sum(r["amount"] for r in rows if r["tax"])
        return {"date": stamp(now), "workspace": self.names(workspace_id), "bill_to": (purchase.get("client") or {}).get("email"),
                "currency": "MYR", "lines": [r for r in rows if not r["tax"]], "subtotal": subtotal,
                "tax": {"name": "Service tax (SST)", "rate": tax_rate(), "amount": tax}, "total": subtotal + tax,
                "paid_with": card_of(purchase), "purchase_id": purchase["id"]}

    # Cancelling

    def cancel(self, workspace_id: str, cancel: bool) -> dict:
        with self.billing.lock:
            account = self.billing.account(workspace_id)
            if account["plan"] == "free":
                raise HTTPException(400, "The Free plan has nothing to cancel.")
            account["cancel_at_period_end"] = cancel
            self.billing.accounts.save(workspace_id, account)
        return account

    # Renewals, run by the scheduler

    def renew_due(self, now: datetime) -> list[str]:
        """Charge saved cards for plans whose month has ended; retry failures, then move to Free."""
        done = []
        for workspace_id in self.billing.accounts.ids():
            account = self.billing.account(workspace_id)
            end = account.get("period_end")
            if account["plan"] == "free" or not end or datetime.fromisoformat(end) > now:
                continue
            if account.get("next_try") and datetime.fromisoformat(account["next_try"]) > now:
                continue
            if account.get("cancel_at_period_end") or not account.get("recurring_token") or self.provider is None:
                self.downgrade(workspace_id, "cancelled" if account.get("cancel_at_period_end") else "no card")
                done.append(workspace_id)
                continue
            self.renew(workspace_id, account, now)
            done.append(workspace_id)
        return done

    def renew(self, workspace_id: str, account: dict, now: datetime) -> None:
        plan, seats = account["plan"], account.get("seats", 1)
        price = PRICES.get(plan)
        status = "error"
        if price is not None:
            lines = self.with_tax([{"name": f"datawazi {PLANS[plan]['name']}, one month", "price": price, "quantity": seats}])
            try:
                purchase = self.provider.charge(account.get("payer") or "", lines, f"{workspace_id}:renewal:{plan}:{seats}",
                                                account["recurring_token"])
                status = purchase.get("status")
                if status == "paid":
                    self.apply(purchase, workspace_id, "renewal", plan, seats)
                    return
            except PaymentError:
                log.warning("Renewal charge failed for %s", workspace_id, exc_info=True)
        with self.billing.lock:
            account = self.billing.account(workspace_id)
            tries = account.get("retries", 0) + 1
            if tries <= len(RETRY_DAYS):
                account.update({"status": "past_due", "retries": tries,
                                "next_try": stamp(now + timedelta(days=RETRY_DAYS[tries - 1]))})
                self.billing.accounts.save(workspace_id, account)
        if tries > len(RETRY_DAYS):
            self.downgrade(workspace_id, "payment failed")
            return
        self.tell(workspace_id, "The plan's payment didn't go through",
                  f"The card is tried again in {RETRY_DAYS[tries - 1]} days. Update it in Billing to keep "
                  f"{PLANS[plan]['name']}.")

    def downgrade(self, workspace_id: str, why: str) -> None:
        with self.billing.lock:
            account = self.billing.account(workspace_id)
            token = account.get("recurring_token")
            was = PLANS.get(account["plan"], PLANS["free"])["name"]
            fresh = default_account()
            fresh["topup_credits"] = account.get("topup_credits") or {}
            self.billing.accounts.save(workspace_id, fresh)
        if token and self.provider is not None:
            try:
                self.provider.forget_card(token)
            except PaymentError:
                log.warning("Couldn't remove the saved card for %s", workspace_id, exc_info=True)
        self.tell(workspace_id, f"Moved from {was} to Free",
                  "The plan ended" + (" after its payment failed three times." if why == "payment failed" else ".")
                  + " Everything is kept; limits are Free's from now on.")

    def tell(self, workspace_id: str, title: str, text: str) -> None:
        with workspaces.use(workspace_id):
            notices.notify(self.preferences, "billing", title, text, link="/billing", by=notices.SYSTEM)
