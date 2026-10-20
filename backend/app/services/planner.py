import asyncio
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.errors import DomainError
from ..core.money import exponent
from ..providers.base import TravelProvider
from . import search

_DAY_IDEAS = [
    "Old town & landmarks walk", "Local food tour", "Museum or cultural site", "Nature / park / viewpoint",
    "Markets & shopping", "Day trip nearby", "Relax & leisure",
]


async def plan_trip(db: AsyncSession, providers: dict[str, TravelProvider], tenant_id: str, *, origin: str, destination: str,
                    start: date, end: date, travelers: int, mode: str, budget: int | None, stars: int) -> dict:
    """Cheapest outbound + return and best-value hotel, all in one currency, within budget."""
    if end <= start:
        raise DomainError(422, "bad_dates", "end must be after start")
    out_r, back_r, hotel_r = await asyncio.gather(
        search.collect_transport(providers, mode, origin, destination, start, travelers),
        search.collect_transport(providers, mode, destination, origin, end, travelers),
        search.collect_hotels(providers, destination, start, end, stars, travelers),
    )
    warnings = out_r.warnings + back_r.warnings + hotel_r.warnings
    if not out_r.found or not back_r.found:
        raise DomainError(404, "no_transport", f"No {mode} options found for these dates")
    if not hotel_r.found:
        raise DomainError(404, "no_hotels", "No hotels match")

    out = out_r.found[0]
    cur = out.offer["currency"]
    back = next((f for f in back_r.found if f.offer["currency"] == cur), None)
    hotels = [f for f in hotel_r.found if f.offer["currency"] == cur]
    if not back or not hotels:
        raise DomainError(422, "mixed_currency", "Suppliers returned prices in different currencies")

    transport_cost = out.offer["price"] + back.offer["price"]
    limit = None if budget is None else budget * 10 ** exponent(cur)
    affordable = [h for h in hotels if limit is None or transport_cost + h.offer["price"] <= limit]
    if not affordable:
        need = transport_cost + min(h.offer["price"] for h in hotels)
        raise DomainError(422, "over_budget", f"No combination fits the budget; the cheapest needs {need / 10 ** exponent(cur):.0f} {cur}")
    # Scale-free value score: rewards rating (squared), penalises price.
    hotel = max(affordable, key=lambda h: h.offer["rating"] ** 2 / h.offer["price"])

    offers = await search.persist(db, [out, back, hotel], tenant_id)
    days = (end - start).days
    return {
        "currency": cur,
        "outbound": offers[0], "return": offers[1], "hotel": offers[2],
        "travelers": travelers,
        "total": transport_cost + hotel.offer["price"],
        "itinerary": [{"day": i + 1, "idea": _DAY_IDEAS[i % len(_DAY_IDEAS)]} for i in range(days)],
        "warnings": warnings,
    }
