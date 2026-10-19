"""Duffel flights adapter (https://duffel.com/docs/api).

Verified against Duffel docs: base URL, headers (Authorization, Duffel-Version: v2),
POST /air/offer_requests body/query and the offer response shape, and the order
passenger/payment fields.
NOT verified (written from memory of the API, never run against Duffel): the top-level
`type: "instant"` in the order body, GET /places/suggestions, and the two-step
order_cancellations flow. Test them in the Duffel sandbox before relying on them.
"""
import logging
import re
from datetime import date

from ..core.errors import ProviderError
from ..core.money import to_minor
from .base import BookResult, Found, TravelProvider
from .http import CircuitBreaker, ProviderHTTP, parse_duration_min

log = logging.getLogger(__name__)
_IATA = re.compile(r"^[A-Za-z]{3}$")
MAX_OFFERS = 25


class DuffelProvider(TravelProvider):
    name = "duffel"
    kinds = frozenset({"flight"})

    def __init__(self, token: str, base_url: str, *, timeout: float, retries: int, breaker: CircuitBreaker, transport=None):
        self.timeout = timeout
        self.breaker = breaker
        self.http = ProviderHTTP(
            "duffel", base_url, timeout=timeout, retries=retries, breaker=breaker, transport=transport,
            headers={"Authorization": f"Bearer {token}", "Duffel-Version": "v2",
                     "Accept": "application/json", "Content-Type": "application/json"},
        )
        self._places: dict[str, str | None] = {}

    @classmethod
    def from_settings(cls, s, transport=None, breaker=None):
        return cls(s.duffel_token.get_secret_value(), s.duffel_base_url, timeout=s.provider_timeout_s,
                   retries=s.provider_retries, transport=transport,
                   breaker=breaker or CircuitBreaker(s.breaker_threshold, s.breaker_reset_s))

    async def _iata(self, query: str) -> str | None:
        q = query.strip()
        if _IATA.match(q):
            return q.upper()
        key = q.lower()
        if key not in self._places:
            res = await self.http.request("GET", "/places/suggestions", params={"query": q}, retry=True)
            items = res.get("data", [])
            pick = next((i for i in items if i.get("type") == "city"), items[0] if items else None)
            self._places[key] = pick.get("iata_code") if pick else None
        return self._places[key]

    async def search_transport(self, kind, origin, destination, day: date, adults):
        if kind != "flight":
            return []
        o, d = await self._iata(origin), await self._iata(destination)
        if not o or not d:
            log.info("duffel: could not resolve %r or %r to IATA codes", origin, destination)
            return []
        body = {"data": {
            "slices": [{"origin": o, "destination": d, "departure_date": day.isoformat()}],
            "passengers": [{"type": "adult"}] * adults,
            "cabin_class": "economy",
            "max_connections": 1,
        }}
        timeout_ms = min(max(int(self.timeout * 1000) - 2000, 2000), 60000)
        res = await self.http.request("POST", "/air/offer_requests", json=body, retry=True,
                                      params={"return_offers": "true", "supplier_timeout": timeout_ms})
        offers = sorted(res.get("data", {}).get("offers", []), key=lambda x: float(x["total_amount"]))[:MAX_OFFERS]
        return [self._parse(x, origin, destination) for x in offers]

    @staticmethod
    def _parse(x: dict, origin: str, destination: str) -> Found:
        sl = x["slices"][0]
        segs = sl["segments"]
        first, last = segs[0], segs[-1]
        mc = first.get("marketing_carrier") or {}
        cur = x["total_currency"]
        return Found(
            offer={
                "kind": "flight",
                "carrier": (x.get("owner") or {}).get("name") or mc.get("name", ""),
                "number": f"{mc.get('iata_code', '')}{first.get('marketing_carrier_flight_number', '')}",
                "origin": sl["origin"]["iata_code"] if isinstance(sl.get("origin"), dict) else origin,
                "destination": sl["destination"]["iata_code"] if isinstance(sl.get("destination"), dict) else destination,
                "departure": first["departing_at"], "arrival": last["arriving_at"],
                "duration_min": parse_duration_min(sl.get("duration")),
                "stops": len(segs) - 1,
                "price": to_minor(x["total_amount"], cur), "currency": cur,
                "expires_at": x.get("expires_at"),
            },
            raw={"offer_id": x["id"], "passenger_ids": [p["id"] for p in x.get("passengers", [])],
                 "total_amount": x["total_amount"], "total_currency": cur},
        )

    async def book(self, offer, raw, passengers, contact, reference):
        ids = raw["passenger_ids"]
        if len(ids) != len(passengers):
            raise ProviderError(self.name, "passenger count does not match the offer", client_error=True)
        body = {"data": {
            "type": "instant",
            "selected_offers": [raw["offer_id"]],
            # 'balance' draws on the merchant's Duffel balance; the customer is charged by our gateway.
            "payments": [{"type": "balance", "currency": raw["total_currency"], "amount": raw["total_amount"]}],
            "passengers": [
                {"id": pid, "title": p["title"], "gender": p["gender"], "given_name": p["given_name"],
                 "family_name": p["family_name"], "born_on": p["born_on"],
                 "email": contact["email"], "phone_number": contact["phone"]}
                for pid, p in zip(ids, passengers, strict=False)
            ],
            "metadata": {"wanderly_ref": reference},
        }}
        res = await self.http.request("POST", "/air/orders", json=body, retry=False)
        d = res["data"]
        return BookResult(provider_order_id=d["id"], reference=d.get("booking_reference") or d["id"])

    async def cancel(self, provider_order_id):
        created = await self.http.request("POST", "/air/order_cancellations",
                                          json={"data": {"order_id": provider_order_id}}, retry=False)
        cid = created["data"]["id"]
        await self.http.request("POST", f"/air/order_cancellations/{cid}/actions/confirm", retry=False)

    def health(self):
        return {**super().health(), "breaker": self.breaker.state}

    async def aclose(self):
        await self.http.aclose()
