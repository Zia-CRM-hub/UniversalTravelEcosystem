"""Tenant-admin and organization-admin API.

tenant_admin: everything inside their tenant.  org_admin: only their own organization's members and bookings.
Every query is filtered by the caller's tenant (and organization for org admins); out-of-scope ids return 404.
"""
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.errors import DomainError
from ..core.money import to_major, to_minor
from ..db import get_db
from ..models import MEMBER, ORG_ADMIN, TENANT_ADMIN, AuditEvent, Booking, Organization, Tenant, User
from ..schemas import (
    AdminBookingOut,
    AdminUserIn,
    AdminUserPatchIn,
    AuditOut,
    OrgIn,
    OrgOut,
    OrgPatchIn,
    TenantOut,
    TenantSettingsIn,
)
from ..security import Principal, hash_password
from ..tenancy import audit, require_roles
from .platform import apply_settings

router = APIRouter(prefix="/admin", tags=["admin (tenant / organization)"])
tenant_admin = require_roles(TENANT_ADMIN)
any_admin = require_roles(TENANT_ADMIN, ORG_ADMIN)


def _org_id(p: Principal) -> str:
    if not p.user.organization_id:
        raise DomainError(403, "no_organization", "Your account is not attached to an organization")
    return p.user.organization_id


def _org_out(o: Organization, members: int | None = None) -> OrgOut:
    major = None
    if o.max_trip_amount is not None and o.policy_currency:
        major = float(to_major(o.max_trip_amount, o.policy_currency))
    return OrgOut(id=o.id, tenant_id=o.tenant_id, name=o.name, status=o.status, max_trip_amount=major,
                  policy_currency=o.policy_currency, member_count=members)


async def _active_users(db: AsyncSession, tenant_id: str) -> int:
    return await db.scalar(select(func.count()).select_from(User).where(User.tenant_id == tenant_id, User.is_active))


async def _org_in_tenant(db: AsyncSession, tenant_id: str, org_id: str) -> Organization:
    org = await db.get(Organization, org_id)
    if not org or org.tenant_id != tenant_id:
        raise DomainError(404, "organization_not_found", "Organization not found")
    return org


def _policy(max_amount: int | None, currency: str | None) -> tuple[int | None, str | None]:
    if max_amount is None:
        return None, currency
    if not currency:
        raise DomainError(422, "policy_currency_required", "policy_currency is required with max_trip_amount")
    return to_minor(max_amount, currency), currency


# ---- tenant ---------------------------------------------------------------------------------
@router.get("/tenant", response_model=TenantOut)
async def get_tenant(p: Principal = Depends(tenant_admin), db: AsyncSession = Depends(get_db)):
    t = p.tenant
    n = await _active_users(db, t.id)
    return TenantOut(id=t.id, slug=t.slug, name=t.name, status=t.status, max_users=t.max_users, max_orgs=t.max_orgs,
                     settings=t.settings or {}, user_count=n)


@router.patch("/tenant", response_model=TenantOut)
async def patch_tenant(body: TenantSettingsIn, request: Request, p: Principal = Depends(tenant_admin),
                       db: AsyncSession = Depends(get_db)):
    """Branding, sign-up policy and which suppliers this tenant's users may search. Plan limits are the platform's."""
    tenant = await db.get(Tenant, p.tenant.id)
    changed = apply_settings(tenant, body, list(request.app.state.providers))
    audit(db, p.user, "tenant.settings", "tenant", tenant.id, tenant.id, **changed)
    await db.commit()
    return await get_tenant(Principal(p.user, tenant), db)


# ---- organizations ----------------------------------------------------------------------------
@router.get("/organizations", response_model=list[OrgOut])
async def list_orgs(p: Principal = Depends(any_admin), db: AsyncSession = Depends(get_db)):
    q = select(Organization).where(Organization.tenant_id == p.tenant.id).order_by(Organization.name)
    if p.user.role == ORG_ADMIN:
        q = q.where(Organization.id == _org_id(p))
    counts = dict((await db.execute(select(User.organization_id, func.count()).where(
        User.tenant_id == p.tenant.id, User.organization_id.is_not(None)).group_by(User.organization_id))).all())
    return [_org_out(o, counts.get(o.id, 0)) for o in (await db.scalars(q)).all()]


