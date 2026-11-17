"""Shared test setup.

Tests use their OWN database ("<db>_test") so they can freely delete data
without touching your development data.
"""
import asyncio
import os
import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import PROJECT_ROOT, get_settings

# ---- Point the whole app at the test database BEFORE anything connects ----
_dev_url = make_url(get_settings().database_url)
TEST_DB_NAME = f"{_dev_url.database}_test"
TEST_DB_URL = _dev_url.set(database=TEST_DB_NAME).render_as_string(hide_password=False)
os.environ["DATABASE_URL"] = TEST_DB_URL
get_settings.cache_clear()


async def _recreate_test_database() -> None:
    """Drop and re-create the test database: every run starts from a truly empty database."""
    conn = await asyncpg.connect(
        user=_dev_url.username,
        password=_dev_url.password,
        host=_dev_url.host,
        port=_dev_url.port,
        database=_dev_url.database,
    )
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{TEST_DB_NAME}"')
    finally:
        await conn.close()


@pytest.fixture(scope="session", autouse=True)
def migrated_test_database() -> None:
    """Once per test run: fresh DB, then ALL migrations up, all the way down, and up again.

    That tests both directions of every migration, not just the latest state.
    """
    from alembic import command
    from alembic.config import Config

    asyncio.run(_recreate_test_database())
    cfg = Config(str(PROJECT_ROOT / "backend" / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", TEST_DB_URL.replace("%", "%%"))
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


@pytest.fixture
async def client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """An HTTP client that calls the FastAPI app directly in memory (no real server).

    The app's database dependency is swapped for one pointing at the test database.
    Depends on `session` so every test starts with empty tables.
    """
    from app.db import get_session
    from app.main import app

    engine = create_async_engine(TEST_DB_URL, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_session():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = override_get_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
    await engine.dispose()


@pytest.fixture
def make_agent(session: AsyncSession):
    """Factory: create a team member with a role and get auth headers for them."""
    from app.core.security import create_access_token, hash_password
    from app.models import HumanAgent
    from app.models.enums import AgentRole

    async def _make(role: AgentRole = AgentRole.ADMIN, email: str | None = None,
                    password: str = "Correct-Horse-1") -> tuple[HumanAgent, dict[str, str]]:
        agent = HumanAgent(full_name=f"{role.value} user", role=role,
                           email=email or f"{role.value}-{uuid.uuid4().hex[:6]}@test.dev",
                           password_hash=hash_password(password))
        session.add(agent)
        await session.commit()
        return agent, {"Authorization": f"Bearer {create_access_token(agent.id)}"}

    return _make


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """A fresh, EMPTY database session for each test."""
    engine = create_async_engine(TEST_DB_URL, poolclass=NullPool)
    async with engine.begin() as conn:
        tables = (await conn.execute(text(
            "SELECT string_agg(quote_ident(tablename), ', ') FROM pg_tables "
            "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
        ))).scalar_one()
        await conn.execute(text(f"TRUNCATE {tables} CASCADE"))
    async with AsyncSession(engine, expire_on_commit=False) as s:
        yield s
    await engine.dispose()
