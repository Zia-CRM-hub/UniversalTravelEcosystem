"""Background worker: housekeeping that must not depend on user traffic.

    python -m app.worker            # loops every WORKER_INTERVAL_S seconds

Run it as its own process/container (see docker-compose.yml), separate from the API replicas, so API
scaling and background work scale independently. Every task is idempotent and safe to run on several
workers at once.
"""
import asyncio
import logging
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .core.config import settings
from .core.observability import configure_logging
from .db import SessionLocal, engine
from .models import Booking, Checkout, OfferSnapshot, now

log = logging.getLogger("ute.worker")


async def cleanup_offers(db: AsyncSession, keep: timedelta = timedelta(hours=1)) -> int:
    """Delete expired offer snapshots (one row per search result, so this table grows fastest)."""
    res = await db.execute(delete(OfferSnapshot).where(OfferSnapshot.expires_at < now() - keep))
    await db.commit()
    return res.rowcount or 0


async def flag_stale_pending(db: AsyncSession, older_than: timedelta | None = None) -> dict:
    """A process that died mid-checkout leaves `pending` bookings and a `processing` checkout.

    We cannot know whether the supplier order exists, so a human must check: bookings become
    `needs_attention`, the checkout fails (an uncaptured payment is released by the gateway), and we log CRITICAL.
    """
    cutoff = now() - (older_than or timedelta(minutes=settings.stale_pending_minutes))
    stuck_bookings = (await db.scalars(select(Booking).where(Booking.status == "pending", Booking.created_at < cutoff))).all()
    for b in stuck_bookings:
        b.status, b.failure_reason = "needs_attention", "interrupted before the supplier answered; check the supplier"
        log.critical("STALE PENDING booking %s (%s, supplier %s): verify with the supplier", b.reference, b.id, b.provider)
    stuck_checkouts = (await db.scalars(select(Checkout).where(Checkout.status == "processing", Checkout.updated_at < cutoff))).all()
    for c in stuck_checkouts:
        c.status, c.failure_reason = "failed", "interrupted; needs review"
        log.critical("STALE checkout %s (payment %s, state %s): reconcile with the gateway", c.id, c.payment_id, c.payment_state)
    await db.commit()
    return {"bookings": len(stuck_bookings), "checkouts": len(stuck_checkouts)}


async def run_once() -> dict:
    async with SessionLocal() as db:
        offers = await cleanup_offers(db)
    async with SessionLocal() as db:
        stale = await flag_stale_pending(db)
    return {"offers_deleted": offers, **{f"stale_{k}": v for k, v in stale.items()}}


async def main() -> None:
    configure_logging()
    log.info("worker started, interval %ss", settings.worker_interval_s)
    while True:
        try:
            log.info("cycle done: %s", await run_once())
        except Exception:
            log.exception("worker cycle failed")
        await asyncio.sleep(settings.worker_interval_s)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        asyncio.run(engine.dispose())
