"""Shared pytest fixtures.

Integration tests use a dedicated database (``procenka_test`` by default) on the
compose PostgreSQL. The database is created on demand. If PostgreSQL is not
reachable, the fixtures raise and the integration tests fail instead of being
skipped, so a missing database never produces a false green run.
"""

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from _pytest.terminal import TerminalReporter
from alembic import command
from alembic.config import Config
from sqlalchemy import make_url, text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from app.db.models import Base

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"
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


async def _ensure_database(url: URL, *, recreate: bool = False) -> None:
    """Create the database if it does not exist yet; with ``recreate``, start from scratch."""
    # CREATE DATABASE cannot run inside a transaction block.
    admin_engine = _make_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        async with admin_engine.connect() as conn:
            quoted = conn.dialect.identifier_preparer.quote(str(url.database))
            if recreate:
                await conn.execute(text(f"DROP DATABASE IF EXISTS {quoted} WITH (FORCE)"))
            exists = await conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": url.database},
            )
            if not exists:
                await conn.execute(text(f"CREATE DATABASE {quoted}"))
    finally:
        await admin_engine.dispose()


def alembic_config(url: URL) -> Config:
    """Alembic configuration of backend/alembic.ini pointed at ``url``."""
    config = Config(str(ALEMBIC_INI))
    # ConfigParser interpolates %, which may appear in an encoded password.
    rendered = url.render_as_string(hide_password=False).replace("%", "%%")
    config.set_main_option("sqlalchemy.url", rendered)
    config.attributes["configure_logger"] = False
    return config


async def run_alembic(url: URL, action: str, *args: str) -> None:
    """Run an alembic command; env.py starts its own event loop, so use a thread."""
    await asyncio.to_thread(getattr(command, action), alembic_config(url), *args)


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


@pytest.fixture(scope="session")
async def migrated_database_url(test_database_url: URL) -> URL:
    """The test database with a fresh public schema migrated to head (once per session)."""
    engine = _make_engine(test_database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
    finally:
        await engine.dispose()
    await run_alembic(test_database_url, "upgrade", "head")
    return test_database_url


@pytest.fixture
async def migrated_engine(migrated_database_url: URL) -> AsyncIterator[AsyncEngine]:
    """An engine on the migrated test database; every table is empty at the start of a test."""
    engine = _make_engine(migrated_database_url)
    try:
        tables = ", ".join(table.name for table in Base.metadata.sorted_tables)
        async with engine.begin() as conn:
            await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def db_session(migrated_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with AsyncSession(migrated_engine, expire_on_commit=False) as session:
        yield session


@pytest.fixture
async def scratch_database_url(test_database_url: URL) -> URL:
    """A separate, freshly created empty database for migration tests."""
    url = test_database_url.set(database=f"{test_database_url.database}_migrations")
    await _ensure_database(url, recreate=True)
    return url


# --- Control cases summary -------------------------------------------------

_CONTROL_CASE_TEST = "test_control_cases.py::test_control_case["
_control_case_outcomes: dict[str, int] = {"matched": 0, "mismatched": 0}


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    if _CONTROL_CASE_TEST not in report.nodeid:
        return
    if report.when == "call":
        _control_case_outcomes["matched" if report.passed else "mismatched"] += 1
    elif report.failed:  # setup or teardown error also means the case did not match
        _control_case_outcomes["mismatched"] += 1


def pytest_terminal_summary(terminalreporter: TerminalReporter) -> None:
    matched = _control_case_outcomes["matched"]
    mismatched = _control_case_outcomes["mismatched"]
    if matched or mismatched:
        terminalreporter.write_line(
            f"control cases: {matched} matched, {mismatched} mismatched",
            red=bool(mismatched),
            green=not mismatched,
        )
