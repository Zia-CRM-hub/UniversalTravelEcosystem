"""Checkout saga: one payment, several supplier bookings, all-or-nothing.

created --claim--> processing --authorize--> (book each offer) --capture--> booked
                       \\--any failure--> undo earlier supplier bookings, void payment --> failed

* A checkout can be confirmed once. Re-confirming a booked checkout replays the result,
  so a client retry after a dropped connection is safe.
* Each Booking row is committed as `pending` before its supplier call; supplier orders are
  never auto-retried. A crash leaves `pending` rows for a reconciliation job (see docs).
* Money is only captured after every supplier confirmed.
"""
import logging
import secrets

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.errors import DomainError, PaymentError, ProviderError
from ..core.money import to_major
from ..core.observability import BOOKINGS
from ..core.observability import log as jlog
from ..models import Booking, Checkout, OfferSnapshot, Organization, User, aware, new_id, now
from ..providers.base import TravelProvider
from ..schemas import ConfirmIn
from .booking import provider_failure
from .payments import PaymentGateway

logger = logging.getLogger(__name__)


async def _snapshots(db: AsyncSession, ids: list[str], tenant_id: str) -> list[OfferSnapshot]:
    out = []
    for i in ids:
        snap = await db.get(OfferSnapshot, i)
        if not snap or snap.tenant_id != tenant_id:  # another tenant's offer looks exactly like a missing one
            raise DomainError(404, "offer_not_found", "An offer was not found. Please search again.")
        if aware(snap.expires_at) <= now():
            raise DomainError(410, "offer_expired", "An offer has expired. Please search again.")
        out.append(snap)
    return out


async def _enforce_policy(db: AsyncSession, user: User, amount: int, currency: str) -> None:
    """Organization travel policy: one checkout may not exceed the organization's limit."""
    if not user.organization_id:
        return
    org = await db.get(Organization, user.organization_id)
    if not org:
        return
    if org.status != "active":
        raise DomainError(403, "organization_suspended", "Your organization is suspended")
    if org.max_trip_amount is None:
        return
    if currency != org.policy_currency:
        raise DomainError(422, "policy_violation", f"Your organization's travel policy only allows {org.policy_currency} bookings")
    if amount > org.max_trip_amount:
        limit = f"{to_major(org.max_trip_amount, currency):,.0f} {currency}"
        raise DomainError(422, "policy_violation", f"This trip exceeds your organization's limit of {limit}")


async def create_checkout(db: AsyncSession, providers: dict[str, TravelProvider], gateway: PaymentGateway,
                          user: User, offer_ids: list[str]) -> tuple[Checkout, list[OfferSnapshot]]:
    ids = list(dict.fromkeys(offer_ids))
    snaps = await _snapshots(db, ids, user.tenant_id)
    if len({s.currency for s in snaps}) != 1:
        raise DomainError(422, "mixed_currency", "These offers are priced in different currencies")
    if len({s.offer["pax"] for s in snaps}) != 1:
        raise DomainError(422, "mixed_party", "These offers are for different numbers of travellers")
    if any(s.provider not in providers for s in snaps):
        raise DomainError(503, "provider_disabled", "A supplier is not currently available")
    amount = sum(s.price for s in snaps)
    await _enforce_policy(db, user, amount, snaps[0].currency)
    c = Checkout(id=new_id(), tenant_id=user.tenant_id, organization_id=user.organization_id, user_id=user.id, offer_ids=ids, amount=sum(s.price for s in snaps),
                 currency=snaps[0].currency, gateway=gateway.name, status="created", payment_state="none")
    try:
        c.gateway_order_id = await gateway.create_order(c.amount, c.currency, c.id)
    except PaymentError as e:
        logger.warning("create_order failed: %s", e)
        raise DomainError(502, "payment_unavailable", "Payments are unavailable right now. Try again shortly.") from None
    db.add(c)
    await db.commit()
    return c, snaps


async def bookings_for(db: AsyncSession, checkout_id: str) -> list[Booking]:
    return list((await db.scalars(select(Booking).where(Booking.checkout_id == checkout_id).order_by(Booking.created_at))).all())


