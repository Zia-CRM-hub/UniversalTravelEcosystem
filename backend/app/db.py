from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from .core.config import settings

_kw = {"poolclass": NullPool} if settings.database_url.startswith("sqlite") else {"pool_pre_ping": True, "pool_size": 10}
engine = create_async_engine(settings.database_url, **_kw)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with SessionLocal() as session:
        yield session
