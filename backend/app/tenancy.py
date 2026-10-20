"""Multi-tenancy: tenant resolution, role checks, per-tenant supplier scoping, audit, bootstrap."""
import logging
from collections.abc import Callable

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .core.config import settings
from .core.errors import DomainError
from .db import get_db
from .models import MEMBER, ORG_ADMIN, SUPER_ADMIN, TENANT_ADMIN, AuditEvent, Booking, Tenant, User
from .security import Principal, current_principal, hash_password

log = logging.getLogger(__name__)
PLATFORM_SLUG = "platform"  # reserved: log in here to reach the super-admin (tenant-less) account


def tenant_slug(request: Request) -> str:
    return (request.headers.get("X-Tenant-Slug") or settings.default_tenant_slug).strip().lower()


async def resolve_tenant(request: Request, db: AsyncSession = Depends(get_db)) -> Tenant:
    """The tenant for PUBLIC requests (search, login, sign-up): from the X-Tenant-Slug header, else the default tenant.
    Authenticated requests use the tenant of the logged-in user instead and ignore the header."""
    slug = tenant_slug(request)
    tenant = await db.scalar(select(Tenant).where(Tenant.slug == slug))
    if not tenant:
        raise DomainError(404, "tenant_not_found", "Unknown organization")
    if tenant.status != "active":
        raise DomainError(403, "tenant_suspended", "This organization's account is suspended")
    return tenant


def providers_for(all_providers: dict, tenant: Tenant) -> dict:
    """Suppliers this tenant may use: the platform's enabled set, narrowed by the tenant's allow-list."""
    allowed = (tenant.settings or {}).get("providers")
    if allowed is None:
        return dict(all_providers)
    return {n: p for n, p in all_providers.items() if n in allowed}


def require_roles(*roles: str) -> Callable:
    async def dep(p: Principal = Depends(current_principal)) -> Principal:
        if p.user.role not in roles:
            raise DomainError(403, "forbidden", "You do not have permission to do this")
        return p

    return dep


def can_access_booking(user: User, b: Booking) -> bool:
    """Members see their own bookings, org admins their organization's, tenant admins their tenant's."""
    if user.role == TENANT_ADMIN:
        return b.tenant_id == user.tenant_id
    if user.role == ORG_ADMIN:
        return b.tenant_id == user.tenant_id and user.organization_id is not None and b.organization_id == user.organization_id
    return user.role == MEMBER and b.user_id == user.id


def audit(db: AsyncSession, actor: User | None, action: str, target_type: str, target_id: str | None,
          tenant_id: str | None, **detail) -> None:
    """Queue an audit row; commits with the caller's transaction so the action and its record land together."""
    db.add(AuditEvent(tenant_id=tenant_id, actor_id=actor.id if actor else None, actor_email=actor.email if actor else None,
                      action=action, target_type=target_type, target_id=target_id, detail=detail))


async def ensure_platform(db: AsyncSession) -> None:
    """Idempotent start-up bootstrap: the default consumer tenant and (optionally) the super admin."""
    slug = settings.default_tenant_slug
    if not await db.scalar(select(Tenant).where(Tenant.slug == slug)):
        db.add(Tenant(slug=slug, name="Public", max_users=1_000_000, max_orgs=1_000,
                      settings={"self_signup": True, "providers": None}))
    email, pw = settings.superadmin_email, settings.superadmin_password
    if email and pw:
        exists = await db.scalar(select(User).where(User.tenant_id.is_(None), User.email == email.lower()))
        if not exists:
            db.add(User(tenant_id=None, role=SUPER_ADMIN, email=email.lower(), name="Platform Admin",
                        password_hash=hash_password(pw.get_secret_value())))
            log.info("super admin bootstrapped for %s", email)
    await db.commit()
