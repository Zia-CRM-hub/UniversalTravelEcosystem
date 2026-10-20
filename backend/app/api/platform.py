"""Super-admin API: the platform operator manages tenants (customers). Only role `super_admin`."""
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.errors import DomainError
from ..db import get_db
from ..models import SUPER_ADMIN, TENANT_ADMIN, AuditEvent, Booking, Tenant, User
from ..schemas import AuditOut, TenantCreateIn, TenantOut, TenantPatchIn, TenantSettingsIn
from ..security import Principal, hash_password
from ..tenancy import PLATFORM_SLUG, audit, require_roles

router = APIRouter(prefix="/platform", tags=["platform (super admin)"])


def apply_settings(tenant: Tenant, body: TenantSettingsIn, platform_providers: list[str]) -> dict:
    """Apply only the fields the caller sent. Returns what changed (for the audit log)."""
    sent, s, changed = body.model_fields_set, dict(tenant.settings or {}), {}
    if "providers" in sent:
        if body.providers is not None and (unknown := set(body.providers) - set(platform_providers)):
            raise DomainError(422, "unknown_provider", f"Not enabled on this platform: {', '.join(sorted(unknown))}")
        s["providers"] = body.providers
        changed["providers"] = body.providers
    if "self_signup" in sent and body.self_signup is not None:
        s["self_signup"] = body.self_signup
        changed["self_signup"] = body.self_signup
    if "branding" in sent and body.branding is not None:
        patch = body.branding.model_dump(exclude_none=True)
        s["branding"] = {**s.get("branding", {}), **patch}
        changed["branding"] = patch
    tenant.settings = s  # reassign: in-place JSON mutation is not tracked
    return changed


async def _out(db: AsyncSession, t: Tenant) -> TenantOut:
    n = await db.scalar(select(func.count()).select_from(User).where(User.tenant_id == t.id))
    return TenantOut(id=t.id, slug=t.slug, name=t.name, status=t.status, max_users=t.max_users, max_orgs=t.max_orgs,
                     settings=t.settings or {}, user_count=n)


@router.get("/tenants", response_model=list[TenantOut])
async def list_tenants(_: Principal = Depends(require_roles(SUPER_ADMIN)), db: AsyncSession = Depends(get_db)):
    counts = dict((await db.execute(select(User.tenant_id, func.count()).where(User.tenant_id.is_not(None)).group_by(User.tenant_id))).all())
    out = []
    for t in (await db.scalars(select(Tenant).order_by(Tenant.created_at))).all():
        out.append(TenantOut(id=t.id, slug=t.slug, name=t.name, status=t.status, max_users=t.max_users, max_orgs=t.max_orgs,
                             settings=t.settings or {}, user_count=counts.get(t.id, 0)))
    return out


@router.post("/tenants", response_model=TenantOut, status_code=201)
async def create_tenant(body: TenantCreateIn, p: Principal = Depends(require_roles(SUPER_ADMIN)), db: AsyncSession = Depends(get_db)):
    """Onboard a customer: creates the tenant and its first tenant admin in one transaction."""
    if body.slug == PLATFORM_SLUG:
        raise DomainError(422, "reserved_slug", "That name is reserved")
    tenant = Tenant(slug=body.slug, name=body.name, max_users=body.max_users, max_orgs=body.max_orgs,
                    settings={"self_signup": False, "providers": None})
    db.add(tenant)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise DomainError(409, "slug_taken", "That tenant slug is already in use") from None
    admin = User(tenant_id=tenant.id, role=TENANT_ADMIN, email=body.admin_email.lower(), name=body.admin_name,
                 password_hash=hash_password(body.admin_password))
    db.add(admin)
    audit(db, p.user, "tenant.create", "tenant", tenant.id, tenant.id, slug=tenant.slug, admin_email=admin.email)
    await db.commit()
    return await _out(db, tenant)


@router.patch("/tenants/{tenant_id}", response_model=TenantOut)
async def patch_tenant(tenant_id: str, body: TenantPatchIn, request: Request, p: Principal = Depends(require_roles(SUPER_ADMIN)),
                       db: AsyncSession = Depends(get_db)):
    tenant = await db.get(Tenant, tenant_id)
    if not tenant:
        raise DomainError(404, "tenant_not_found", "Tenant not found")
    changed = apply_settings(tenant, body, list(request.app.state.providers))
    for f in ("name", "status", "max_users", "max_orgs"):
        v = getattr(body, f)
        if v is not None and v != getattr(tenant, f):
            changed[f] = v
            setattr(tenant, f, v)
    audit(db, p.user, "tenant.update", "tenant", tenant.id, tenant.id, **changed)
    await db.commit()
    return await _out(db, tenant)


@router.get("/stats")
async def stats(_: Principal = Depends(require_roles(SUPER_ADMIN)), db: AsyncSession = Depends(get_db)):
    """Platform-wide counts, plus confirmed booking value per currency."""
    one = lambda q: db.scalar(q)  # noqa: E731
    gmv = (await db.execute(select(Booking.currency, func.count(), func.sum(Booking.total_price))
                            .where(Booking.status == "confirmed").group_by(Booking.currency))).all()
    return {
        "tenants": await one(select(func.count()).select_from(Tenant)),
        "tenants_active": await one(select(func.count()).select_from(Tenant).where(Tenant.status == "active")),
        "users": await one(select(func.count()).select_from(User).where(User.tenant_id.is_not(None))),
        "bookings_by_status": dict((await db.execute(select(Booking.status, func.count()).group_by(Booking.status))).all()),
        "confirmed_value": [{"currency": c, "bookings": n, "total_minor": int(t or 0)} for c, n, t in gmv],
    }


@router.get("/audit", response_model=list[AuditOut])
async def platform_audit(tenant_id: str | None = None, limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
                         _: Principal = Depends(require_roles(SUPER_ADMIN)), db: AsyncSession = Depends(get_db)):
    q = select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(limit).offset(offset)
    if tenant_id:
        q = q.where(AuditEvent.tenant_id == tenant_id)
    return (await db.scalars(q)).all()
