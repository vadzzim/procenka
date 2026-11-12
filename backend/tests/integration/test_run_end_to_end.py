"""The thin end-to-end slice on PostgreSQL.

seed -> POST /api/runs -> worker claims the run -> HTML parser against the real
fake-sites app (ASGI, no network) -> offers, calculations -> publication ->
GET /api/published-prices.
"""

from decimal import Decimal
from typing import Any

import httpx
import pytest
from fake_sites.app import create_app as create_fake_sites_app
from fake_sites.catalog import LISTINGS, Site
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.enums import (
    RunFinishReason,
    RunStatus,
    RunTrigger,
    Source,
    SourceErrorKind,
)
from app.db.models import Calculation, Offer, Product, PublishedPrice, Run, SourceResult
from app.db.runs import claim_next_run, enqueue_run
from app.db.seed_data import (
    PRODUCT_A,
    PRODUCT_NO_OFFERS,
    PRODUCT_SPIKE,
    PRODUCT_UNPUBLISHED,
    SEED_PRODUCTS,
    SeedProduct,
)
from app.parsers.base import OfferSource
from app.parsers.html_catalog import HtmlCatalogSource
from app.parsers.json_api import JsonApiSource
from app.pricing.models import ResultReason, ResultState
from app.pricing.normalize import normalize_article, normalize_brand
from app.worker.execute import RunNotRunningError, execute_run
from app.worker.loop import RunCrashedError, process_next_run
from tests.fakes import (
    OPEN_TRANSACTIONS_SQL,
    FailingSource,
    FakeClock,
    TransactionCheckingTransport,
)
from tests.unit.db.test_seed_consistency import OTHER_PLAN

pytestmark = pytest.mark.integration

SETTINGS = Settings()
GLOBAL = SETTINGS.global_settings()
HTML_LISTINGS = sum(item.site is Site.HTML for item in LISTINGS)
JSON_LISTINGS = sum(item.site is Site.JSON for item in LISTINGS)


def _html_source(transport: httpx.AsyncBaseTransport | None = None) -> HtmlCatalogSource:
    return HtmlCatalogSource(
        "http://fake-sites.test",
        transport=transport or httpx.ASGITransport(app=create_fake_sites_app()),
        clock=FakeClock(),
    )


def _json_source(transport: httpx.AsyncBaseTransport | None = None) -> JsonApiSource:
    return JsonApiSource(
        "http://fake-sites.test",
        transport=transport or httpx.ASGITransport(app=create_fake_sites_app()),
        clock=FakeClock(),
    )


def _failing_transport(status: int) -> httpx.MockTransport:
    return httpx.MockTransport(lambda request: httpx.Response(status))


async def _product_id(session: AsyncSession, item: SeedProduct) -> int:
    product_id = await session.scalar(
        select(Product.id).where(
            Product.brand == normalize_brand(item.brand),
            Product.article == normalize_article(item.article),
        )
    )
    assert product_id is not None
    return product_id


async def _calculation(session: AsyncSession, run_id: int, item: SeedProduct) -> Calculation:
    product_id = await _product_id(session, item)
    calculation = await session.scalar(
        select(Calculation).where(
            Calculation.run_id == run_id, Calculation.product_id == product_id
        )
    )
    assert calculation is not None
    return calculation


async def _published(session: AsyncSession) -> dict[int, tuple[Decimal, int | None, Any]]:
    rows = await session.execute(select(PublishedPrice))
    return {p.product_id: (p.price, p.calculation_id, p.published_at) for p in rows.scalars()}


async def _start_run(api: httpx.AsyncClient) -> int:
    response = await api.post("/api/runs")
    assert response.status_code == 201, response.text
    run_id: int = response.json()["id"]
    return run_id


