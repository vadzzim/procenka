"""Quarantine and publication on PostgreSQL (``plan.md``, "Правила расчёта" 8-9,
"Прогоны и устойчивость", "UI и обмен без Excel").

Quarantine is a state of a calculation, not an error of the run; only
calculations in state ``published`` reach ``published_prices``. Real parsers
against fake-sites (``tests.integration.demo``).
"""

from collections.abc import Sequence
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.db.enums import Source, SourceStatus
from app.db.models import Calculation, Offer, SourceResult
from app.db.seed_data import PRODUCT_A, PRODUCT_NO_OFFERS, PRODUCT_SPIKE, PRODUCT_UNPUBLISHED
from app.parsers.base import OfferSource, ParsedOffer
from app.pricing.models import ResultReason, ResultState
from app.pricing.normalize import normalize_article
from app.worker.loop import process_next_run
from tests.integration.demo import SETTINGS, Demo, product_id, source_states

pytestmark = pytest.mark.integration


async def test_price_spike_and_missing_previous_price_are_quarantined_not_published(
    demo: Demo,
) -> None:
    await demo.set_mode(Source.JSON_API, "price_spike")
    published = await demo.published_rows()
    snapshot = await demo.published_snapshot()

    run = await demo.run()

    # Quarantine is the state of a product, not an error of the run.
    assert run["status"] == "succeeded"
    spike = await demo.calculation(run, PRODUCT_SPIKE)
    assert (spike.state, spike.reason, spike.recommended_price) == (
        ResultState.QUARANTINE,
        ResultReason.PRICE_SPIKE,
        Decimal("1490.00"),
    )
    febi = await demo.calculation(run, PRODUCT_UNPUBLISHED)
    assert (febi.state, febi.reason, febi.previous_price) == (
        ResultState.QUARANTINE,
        ResultReason.NO_PREVIOUS_PRICE,
        None,
    )
    assert febi.recommended_price == Decimal("1090.00")

    # Their rows in published_prices are exactly as before (Febi has none).
    after = await demo.published_snapshot()
    assert after[spike.product_id] == snapshot[spike.product_id]
    assert febi.product_id not in after
    # Every row changed by the run comes from a calculation in state "published".
    async with demo.session_factory() as session:
        published_calcs = set(
            await session.scalars(
                select(Calculation.id).where(
                    Calculation.run_id == run["id"],
                    Calculation.state == ResultState.PUBLISHED,
                )
            )
        )
    changed = {pid: row for pid, row in after.items() if row != snapshot.get(pid)}
    assert {row[1] for row in changed.values()} == published_calcs
    assert published != await demo.published_rows()  # A and others were published

    # The JSON API of published prices carries none of the quarantined recommendations.
    response = await demo.api.get("/api/published-prices")
    prices = {p["product_id"]: p for p in response.json()["prices"]}
    assert (prices[spike.product_id]["price"], prices[spike.product_id]["calculation_id"]) == (
        "1990.00",
        None,
    )
    assert febi.product_id not in prices
    assert {spike.id, febi.id}.isdisjoint(p["calculation_id"] for p in prices.values())

    # The next run quarantines them again; still nothing of them is published.
    again = await demo.run()
    assert (await demo.calculation(again, PRODUCT_UNPUBLISHED)).state is ResultState.QUARANTINE
    assert (await demo.calculation(again, PRODUCT_SPIKE)).state is ResultState.QUARANTINE
    assert (await demo.published_snapshot())[spike.product_id] == snapshot[spike.product_id]
    assert febi.product_id not in await demo.published_snapshot()


async def test_unchanged_does_not_rewrite_the_published_price(demo: Demo) -> None:
    first = await demo.run()
    a_first = await demo.calculation(first, PRODUCT_A)
    assert a_first.state is ResultState.PUBLISHED
    published = await demo.published_rows()

    second = await demo.run()

    a_second = await demo.calculation(second, PRODUCT_A)
    assert (a_second.state, a_second.recommended_price) == (
        ResultState.UNCHANGED,
        Decimal("1240.00"),
    )
    async with demo.session_factory() as session:
        states = set(
            await session.scalars(
                select(Calculation.state).where(Calculation.run_id == second["id"])
            )
        )
    assert ResultState.PUBLISHED not in states
    # Price, calculation id and published_at stay those of the first run, byte for byte.
    assert await demo.published_rows() == published
    snapshot = await demo.published_snapshot()
    assert snapshot[a_first.product_id][1] == a_first.id


