"""Taking payment: a PaymentProvider interface, with CHIP (chip-in.asia) first and a stand-in for tests.

Built to CHIP Collect's OpenAPI spec (docs.chip-in.asia/openapi/chip-collect.yaml):
- POST /purchases/ with brand_id, client.email and purchase.products (prices in cents) gives a checkout_url.
  force_recurring asks CHIP to keep the card for renewals; that first purchase's id is then the recurring token.
- POST /purchases/{new id}/charge/ with {"recurring_token": <first purchase id>} charges the saved card.
- Callbacks are signed: X-Signature is base64 RSA PKCS#1 v1.5 over the SHA-256 of the body, checked against the
  PEM key from GET /public_key/ (a JSON-encoded string).
Card details never reach this server; CHIP's checkout page takes them.
"""

import base64
import json
import threading
import urllib.error
import urllib.request
import uuid

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


class PaymentError(Exception):
    pass


class ChipProvider:
    name = "chip"

    def __init__(self, secret_key: str, brand_id: str, base_url: str = "https://gate.chip-in.asia/api/v1",
                 timeout: float = 30):
        self.key = secret_key
        self.brand_id = brand_id
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        self._public_key = None

    def _call(self, method: str, path: str, body: dict | None = None):
        request = urllib.request.Request(f"{self.base}{path}", method=method,
                                         data=json.dumps(body).encode() if body is not None else None,
                                         headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read() or b"null")
        except urllib.error.HTTPError as exc:
            raise PaymentError(f"CHIP refused the request ({exc.code}): {exc.read()[:300].decode(errors='replace')}") from exc
        except urllib.error.URLError as exc:
            raise PaymentError(f"CHIP could not be reached: {exc.reason}") from exc

    def create(self, email: str, lines: list[dict], reference: str, success_url: str, failure_url: str,
               callback_url: str, save_card: bool) -> dict:
        """A purchase to pay at CHIP's checkout. Lines: {name, price (cents), quantity, discount (cents)}."""
        body = {
            "brand_id": self.brand_id,
            "client": {"email": email},
            "purchase": {"currency": "MYR", "products": [
                {"name": line["name"][:256], "price": int(line["price"]), "quantity": str(line.get("quantity", 1)),
                 **({"discount": int(line["discount"])} if line.get("discount") else {})} for line in lines]},
            "reference": reference,
            "success_redirect": success_url,
            "failure_redirect": failure_url,
            "success_callback": callback_url,
            "force_recurring": save_card,
            "send_receipt": False,  # datawazi issues its own numbered receipts
        }
        return self._call("POST", "/purchases/", body)

    def charge(self, email: str, lines: list[dict], reference: str, token: str) -> dict:
        """Charge a saved card: a new purchase, then charge it with the first purchase's id as the token."""
        purchase = self.create(email, lines, reference, "", "", "", save_card=False)
        return self._call("POST", f"/purchases/{purchase['id']}/charge/", {"recurring_token": token})

    def get(self, purchase_id: str) -> dict:
        return self._call("GET", f"/purchases/{purchase_id}/")

    def forget_card(self, token: str) -> None:
        self._call("POST", f"/purchases/{token}/delete_recurring_token/")

    def public_key(self):
        if self._public_key is None:
            pem = self._call("GET", "/public_key/")  # a JSON string holding the PEM
            self._public_key = serialization.load_pem_public_key(pem.encode())
        return self._public_key

    def verify(self, body: bytes, signature: str) -> bool:
        try:
            self.public_key().verify(base64.b64decode(signature), body, padding.PKCS1v15(), hashes.SHA256())
            return True
        except (InvalidSignature, ValueError, TypeError):
            return False


class StandInProvider:
    """Answers like CHIP, in memory: for tests, and for trying the flow without a CHIP account. Purchases are paid
    when `pay` is called (the stand-in for someone finishing checkout); saved-card charges succeed unless
    `declining` is set."""

    name = "stand-in"

    def __init__(self):
        self.purchases: dict[str, dict] = {}
        self.declining = False
        self.lock = threading.Lock()

    def _new(self, email, lines, reference, save_card, status="created") -> dict:
        total = sum(int(line["price"]) * int(line.get("quantity", 1)) - int(line.get("discount", 0)) for line in lines)
        purchase = {"id": str(uuid.uuid4()), "status": status, "reference": reference, "client": {"email": email},
                    "purchase": {"currency": "MYR", "total": total, "products": lines},
                    "is_recurring_token": False, "recurring_token": None, "save_card": save_card,
                    "transaction_data": {"payment_method": "visa", "extra": {"card_brand": "visa", "masked_pan": "4242"}}}
        with self.lock:
            self.purchases[purchase["id"]] = purchase
        return purchase

    def create(self, email, lines, reference, success_url, failure_url, callback_url, save_card) -> dict:
        purchase = self._new(email, lines, reference, save_card)
        return {**purchase, "checkout_url": f"https://stand-in.chip/checkout/{purchase['id']}"}

    def pay(self, purchase_id: str) -> dict:
        purchase = self.purchases[purchase_id]
        purchase["status"] = "paid"
        if purchase["save_card"]:
            purchase["is_recurring_token"] = True
        return purchase

    def charge(self, email, lines, reference, token) -> dict:
        if token not in self.purchases or not self.purchases[token]["is_recurring_token"]:
            raise PaymentError("Unknown card.")
        return self._new(email, lines, reference, False, status="error" if self.declining else "paid")

    def get(self, purchase_id: str) -> dict:
        if purchase_id not in self.purchases:
            raise PaymentError("No such purchase.")
        return self.purchases[purchase_id]

    def forget_card(self, token: str) -> None:
        if token in self.purchases:
            self.purchases[token]["is_recurring_token"] = False

    def verify(self, body: bytes, signature: str) -> bool:
        return signature == "stand-in-signature"


def card_of(purchase: dict) -> dict | None:
    extra = (purchase.get("transaction_data") or {}).get("extra") or {}
    if not extra.get("masked_pan"):
        return None
    return {"brand": str(extra.get("card_brand") or "card").title(), "last4": str(extra["masked_pan"])[-4:]}
