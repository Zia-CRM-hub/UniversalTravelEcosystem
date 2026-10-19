from datetime import date

import httpx
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..core.errors import DomainError
from ..db import get_db
from ..models import OfferSnapshot, aware, now
from ..schemas import HotelSearchOut, TransportSearchOut
from ..services import search
from .deps import get_providers

router = APIRouter(tags=["search"])


@router.get("/search/transport", response_model=TransportSearchOut)
async def transport(
    kind: str = Query(pattern="^(flight|train|bus)$"),
    origin: str = Query(min_length=2, max_length=64),
    destination: str = Query(min_length=2, max_length=64),
    day: date = Query(alias="date"),
    adults: int = Query(1, ge=1, le=9),
    providers=Depends(get_providers),
    db: AsyncSession = Depends(get_db),
):
    if origin.strip().lower() == destination.strip().lower():
        raise DomainError(422, "same_place", "Origin and destination must differ")
    if day < date.today():
        raise DomainError(422, "past_date", "Date is in the past")
    res = await search.collect_transport(providers, kind, origin, destination, day, adults)
    return {"offers": await search.persist(db, res.found), "warnings": res.warnings}


@router.get("/search/hotels", response_model=HotelSearchOut)
async def hotels(
    city: str = Query(min_length=2, max_length=64),
    check_in: date = Query(),
    check_out: date = Query(),
    stars_min: int = Query(1, ge=1, le=5),
    guests: int = Query(1, ge=1, le=9),
    providers=Depends(get_providers),
    db: AsyncSession = Depends(get_db),
):
    if check_out <= check_in:
        raise DomainError(422, "bad_dates", "check_out must be after check_in")
    if check_in < date.today():
        raise DomainError(422, "past_date", "Date is in the past")
    res = await search.collect_hotels(providers, city, check_in, check_out, stars_min, guests)
    return {"offers": await search.persist(db, res.found), "warnings": res.warnings}


@router.get("/offers/{offer_id}")
async def get_offer(offer_id: str, db: AsyncSession = Depends(get_db)):
    """Resolve a hand-off link (from chat / WhatsApp) back to the offer the user was shown."""
    snap = await db.get(OfferSnapshot, offer_id)
    if not snap:
        raise DomainError(404, "offer_not_found", "Offer not found. Please search again.")
    if aware(snap.expires_at) <= now():
        raise DomainError(410, "offer_expired", "This offer has expired. Please search again.")
    return {**snap.offer, "offer_id": snap.id}


@router.get("/places")
async def places(q: str = Query(min_length=2, max_length=64)):
    """Place autocomplete via OpenStreetMap Nominatim (point NOMINATIM_URL at a self-hosted instance for scale)."""
    try:
        async with httpx.AsyncClient(timeout=6) as c:
            r = await c.get(f"{settings.nominatim_url}/search",
                            params={"q": q, "format": "jsonv2", "limit": 6, "addressdetails": 1},
                            headers={"User-Agent": settings.user_agent})
            r.raise_for_status()
    except httpx.HTTPError:
        raise DomainError(502, "places_unavailable", "Place lookup unavailable") from None
    return [
        {"name": p["display_name"], "lat": float(p["lat"]), "lon": float(p["lon"]),
         "city": p.get("address", {}).get("city") or p.get("name")}
        for p in r.json()
    ]
