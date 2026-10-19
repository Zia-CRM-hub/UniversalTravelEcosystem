import asyncio
from datetime import timedelta

from app.core.config import settings
from app.core.errors import PriceChanged, ProviderError, ProviderUnavailable
from app.db import SessionLocal
from app.models import OfferSnapshot, now
from app.providers.base import TravelProvider
from tests.conftest import D1, D2, book, confirm, first_offer, open_checkout, register


def test_register_login_duplicate(client):
    register(client)
    dup = {"email": "A@b.com", "name": "x", "password": "password123"}
    assert client.post("/v1/auth/register", json=dup).status_code == 409
    assert client.post("/v1/auth/login", json={"email": "a@b.com", "password": "wrong-pass"}).status_code == 401
    assert client.post("/v1/auth/login", json={"email": "a@b.com", "password": "password123"}).status_code == 200


def test_search_shape_sorted_and_party_pricing(client):
    one = first_offer(client, "train", 1)
    two = first_offer(client, "train", 2)
    assert two["price"] == one["price"] * 2 and two["pax"] == 2
    r = client.get("/v1/search/transport", params={"kind": "train", "origin": "Delhi", "destination": "Goa", "date": D1})
    prices = [o["price"] for o in r.json()["offers"]]
    assert prices == sorted(prices) and r.json()["warnings"] == []
    assert "raw" not in one and one["offer_id"] and one["provider"] == "demo"


def test_search_validation_has_codes(client):
    same = client.get("/v1/search/transport", params={"kind": "bus", "origin": "Delhi", "destination": "delhi", "date": D1})
    assert same.status_code == 422 and same.json()["code"] == "same_place"
    bad = client.get("/v1/search/transport", params={"kind": "plane", "origin": "Delhi", "destination": "Goa", "date": D1})
    assert bad.status_code == 422 and bad.json()["code"] == "validation_error"
    rev = client.get("/v1/search/hotels", params={"city": "Goa", "check_in": D2, "check_out": D1})
    assert rev.status_code == 422


def test_no_booking_without_payment_step(client):
    """The old direct-booking endpoint is gone: payment can't be bypassed."""
    h = register(client)
    assert client.post("/v1/bookings", headers=h, json={}).status_code == 405
    assert client.post("/v1/checkouts", json={"offer_ids": ["x"]}).status_code == 401


def test_checkout_flow_replay_and_ownership(client):
    h = register(client)
    o = first_offer(client)
    co = open_checkout(client, h, [o])
    assert co.status_code == 201
    body = co.json()
    assert body["amount"] == o["price"] and body["gateway"] == "demo" and body["offers"][0]["offer_id"] == o["offer_id"]

    stranger = register(client, "c@d.com")
    assert confirm(client, stranger, body["id"]).status_code == 404

    r = confirm(client, h, body["id"])
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "booked" and len(out["bookings"]) == 1
    b = out["bookings"][0]
    assert b["status"] == "confirmed" and b["total_price"] == o["price"] and b["reference"].startswith("UT")
    assert "born_on" not in b["passengers"][0]  # PII not echoed back

    replay = confirm(client, h, body["id"])  # e.g. client retry after a dropped connection
    assert replay.status_code == 200 and replay.json()["bookings"][0]["id"] == b["id"]
    assert len(client.get("/v1/bookings", headers=h).json()) == 1

    assert client.post(f"/v1/bookings/{b['id']}/cancel", headers=stranger).status_code == 404
    assert client.post(f"/v1/bookings/{b['id']}/cancel", headers=h).json()["status"] == "cancelled"
    assert client.post(f"/v1/bookings/{b['id']}/cancel", headers=h).status_code == 409


def test_checkout_validates_offers(client):
    h = register(client)
    assert open_checkout(client, h, [{"offer_id": "doesnotexist"}]).status_code == 404
    flight, train = first_offer(client, "flight", 1), first_offer(client, "train", 2)
    mixed = open_checkout(client, h, [flight, train])
    assert mixed.status_code == 422 and mixed.json()["code"] == "mixed_party"


def test_passenger_count_must_match_offer(client):
    h = register(client)
    o = first_offer(client, adults=2)
    co = open_checkout(client, h, [o]).json()
    r = confirm(client, h, co["id"], n=1)
    assert r.status_code == 422 and r.json()["code"] == "passenger_count"
    # a failed checkout is final; the customer starts a new one
    assert confirm(client, h, co["id"], n=2).status_code == 409
    assert book(client, h, o, n=2).status_code == 200


def test_expired_offer(client):
    h = register(client)
    o = first_offer(client)

    async def expire():
        async with SessionLocal() as db:
            (await db.get(OfferSnapshot, o["offer_id"])).expires_at = now() - timedelta(minutes=1)
            await db.commit()

    asyncio.run(expire())
    assert open_checkout(client, h, [o]).status_code == 410
    assert client.get(f"/v1/offers/{o['offer_id']}").status_code == 410


def test_offer_expiring_between_checkout_and_confirm_is_never_charged(client):
    h = register(client)
    o = first_offer(client)
    co = open_checkout(client, h, [o]).json()

    async def expire():
        async with SessionLocal() as db:
            (await db.get(OfferSnapshot, o["offer_id"])).expires_at = now() - timedelta(minutes=1)
            await db.commit()

    asyncio.run(expire())
    r = confirm(client, h, co["id"])
    assert r.status_code == 410 and r.json()["code"] == "offer_expired"


