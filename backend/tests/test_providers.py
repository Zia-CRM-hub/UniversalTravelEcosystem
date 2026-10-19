import json
from datetime import date

import httpx
import pytest

from app.core.config import Settings
from app.core.errors import PriceChanged, ProviderError, ProviderUnavailable
from app.providers.amadeus import AmadeusProvider
from app.providers.duffel import DuffelProvider
from app.providers.http import CircuitBreaker, ProviderHTTP, parse_duration_min
from app.providers.registry import build_providers

pytestmark = pytest.mark.anyio
DAY = date(2030, 1, 15)
PAX = [{"title": "ms", "gender": "f", "given_name": "Asha", "family_name": "Rao", "born_on": "1990-05-17"}]
CONTACT = {"email": "a@b.com", "phone": "+919876543210"}


async def _nosleep(_):
    return None


def make_http(handler, **kw):
    return ProviderHTTP("t", "https://x.test", timeout=5, retries=kw.pop("retries", 2),
                        breaker=kw.pop("breaker", CircuitBreaker(3, 30)), transport=httpx.MockTransport(handler),
                        sleep=_nosleep)


# ---- resilience -------------------------------------------------------------

def test_breaker_states():
    t = [0.0]
    b = CircuitBreaker(2, 10, clock=lambda: t[0])
    b.failure()
    assert b.state == "closed"
    b.failure()
    assert b.state == "open" and not b.allow()
    t[0] = 11
    assert b.state == "half_open" and b.allow()
    b.failure()  # a failed probe re-opens immediately
    assert b.state == "open"
    t[0] = 25
    b.success()
    assert b.state == "closed"


def test_parse_duration():
    assert parse_duration_min("PT2H30M") == 150
    assert parse_duration_min("P1DT1H") == 1500
    assert parse_duration_min(None) == 0 and parse_duration_min("garbage") == 0


async def test_retries_only_when_safe():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(500, json={"errors": [{"message": "oops"}]}) if len(calls) < 3 else httpx.Response(200, json={"ok": 1})

    assert await make_http(handler).request("GET", "/a", retry=True) == {"ok": 1} and len(calls) == 3
    calls.clear()
    with pytest.raises(ProviderError):
        await make_http(handler).request("POST", "/orders", retry=False)  # orders are never retried
    assert len(calls) == 1


async def test_client_error_does_not_trip_breaker_but_server_errors_do():
    b = CircuitBreaker(2, 30)
    bad = make_http(lambda r: httpx.Response(422, json={"errors": [{"message": "nope"}]}), breaker=b)
    for _ in range(5):
        with pytest.raises(ProviderError) as e:
            await bad.request("GET", "/x")
        assert e.value.client_error and e.value.message == "nope"
    assert b.state == "closed"
    down = make_http(lambda r: httpx.Response(503), breaker=b, retries=0)
    for _ in range(2):
        with pytest.raises(ProviderError):
            await down.request("GET", "/x")
    with pytest.raises(ProviderUnavailable):
        await down.request("GET", "/x")


# ---- Duffel -----------------------------------------------------------------

def duffel(handler):
    return DuffelProvider("tok", "https://api.duffel.test", timeout=10, retries=0, breaker=CircuitBreaker(),
                          transport=httpx.MockTransport(handler))


DUFFEL_OFFER = {
    "id": "off_1", "total_amount": "123.45", "total_currency": "GBP", "expires_at": "2030-01-10T10:00:00.000Z",
    "owner": {"name": "British Airways", "iata_code": "BA"},
    "passengers": [{"id": "pas_1"}, {"id": "pas_2"}],
    "slices": [{"origin": {"iata_code": "LHR"}, "destination": {"iata_code": "JFK"}, "duration": "PT8H5M",
                "segments": [{"departing_at": "2030-01-15T09:00:00", "arriving_at": "2030-01-15T12:05:00",
                              "marketing_carrier": {"iata_code": "BA", "name": "British Airways"},
                              "marketing_carrier_flight_number": "117"}]}],
}


