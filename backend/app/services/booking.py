"""Booking helpers shared by checkout (create) and the bookings API (cancel)."""
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.errors import DomainError, PaymentError, PriceChanged, ProviderError, ProviderUnavailable
from ..models import Booking, Checkout, User
from ..providers.base import TravelProvider
from .payments import PaymentGateway

logger = logging.getLogger(__name__)


def provider_failure(e: ProviderError) -> DomainError:
    if isinstance(e, PriceChanged):
        return DomainError(409, "price_changed", "The price changed. Please search again.")
    if isinstance(e, ProviderUnavailable):
        return DomainError(503, "provider_unavailable", "The supplier is temporarily unavailable. Try again shortly.")
    if e.client_error:
        return DomainError(422, "provider_rejected", f"The supplier rejected the booking: {e.message}")
    return DomainError(502, "provider_error", "The supplier could not complete the booking.")


async def cancel_booking(db: AsyncSession, providers: dict[str, TravelProvider], gateway: PaymentGateway,
                         user: User, booking_id: str) -> Booking:
    b = await db.get(Booking, booking_id)
    if not b or b.user_id != user.id:
        raise DomainError(404, "booking_not_found", "Booking not found")
    if b.status != "confirmed":
        raise DomainError(409, "not_cancellable", f"A {b.status} booking cannot be cancelled")
    provider = providers.get(b.provider)
    if not provider:
        raise DomainError(503, "provider_disabled", "This supplier is not currently available")
    try:
        await provider.cancel(b.provider_order_id)
    except ProviderError as e:
        raise provider_failure(e) from None
    b.status = "cancelled"
    checkout = await db.get(Checkout, b.checkout_id)
    if checkout and checkout.payment_state == "captured" and checkout.payment_id:
        try:  # refund only this booking's share of the shared payment
            await gateway.refund(checkout.payment_id, b.total_price)
        except PaymentError:
            b.failure_reason = "refund pending manual review"
            logger.critical("REFUND FAILED for cancelled booking %s (payment %s)", b.reference, checkout.payment_id)
    await db.commit()
    return b
