from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import Checkout
from ..schemas import CheckoutIn, CheckoutOut, ConfirmIn, ConfirmOut
from ..services import checkout as svc
from .deps import Scope, get_gateway, user_scope

router = APIRouter(prefix="/checkouts", tags=["checkouts"])


@router.post("", response_model=CheckoutOut, status_code=201)
async def create(body: CheckoutIn, scope: Scope = Depends(user_scope), db: AsyncSession = Depends(get_db),
                 gateway=Depends(get_gateway)):
    """Step 1: price the selected offers (enforcing the organization's travel policy) and open a payment order."""
    c, snaps = await svc.create_checkout(db, scope.providers, gateway, scope.user, body.offer_ids)
    return CheckoutOut(id=c.id, status=c.status, amount=c.amount, currency=c.currency, gateway=c.gateway,
                       gateway_order_id=c.gateway_order_id, gateway_key_id=gateway.client_config().get("key_id"),
                       offers=[{**s.offer, "offer_id": s.id} for s in snaps])


@router.post("/{checkout_id}/confirm", response_model=ConfirmOut)
async def confirm(checkout_id: str, body: ConfirmIn, response: Response, scope: Scope = Depends(user_scope),
                  db: AsyncSession = Depends(get_db), gateway=Depends(get_gateway)):
    """Step 2: after the customer paid in the gateway UI, verify payment and place every supplier booking.
    Safe to retry: confirming an already-booked checkout returns the same bookings."""
    already = await db.get(Checkout, checkout_id)
    replay = bool(already and already.status == "booked")
    c, bookings = await svc.confirm_checkout(db, scope.providers, gateway, scope.user, checkout_id, body)
    if replay:
        response.status_code = 200
    return ConfirmOut(checkout_id=c.id, status=c.status, bookings=bookings)