async def test_duffel_search_resolves_places_and_normalizes():
    seen = {}

    def handler(req):
        if req.url.path == "/places/suggestions":
            seen["places"] = req.url.params["query"]
            return httpx.Response(200, json={"data": [{"type": "airport", "iata_code": "LGW"}, {"type": "city", "iata_code": "LON"}]})
        seen["headers"] = req.headers
        seen["query"] = dict(req.url.params)
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"data": {"offers": [DUFFEL_OFFER]}})

    found = await duffel(handler).search_transport("flight", "London", "JFK", DAY, 2)
    sl = seen["body"]["data"]["slices"][0]
    assert (sl["origin"], sl["destination"], sl["departure_date"]) == ("LON", "JFK", "2030-01-15")  # city preferred, IATA passthrough
    assert seen["body"]["data"]["passengers"] == [{"type": "adult"}] * 2
    assert seen["headers"]["Duffel-Version"] == "v2" and seen["headers"]["Authorization"] == "Bearer tok"
    assert seen["query"]["return_offers"] == "true"
    o = found[0].offer
    assert o["price"] == 12345 and o["currency"] == "GBP" and o["number"] == "BA117"
    assert o["duration_min"] == 485 and o["stops"] == 0 and o["carrier"] == "British Airways"
    assert found[0].raw["passenger_ids"] == ["pas_1", "pas_2"]


async def test_duffel_unresolvable_place_yields_no_offers():
    p = duffel(lambda r: httpx.Response(200, json={"data": []}))
    assert await p.search_transport("flight", "Atlantis", "JFK", DAY, 1) == []
    assert await p.search_transport("train", "LHR", "JFK", DAY, 1) == []  # flights only


async def test_duffel_book_and_cancel_payloads():
    log = []

    def handler(req):
        log.append((req.method, req.url.path, json.loads(req.content) if req.content else None))
        if req.url.path == "/air/orders":
            return httpx.Response(201, json={"data": {"id": "ord_1", "booking_reference": "ABC123"}})
        if req.url.path == "/air/order_cancellations":
            return httpx.Response(201, json={"data": {"id": "ore_1"}})
        return httpx.Response(200, json={"data": {}})

    p = duffel(handler)
    raw = {"offer_id": "off_1", "passenger_ids": ["pas_1"], "total_amount": "123.45", "total_currency": "GBP"}
    res = await p.book({}, raw, PAX, CONTACT, "WN1")
    assert (res.provider_order_id, res.reference) == ("ord_1", "ABC123")
    body = log[0][2]["data"]
    assert body["selected_offers"] == ["off_1"]
    assert body["payments"] == [{"type": "balance", "currency": "GBP", "amount": "123.45"}]
    assert body["passengers"][0]["id"] == "pas_1" and body["passengers"][0]["phone_number"] == "+919876543210"
    await p.cancel("ord_1")
    assert [(m, path) for m, path, _ in log[1:]] == [
        ("POST", "/air/order_cancellations"), ("POST", "/air/order_cancellations/ore_1/actions/confirm")]
    with pytest.raises(ProviderError):
        await p.book({}, {**raw, "passenger_ids": ["a", "b"]}, PAX, CONTACT, "WN1")  # count mismatch


# ---- Amadeus ----------------------------------------------------------------

AM_OFFER = {
    "id": "1", "price": {"grandTotal": "5000.00", "currency": "INR"},
    "itineraries": [{"duration": "PT2H5M", "segments": [
        {"departure": {"iataCode": "DEL", "at": "2030-01-15T06:00:00"},
         "arrival": {"iataCode": "GOI", "at": "2030-01-15T08:05:00"}, "carrierCode": "6E", "number": "123"}]}],
}


def amadeus(handler, clock=None):
    kw = {"clock": clock} if clock else {}
    return AmadeusProvider("id", "secret", "https://am.test", currency="INR", offer_ttl_s=600, timeout=10, retries=0,
                           breaker=CircuitBreaker(), transport=httpx.MockTransport(handler), **kw)