@pytest.mark.usefixtures("seeded")
async def test_manual_run_publishes_product_a_at_1240(
    session_factory: async_sessionmaker[AsyncSession],
    monitor_engine: AsyncEngine,
    api: httpx.AsyncClient,
) -> None:
    checking = TransactionCheckingTransport(
        httpx.ASGITransport(app=create_fake_sites_app()), monitor_engine
    )
    sources: list[OfferSource] = [_html_source(checking), _json_source(checking)]

    run_id = await _start_run(api)
    outcome = await process_next_run(session_factory, sources, SETTINGS)

    assert outcome is not None
    assert outcome.run_id == run_id
    assert outcome.status is RunStatus.SUCCEEDED
    assert outcome.calculations == len(SEED_PRODUCTS)

    # No database transaction was open during any HTTP request: one per article
    # and source, plus the JSON storefront page that opens the session.
    articles = {normalize_article(item.article) for item in SEED_PRODUCTS}
    assert len(checking.open_transactions) == 2 * len(articles) + 1
    assert set(checking.open_transactions) == {0}

    # The run and its sources.
    run = (await api.get(f"/api/runs/{run_id}")).json()
    assert run["status"] == "succeeded"
    assert run["finished_at"] is not None
    assert run["finish_reason"] is None
    sources_out = [
        (s["source"], s["status"], s["error_kind"], s["offer_count"]) for s in run["sources"]
    ]
    assert sources_out == [
        ("html_catalog", "ok", None, HTML_LISTINGS),
        ("json_api", "ok", None, JSON_LISTINGS),
    ]

    async with session_factory() as session:
        a_id = await _product_id(session, PRODUCT_A)
        spike_id = await _product_id(session, PRODUCT_SPIKE)
        unpublished_id = await _product_id(session, PRODUCT_UNPUBLISHED)
        published = await _published(session)

        # Product A: 1 250 counts; other brand and suspiciously low price are discarded.
        a_calc = await _calculation(session, run_id, PRODUCT_A)
        assert a_calc.state is ResultState.PUBLISHED
        assert a_calc.recommended_price == Decimal("1240.00")
        assert a_calc.previous_price == Decimal("1260.00")
        assert a_calc.lower_bound == Decimal("1200.00")
        assert a_calc.min_competitor_price == Decimal("1250.00")
        assert a_calc.partial_data is False
        assert a_calc.change_percent_display == Decimal("-1.6")
        assert [(o["source"], o["brand"], o["price"]) for o in a_calc.accepted_offers] == [
            ("html_catalog", "BOSCH", "1250.00"),
            ("json_api", "Bosch", "1250.00"),
        ]
        assert a_calc.rejected_offers == [
            {
                "source": "html_catalog",
                "brand": "MANN",
                "article": "0986452041",
                "normalized_brand": "MANN-FILTER",
                "normalized_article": "0986452041",
                "price": "1150.00",
                "stock": 3,
                "delivery_days": 4,
                "reason": "other_brand",
            },
            {
                "source": "html_catalog",
                "brand": "Bosch",
                "article": "0 986 452 041",
                "normalized_brand": "Bosch",
                "normalized_article": "0986452041",
                "price": "1.00",
                "stock": 8,
                "delivery_days": 2,
                "reason": "suspiciously_low_price",
            },
        ]
        assert published[a_id][:2] == (Decimal("1240.00"), a_calc.id)

        # S stays at 1 990 "unchanged"; the seed price row is untouched.
        spike_calc = await _calculation(session, run_id, PRODUCT_SPIKE)
        assert spike_calc.state is ResultState.UNCHANGED
        assert spike_calc.recommended_price == Decimal("1990.00")
        assert published[spike_id][:2] == (Decimal("1990.00"), None)

        # No published price -> quarantine, and it is not published.
        unpublished_calc = await _calculation(session, run_id, PRODUCT_UNPUBLISHED)
        assert unpublished_calc.state is ResultState.QUARANTINE
        assert unpublished_calc.reason is ResultReason.NO_PREVIOUS_PRICE
        assert unpublished_calc.recommended_price == Decimal("1090.00")
        assert unpublished_id not in published

        no_offers_calc = await _calculation(session, run_id, PRODUCT_NO_OFFERS)
        assert no_offers_calc.state is ResultState.RETAINED
        assert no_offers_calc.reason is ResultReason.NO_ELIGIBLE_OFFERS

        # Every other product follows the plan; "published" ones carry this run's calculation.
        for item in SEED_PRODUCTS:
            article = normalize_article(item.article)
            if article not in OTHER_PLAN:
                continue
            _, state, recommended = OTHER_PLAN[article]
            calc = await _calculation(session, run_id, item)
            assert (calc.state, calc.recommended_price) == (state, Decimal(recommended)), article
            product_id = await _product_id(session, item)
            if state is ResultState.PUBLISHED:
                assert published[product_id][:2] == (Decimal(recommended), calc.id), article
            elif item.published_price is not None:
                assert published[product_id][:2] == (item.published_price, None), article

        # Offers: one batch with raw and normalized values; product_id only on an exact match.
        offers = (await session.scalars(select(Offer).where(Offer.run_id == run_id))).all()
        assert len(offers) == HTML_LISTINGS + JSON_LISTINGS
        mann = next(o for o in offers if o.brand_raw == "MANN" and o.article == "0986452041")
        assert (mann.brand, mann.product_id) == ("MANN-FILTER", None)
        bosch = next(o for o in offers if o.brand_raw == "BOSCH" and o.article == "0986452041")
        assert (bosch.article_raw, bosch.brand) == ("0 986-452 041", "Bosch")
        assert bosch.product_id == a_id

    # The JSON API: published prices only, money as strings.
    response = await api.get("/api/published-prices")
    assert response.status_code == 200
    prices = {p["product_id"]: p for p in response.json()["prices"]}
    assert prices[a_id]["price"] == "1240.00"
    assert prices[a_id]["calculation_id"] == a_calc.id
    assert (prices[a_id]["brand"], prices[a_id]["article"]) == ("Bosch", "0986452041")
    assert prices[spike_id]["price"] == "1990.00"
    assert prices[spike_id]["calculation_id"] is None
    assert unpublished_id not in prices
    # Every price from this run comes from a calculation in state "published".
    async with session_factory() as session:
        published_ids = set(
            await session.scalars(
                select(Calculation.id).where(
                    Calculation.run_id == run_id, Calculation.state == ResultState.PUBLISHED
                )
            )
        )
    assert {p["calculation_id"] for p in prices.values()} - {None} == published_ids
    assert all(isinstance(p["price"], str) for p in prices.values())


