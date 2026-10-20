import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..core.errors import ProviderError, ProviderUnavailable
from ..core.observability import log as jlog
from ..models import OfferSnapshot, aware, new_id, now
from ..providers.base import Found, TravelProvider

logger = logging.getLogger(__name__)


@dataclass
class Collected:
    found: list[Found] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)


async def _fanout(calls: dict[str, object], pax: int) -> Collected:
    """Query every capable provider concurrently. One slow/broken supplier must not sink the search."""

    async def run(name, coro):
        try:
            return name, await asyncio.wait_for(coro, settings.provider_timeout_s + 2), None
        except TimeoutError:
            return name, [], "timeout"
        except ProviderUnavailable:
            return name, [], "unavailable"
        except ProviderError as e:
            jlog(logger, "provider search failed", logging.WARNING, provider=name, error=e.message)
            return name, [], "error"
        except Exception:  # a buggy adapter must not take the request down
            logger.exception("provider %s crashed during search", name)
            return name, [], "error"

    out = Collected()
    for name, found, problem in await asyncio.gather(*(run(n, c) for n, c in calls.items())):
        if problem:
            out.warnings.append({"provider": name, "problem": problem})
        for f in found:
            f.offer["provider"] = name
            f.offer["pax"] = pax
            out.found.append(f)
    # Prices in different currencies are not comparable, so group by currency first.
    out.found.sort(key=lambda f: (f.offer["currency"], f.offer["price"]))
    return out


async def collect_transport(providers: dict[str, TravelProvider], kind: str, origin: str, destination: str,
                            day: date, adults: int) -> Collected:
    calls = {n: p.search_transport(kind, origin, destination, day, adults) for n, p in providers.items() if p.supports(kind)}
    return await _fanout(calls, adults)


async def collect_hotels(providers: dict[str, TravelProvider], city: str, check_in: date, check_out: date,
                         stars_min: int, guests: int) -> Collected:
    calls = {n: p.search_hotels(city, check_in, check_out, stars_min, guests) for n, p in providers.items() if p.supports("hotel")}
    return await _fanout(calls, guests)


def _expiry(offer: dict) -> datetime:
    raw = offer.get("expires_at")
    if raw:
        try:
            return aware(datetime.fromisoformat(raw))
        except ValueError:
            pass
    return now() + timedelta(minutes=settings.offer_ttl_minutes)


async def persist(db: AsyncSession, found: list[Found], tenant_id: str) -> list[dict]:
    """Snapshot offers server-side and hand clients an opaque offer_id."""
    out = []
    for f in found:
        exp = _expiry(f.offer)
        snap = OfferSnapshot(id=new_id(), tenant_id=tenant_id, provider=f.offer["provider"], kind=f.offer["kind"], price=f.offer["price"],
                             currency=f.offer["currency"], offer={**f.offer, "expires_at": exp.isoformat()},
                             raw=f.raw, expires_at=exp)
        db.add(snap)
        out.append({**f.offer, "offer_id": snap.id, "expires_at": exp.isoformat()})
    # Opportunistic cleanup; a periodic job is better at scale (see README).
    await db.execute(delete(OfferSnapshot).where(OfferSnapshot.expires_at < now() - timedelta(hours=1)))
    await db.commit()
    return out
