"""Amadeus flights adapter.

IMPORTANT: Amadeus for Developers *Self-Service* was decommissioned on 2026-07-17 and
no new free keys can be issued. This adapter follows the Self-Service API shape that
Amadeus Enterprise customers are expected to retain, but NONE of it is verified:
confirm paths, payloads and auth with your Enterprise documentation. `AMADEUS_BASE_URL`
has no default for that reason.
Written from memory of the Self-Service API: OAuth2 client_credentials at
/v1/security/oauth2/token, GET /v2/shopping/flight-offers, GET /v1/reference-data/locations,
POST /v1/shopping/flight-offers/pricing, POST /v1/booking/flight-orders,
DELETE /v1/booking/flight-orders/{id}.
"""
import asyncio
import logging
import re
import time
from datetime import date

import phonenumbers

from ..core.errors import PriceChanged, ProviderError
from ..core.money import to_minor
from .base import BookResult, Found, TravelProvider
from .http import CircuitBreaker, ProviderHTTP, parse_duration_min

log = logging.getLogger(__name__)
_IATA = re.compile(r"^[A-Za-z]{3}$")


class AmadeusProvider(TravelProvider):
    name = "amadeus"
    kinds = frozenset({"flight"})

    def __init__(self, client_id: str, client_secret: str, base_url: str, *, currency: str | None, offer_ttl_s: int,
                 timeout: float, retries: int, breaker: CircuitBreaker, transport=None, clock=time.monotonic):
        self.client_id, self.client_secret, self.currency = client_id, client_secret, currency
        self.offer_ttl_s, self.breaker, self._clock = offer_ttl_s, breaker, clock
        self.http = ProviderHTTP("amadeus", base_url, timeout=timeout, retries=retries, breaker=breaker, transport=transport)
        self._token: str | None = None
        self._token_exp = 0.0
        self._lock = asyncio.Lock()
        self._places: dict[str, str | None] = {}

    @classmethod
    def from_settings(cls, s, transport=None, breaker=None):
        return cls(s.amadeus_client_id, s.amadeus_client_secret.get_secret_value(), s.amadeus_base_url,
                   currency=s.amadeus_currency, offer_ttl_s=s.offer_ttl_minutes * 60, timeout=s.provider_timeout_s,
                   retries=s.provider_retries, transport=transport,
                   breaker=breaker or CircuitBreaker(s.breaker_threshold, s.breaker_reset_s))

    async def _auth(self) -> dict:
        async with self._lock:
            if not self._token or self._clock() >= self._token_exp:
                res = await self.http.request(
                    "POST", "/v1/security/oauth2/token", retry=True,
                    data={"grant_type": "client_credentials", "client_id": self.client_id,
                          "client_secret": self.client_secret})
                self._token = res["access_token"]
                self._token_exp = self._clock() + int(res.get("expires_in", 1700)) - 30
            return {"Authorization": f"Bearer {self._token}"}

    async def _iata(self, query: str) -> str | None:
        q = query.strip()
        if _IATA.match(q):
            return q.upper()
        key = q.lower()
        if key not in self._places:
            res = await self.http.request(
                "GET", "/v1/reference-data/locations", retry=True, headers=await self._auth(),
                params={"subType": "CITY,AIRPORT", "keyword": q, "page[limit]": 1})
            data = res.get("data", [])
            self._places[key] = data[0].get("iataCode") if data else None
        return self._places[key]

    async def search_transport(self, kind, origin, destination, day: date, adults):
        if kind != "flight":
            return []
        o, d = await self._iata(origin), await self._iata(destination)
        if not o or not d:
            return []
        params = {"originLocationCode": o, "destinationLocationCode": d, "departureDate": day.isoformat(),
                  "adults": adults, "max": 25}
        if self.currency:
            params["currencyCode"] = self.currency
        res = await self.http.request("GET", "/v2/shopping/flight-offers", params=params, retry=True,
                                      headers=await self._auth())
        carriers = (res.get("dictionaries") or {}).get("carriers", {})
        return [self._parse(x, carriers) for x in res.get("data", [])]

    def _parse(self, x: dict, carriers: dict) -> Found:
        it = x["itineraries"][0]
        segs = it["segments"]
        first, last = segs[0], segs[-1]
        cur = x["price"]["currency"]
        code = first["carrierCode"]
        expires = time.time() + self.offer_ttl_s
        return Found(
            offer={
                "kind": "flight", "carrier": carriers.get(code, code), "number": f"{code}{first['number']}",
                "origin": first["departure"]["iataCode"], "destination": last["arrival"]["iataCode"],
                "departure": first["departure"]["at"], "arrival": last["arrival"]["at"],
                "duration_min": parse_duration_min(it.get("duration")), "stops": len(segs) - 1,
                "price": to_minor(x["price"]["grandTotal"], cur), "currency": cur,
                "expires_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(expires)),
            },
            raw={"flight_offer": x},
        )

    async def book(self, offer, raw, passengers, contact, reference):
        headers = await self._auth()
        # 1) Re-price: Amadeus requires it, and it catches stale fares before we take money.
        priced = await self.http.request(
            "POST", "/v1/shopping/flight-offers/pricing", retry=True, headers=headers,
            json={"data": {"type": "flight-offers-pricing", "flightOffers": [raw["flight_offer"]]}})
        fo = priced["data"]["flightOffers"][0]
        new_price = to_minor(fo["price"]["grandTotal"], fo["price"]["currency"])
        if new_price != offer["price"] or fo["price"]["currency"] != offer["currency"]:
            raise PriceChanged(self.name, f"fare changed from {offer['price']} to {new_price}")
        # 2) Create the order (not retried).
        try:
            ph = phonenumbers.parse(contact["phone"], None)
        except phonenumbers.NumberParseException:
            raise ProviderError(self.name, "invalid phone number", client_error=True) from None
        travelers = [
            {"id": str(i + 1), "dateOfBirth": p["born_on"],
             "name": {"firstName": p["given_name"].upper(), "lastName": p["family_name"].upper()},
             "gender": "MALE" if p["gender"] == "m" else "FEMALE",
             "contact": {"emailAddress": contact["email"],
                         "phones": [{"deviceType": "MOBILE", "countryCallingCode": str(ph.country_code),
                                     "number": str(ph.national_number)}]}}
            for i, p in enumerate(passengers)
        ]
        res = await self.http.request(
            "POST", "/v1/booking/flight-orders", retry=False, headers=headers,
            json={"data": {"type": "flight-order", "flightOffers": [fo], "travelers": travelers}})
        d = res["data"]
        recs = d.get("associatedRecords") or [{}]
        return BookResult(provider_order_id=d["id"], reference=recs[0].get("reference") or d["id"])

    async def cancel(self, provider_order_id):
        await self.http.request("DELETE", f"/v1/booking/flight-orders/{provider_order_id}", retry=False,
                                headers=await self._auth())

    def health(self):
        return {**super().health(), "breaker": self.breaker.state}

    async def aclose(self):
        await self.http.aclose()
