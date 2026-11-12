"""The demo setup shared by integration tests: the API, one fake-sites instance
and the worker's real sources (``build_sources``) talking to it over ASGI.

Modes are switched over the fake-sites control API between runs, as in the
demo. The clock is fake, so retry pauses do not slow the tests down.
"""

from decimal import Decimal
from typing import Any

import httpx
from fake_sites.app import create_app as create_fake_sites_app
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.enums import Source
from app.db.models import Calculation, Product, PublishedPrice
from app.db.seed_data import SeedProduct
from app.parsers.registry import build_sources
from app.pricing.normalize import normalize_article, normalize_brand
from app.worker.loop import process_next_run
from tests.fakes import FakeClock

SETTINGS = Settings(enabled_sources=(Source.HTML_CATALOG, Source.JSON_API))
BASE_URL = "http://fake-sites.test"


class Demo:
    """The API, one fake-sites instance and the worker's real sources."""

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], api: httpx.AsyncClient
    ) -> None:
        self.session_factory = session_factory
        self.api = api
        self.clock = FakeClock()
        transport = httpx.ASGITransport(app=create_fake_sites_app())
        self.control = httpx.AsyncClient(transport=transport, base_url=BASE_URL)
        settings = SETTINGS.model_copy(update={"fake_sites_url": BASE_URL})
        self.sources = build_sources(settings, transport=transport, clock=self.clock)

    async def set_mode(self, source: Source, mode: str) -> None:
        response = await self.control.put(f"/control/modes/{source.value}", json={"mode": mode})
        assert response.status_code == 200, response.text

    async def run(self) -> dict[str, Any]:
        """POST /api/runs, let the worker execute it, return GET /api/runs/{id}."""
        response = await self.api.post("/api/runs")
        assert response.status_code == 201, response.text
        run_id = response.json()["id"]
        outcome = await process_next_run(self.session_factory, self.sources, SETTINGS)
        assert outcome is not None
        assert outcome.run_id == run_id
        run: dict[str, Any] = (await self.api.get(f"/api/runs/{run_id}")).json()
        assert run["status"] == outcome.status.value
        return run

    async def calculation(self, run: dict[str, Any], item: SeedProduct) -> Calculation:
        async with self.session_factory() as session:
            calculation = await session.scalar(
                select(Calculation).where(
                    Calculation.run_id == run["id"],
                    Calculation.product_id == await product_id(session, item),
                )
            )
        assert calculation is not None
        return calculation

    async def published(self, item: SeedProduct) -> Decimal:
        async with self.session_factory() as session:
            price = await session.scalar(
                select(PublishedPrice.price).where(
                    PublishedPrice.product_id == await product_id(session, item)
                )
            )
        assert price is not None
        return price

    async def published_snapshot(self) -> dict[int, tuple[Decimal, int | None, Any]]:
        async with self.session_factory() as session:
            rows = await session.scalars(select(PublishedPrice))
            return {p.product_id: (p.price, p.calculation_id, p.published_at) for p in rows}

    async def published_rows(self) -> list[str]:
        """``published_prices`` row by row as PostgreSQL prints it, for byte-exact comparison."""
        async with self.session_factory() as session:
            rows = await session.scalars(
                text("SELECT p::text FROM published_prices p ORDER BY p.product_id")
            )
            return list(rows)


async def product_id(session: AsyncSession, item: SeedProduct) -> int:
    product_id = await session.scalar(
        select(Product.id).where(
            Product.brand == normalize_brand(item.brand),
            Product.article == normalize_article(item.article),
        )
    )
    assert product_id is not None
    return product_id


def source_states(run: dict[str, Any]) -> list[tuple[str, str, str | None]]:
    return [(s["source"], s["status"], s["error_kind"]) for s in run["sources"]]
