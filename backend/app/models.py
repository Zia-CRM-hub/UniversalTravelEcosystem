import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base

# Roles, from broadest to narrowest scope.
SUPER_ADMIN = "super_admin"  # the platform operator; belongs to no tenant
TENANT_ADMIN = "tenant_admin"  # runs one tenant (a customer company or white-label brand)
ORG_ADMIN = "org_admin"  # runs one organization (department / corporate client) inside a tenant
MEMBER = "member"  # a traveller
ROLES = (SUPER_ADMIN, TENANT_ADMIN, ORG_ADMIN, MEMBER)


def now() -> datetime:
    return datetime.now(UTC)


def aware(dt: datetime) -> datetime:
    """SQLite drops tzinfo; everything we store is UTC."""
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def new_id() -> str:
    return uuid.uuid4().hex


class Tenant(Base):
    """A customer of the platform. Every business row below carries its tenant_id."""

    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    slug: Mapped[str] = mapped_column(String, unique=True, index=True)
    name: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="active")  # active | suspended
    max_users: Mapped[int] = mapped_column(Integer, default=50)  # plan limits, set by the super admin
    max_orgs: Mapped[int] = mapped_column(Integer, default=10)
    # {"providers": ["duffel"] | null (= all), "self_signup": bool, "branding": {display_name, primary_color, logo_url}}
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Organization(Base):
    __tablename__ = "organizations"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_org_name"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="active")
    # Travel policy: a single checkout may not exceed this (minor units of `policy_currency`).
    max_trip_amount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    policy_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class User(Base):
    __tablename__ = "users"
    # Same email may exist in different tenants. (Super admins have tenant_id NULL; uniqueness is enforced in code.)
    __table_args__ = (UniqueConstraint("tenant_id", "email", name="uq_user_tenant_email"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    tenant_id: Mapped[str | None] = mapped_column(ForeignKey("tenants.id"), nullable=True, index=True)
    organization_id: Mapped[str | None] = mapped_column(ForeignKey("organizations.id"), nullable=True, index=True)
    role: Mapped[str] = mapped_column(String, default=MEMBER)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    email: Mapped[str] = mapped_column(String, index=True)
    name: Mapped[str] = mapped_column(String)
    password_hash: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AuditEvent(Base):
    """Who did what, for admin actions. Append-only."""

    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    tenant_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    actor_id: Mapped[str | None] = mapped_column(String, nullable=True)
    actor_email: Mapped[str | None] = mapped_column(String, nullable=True)
    action: Mapped[str] = mapped_column(String, index=True)
    target_type: Mapped[str] = mapped_column(String)
    target_id: Mapped[str | None] = mapped_column(String, nullable=True)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class OfferSnapshot(Base):
    """What the user was shown. Booking trusts this row, never client-supplied prices."""

    __tablename__ = "offer_snapshots"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
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
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    organization_id: Mapped[str | None] = mapped_column(ForeignKey("organizations.id"), nullable=True, index=True)
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
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    organization_id: Mapped[str | None] = mapped_column(ForeignKey("organizations.id"), nullable=True, index=True)
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
    __table_args__ = (UniqueConstraint("tenant_id", "channel", "external_id", name="uq_agent_session"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    channel: Mapped[str] = mapped_column(String)  # app | whatsapp
    external_id: Mapped[str] = mapped_column(String)  # user id, or WhatsApp wa_id
    messages: Mapped[list] = mapped_column(JSON, default=list)
    seen_message_ids: Mapped[list] = mapped_column(JSON, default=list)  # webhook de-duplication
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
