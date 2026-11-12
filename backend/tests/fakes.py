"""Test doubles of sources and transports."""

from collections.abc import Sequence

import httpx
from fake_sites.catalog import Site, search
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.enums import Source, SourceErrorKind
from app.parsers.base import ParsedOffer, SourceError


class CatalogJsonSource:
    """Stands in for the JSON API source (step 5): the JSON listings of fake-sites."""

    source = Source.JSON_API

    async def fetch_offers(self, articles: Sequence[str]) -> list[ParsedOffer]:
        return [
            ParsedOffer(item.brand, item.article, item.price, item.stock, item.delivery_days)
            for article in articles
            for item in search(Site.JSON, article)
        ]


class FailingSource:
    def __init__(self, source: Source, kind: SourceErrorKind, message: str = "сбой") -> None:
        self.source = source
        self._error = SourceError(kind, message)

    async def fetch_offers(self, articles: Sequence[str]) -> list[ParsedOffer]:
        raise self._error


# application_name of the monitoring connections, so they never count themselves.
MONITOR_APPLICATION_NAME = "procenka-transaction-monitor"

# Client sessions of the current database, other than monitors, inside a transaction.
OPEN_TRANSACTIONS_SQL = text(
    f"""
    SELECT count(*) FROM pg_stat_activity
    WHERE datname = current_database()
      AND backend_type = 'client backend'
      AND application_name <> '{MONITOR_APPLICATION_NAME}'
      AND xact_start IS NOT NULL
    """
)


class TransactionCheckingTransport(httpx.AsyncBaseTransport):
    """Before every HTTP request, records how many other database sessions are in a
    transaction (from ``pg_stat_activity``, i.e. what PostgreSQL itself sees)."""

    def __init__(self, inner: httpx.AsyncBaseTransport, monitor: AsyncEngine) -> None:
        """``monitor`` must connect with ``MONITOR_APPLICATION_NAME``."""
        self._inner = inner
        self._monitor = monitor
        self.open_transactions: list[int] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        async with self._monitor.connect() as conn:
            count = await conn.scalar(OPEN_TRANSACTIONS_SQL)
        self.open_transactions.append(int(count or 0))
        return await self._inner.handle_async_request(request)
