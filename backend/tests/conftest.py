"""Shared pytest fixtures.

Integration tests use a dedicated database (``procenka_test`` by default) on the
compose PostgreSQL. The database is created on demand. If PostgreSQL is not
reachable, the fixtures raise and the integration tests fail instead of being
skipped, so a missing database never produces a false green run.
"""

import os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import make_url, text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

DEFAULT_TEST_DATABASE_URL = "postgresql+asyncpg://procenka:procenka@localhost:54329/procenka_test"
CONNECT_TIMEOUT_SECONDS = 5


def _test_database_url() -> URL:
    url = make_url(os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL))
    if url.drivername != "postgresql+asyncpg":
        raise RuntimeError(f"TEST_DATABASE_URL must use postgresql+asyncpg, got {url.drivername!r}")
    if not url.database:
        raise RuntimeError("TEST_DATABASE_URL must name a database")
    return url


def _make_engine(url: URL, *, isolation_level: str | None = None) -> AsyncEngine:
    options = {"isolation_level": isolation_level} if isolation_level else {}
    return create_async_engine(
        url,
        connect_args={"timeout": CONNECT_TIMEOUT_SECONDS},
        pool_pre_ping=True,
        **options,
    )


async def _ensure_database(url: URL) -> None:
    """Create the test database if it does not exist yet."""
    # CREATE DATABASE cannot run inside a transaction block.
    admin_engine = _make_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        async with admin_engine.connect() as conn:
            exists = await conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": url.database},
            )
            if not exists:
                quoted = conn.dialect.identifier_preparer.quote(str(url.database))
                await conn.execute(text(f"CREATE DATABASE {quoted}"))
    finally:
        await admin_engine.dispose()


@pytest.fixture(scope="session")
async def test_database_url() -> URL:
    url = _test_database_url()
    await _ensure_database(url)
    return url


@pytest.fixture
async def db_engine(test_database_url: URL) -> AsyncIterator[AsyncEngine]:
    engine = _make_engine(test_database_url)
    try:
        yield engine
    finally:
        await engine.dispose()
