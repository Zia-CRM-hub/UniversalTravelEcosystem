import hashlib
import hmac
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from .core.config import settings
from .core.errors import DomainError
from .db import get_db
from .models import ROLES, SUPER_ADMIN, Tenant, User

_bearer = HTTPBearer(auto_error=False)


def hash_password(pw: str) -> str:
    salt = os.urandom(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1)
    return salt.hex() + "$" + h.hex()


def verify_password(pw: str, stored: str) -> bool:
    salt_hex, h_hex = stored.split("$")
    h = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1)
    return hmac.compare_digest(h.hex(), h_hex)


def make_access_token(user: User) -> str:
    exp = datetime.now(UTC) + timedelta(minutes=settings.token_minutes)
    claims = {"sub": user.id, "tid": user.tenant_id, "exp": exp}
    return jwt.encode(claims, settings.secret_key.get_secret_value(), algorithm="HS256")


@dataclass
class Principal:
    user: User
    tenant: Tenant | None  # None only for the platform super admin


async def current_principal(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> Principal:
    """Authenticate, then re-check the account and its tenant on EVERY request, so disabling a user or
    suspending a tenant takes effect immediately instead of when the token expires."""
    if not creds:
        raise DomainError(401, "unauthenticated", "Not authenticated")
    try:
        data = jwt.decode(creds.credentials, settings.secret_key.get_secret_value(), algorithms=["HS256"])
    except jwt.PyJWTError:
        raise DomainError(401, "invalid_token", "Invalid or expired token") from None
    user = await db.get(User, data.get("sub"))
    if not user or user.tenant_id != data.get("tid") or user.role not in ROLES:
        raise DomainError(401, "invalid_token", "User not found")
    if not user.is_active:
        raise DomainError(403, "account_disabled", "This account has been disabled")
    tenant = None
    if user.role != SUPER_ADMIN:
        tenant = await db.get(Tenant, user.tenant_id)
        if not tenant:
            raise DomainError(401, "invalid_token", "User not found")
        if tenant.status != "active":
            raise DomainError(403, "tenant_suspended", "This organization's account is suspended")
    return Principal(user, tenant)


async def current_user(p: Principal = Depends(current_principal)) -> User:
    return p.user
