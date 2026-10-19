import hashlib
import hmac
import os
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from .core.config import settings
from .core.errors import DomainError
from .db import get_db
from .models import User

_bearer = HTTPBearer(auto_error=False)


def hash_password(pw: str) -> str:
    salt = os.urandom(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1)
    return salt.hex() + "$" + h.hex()


def verify_password(pw: str, stored: str) -> bool:
    salt_hex, h_hex = stored.split("$")
    h = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1)
    return hmac.compare_digest(h.hex(), h_hex)


def make_access_token(user_id: str) -> str:
    exp = datetime.now(UTC) + timedelta(minutes=settings.token_minutes)
    return jwt.encode({"sub": user_id, "exp": exp}, settings.secret_key.get_secret_value(), algorithm="HS256")


async def current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    if not creds:
        raise DomainError(401, "unauthenticated", "Not authenticated")
    try:
        data = jwt.decode(creds.credentials, settings.secret_key.get_secret_value(), algorithms=["HS256"])
    except jwt.PyJWTError:
        raise DomainError(401, "invalid_token", "Invalid or expired token") from None
    user = await db.get(User, data.get("sub"))
    if not user:
        raise DomainError(401, "invalid_token", "User not found")
    return user
