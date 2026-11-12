"""Return the demo to its initial state: ``uv run python -m app.db.reset``.

Empties every table (runs, source results, offers, calculations, published
prices, products), restarts identities and loads the seed again, all in one
transaction. The result is exactly the state of a fresh ``seed`` on an empty
database, including ids. Migrations are not applied here.
"""

import asyncio

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.enums import ACTIVE_RUN_STATUSES
from app.db.models import Base, Run
from app.db.seed import SeedReport, seed
from app.db.session import create_engine, create_session_factory


async def reset(session: AsyncSession) -> SeedReport:
    """Truncate all demo tables and seed again; the caller commits."""
    preparer = session.get_bind().dialect.identifier_preparer
    tables = ", ".join(preparer.format_table(table) for table in Base.metadata.sorted_tables)
    await session.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    return await seed(session)


class ActiveRunError(RuntimeError):
    """A run is queued or running, so the demo is not reset."""

    def __init__(self, run_id: int) -> None:
        super().__init__(f"run {run_id} is queued or running")
        self.run_id = run_id


async def active_run_id(session: AsyncSession) -> int | None:
    """The queued or running run, if any."""
    run_id: int | None = await session.scalar(
        select(Run.id).where(Run.status.in_(ACTIVE_RUN_STATUSES)).limit(1)
    )
    return run_id


async def reset_when_idle(session: AsyncSession) -> SeedReport:
    """``reset`` unless a run is queued or running (then ``ActiveRunError``); the caller commits.

    ``runs`` is locked first (EXCLUSIVE: reads go on, writes wait), so no run can
    be queued or claimed between the check and the truncation.
    """
    await session.execute(text("LOCK TABLE runs IN EXCLUSIVE MODE"))
    active = await active_run_id(session)
    if active is not None:
        raise ActiveRunError(active)
    return await reset(session)


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