async def test_amadeus_token_cached_and_refreshed():
    t = [0.0]
    tokens = []

    def handler(req):
        if req.url.path == "/v1/security/oauth2/token":
            assert b"grant_type=client_credentials" in req.content
            tokens.append(1)
            return httpx.Response(200, json={"access_token": f"t{len(tokens)}", "expires_in": 100})
        assert req.headers["Authorization"] == f"Bearer t{len(tokens)}"
        return httpx.Response(200, json={"data": [AM_OFFER], "dictionaries": {"carriers": {"6E": "IndiGo"}}})

    p = amadeus(handler, clock=lambda: t[0])
    found = await p.search_transport("flight", "DEL", "GOI", DAY, 1)
    await p.search_transport("flight", "DEL", "GOI", DAY, 1)
    assert len(tokens) == 1
    t[0] = 200  # past expiry
    await p.search_transport("flight", "DEL", "GOI", DAY, 1)
    assert len(tokens) == 2
    o = found[0].offer
    assert o["price"] == 500000 and o["carrier"] == "IndiGo" and o["number"] == "6E123" and o["duration_min"] == 125


async def test_amadeus_book_reprices_and_detects_change():
    state = {"price": "5000.00"}
    orders = []

    def handler(req):
        if req.url.path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 1700})
        if req.url.path == "/v1/shopping/flight-offers/pricing":
            return httpx.Response(200, json={"data": {"flightOffers": [{**AM_OFFER, "price": {"grandTotal": state["price"], "currency": "INR"}}]}})
        if req.url.path == "/v1/booking/flight-orders" and req.method == "POST":
            orders.append(json.loads(req.content))
            return httpx.Response(201, json={"data": {"id": "eJz", "associatedRecords": [{"reference": "XYZ789"}]}})
        return httpx.Response(204)

    p = amadeus(handler)
    offer = {"price": 500000, "currency": "INR"}
    raw = {"flight_offer": AM_OFFER}
    res = await p.book(offer, raw, PAX, CONTACT, "WN1")
    assert (res.provider_order_id, res.reference) == ("eJz", "XYZ789")
    t = orders[0]["data"]["travelers"][0]
    assert t["name"] == {"firstName": "ASHA", "lastName": "RAO"} and t["gender"] == "FEMALE"
    assert t["contact"]["phones"][0] == {"deviceType": "MOBILE", "countryCallingCode": "91", "number": "9876543210"}
    state["price"] = "5100.00"
    with pytest.raises(PriceChanged):
        await p.book(offer, raw, PAX, CONTACT, "WN2")
    assert len(orders) == 1  # no order is placed after a price change
    await p.cancel("eJz")


# ---- configuration ----------------------------------------------------------

def test_config_fails_fast():
    def err(**kw):
        with pytest.raises(RuntimeError) as e:
            Settings(_env_file=None, **kw).validate_runtime()
        return str(e.value)

    assert "DUFFEL_TOKEN" in err(providers="duffel")
    assert "AMADEUS_BASE_URL" in err(providers="amadeus", amadeus_client_id="a", amadeus_client_secret="b")
    assert "unknown provider" in err(providers="skyscanner")
    assert "SECRET_KEY" in err(app_env="prod", providers="duffel", duffel_token="x")
    assert "ALLOW_DEMO_IN_PROD" in err(app_env="prod", providers="demo", secret_key="k" * 40)
    Settings(_env_file=None, app_env="prod", providers="duffel,amadeus", duffel_token="x", secret_key="k" * 40,
             amadeus_client_id="a", amadeus_client_secret="b", amadeus_base_url="https://h",
             payment_gateway="razorpay", razorpay_key_id="k", razorpay_key_secret="s").validate_runtime()


def test_registry_builds_only_enabled():
    s = Settings(_env_file=None, providers="duffel,amadeus,demo", duffel_token="x", amadeus_client_id="a",
                 amadeus_client_secret="b", amadeus_base_url="https://h")
    assert set(build_providers(s)) == {"duffel", "amadeus", "demo"}
    assert set(build_providers(Settings(_env_file=None, providers="demo"))) == {"demo"}
