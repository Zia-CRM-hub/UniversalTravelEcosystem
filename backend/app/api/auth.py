from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.errors import DomainError
from ..db import get_db
from ..models import User
from ..schemas import LoginIn, RegisterIn, TokenOut, UserOut
from ..security import current_user, hash_password, make_access_token, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=TokenOut, status_code=201)
async def register(body: RegisterIn, db: AsyncSession = Depends(get_db)):
    email = body.email.lower()
    if await db.scalar(select(User).where(User.email == email)):
        raise DomainError(409, "email_taken", "Email already registered")
    user = User(email=email, name=body.name, password_hash=hash_password(body.password))
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise DomainError(409, "email_taken", "Email already registered") from None
    return TokenOut(access_token=make_access_token(user.id))


@router.post("/login", response_model=TokenOut)
async def login(body: LoginIn, db: AsyncSession = Depends(get_db)):
    user = await db.scalar(select(User).where(User.email == body.email.lower()))
    if not user or not verify_password(body.password, user.password_hash):
        raise DomainError(401, "bad_credentials", "Invalid email or password")
    return TokenOut(access_token=make_access_token(user.id))


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(current_user)):
    return user
