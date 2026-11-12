from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.api.main import create_app
from app.db.seed import seed
from tests.fakes import MONITOR_APPLICATION_NAME
from tests.integration.demo import Demo


@pytest.fixture
def session_factory(migrated_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(migrated_engine, expire_on_commit=False)


@pytest.fixture
async def seeded(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session, session.begin():
        await seed(session)


@pytest.fixture
async def api(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=create_app(session_factory))
    async with httpx.AsyncClient(transport=transport, base_url="http://api.test") as client:
        yield client


@pytest.fixture
async def monitor_engine(migrated_database_url: URL) -> AsyncIterator[AsyncEngine]:
    """Connections that watch pg_stat_activity and are excluded from what they count."""
    engine = create_async_engine(
        migrated_database_url,
        connect_args={"server_settings": {"application_name": MONITOR_APPLICATION_NAME}},
    )
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def demo(
    seeded: None, session_factory: async_sessionmaker[AsyncSession], api: httpx.AsyncClient
) -> AsyncIterator[Demo]:
    demo = Demo(session_factory, api)
    try:
        yield demo
    finally:
        await demo.control.aclose()
