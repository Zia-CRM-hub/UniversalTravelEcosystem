"""Deterministic synthetic inventory. Prices and bookings are NOT real."""
import hashlib
import random
import secrets
from datetime import datetime, time, timedelta

from .base import BookResult, Found, TravelProvider

CURRENCY = "INR"
_SPEED_KMH = {"flight": 750, "train": 80, "bus": 55}
_CARRIERS = {
    "flight": ["SkyJet", "AeroBlue", "Horizon Air", "Nimbus"],
    "train": ["Rapid Rail", "Express Line", "Metro Link", "Intercity"],
    "bus": ["RoadRunner", "GreenWay", "NightOwl Coaches", "CityLiner"],
}
_AMENITIES = ["WiFi", "Breakfast", "Pool", "Gym", "Parking", "Spa", "Airport shuttle", "Kitchen"]
_HOTEL_WORDS = ["Grand", "Royal", "Palm", "Lotus", "Harbor", "Central", "Garden", "Summit", "Heritage"]


def _rng(*parts) -> random.Random:
    seed = hashlib.sha256("|".join(str(p).lower().strip() for p in parts).encode()).digest()
    return random.Random(seed)


class DemoProvider(TravelProvider):
    name = "demo"
    kinds = frozenset({"flight", "train", "bus", "hotel"})

    @classmethod
    def from_settings(cls, s, transport=None, breaker=None):
        return cls()

    async def search_transport(self, kind, origin, destination, day, adults):
        a, b = sorted([origin.lower().strip(), destination.lower().strip()])
        dist = 150 + _rng("dist", a, b).random() * (8000 if kind == "flight" else 900)
        rng = _rng(kind, origin, destination, day)
        out = []
        for _ in range(rng.randint(5, 8)):
            duration = int(dist / _SPEED_KMH[kind] * 60) + (90 if kind == "flight" else 20)
            dep = datetime.combine(day, time(5, 0)) + timedelta(minutes=rng.randint(0, 17 * 60))
            base = {"flight": 4.5, "train": 1.2, "bus": 0.9}[kind]
            per_person = int((dist * base + 500) * rng.uniform(0.8, 1.4) / 10) * 10
            carrier = rng.choice(_CARRIERS[kind])
            out.append(Found(
                offer={
                    "kind": kind, "carrier": carrier, "number": f"{carrier[:2].upper()}{rng.randint(100, 9999)}",
                    "origin": origin.strip(), "destination": destination.strip(),
                    "departure": dep.isoformat(), "arrival": (dep + timedelta(minutes=duration)).isoformat(),
                    "duration_min": duration, "price": per_person * 100 * adults, "currency": CURRENCY,
                    "stops": rng.choice([0, 0, 1]) if kind == "flight" else 0,
                },
                raw={},
            ))
        return sorted(out, key=lambda f: f.offer["price"])

    async def search_hotels(self, city, check_in, check_out, stars_min, guests):
        nights = (check_out - check_in).days
        rooms = -(-guests // 2)
        rng = _rng("hotel", city, check_in)
        out = []
        for _ in range(8):
            stars = rng.randint(1, 5)
            if stars < stars_min:
                continue
            ppn = int(rng.uniform(900, 1800) * stars ** 1.6 / 10) * 10 * 100 * rooms
            out.append(Found(
                offer={
                    "kind": "hotel",
                    "name": f"{rng.choice(_HOTEL_WORDS)} {rng.choice(_HOTEL_WORDS)} {city.title()}",
                    "city": city.strip(), "stars": stars, "rating": round(rng.uniform(3.2, 4.9), 1),
                    "check_in": check_in.isoformat(), "check_out": check_out.isoformat(), "nights": nights,
                    "price_per_night": ppn, "price": ppn * nights, "currency": CURRENCY,
                    "amenities": rng.sample(_AMENITIES, 4),
                },
                raw={},
            ))
        return sorted(out, key=lambda f: f.offer["price"])

    async def book(self, offer, raw, passengers, contact, reference):
        return BookResult(provider_order_id=f"demo_{secrets.token_hex(6)}", reference="DM" + secrets.token_hex(3).upper())

    async def cancel(self, provider_order_id):
        return None
