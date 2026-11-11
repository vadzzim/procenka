import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = pytest.mark.integration


async def test_connects_to_postgres_16(db_engine: AsyncEngine) -> None:
    async with db_engine.connect() as conn:
        version = await conn.scalar(text("SELECT version()"))
        server_version_num = await conn.scalar(text("SHOW server_version_num"))
        database = await conn.scalar(text("SELECT current_database()"))

    assert isinstance(version, str)
    assert version.startswith("PostgreSQL 16.")
    assert isinstance(server_version_num, str)
    assert 160000 <= int(server_version_num) < 170000
    assert database == db_engine.url.database
