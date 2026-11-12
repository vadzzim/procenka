"""Failure modes of the demo scenario on PostgreSQL (``plan.md``, "Сценарий показа" 2-6).

The real sources from ``build_sources`` (HTML parser and JSON API parser with
its session) talk to one fake-sites application instance; modes are switched
over its control API between runs, as in the demo. The clock is fake, so the
retry pauses do not slow the tests down.
"""

from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import httpx
import pytest
from fake_sites.app import create_app as create_fake_sites_app
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.enums import Source
from app.db.models import Calculation, Product, PublishedPrice
from app.db.seed_data import PRODUCT_A, PRODUCT_SPIKE, SeedProduct
from app.parsers.registry import build_sources
from app.pricing.models import ResultReason, ResultState
from app.pricing.normalize import normalize_article, normalize_brand
from app.worker.loop import process_next_run
from tests.fakes import FakeClock

pytestmark = pytest.mark.integration

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
                    Calculation.product_id == await _product_id(session, item),
                )
            )
        assert calculation is not None
        return calculation

    async def published(self, item: SeedProduct) -> Decimal:
        async with self.session_factory() as session:
            price = await session.scalar(
                select(PublishedPrice.price).where(
                    PublishedPrice.product_id == await _product_id(session, item)
                )
            )
        assert price is not None
        return price

    async def published_snapshot(self) -> dict[int, tuple[Decimal, int | None, Any]]:
        async with self.session_factory() as session:
            rows = await session.scalars(select(PublishedPrice))
            return {p.product_id: (p.price, p.calculation_id, p.published_at) for p in rows}


async def _product_id(session: AsyncSession, item: SeedProduct) -> int:
    product_id = await session.scalar(
        select(Product.id).where(
            Product.brand == normalize_brand(item.brand),
            Product.article == normalize_article(item.article),
        )
    )
    assert product_id is not None
    return product_id


def _sources(run: dict[str, Any]) -> list[tuple[str, str, str | None]]:
    return [(s["source"], s["status"], s["error_kind"]) for s in run["sources"]]


@pytest.fixture
async def demo(
    seeded: None, session_factory: async_sessionmaker[AsyncSession], api: httpx.AsyncClient
) -> AsyncIterator[Demo]:
    demo = Demo(session_factory, api)
    try:
        yield demo
    finally:
        await demo.control.aclose()


async def test_normal_publishes_a_at_1240_and_keeps_s_at_1990(demo: Demo) -> None:
    run = await demo.run()

    assert run["status"] == "succeeded"
    assert _sources(run) == [("html_catalog", "ok", None), ("json_api", "ok", None)]
    a_calc = await demo.calculation(run, PRODUCT_A)
    assert (a_calc.state, a_calc.recommended_price) == (ResultState.PUBLISHED, Decimal("1240.00"))
    assert {o["source"] for o in a_calc.accepted_offers} == {"html_catalog", "json_api"}
    spike_calc = await demo.calculation(run, PRODUCT_SPIKE)
    assert (spike_calc.state, spike_calc.recommended_price) == (
        ResultState.UNCHANGED,
        Decimal("1990.00"),
    )
    # The minimum of S (2 000) comes from the JSON source.
    assert spike_calc.min_competitor_price == Decimal("2000.00")
    assert await demo.published(PRODUCT_A) == Decimal("1240.00")
    assert await demo.published(PRODUCT_SPIKE) == Decimal("1990.00")
    assert demo.clock.sleeps == []


async def test_markup_changed_on_html_gives_partial_with_structure_error(demo: Demo) -> None:
    await demo.run()  # scenario step 2: A is published at 1 240
    await demo.set_mode(Source.HTML_CATALOG, "markup_changed")

    run = await demo.run()

    assert run["status"] == "partial"
    assert _sources(run) == [
        ("html_catalog", "failed", "structure"),
        ("json_api", "ok", None),
    ]
    html = run["sources"][0]
    assert html["offer_count"] == 0
    assert "нет блока результатов" in html["error_message"]
    # Prices are calculated from the JSON source alone.
    a_calc = await demo.calculation(run, PRODUCT_A)
    assert (a_calc.state, a_calc.recommended_price) == (ResultState.UNCHANGED, Decimal("1240.00"))
    assert a_calc.partial_data is True
    assert {o["source"] for o in a_calc.accepted_offers} == {"json_api"}
    spike_calc = await demo.calculation(run, PRODUCT_SPIKE)
    assert (spike_calc.state, spike_calc.recommended_price) == (
        ResultState.UNCHANGED,
        Decimal("1990.00"),
    )
    assert await demo.published(PRODUCT_A) == Decimal("1240.00")
    assert await demo.published(PRODUCT_SPIKE) == Decimal("1990.00")
    assert demo.clock.sleeps == []  # a structure error is not retried


