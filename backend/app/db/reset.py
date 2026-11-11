"""Return the demo to its initial state: ``uv run python -m app.db.reset``.

Empties every table (runs, source results, offers, calculations, published
prices, products), restarts identities and loads the seed again, all in one
transaction. The result is exactly the state of a fresh ``seed`` on an empty
database, including ids. Migrations are not applied here.
"""

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import Base
from app.db.seed import SeedReport, seed
from app.db.session import create_engine, create_session_factory


async def reset(session: AsyncSession) -> SeedReport:
    """Truncate all demo tables and seed again; the caller commits."""
    preparer = session.get_bind().dialect.identifier_preparer
    tables = ", ".join(preparer.format_table(table) for table in Base.metadata.sorted_tables)
    await session.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    return await seed(session)


async def _main() -> None:
    engine = create_engine(get_settings().database_url)
    try:
        async with create_session_factory(engine)() as session, session.begin():
            report = await reset(session)
    finally:
        await engine.dispose()
    print(
        f"reset: all tables emptied; {report.products_inserted} products and "
        f"{report.published_prices_inserted} published prices loaded"
    )


if __name__ == "__main__":
    asyncio.run(_main())
