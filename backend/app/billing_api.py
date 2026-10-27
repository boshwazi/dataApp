"""Billing routes: what admins see, checkout, settling a purchase, cancelling, and CHIP's callback."""

import json
import os

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from . import workspaces
from .subscriptions import PRICES, TOPUP


class CheckoutBody(BaseModel):
    kind: str = "plan"  # plan | topup
    plan: str | None = None
    seats: int = 1


class CancelBody(BaseModel):
    cancel: bool = True


def build_router(plans, subscriptions, mode: str) -> APIRouter:
    router = APIRouter(prefix="/api/billing")

    def admin() -> workspaces.Access:
        access = workspaces.current()
        if not access.at_least("admin"):
            raise HTTPException(403, "Only workspace admins can see or change billing.")
        return access

    @router.get("")
    def get_billing():
        """The plan, AI credits this month, payment details and receipts."""
        access = admin()
        return {**plans.view(access.workspace_id), "receipts": subscriptions.payments.receipts(access.workspace_id),
                "payments_ready": subscriptions.provider is not None and mode != "local",
                "prices": dict(PRICES), "topup": dict(TOPUP)}

    @router.post("/checkout")
    def checkout(body: CheckoutBody, request: Request):
        access = admin()
        if mode == "local":
            raise HTTPException(400, "Plans and payments come with the hosted version.")
        if body.kind not in ("plan", "topup"):
            raise HTTPException(400, "Choose a plan or extra credits.")
        site = os.getenv("PUBLIC_URL", "").rstrip("/") or str(request.base_url).rstrip("/")
        return subscriptions.checkout(access.workspace_id, access.user.email, body.kind, body.plan, body.seats, site)

    @router.post("/purchases/{purchase_id}/settle")
    def settle(purchase_id: str):
        """After CHIP's page sends the admin back: check the purchase with CHIP and apply it if it's paid."""
        access = admin()
        return {"status": subscriptions.settle(purchase_id, expect_workspace=access.workspace_id)}

    @router.post("/cancel")
    def cancel(body: CancelBody):
        access = admin()
        account = subscriptions.cancel(access.workspace_id, body.cancel)
        return {"cancel_at_period_end": account["cancel_at_period_end"]}

    @router.post("/callback")
    async def callback(request: Request):
        """CHIP's signed callback for a paid purchase. Open without sign-in (auth.PUBLIC); the signature is what
        proves it came from CHIP, and the purchase is still fetched from CHIP before anything is applied."""
        if subscriptions.provider is None:
            raise HTTPException(404, "Payments aren't set up.")
        body = await request.body()
        if not subscriptions.provider.verify(body, request.headers.get("x-signature", "")):
            raise HTTPException(401, "Bad signature.")
        try:
            purchase_id = json.loads(body)["id"]
        except (ValueError, KeyError, TypeError):
            raise HTTPException(400, "No purchase id.") from None
        return {"status": subscriptions.settle(str(purchase_id))}

    return router
