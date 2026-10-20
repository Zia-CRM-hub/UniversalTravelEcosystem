from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.errors import DomainError
from ..db import get_db
from ..models import MEMBER, Organization, Tenant, User
from ..schemas import LoginIn, MeOut, RegisterIn, TokenOut
from ..security import Principal, current_principal, hash_password, make_access_token, verify_password
from ..tenancy import PLATFORM_SLUG, resolve_tenant, tenant_slug

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=TokenOut, status_code=201)
async def register(body: RegisterIn, tenant: Tenant = Depends(resolve_tenant), db: AsyncSession = Depends(get_db)):
    """Self-service sign-up into a tenant. Tenants that onboard staff themselves switch this off."""
    if not (tenant.settings or {}).get("self_signup", False):
        raise DomainError(403, "signup_disabled", "Sign-up is by invitation for this organization")
    email = body.email.lower()
    if await db.scalar(select(User).where(User.tenant_id == tenant.id, User.email == email)):
        raise DomainError(409, "email_taken", "Email already registered")
    users = await db.scalar(select(func.count()).select_from(User).where(User.tenant_id == tenant.id, User.is_active))
    if users >= tenant.max_users:
        raise DomainError(403, "plan_limit", "This organization has reached its user limit")
    user = User(tenant_id=tenant.id, role=MEMBER, email=email, name=body.name, password_hash=hash_password(body.password))
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise DomainError(409, "email_taken", "Email already registered") from None
    return TokenOut(access_token=make_access_token(user))


@router.post("/login", response_model=TokenOut)
async def login(body: LoginIn, request: Request, db: AsyncSession = Depends(get_db)):
    """Authenticate within one tenant (X-Tenant-Slug, default tenant otherwise). Slug `platform` = super admins."""
    slug = tenant_slug(request)
    tenant_id = None
    if slug != PLATFORM_SLUG:
        tenant = await db.scalar(select(Tenant).where(Tenant.slug == slug))
        if not tenant:
            raise DomainError(404, "tenant_not_found", "Unknown organization")
        if tenant.status != "active":
            raise DomainError(403, "tenant_suspended", "This organization's account is suspended")
        tenant_id = tenant.id
    cond = User.tenant_id.is_(None) if tenant_id is None else User.tenant_id == tenant_id
    user = await db.scalar(select(User).where(cond, User.email == body.email.lower()))
    if not user or not verify_password(body.password, user.password_hash):
        raise DomainError(401, "bad_credentials", "Invalid email or password")
    if not user.is_active:
        raise DomainError(403, "account_disabled", "This account has been disabled")
    return TokenOut(access_token=make_access_token(user))


@router.get("/me", response_model=MeOut)
async def me(p: Principal = Depends(current_principal), db: AsyncSession = Depends(get_db)):
    org = await db.get(Organization, p.user.organization_id) if p.user.organization_id else None
    out = MeOut.model_validate(p.user)
    out.tenant = {"slug": p.tenant.slug, "name": p.tenant.name} if p.tenant else None
    out.organization = {"id": org.id, "name": org.name} if org else None
    return out
