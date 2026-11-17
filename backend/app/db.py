"""Database engine and session factory (async SQLAlchemy)."""
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings

engine = create_async_engine(
    get_settings().database_url,
    pool_pre_ping=True,   # check a connection is alive before using it
)

# A "session" is one unit of work: queries + changes, then commit or rollback.
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    """Used by FastAPI routes (Stage 4) to get a session per request."""
    async with SessionLocal() as session:
        yield session