async def test_blocked_on_html_gives_partial_with_blocked_error(demo: Demo) -> None:
    await demo.set_mode(Source.HTML_CATALOG, "blocked")

    run = await demo.run()

    assert run["status"] == "partial"
    assert _sources(run) == [
        ("html_catalog", "failed", "blocked"),
        ("json_api", "ok", None),
    ]
    assert "HTTP 403" in run["sources"][0]["error_message"]
    a_calc = await demo.calculation(run, PRODUCT_A)
    assert (a_calc.recommended_price, a_calc.partial_data) == (Decimal("1240.00"), True)
    assert await demo.published(PRODUCT_SPIKE) == Decimal("1990.00")
    assert demo.clock.sleeps == []  # 403 is not retried


async def test_price_spike_on_json_puts_s_into_quarantine(demo: Demo) -> None:
    await demo.set_mode(Source.JSON_API, "price_spike")

    run = await demo.run()

    assert run["status"] == "succeeded"
    spike_calc = await demo.calculation(run, PRODUCT_SPIKE)
    assert spike_calc.state is ResultState.QUARANTINE
    assert spike_calc.reason is ResultReason.PRICE_SPIKE
    assert spike_calc.recommended_price == Decimal("1490.00")
    assert spike_calc.min_competitor_price == Decimal("1500.00")
    assert spike_calc.change_percent_display == Decimal("-25.1")
    assert spike_calc.previous_price == Decimal("1990.00")
    assert spike_calc.partial_data is False
    assert await demo.published(PRODUCT_SPIKE) == Decimal("1990.00")

    # The JSON API of published prices does not contain the quarantined recommendation.
    response = await demo.api.get("/api/published-prices")
    assert response.status_code == 200
    prices = response.json()["prices"]
    spike = next(p for p in prices if p["product_id"] == spike_calc.product_id)
    assert (spike["price"], spike["calculation_id"]) == ("1990.00", None)
    assert "1490.00" not in {p["price"] for p in prices}
    assert spike_calc.id not in {p["calculation_id"] for p in prices}
    # A is unaffected by the spike.
    a_calc = await demo.calculation(run, PRODUCT_A)
    assert (a_calc.state, a_calc.recommended_price) == (ResultState.PUBLISHED, Decimal("1240.00"))


async def test_scenario_modes_in_sequence(demo: Demo) -> None:
    """Steps 2-5 of the demo one after another on the same sites, as the seed promises."""
    run = await demo.run()
    assert run["status"] == "succeeded"

    await demo.set_mode(Source.HTML_CATALOG, "markup_changed")
    run = await demo.run()
    assert (run["status"], run["sources"][0]["error_kind"]) == ("partial", "structure")

    await demo.set_mode(Source.HTML_CATALOG, "blocked")
    run = await demo.run()
    assert (run["status"], run["sources"][0]["error_kind"]) == ("partial", "blocked")
    assert await demo.published(PRODUCT_SPIKE) == Decimal("1990.00")

    await demo.set_mode(Source.HTML_CATALOG, "normal")
    await demo.set_mode(Source.JSON_API, "price_spike")
    run = await demo.run()
    assert run["status"] == "succeeded"
    spike_calc = await demo.calculation(run, PRODUCT_SPIKE)
    assert (spike_calc.state, spike_calc.recommended_price) == (
        ResultState.QUARANTINE,
        Decimal("1490.00"),
    )
    assert await demo.published(PRODUCT_SPIKE) == Decimal("1990.00")
    assert await demo.published(PRODUCT_A) == Decimal("1240.00")


async def test_unavailable_on_both_fails_the_run_and_changes_no_price(demo: Demo) -> None:
    await demo.run()
    before = await demo.published_snapshot()
    prices_before = (await demo.api.get("/api/published-prices")).json()
    await demo.set_mode(Source.HTML_CATALOG, "unavailable")
    await demo.set_mode(Source.JSON_API, "unavailable")

    run = await demo.run()

    assert run["status"] == "failed"
    assert run["finish_reason"] == "all_sources_failed"
    assert _sources(run) == [
        ("html_catalog", "failed", "network"),
        ("json_api", "failed", "network"),
    ]
    for source in run["sources"]:
        assert "HTTP 503" in source["error_message"]
        assert "попыток: 3" in source["error_message"]
    # Retried with pauses (both sources share the fake clock).
    assert demo.clock.sleeps
    assert set(demo.clock.sleeps) == {1.0}
    assert await demo.published_snapshot() == before
    assert (await demo.api.get("/api/published-prices")).json() == prices_before
    async with demo.session_factory() as session:
        calculations = await session.scalars(
            select(Calculation.id).where(Calculation.run_id == run["id"])
        )
        assert list(calculations) == []


async def test_json_blocked_beyond_budget_gives_partial_without_waiting(demo: Demo) -> None:
    await demo.set_mode(Source.JSON_API, "blocked")  # Retry-After 60 s > the 30 s budget

    run = await demo.run()

    assert run["status"] == "partial"
    assert _sources(run) == [
        ("html_catalog", "ok", None),
        ("json_api", "failed", "blocked"),
    ]
    assert "Retry-After 60 сек." in run["sources"][1]["error_message"]
    assert demo.clock.sleeps == []