@pytest.mark.usefixtures("seeded")
async def test_huge_stock_fails_the_source_not_the_run(
    session_factory: async_sessionmaker[AsyncSession], api: httpx.AsyncClient
) -> None:
    page = (
        '<section id="offers"><table><tr class="offer"><td class="brand">BOSCH</td>'
        '<td class="article">0986452041</td><td class="price">1 250,00 ₽</td>'
        '<td class="stock">2147483648 шт.</td><td class="delivery">1 дн.</td>'
        "</tr></table></section>"
    )
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=page))
    run_id = await _start_run(api)

    outcome = await process_next_run(
        session_factory, [_html_source(transport), _json_source()], SETTINGS
    )

    assert outcome is not None
    assert outcome.status is RunStatus.PARTIAL
    run = (await api.get(f"/api/runs/{run_id}")).json()
    assert [(s["status"], s["error_kind"]) for s in run["sources"]] == [
        ("failed", "structure"),
        ("ok", None),
    ]


@pytest.mark.usefixtures("seeded")
async def test_html_source_alone_publishes_product_a_at_1240(
    session_factory: async_sessionmaker[AsyncSession], api: httpx.AsyncClient
) -> None:
    run_id = await _start_run(api)
    outcome = await process_next_run(session_factory, [_html_source()], SETTINGS)

    assert outcome is not None
    assert outcome.status is RunStatus.SUCCEEDED
    async with session_factory() as session:
        a_calc = await _calculation(session, run_id, PRODUCT_A)
        assert (a_calc.state, a_calc.recommended_price) == (
            ResultState.PUBLISHED,
            Decimal("1240.00"),
        )
        assert {o["source"] for o in a_calc.accepted_offers} == {"html_catalog"}
    prices = (await api.get("/api/published-prices")).json()["prices"]
    assert {"price": "1240.00", "calculation_id": a_calc.id}.items() <= next(
        p for p in prices if p["product_id"] == a_calc.product_id
    ).items()


@pytest.mark.usefixtures("seeded")
async def test_one_source_failed_gives_partial_run(
    session_factory: async_sessionmaker[AsyncSession], api: httpx.AsyncClient
) -> None:
    run_id = await _start_run(api)
    sources: list[OfferSource] = [
        _html_source(_failing_transport(503)),
        _json_source(),
    ]
    outcome = await process_next_run(session_factory, sources, SETTINGS)

    assert outcome is not None
    assert outcome.status is RunStatus.PARTIAL
    run = (await api.get(f"/api/runs/{run_id}")).json()
    assert run["status"] == "partial"
    html, json_ = run["sources"]
    assert (html["status"], html["error_kind"], html["offer_count"]) == ("failed", "network", 0)
    assert "503" in html["error_message"]
    assert (json_["status"], json_["error_kind"]) == ("ok", None)

    async with session_factory() as session:
        a_calc = await _calculation(session, run_id, PRODUCT_A)
        assert a_calc.partial_data is True
        assert a_calc.recommended_price == Decimal("1240.00")
        assert {o["source"] for o in a_calc.accepted_offers} == {"json_api"}
        assert a_calc.rejected_offers == []
        offer_sources = set(
            await session.scalars(select(Offer.source).where(Offer.run_id == run_id))
        )
        assert offer_sources == {Source.JSON_API}


