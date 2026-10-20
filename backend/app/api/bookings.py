from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import Booking, User
from ..schemas import BookingOut
from ..security import current_user
from ..services import booking as svc
from .deps import get_gateway, get_providers

router = APIRouter(prefix="/bookings", tags=["bookings"])


@router.get("", response_model=list[BookingOut])
async def mine(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """The caller's own bookings. Admins see wider scopes under /v1/admin/bookings."""
    q = select(Booking).where(Booking.user_id == user.id, Booking.status != "pending").order_by(Booking.created_at.desc())
    return (await db.scalars(q)).all()


@router.post("/{booking_id}/cancel", response_model=BookingOut)
async def cancel(
    booking_id: str,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    providers=Depends(get_providers),
    gateway=Depends(get_gateway),
):
    """Members cancel their own; org admins their organization's; tenant admins their tenant's."""
    return await svc.cancel_booking(db, providers, gateway, user, booking_id)