async def confirm_checkout(db: AsyncSession, providers: dict[str, TravelProvider], gateway: PaymentGateway,
                           user: User, checkout_id: str, body: ConfirmIn) -> tuple[Checkout, list[Booking]]:
    c = await db.get(Checkout, checkout_id)
    if not c or c.user_id != user.id:
        raise DomainError(404, "checkout_not_found", "Checkout not found")
    if c.status == "booked":
        return c, await bookings_for(db, c.id)  # idempotent replay
    if c.status == "failed":
        raise DomainError(409, "checkout_failed", c.failure_reason or "This checkout failed. Please start again.")
    if c.status == "processing":
        raise DomainError(409, "in_progress", "This checkout is already being processed")
    # Atomic claim: only one concurrent confirm can move created -> processing.
    claimed = await db.execute(update(Checkout).where(Checkout.id == c.id, Checkout.status == "created")
                               .values(status="processing").execution_options(synchronize_session=False))
    await db.commit()
    if claimed.rowcount != 1:
        raise DomainError(409, "in_progress", "This checkout is already being processed")
    await db.refresh(c)

    async def fail(err: DomainError, reason: str, confirmed: list[Booking] | None = None):
        for b in reversed(confirmed or []):  # compensate: undo supplier orders already placed
            try:
                await providers[b.provider].cancel(b.provider_order_id)
                b.status = "cancelled"
            except Exception:
                b.status = "needs_attention"
                logger.critical("COMPENSATION FAILED: supplier order %s (%s) for booking %s must be cancelled manually",
                                b.provider_order_id, b.provider, b.reference)
        if c.payment_state == "authorized" and c.payment_id:
            try:
                await gateway.void(c.payment_id)
                c.payment_state = "released"
            except Exception:
                c.payment_state = "void_failed"
                logger.critical("VOID FAILED for checkout %s (payment %s)", c.id, c.payment_id)
        c.status, c.failure_reason = "failed", reason[:500]
        await db.commit()
        raise err

    # --- validate before any money is touched ---
    try:
        snaps = await _snapshots(db, c.offer_ids, c.tenant_id)
    except DomainError as e:
        await fail(e, e.detail)
    if any(len(body.passengers) != s.offer["pax"] for s in snaps):
        n = snaps[0].offer["pax"]
        await fail(DomainError(422, "passenger_count", f"These offers are priced for {n} traveller(s)"), "passenger count mismatch")

    # --- payment authorization (money is held, not taken) ---
    try:
        c.payment_id = await gateway.authorize(c.gateway_order_id, c.amount, c.currency,
                                               body.payment.model_dump() if body.payment else None)
        c.payment_state = "authorized"
        await db.commit()
    except PaymentError as e:
        await fail(DomainError(402, "payment_failed", f"Payment could not be verified: {e}"), str(e))

    # --- supplier bookings ---
    passengers = [p.model_dump(mode="json") for p in body.passengers]
    contact = body.contact.model_dump(mode="json")
    confirmed: list[Booking] = []
    for snap in snaps:
        b = Booking(reference="UT" + secrets.token_hex(4).upper(), tenant_id=c.tenant_id, organization_id=c.organization_id,
                    user_id=user.id, checkout_id=c.id, offer_id=snap.id,
                    kind=snap.kind, provider=snap.provider, total_price=snap.price, currency=snap.currency,
                    offer=snap.offer, passengers=passengers, contact=contact)
        db.add(b)
        await db.commit()  # trace exists before the supplier call
        try:
            res = await providers[snap.provider].book(snap.offer, snap.raw, passengers, contact, b.reference)
        except ProviderError as e:
            jlog(logger, "supplier booking failed", logging.WARNING, booking=b.reference, provider=e.provider, error=e.message)
            b.status, b.failure_reason = "failed", e.message[:500]
            BOOKINGS.labels(snap.provider, "failed").inc()
            await fail(provider_failure(e), e.message, confirmed)
        except Exception:
            logger.exception("supplier adapter crashed booking %s", b.reference)
            b.status, b.failure_reason = "failed", "adapter error"
            BOOKINGS.labels(snap.provider, "failed").inc()
            await fail(DomainError(502, "provider_error", "The supplier could not complete the booking."), "adapter error", confirmed)
        b.provider_order_id, b.status = res.provider_order_id, "confirmed"
        await db.commit()
        BOOKINGS.labels(snap.provider, "confirmed").inc()
        confirmed.append(b)

    # --- every supplier confirmed: now take the money ---
    try:
        await gateway.capture(c.payment_id, c.amount, c.currency)
        c.payment_state = "captured"
    except PaymentError:
        c.payment_state = "capture_failed"  # bookings stand; finance must resolve
        logger.critical("CAPTURE FAILED for booked checkout %s (payment %s)", c.id, c.payment_id)
    c.status = "booked"
    await db.commit()
    jlog(logger, "checkout booked", checkout=c.id, bookings=[b.reference for b in confirmed], payment_state=c.payment_state)
    return c, confirmed