def test_offer_lookup_hides_raw(client):
    o = first_offer(client)
    r = client.get(f"/v1/offers/{o['offer_id']}")
    assert r.status_code == 200 and r.json()["price"] == o["price"] and "raw" not in r.json()


class _Failing(TravelProvider):
    name, kinds = "demo", frozenset({"flight", "train", "bus", "hotel"})

    def __init__(self, exc):
        self.exc = exc

    async def book(self, *a, **k):
        raise self.exc

    async def cancel(self, *a, **k):
        raise self.exc


def test_supplier_failures_map_to_codes(client):
    h = register(client)
    cases = [
        (ProviderError("demo", "bad passport", client_error=True), 422, "provider_rejected"),
        (PriceChanged("demo", "moved"), 409, "price_changed"),
        (ProviderUnavailable("demo", "open"), 503, "provider_unavailable"),
        (ProviderError("demo", "boom", status=500), 502, "provider_error"),
    ]
    for exc, status, code in cases:
        o = first_offer(client)
        real = client.app.state.providers["demo"]
        client.app.state.providers["demo"] = _Failing(exc)
        r = book(client, h, o)
        client.app.state.providers["demo"] = real
        assert (r.status_code, r.json()["code"]) == (status, code)
    rows = client.get("/v1/bookings", headers=h).json()
    assert len(rows) == 4 and {b["status"] for b in rows} == {"failed"}


def test_failed_cancel_keeps_booking_confirmed(client):
    h = register(client)
    b = book(client, h, first_offer(client)).json()["bookings"][0]
    real = client.app.state.providers["demo"]
    client.app.state.providers["demo"] = _Failing(ProviderError("demo", "down", status=500))
    r = client.post(f"/v1/bookings/{b['id']}/cancel", headers=h)
    client.app.state.providers["demo"] = real
    assert r.status_code == 502
    assert client.get("/v1/bookings", headers=h).json()[0]["status"] == "confirmed"


def test_hotel_booking_for_party(client):
    h = register(client)
    r = client.get("/v1/search/hotels", params={"city": "Goa", "check_in": D1, "check_out": D2, "guests": 3})
    o = r.json()["offers"][0]
    assert o["pax"] == 3 and o["price"] == o["price_per_night"] * 4
    assert book(client, h, o, n=3).json()["bookings"][0]["total_price"] == o["price"]


def test_one_slow_supplier_degrades_not_fails(client):
    class Boom(_Failing):
        name = "boom"

        async def search_transport(self, *a, **k):
            raise ProviderError("boom", "down", status=500)

    client.app.state.providers["boom"] = Boom(None)
    try:
        r = client.get("/v1/search/transport", params={"kind": "flight", "origin": "Delhi", "destination": "Goa", "date": D1})
    finally:
        del client.app.state.providers["boom"]
    body = r.json()
    assert r.status_code == 200 and body["offers"]
    assert body["warnings"] == [{"provider": "boom", "problem": "error"}]


def test_trip_plan_budget_in_major_units(client):
    body = {"origin": "Mumbai", "destination": "Goa", "start": D1, "end": D2, "travelers": 2, "mode": "train"}
    r = client.post("/v1/trips/plan", json=body)
    assert r.status_code == 200
    plan = r.json()
    assert len(plan["itinerary"]) == 4 and plan["total"] == (
        plan["outbound"]["price"] + plan["return"]["price"] + plan["hotel"]["price"])
    over = client.post("/v1/trips/plan", json={**body, "budget": 100})
    assert over.status_code == 422 and over.json()["code"] == "over_budget"
    exact = -(-plan["total"] // 100)
    assert client.post("/v1/trips/plan", json={**body, "budget": exact}).status_code == 200


def test_whole_trip_is_one_payment_and_three_bookings(client):
    h = register(client)
    body = {"origin": "Mumbai", "destination": "Goa", "start": D1, "end": D2, "travelers": 2, "mode": "train"}
    plan = client.post("/v1/trips/plan", json=body).json()
    offers = [plan["outbound"], plan["return"], plan["hotel"]]
    co = open_checkout(client, h, offers).json()
    assert co["amount"] == plan["total"]
    r = confirm(client, h, co["id"], n=2)
    assert r.status_code == 200
    assert [b["kind"] for b in r.json()["bookings"]] == ["train", "train", "hotel"]
    assert {b["checkout_id"] for b in r.json()["bookings"]} == {co["id"]}


def test_rate_limit(client, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_per_minute", 3)
    codes = [client.get("/v1/places", params={"q": "x"}).status_code for _ in range(5)]  # 422s still count
    assert codes[-1] == 429 and "Retry-After" in client.get("/v1/places", params={"q": "x"}).headers
    assert client.get("/health/live").status_code == 200  # exempt


def test_health_and_metrics(client):
    r = client.get("/health/ready")
    assert r.status_code == 200 and r.json()["providers"][0]["name"] == "demo"
    assert r.headers["X-Request-ID"]
    assert "http_requests_total" in client.get("/metrics").text