@router.post("/organizations", response_model=OrgOut, status_code=201)
async def create_org(body: OrgIn, p: Principal = Depends(tenant_admin), db: AsyncSession = Depends(get_db)):
    n = await db.scalar(select(func.count()).select_from(Organization).where(Organization.tenant_id == p.tenant.id))
    if n >= p.tenant.max_orgs:
        raise DomainError(403, "plan_limit", "This account has reached its organization limit")
    minor, cur = _policy(body.max_trip_amount, body.policy_currency)
    org = Organization(tenant_id=p.tenant.id, name=body.name, max_trip_amount=minor, policy_currency=cur)
    db.add(org)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise DomainError(409, "organization_exists", "An organization with that name already exists") from None
    audit(db, p.user, "org.create", "organization", org.id, p.tenant.id, name=org.name)
    await db.commit()
    return _org_out(org, 0)


@router.patch("/organizations/{org_id}", response_model=OrgOut)
async def patch_org(org_id: str, body: OrgPatchIn, p: Principal = Depends(tenant_admin), db: AsyncSession = Depends(get_db)):
    org = await _org_in_tenant(db, p.tenant.id, org_id)
    sent, changed = body.model_fields_set, {}
    if body.name is not None:
        org.name, changed["name"] = body.name, body.name
    if body.status is not None:
        org.status, changed["status"] = body.status, body.status
    if "max_trip_amount" in sent or "policy_currency" in sent:
        amount = body.max_trip_amount if "max_trip_amount" in sent else (
            float(to_major(org.max_trip_amount, org.policy_currency)) if org.max_trip_amount is not None else None)
        minor, cur = _policy(int(amount) if amount is not None else None, body.policy_currency or org.policy_currency)
        org.max_trip_amount, org.policy_currency = minor, cur
        changed["max_trip_amount"], changed["policy_currency"] = amount, cur
    audit(db, p.user, "org.update", "organization", org.id, p.tenant.id, **changed)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise DomainError(409, "organization_exists", "An organization with that name already exists") from None
    return _org_out(org)


# ---- users -------------------------------------------------------------------------------------
def _user_scope(p: Principal) -> list:
    conds = [User.tenant_id == p.tenant.id]
    if p.user.role == ORG_ADMIN:
        conds.append(User.organization_id == _org_id(p))
    return conds


async def _target(db: AsyncSession, p: Principal, user_id: str) -> User:
    u = await db.scalar(select(User).where(User.id == user_id, *_user_scope(p)))
    if not u:
        raise DomainError(404, "user_not_found", "User not found")
    return u


def _user_json(u: User) -> dict:
    return {"id": u.id, "email": u.email, "name": u.name, "role": u.role, "tenant_id": u.tenant_id,
            "organization_id": u.organization_id, "is_active": u.is_active}


@router.get("/users")
async def list_users(organization_id: str | None = None, limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
                     p: Principal = Depends(any_admin), db: AsyncSession = Depends(get_db)):
    q = select(User).where(*_user_scope(p)).order_by(User.created_at).limit(limit).offset(offset)
    if organization_id and p.user.role == TENANT_ADMIN:
        q = q.where(User.organization_id == organization_id)
    return [_user_json(u) for u in (await db.scalars(q)).all()]