async def test_no_eligible_offers_differs_from_a_technical_failure(demo: Demo) -> None:
    """Hengst E340H D247 has no offers on either site."""
    # 1. Both sources collected fine: the product simply has no eligible offers.
    collected = await demo.run()
    assert collected["status"] == "succeeded"
    assert source_states(collected) == [("html_catalog", "ok", None), ("json_api", "ok", None)]
    hengst = await demo.calculation(collected, PRODUCT_NO_OFFERS)
    assert (hengst.state, hengst.reason, hengst.partial_data) == (
        ResultState.RETAINED,
        ResultReason.NO_ELIGIBLE_OFFERS,
        False,
    )
    assert hengst.accepted_offers == hengst.rejected_offers == []
    assert hengst.recommended_price is None
    assert await demo.published(PRODUCT_NO_OFFERS) == Decimal("690.00")

    # 2. One source failed technically: the source result says so, the run is partial
    # and every calculation carries the partial-data flag.
    await demo.set_mode(Source.HTML_CATALOG, "unavailable")
    partial = await demo.run()
    assert partial["status"] == "partial"
    assert source_states(partial) == [
        ("html_catalog", "failed", "network"),
        ("json_api", "ok", None),
    ]
    hengst_partial = await demo.calculation(partial, PRODUCT_NO_OFFERS)
    assert (hengst_partial.state, hengst_partial.partial_data) == (ResultState.RETAINED, True)

    # 3. Both failed: no calculation at all, the run is failed.
    await demo.set_mode(Source.JSON_API, "unavailable")
    failed = await demo.run()
    assert (failed["status"], failed["finish_reason"]) == ("failed", "all_sources_failed")
    async with demo.session_factory() as session:
        rows = list(
            await session.execute(
                select(SourceResult.run_id, SourceResult.source, SourceResult.status)
                .where(SourceResult.run_id.in_([collected["id"], failed["id"]]))
                .order_by(SourceResult.run_id, SourceResult.source)
            )
        )
        assert [tuple(r) for r in rows] == [
            (collected["id"], Source.HTML_CATALOG, SourceStatus.OK),
            (collected["id"], Source.JSON_API, SourceStatus.OK),
            (failed["id"], Source.HTML_CATALOG, SourceStatus.FAILED),
            (failed["id"], Source.JSON_API, SourceStatus.FAILED),
        ]
        count = await session.scalar(
            select(func.count()).select_from(Calculation).where(Calculation.run_id == failed["id"])
        )
        assert count == 0
    assert await demo.published(PRODUCT_NO_OFFERS) == Decimal("690.00")


class _WithoutArticles:
    """The real source, but the site no longer lists some articles."""

    def __init__(self, inner: OfferSource, articles: set[str]) -> None:
        self.source = inner.source
        self._inner = inner
        self._articles = articles

    async def fetch_offers(self, articles: Sequence[str]) -> list[ParsedOffer]:
        return await self._inner.fetch_offers([a for a in articles if a not in self._articles])


async def test_offers_of_earlier_runs_do_not_enter_a_new_calculation(demo: Demo) -> None:
    first = await demo.run()
    a_first = await demo.calculation(first, PRODUCT_A)
    assert (a_first.state, a_first.recommended_price) == (ResultState.PUBLISHED, Decimal("1240.00"))
    published = await demo.published_rows()

    gone = {normalize_article(PRODUCT_A.article)}
    sources: list[OfferSource] = [_WithoutArticles(s, gone) for s in demo.sources]
    second_id = (await demo.api.post("/api/runs")).json()["id"]
    outcome = await process_next_run(demo.session_factory, sources, SETTINGS)
    assert outcome is not None
    assert outcome.run_id == second_id

    second = (await demo.api.get(f"/api/runs/{second_id}")).json()
    assert second["status"] == "succeeded"
    a_second = await demo.calculation(second, PRODUCT_A)
    assert (a_second.state, a_second.reason) == (
        ResultState.RETAINED,
        ResultReason.NO_ELIGIBLE_OFFERS,
    )
    assert a_second.accepted_offers == a_second.rejected_offers == []
    assert a_second.previous_price == Decimal("1240.00")
    # The offers of run 1 are still there as history, and the price stays.
    async with demo.session_factory() as session:
        a_id = await product_id(session, PRODUCT_A)
        by_run = dict(
            list(
                await session.execute(
                    select(Offer.run_id, func.count())
                    .where(Offer.product_id == a_id)
                    .group_by(Offer.run_id)
                )
            )
        )
    assert by_run == {first["id"]: 3}
    assert await demo.published_rows() == published
