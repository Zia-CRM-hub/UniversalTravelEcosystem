"""Checkout saga and Razorpay gateway."""
import hashlib
import hmac
import json

import httpx
import pytest

from app.core.config import Settings
from app.core.errors import PaymentError, ProviderError
from app.providers.base import BookResult, TravelProvider
from app.providers.http import CircuitBreaker
from app.services.payments import DemoGateway, RazorpayGateway, build_gateway
from tests.conftest import D1, D2, confirm, first_offer, open_checkout, register


class RecordingGateway(DemoGateway):
    def __init__(self, fail_authorize=False, fail_capture=False):
        self.calls, self.fail_authorize, self.fail_capture = [], fail_authorize, fail_capture

    async def authorize(self, order_id, amount, currency, payment):
        self.calls.append(("authorize", amount))
        if self.fail_authorize:
            raise PaymentError("card declined")
        return "pay_1"

    async def capture(self, payment_id, amount, currency):
        self.calls.append(("capture", amount))
        if self.fail_capture:
            raise PaymentError("capture down")

    async def void(self, payment_id):
        self.calls.append(("void",))

    async def refund(self, payment_id, amount):
        self.calls.append(("refund", amount))


class FlakyProvider(TravelProvider):
    """Succeeds for the first `ok` bookings, then fails. Records cancellations."""

    name, kinds = "demo", frozenset({"flight", "train", "bus", "hotel"})

    def __init__(self, ok, cancel_fails=False):
        self.ok, self.booked, self.cancelled, self.cancel_fails = ok, 0, [], cancel_fails

    async def book(self, *a, **k):
        if self.booked >= self.ok:
            raise ProviderError("demo", "sold out", status=500)
        self.booked += 1
        return BookResult(f"ord{self.booked}", f"R{self.booked}")

    async def cancel(self, order_id):
        if self.cancel_fails:
            raise ProviderError("demo", "cancel down", status=500)
        self.cancelled.append(order_id)


def trip_offers(client):
    body = {"origin": "Mumbai", "destination": "Goa", "start": D1, "end": D2, "travelers": 1, "mode": "train"}
    plan = client.post("/v1/trips/plan", json=body).json()
    return [plan["outbound"], plan["return"], plan["hotel"]], plan["total"]


def test_success_authorizes_then_captures_total_once(client):
    h = register(client)
    gw = client.app.state.gateway = RecordingGateway()
    offers, total = trip_offers(client)
    co = open_checkout(client, h, offers).json()
    assert confirm(client, h, co["id"]).status_code == 200
    assert gw.calls == [("authorize", total), ("capture", total)]  # one payment, captured after all bookings


def test_declined_payment_books_nothing(client):
    h = register(client)
    client.app.state.gateway = RecordingGateway(fail_authorize=True)
    offer = first_offer(client)
    flaky = client.app.state.providers["demo"] = FlakyProvider(ok=99)
    co = open_checkout(client, h, [offer]).json()
    r = confirm(client, h, co["id"])
    assert r.status_code == 402 and r.json()["code"] == "payment_failed"
    assert flaky.booked == 0


def test_second_supplier_failure_undoes_first_and_releases_payment(client):
    h = register(client)
    gw = client.app.state.gateway = RecordingGateway()
    offers, _ = trip_offers(client)
    flaky = client.app.state.providers["demo"] = FlakyProvider(ok=1)
    co = open_checkout(client, h, offers).json()
    r = confirm(client, h, co["id"])
    assert r.status_code == 502
    assert flaky.cancelled == ["ord1"]  # the first supplier order was compensated
    assert ("void",) in gw.calls and not any(c[0] == "capture" for c in gw.calls)  # customer never charged
    rows = client.get("/v1/bookings", headers=h).json()
    assert sorted(b["status"] for b in rows) == ["cancelled", "failed"]
    again = confirm(client, h, co["id"])  # a failed checkout is final
    assert again.status_code == 409 and again.json()["code"] == "checkout_failed"


def test_failed_compensation_is_flagged_for_a_human(client):
    h = register(client)
    client.app.state.gateway = RecordingGateway()
    offers, _ = trip_offers(client)
    client.app.state.providers["demo"] = FlakyProvider(ok=1, cancel_fails=True)
    co = open_checkout(client, h, offers).json()
    assert confirm(client, h, co["id"]).status_code == 502
    statuses = sorted(b["status"] for b in client.get("/v1/bookings", headers=h).json())
    assert statuses == ["failed", "needs_attention"]


def test_capture_failure_keeps_bookings(client):
    h = register(client)
    client.app.state.gateway = RecordingGateway(fail_capture=True)
    co = open_checkout(client, h, [first_offer(client)]).json()
    r = confirm(client, h, co["id"])
    assert r.status_code == 200 and r.json()["bookings"][0]["status"] == "confirmed"