@router.post("/users", status_code=201)
async def create_user(body: AdminUserIn, p: Principal = Depends(any_admin), db: AsyncSession = Depends(get_db)):
    if p.user.role == ORG_ADMIN:
        if body.role != MEMBER:
            raise DomainError(403, "forbidden", "Organization admins can only add members")
        org_id = _org_id(p)
    else:
        org_id = body.organization_id
    if org_id:
        await _org_in_tenant(db, p.tenant.id, org_id)
    if body.role == ORG_ADMIN and not org_id:
        raise DomainError(422, "organization_required", "An organization admin must belong to an organization")
    email = body.email.lower()
    if await db.scalar(select(User).where(User.tenant_id == p.tenant.id, User.email == email)):
        raise DomainError(409, "email_taken", "Email already registered")
    if await _active_users(db, p.tenant.id) >= p.tenant.max_users:
        raise DomainError(403, "plan_limit", "This account has reached its user limit")
    u = User(tenant_id=p.tenant.id, organization_id=org_id, role=body.role, email=email, name=body.name,
             password_hash=hash_password(body.password))
    db.add(u)
    await db.flush()
    audit(db, p.user, "user.create", "user", u.id, p.tenant.id, email=email, role=u.role, organization_id=org_id)
    await db.commit()
    return _user_json(u)


@router.patch("/users/{user_id}")
async def patch_user(user_id: str, body: AdminUserPatchIn, p: Principal = Depends(any_admin), db: AsyncSession = Depends(get_db)):
    u = await _target(db, p, user_id)
    sent, changed = body.model_fields_set, {}
    if u.id == p.user.id and ((body.role is not None and body.role != u.role) or body.is_active is False):
        raise DomainError(403, "cannot_modify_self", "You cannot demote or disable your own account")
    if p.user.role == ORG_ADMIN:
        if u.role != MEMBER or (body.role not in (None, MEMBER)):
            raise DomainError(403, "forbidden", "Organization admins can only manage members")
        if "organization_id" in sent and body.organization_id != p.user.organization_id:
            raise DomainError(403, "forbidden", "You cannot move members out of your organization")
    if body.name is not None:
        u.name, changed["name"] = body.name, body.name
    if body.role is not None and body.role != u.role:
        u.role, changed["role"] = body.role, body.role
    if "organization_id" in sent:
        if body.organization_id:
            await _org_in_tenant(db, p.tenant.id, body.organization_id)
        u.organization_id, changed["organization_id"] = body.organization_id, body.organization_id
    if u.role == ORG_ADMIN and not u.organization_id:
        raise DomainError(422, "organization_required", "An organization admin must belong to an organization")
    if body.is_active is not None and body.is_active != u.is_active:
        if body.is_active and await _active_users(db, p.tenant.id) >= p.tenant.max_users:
            raise DomainError(403, "plan_limit", "This account has reached its user limit")
        u.is_active, changed["is_active"] = body.is_active, body.is_active
    if body.password is not None:
        u.password_hash, changed["password"] = hash_password(body.password), "reset"  # never log the value
    audit(db, p.user, "user.update", "user", u.id, p.tenant.id, **changed)
    await db.commit()
    return _user_json(u)


# ---- bookings & audit -----------------------------------------------------------------------------
@router.get("/bookings", response_model=list[AdminBookingOut])
async def list_bookings(organization_id: str | None = None, status: str | None = None, limit: int = Query(100, ge=1, le=500),
                        offset: int = Query(0, ge=0), p: Principal = Depends(any_admin), db: AsyncSession = Depends(get_db)):
    """Every booking in scope, including pending / failed / needs_attention, so operations can spot trouble."""
    q = select(Booking).where(Booking.tenant_id == p.tenant.id).order_by(Booking.created_at.desc()).limit(limit).offset(offset)
    if p.user.role == ORG_ADMIN:
        q = q.where(Booking.organization_id == _org_id(p))
    elif organization_id:
        q = q.where(Booking.organization_id == organization_id)
    if status:
        q = q.where(Booking.status == status)
    return (await db.scalars(q)).all()


@router.get("/audit", response_model=list[AuditOut])
async def tenant_audit(limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0), p: Principal = Depends(tenant_admin),
                       db: AsyncSession = Depends(get_db)):
    q = select(AuditEvent).where(AuditEvent.tenant_id == p.tenant.id).order_by(AuditEvent.created_at.desc()).limit(limit).offset(offset)
    return (await db.scalars(q)).all()
