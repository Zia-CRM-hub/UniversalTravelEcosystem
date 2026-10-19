import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def now() -> datetime:
    return datetime.now(UTC)


def aware(dt: datetime) -> datetime:
    """SQLite drops tzinfo; everything we store is UTC."""
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def new_id() -> str:
    return uuid.uuid4().hex


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String, unique=True, index=True)
    name: Mapped[str] = mapped_column(String)
    password_hash: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class OfferSnapshot(Base):
    """What the user was shown. Booking trusts this row, never client-supplied prices."""

    __tablename__ = "offer_snapshots"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    provider: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String)
    price: Mapped[int] = mapped_column(Integer)  # minor units, total for the whole party
    currency: Mapped[str] = mapped_column(String(3))
    offer: Mapped[dict] = mapped_column(JSON)  # normalized, shown to clients
    raw: Mapped[dict] = mapped_column(JSON)  # provider-specific, never leaves the server
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Checkout(Base):
    """One payment covering one or more bookings (e.g. a whole trip). Saga: created -> processing -> booked | failed."""

    __tablename__ = "checkouts"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    offer_ids: Mapped[list] = mapped_column(JSON)
    amount: Mapped[int] = mapped_column(Integer)  # minor units
    currency: Mapped[str] = mapped_column(String(3))
    gateway: Mapped[str] = mapped_column(String)
    gateway_order_id: Mapped[str | None] = mapped_column(String, nullable=True)
    payment_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # none | authorized | captured | released | void_failed | capture_failed
    payment_state: Mapped[str] = mapped_column(String, default="none")
    status: Mapped[str] = mapped_column(String, default="created")
    failure_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class Booking(Base):
    """One supplier order. Written as `pending` BEFORE the supplier call so a crash leaves a trace."""

    __tablename__ = "bookings"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    reference: Mapped[str] = mapped_column(String, unique=True, index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    checkout_id: Mapped[str] = mapped_column(ForeignKey("checkouts.id"), index=True)
    offer_id: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String)
    provider: Mapped[str] = mapped_column(String)
    provider_order_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # pending | confirmed | failed | cancelled | needs_attention (supplier order may exist; a human must check)
    status: Mapped[str] = mapped_column(String, default="pending")
    failure_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    total_price: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    offer: Mapped[dict] = mapped_column(JSON)
    passengers: Mapped[list] = mapped_column(JSON)
    contact: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class AgentSession(Base):
    """Conversation state for the chat agent, per channel (web, app, whatsapp)."""

    __tablename__ = "agent_sessions"
    __table_args__ = (UniqueConstraint("channel", "external_id", name="uq_agent_session"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    channel: Mapped[str] = mapped_column(String)  # web | app | whatsapp
    external_id: Mapped[str] = mapped_column(String)  # user id, or WhatsApp wa_id
    messages: Mapped[list] = mapped_column(JSON, default=list)
    seen_message_ids: Mapped[list] = mapped_column(JSON, default=list)  # webhook de-duplication
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