@pytest.mark.usefixtures("seeded")
async def test_all_sources_failed_changes_no_published_price(
    session_factory: async_sessionmaker[AsyncSession], api: httpx.AsyncClient
) -> None:
    async with session_factory() as session:
        before = await _published(session)
    prices_before = (await api.get("/api/published-prices")).json()

    run_id = await _start_run(api)
    sources: list[OfferSource] = [
        _html_source(_failing_transport(403)),
        FailingSource(Source.JSON_API, SourceErrorKind.NETWORK, "таймаут"),
    ]
    outcome = await process_next_run(session_factory, sources, SETTINGS)

    assert outcome is not None
    assert outcome.status is RunStatus.FAILED
    run = (await api.get(f"/api/runs/{run_id}")).json()
    assert run["status"] == "failed"
    assert run["finish_reason"] == RunFinishReason.ALL_SOURCES_FAILED
    assert [(s["source"], s["status"], s["error_kind"]) for s in run["sources"]] == [
        ("html_catalog", "failed", "blocked"),
        ("json_api", "failed", "network"),
    ]
    async with session_factory() as session:
        assert await _published(session) == before
        assert await session.scalar(select(func.count()).select_from(Calculation)) == 0
        assert await session.scalar(select(func.count()).select_from(Offer)) == 0
    assert (await api.get("/api/published-prices")).json() == prices_before


@pytest.mark.usefixtures("seeded")
async def test_structure_error_never_becomes_a_price(
    session_factory: async_sessionmaker[AsyncSession], api: httpx.AsyncClient
) -> None:
    broken_page = (
        '<section id="offers"><table><tr class="offer"><td class="brand">BOSCH</td>'
        '<td class="article">0986452041</td><td class="price">цена по запросу</td>'
        '<td class="stock">5 шт.</td><td class="delivery">1 дн.</td></tr></table></section>'
    )
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=broken_page))
    run_id = await _start_run(api)

    outcome = await process_next_run(session_factory, [_html_source(transport)], SETTINGS)

    assert outcome is not None
    assert outcome.status is RunStatus.FAILED
    run = (await api.get(f"/api/runs/{run_id}")).json()
    assert run["sources"][0]["error_kind"] == "structure"
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Offer)) == 0


async def test_transaction_check_detects_an_open_transaction(
    session_factory: async_sessionmaker[AsyncSession], monitor_engine: AsyncEngine
) -> None:
    """The check used above is not vacuous: it sees a transaction held during a request."""
    checking = TransactionCheckingTransport(
        httpx.ASGITransport(app=create_fake_sites_app()), monitor_engine
    )
    async with session_factory() as session, session.begin():
        await session.execute(select(1))
        await _html_source(checking).fetch_offers(["0986452041"])
    assert checking.open_transactions == [1]

    async with monitor_engine.connect() as conn:
        assert await conn.scalar(OPEN_TRANSACTIONS_SQL) == 0

    # Concurrent monitoring probes do not count each other.
    probes = TransactionCheckingTransport(
        httpx.ASGITransport(app=create_fake_sites_app()), monitor_engine
    )
    await _html_source(probes).fetch_offers([f"A{i}" for i in range(12)])
    assert set(probes.open_transactions) == {0}


@pytest.mark.usefixtures("seeded")
async def test_run_that_is_no_longer_running_publishes_nothing(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    enqueued = await enqueue_run(session_factory, RunTrigger.MANUAL)
    assert await claim_next_run(session_factory) == enqueued.id
    async with session_factory() as session:
        before = await _published(session)

    # Something else (step 6: the interrupted-run recovery) finished the run meanwhile.
    async with session_factory() as session, session.begin():
        await session.execute(
            update(Run)
            .where(Run.id == enqueued.id)
            .values(status=RunStatus.FAILED, finished_at=func.now(), finish_reason="interrupted")
        )

    with pytest.raises(RunNotRunningError):
        await execute_run(session_factory, enqueued.id, [_html_source()], global_settings=GLOBAL)
    async with session_factory() as session:
        assert await _published(session) == before
        run = await session.get(Run, enqueued.id)
        assert run is not None
        assert (run.status, run.finish_reason) == (RunStatus.FAILED, "interrupted")


@pytest.mark.usefixtures("seeded")
async def test_unexpected_error_fails_the_run_as_internal_error(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    class BrokenSource:
        source = Source.HTML_CATALOG

        async def fetch_offers(self, articles: object) -> list[Any]:
            raise ZeroDivisionError

    enqueued = await enqueue_run(session_factory, RunTrigger.MANUAL)
    with pytest.raises(RunCrashedError) as raised:
        await process_next_run(session_factory, [BrokenSource()], SETTINGS)
    assert isinstance(raised.value.__cause__, ZeroDivisionError)

    async with session_factory() as session:
        run = await session.get(Run, enqueued.id)
        assert run is not None
        assert (run.status, run.finish_reason) == (RunStatus.FAILED, "internal_error")
        assert await session.scalar(select(func.count()).select_from(SourceResult)) == 0
