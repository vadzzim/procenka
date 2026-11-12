"""Writing ``published_prices`` and the lock that serializes every such write.

Two transactions publish prices: the one that finishes a run
(``app.worker.execute``) and the one that confirms a quarantined
recommendation (``app.db.confirm``). Both first take one transaction-level
advisory lock, ``PUBLICATION_LOCK_KEY``, so they run one after the other:

* a confirmation that waited for a run to finish sees that run (it reads after
  the lock, and every statement under READ COMMITTED sees what was committed
  before it) and refuses with ``superseded`` instead of publishing a stale
  recommendation;
* a run that finishes after a confirmation sees the confirmed price and
  recalculates the products whose published price changed since it read them.

Row locks would not do: a run that supersedes a recommendation may write no
``published_prices`` row of that product at all (it quarantines it again, or
leaves it unchanged), so there is no common row to lock. The lock is released
at commit or rollback.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PublishedPrice

# An arbitrary constant of this application ("proc" in ASCII).
PUBLICATION_LOCK_KEY = 0x70726F63


async def lock_publication(session: AsyncSession) -> None:
    """Wait for and take the publication lock until the end of the transaction."""
    await session.execute(select(func.pg_advisory_xact_lock(PUBLICATION_LOCK_KEY)))


@dataclass(frozen=True, slots=True)
class Publication:
    product_id: int
    calculation_id: int
    price: Decimal


async def upsert_published_prices(
    session: AsyncSession,
    publications: Sequence[Publication],
    *,
    published_at: datetime | ColumnElement[datetime] | None = None,
) -> None:
    """``INSERT ... ON CONFLICT (product_id) DO UPDATE``; ``published_at`` defaults to now()."""
    if not publications:
        return
    at = func.now() if published_at is None else published_at
    statement = insert(PublishedPrice).values(
        [
            {
                "product_id": item.product_id,
                "price": item.price,
                "published_at": at,
                "calculation_id": item.calculation_id,
            }
            for item in publications
        ]
    )
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[PublishedPrice.product_id],
            set_={
                "price": statement.excluded.price,
                "published_at": statement.excluded.published_at,
                "calculation_id": statement.excluded.calculation_id,
            },
        )
    )
