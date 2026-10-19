"""Payment gateways.

Flow (one payment can cover several bookings, e.g. a whole trip):
  create_order -> customer pays in the gateway's checkout UI (authorization only)
  -> authorize (server verifies the payment) -> supplier bookings
  -> capture on success, or void on failure.

Because we never capture before the suppliers confirm, a lost client or a crash
cannot permanently charge a customer: uncaptured payments are released by the gateway.
"""
import hashlib
import hmac
import secrets
from typing import Protocol

from ..core.config import Settings
from ..core.errors import PaymentError, ProviderError
from ..providers.http import CircuitBreaker, ProviderHTTP


class PaymentGateway(Protocol):
    name: str

    def client_config(self) -> dict: ...
    async def create_order(self, amount: int, currency: str, receipt: str) -> str: ...
    async def authorize(self, order_id: str, amount: int, currency: str, payment: dict | None) -> str: ...
    async def capture(self, payment_id: str, amount: int, currency: str) -> None: ...
    async def void(self, payment_id: str) -> None: ...
    async def refund(self, payment_id: str, amount: int) -> None: ...
    async def aclose(self) -> None: ...


class DemoGateway:
    """Takes no money. For development and demos only."""

    name = "demo"

    def client_config(self):
        return {}

    async def create_order(self, amount, currency, receipt):
        return "demo_order_" + secrets.token_hex(6)

    async def authorize(self, order_id, amount, currency, payment):
        return "demo_pay_" + secrets.token_hex(6)

    async def capture(self, payment_id, amount, currency):
        return None

    async def void(self, payment_id):
        return None

    async def refund(self, payment_id, amount):
        return None

    async def aclose(self):
        return None


class RazorpayGateway:
    """Razorpay Orders + manual capture.

    Verified against Razorpay docs: Basic auth with key id/secret; POST /v1/orders
    (amount, currency, receipt<=40, notes); checkout returns razorpay_payment_id /
    razorpay_order_id / razorpay_signature; signature = HMAC-SHA256(order_id|payment_id,
    key_secret); POST /v1/payments/{id}/capture {amount, currency} only works on
    `authorized` payments and the amount must equal the authorized amount; uncaptured
    payments are auto-refunded within the manual-capture timeout (12 min to 3 days).
    NOT verified (from memory, never run against Razorpay): GET /v1/payments/{id} response
    fields used here (status, order_id, amount, currency) and POST /v1/payments/{id}/refund.
    Turn on Manual Capture in the Razorpay dashboard (Account & Settings > Payments Capture).
    """

    name = "razorpay"

    def __init__(self, key_id: str, key_secret: str, base_url: str, *, timeout: float, retries: int,
                 breaker: CircuitBreaker, transport=None):
        self.key_id, self._secret, self._auth = key_id, key_secret, (key_id, key_secret)
        self.http = ProviderHTTP("razorpay", base_url, timeout=timeout, retries=retries, breaker=breaker, transport=transport)

    @classmethod
    def from_settings(cls, s: Settings, transport=None):
        return cls(s.razorpay_key_id, s.razorpay_key_secret.get_secret_value(), s.razorpay_base_url,
                   timeout=s.provider_timeout_s, retries=s.provider_retries, transport=transport,
                   breaker=CircuitBreaker(s.breaker_threshold, s.breaker_reset_s))

    def client_config(self):
        return {"key_id": self.key_id}  # the key id is public by design

    async def _call(self, method: str, path: str, *, retry: bool = False, **kw) -> dict:
        try:
            return await self.http.request(method, path, retry=retry, auth=self._auth, **kw)
        except ProviderError as e:
            raise PaymentError(e.message) from None

    async def _get(self, payment_id: str) -> dict:
        return await self._call("GET", f"/v1/payments/{payment_id}", retry=True)

    async def create_order(self, amount, currency, receipt):
        res = await self._call("POST", "/v1/orders", json={"amount": amount, "currency": currency, "receipt": receipt[:40]})
        return res["id"]

    async def authorize(self, order_id, amount, currency, payment):
        pid, sig = (payment or {}).get("payment_id"), (payment or {}).get("signature")
        if not pid or not sig:
            raise PaymentError("payment details missing")
        expected = hmac.new(self._secret.encode(), f"{order_id}|{pid}".encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            raise PaymentError("invalid payment signature")
        p = await self._get(pid)
        if p.get("order_id") != order_id or p.get("amount") != amount or p.get("currency") != currency:
            raise PaymentError("payment does not match the order")
        if p.get("status") not in ("authorized", "captured"):
            raise PaymentError(f"payment is {p.get('status')}")
        return pid

    async def capture(self, payment_id, amount, currency):
        if (await self._get(payment_id)).get("status") == "captured":
            return  # account is on auto-capture, or a previous attempt landed
        try:
            await self._call("POST", f"/v1/payments/{payment_id}/capture", json={"amount": amount, "currency": currency})
        except PaymentError:
            if (await self._get(payment_id)).get("status") != "captured":
                raise

    async def void(self, payment_id):
        # Authorized but uncaptured: nothing to do, Razorpay releases it automatically.
        # If the account auto-captures, the money is already taken, so refund it.
        if (await self._get(payment_id)).get("status") == "captured":
            await self._call("POST", f"/v1/payments/{payment_id}/refund", json={})

    async def refund(self, payment_id, amount):
        await self._call("POST", f"/v1/payments/{payment_id}/refund", json={"amount": amount})

    async def aclose(self):
        await self.http.aclose()


def build_gateway(s: Settings, transport=None) -> PaymentGateway:
    return RazorpayGateway.from_settings(s, transport) if s.payment_gateway == "razorpay" else DemoGateway()
