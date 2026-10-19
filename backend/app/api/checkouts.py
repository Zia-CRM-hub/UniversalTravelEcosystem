from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import User
from ..schemas import CheckoutIn, CheckoutOut, ConfirmIn, ConfirmOut
from ..security import current_user
from ..services import checkout as svc
from .deps import get_gateway, get_providers

router = APIRouter(prefix="/checkouts", tags=["checkouts"])


@router.post("", response_model=CheckoutOut, status_code=201)
async def create(body: CheckoutIn, user: User = Depends(current_user), db: AsyncSession = Depends(get_db),
                 providers=Depends(get_providers), gateway=Depends(get_gateway)):
    """Step 1: price the selected offers and open a payment order with the gateway."""
    c, snaps = await svc.create_checkout(db, providers, gateway, user, body.offer_ids)
    return CheckoutOut(id=c.id, status=c.status, amount=c.amount, currency=c.currency, gateway=c.gateway,
                       gateway_order_id=c.gateway_order_id, gateway_key_id=gateway.client_config().get("key_id"),
                       offers=[{**s.offer, "offer_id": s.id} for s in snaps])


@router.post("/{checkout_id}/confirm", response_model=ConfirmOut)
async def confirm(checkout_id: str, body: ConfirmIn, response: Response, user: User = Depends(current_user),
                  db: AsyncSession = Depends(get_db), providers=Depends(get_providers), gateway=Depends(get_gateway)):
    """Step 2: after the customer paid in the gateway UI, verify payment and place every supplier booking.
    Safe to retry: confirming an already-booked checkout returns the same bookings."""
    already = (await db.get(svc.Checkout, checkout_id))
    replay = bool(already and already.status == "booked")
    c, bookings = await svc.confirm_checkout(db, providers, gateway, user, checkout_id, body)
    if replay:
        response.status_code = 200
    return ConfirmOut(checkout_id=c.id, status=c.status, bookings=bookings)