def test_cancel_refunds_only_that_bookings_share(client):
    h = register(client)
    gw = client.app.state.gateway = RecordingGateway()
    offers, _ = trip_offers(client)
    co = open_checkout(client, h, offers).json()
    bookings = confirm(client, h, co["id"]).json()["bookings"]
    hotel = next(b for b in bookings if b["kind"] == "hotel")
    assert client.post(f"/v1/bookings/{hotel['id']}/cancel", headers=h).json()["status"] == "cancelled"
    assert gw.calls[-1] == ("refund", hotel["total_price"])


# ---- Razorpay gateway --------------------------------------------------------

SECRET = "rzp_secret"


def rzp(handler):
    return RazorpayGateway("rzp_key", SECRET, "https://rzp.test", timeout=5, retries=0, breaker=CircuitBreaker(),
                           transport=httpx.MockTransport(handler))


def sign(order_id, payment_id, secret=SECRET):
    return hmac.new(secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()


def payment(status="authorized", **kw):
    return {"id": "pay_1", "order_id": "order_1", "amount": 50000, "currency": "INR", "status": status, **kw}


@pytest.mark.anyio
async def test_razorpay_create_order_and_basic_auth():
    seen = {}

    def handler(req):
        seen["auth"] = req.headers["Authorization"]
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"id": "order_1"})

    gw = rzp(handler)
    assert await gw.create_order(50000, "INR", "r" * 60) == "order_1"
    assert seen["body"] == {"amount": 50000, "currency": "INR", "receipt": "r" * 40}
    assert seen["auth"].startswith("Basic ") and gw.client_config() == {"key_id": "rzp_key"}


@pytest.mark.anyio
async def test_razorpay_authorize_checks_signature_order_amount_and_status():
    gw = rzp(lambda r: httpx.Response(200, json=payment()))
    good = {"payment_id": "pay_1", "signature": sign("order_1", "pay_1")}
    assert await gw.authorize("order_1", 50000, "INR", good) == "pay_1"
    for bad_payment, why in [
        (None, "missing"),
        ({"payment_id": "pay_1", "signature": sign("order_1", "pay_1", "wrong-secret")}, "signature"),
        ({"payment_id": "pay_2", "signature": sign("order_1", "pay_1")}, "signature"),  # id swapped
    ]:
        with pytest.raises(PaymentError, match=why):
            await gw.authorize("order_1", 50000, "INR", bad_payment)
    with pytest.raises(PaymentError, match="match"):
        await gw.authorize("order_1", 99999, "INR", good)  # amount differs from our order
    other = rzp(lambda r: httpx.Response(200, json=payment(order_id="order_other")))
    with pytest.raises(PaymentError, match="match"):
        await other.authorize("order_1", 50000, "INR", good)  # payment belongs to a different order
    failed = rzp(lambda r: httpx.Response(200, json=payment(status="failed")))
    with pytest.raises(PaymentError, match="failed"):
        await failed.authorize("order_1", 50000, "INR", good)


@pytest.mark.anyio
async def test_razorpay_capture_void_refund_semantics():
    log, state = [], {"status": "authorized"}

    def handler(req):
        log.append((req.method, req.url.path, json.loads(req.content) if req.content else None))
        if req.method == "GET":
            return httpx.Response(200, json=payment(status=state["status"]))
        return httpx.Response(200, json=payment(status="captured"))

    gw = rzp(handler)
    await gw.capture("pay_1", 50000, "INR")
    assert log[-1] == ("POST", "/v1/payments/pay_1/capture", {"amount": 50000, "currency": "INR"})
    log.clear()
    await gw.void("pay_1")  # authorized: nothing to call, Razorpay releases it
    assert [m for m, _, _ in log] == ["GET"]
    state["status"] = "captured"
    log.clear()
    await gw.capture("pay_1", 50000, "INR")  # already captured: no second capture
    assert [m for m, _, _ in log] == ["GET"]
    log.clear()
    await gw.void("pay_1")  # captured (auto-capture account): refund in full
    assert log[-1][1] == "/v1/payments/pay_1/refund"
    await gw.refund("pay_1", 1234)
    assert log[-1] == ("POST", "/v1/payments/pay_1/refund", {"amount": 1234})


@pytest.mark.anyio
async def test_razorpay_errors_become_payment_errors():
    gw = rzp(lambda r: httpx.Response(400, json={"error": {"description": "bad"}}))
    with pytest.raises(PaymentError):
        await gw.create_order(100, "INR", "r")


def test_gateway_selection_and_config():
    assert isinstance(build_gateway(Settings(_env_file=None)), DemoGateway)
    s = Settings(_env_file=None, payment_gateway="razorpay", razorpay_key_id="k", razorpay_key_secret="s")
    assert isinstance(build_gateway(s), RazorpayGateway)
    with pytest.raises(RuntimeError, match="RAZORPAY_KEY_ID"):
        Settings(_env_file=None, payment_gateway="razorpay").validate_runtime()
    with pytest.raises(RuntimeError, match="PAYMENT_GATEWAY=demo"):
        Settings(_env_file=None, app_env="prod", providers="duffel", duffel_token="x", secret_key="k" * 40,
                 payment_gateway="demo").validate_runtime()
