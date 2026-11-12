"""Confirmation of a quarantined recommendation on PostgreSQL (``plan.md``, extension 1).

``POST /api/calculations/{id}/confirm`` publishes a quarantined recommendation
of the latest finished run if it is not too old and its purchase, rules,
product settings and published price did not change since the calculation;
otherwise 409 with a code and nothing is written. Real parsers against
fake-sites (``tests.integration.demo``).
"""

import asyncio
from collections.abc import Sequence
from datetime import timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.confirm import (
    ConfirmRefusal,
    ConfirmRefusedError,
    confirm_calculation,
    database_now,
)
from app.db.enums import RunStatus, Source
from app.db.models import Calculation, Product, PublishedPrice
from app.db.runs import claim_next_run
from app.db.seed_data import PRODUCT_A, PRODUCT_SPIKE, PRODUCT_UNPUBLISHED, SeedProduct
from app.pricing.models import ResultReason, ResultState
from app.worker import execute
from tests.integration.demo import Demo, product_id

pytestmark = pytest.mark.integration

MAX_AGE = timedelta(minutes=60)  # the default QUARANTINE_CONFIRM_MAX_AGE_MINUTES


async def _spike_run(demo: Demo) -> dict[str, Any]:
    await demo.set_mode(Source.JSON_API, "price_spike")
    run = await demo.run()
    assert run["status"] == "succeeded"
    return run


async def _confirm(demo: Demo, calculation_id: int) -> httpx.Response:
    return await demo.api.post(f"/api/calculations/{calculation_id}/confirm")


async def _assert_refused(demo: Demo, calculation_id: int, code: ConfirmRefusal) -> None:
    """409 with the code and its Russian reason; nothing is published or marked."""
    before = await demo.published_rows()
    response = await _confirm(demo, calculation_id)
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["code"] == code.value
    assert "требуется новый расчёт" in body["detail"] or code in {
        ConfirmRefusal.NOT_QUARANTINE,
        ConfirmRefusal.ALREADY_CONFIRMED,
    }
    assert await demo.published_rows() == before
    async with demo.session_factory() as session:
        confirmed_at = await session.scalar(
            select(Calculation.confirmed_at).where(Calculation.id == calculation_id)
        )
    if code is not ConfirmRefusal.ALREADY_CONFIRMED:
        assert confirmed_at is None


async def _product_row(demo: Demo, item: SeedProduct) -> dict[str, Any]:
    async with demo.session_factory() as session:
        pid = await product_id(session, item)
    rows: list[dict[str, Any]] = (await demo.api.get("/api/products")).json()["products"]
    [row] = [r for r in rows if r["product"]["id"] == pid]
    return row


async def _published_price(demo: Demo, item: SeedProduct) -> dict[str, Any] | None:
    async with demo.session_factory() as session:
        pid = await product_id(session, item)
    prices = (await demo.api.get("/api/published-prices")).json()["prices"]
    found = [p for p in prices if p["product_id"] == pid]
    return found[0] if found else None


async def test_confirming_the_quarantined_spike_publishes_it(demo: Demo) -> None:
    run = await _spike_run(demo)
    spike = await demo.calculation(run, PRODUCT_SPIKE)
    assert (spike.state, spike.recommended_price) == (ResultState.QUARANTINE, Decimal("1490.00"))

    # Before: the screen offers the button.
    row = await _product_row(demo, PRODUCT_SPIKE)
    assert row["latest_calculation"]["confirmation"] == {
        "confirmed": False,
        "confirmed_at": None,
        "can_confirm": True,
        "refusal": None,
        "refusal_label": None,
    }
    assert row["published"]["price"] == "1990.00"

    response = await _confirm(demo, spike.id)

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["product_id"], body["price"], body["calculation_id"]) == (
        spike.product_id,
        "1490.00",
        spike.id,
    )
    # The JSON API of published prices now carries the confirmed recommendation.
    published = await _published_price(demo, PRODUCT_SPIKE)
    assert published == body
    # The calculation stays in quarantine and is marked confirmed at the same moment.
    async with demo.session_factory() as session:
        stored = await session.get(Calculation, spike.id)
        assert stored is not None
        assert stored.state is ResultState.QUARANTINE
        assert stored.reason is ResultReason.PRICE_SPIKE
        assert stored.confirmed_at is not None
        published_row = await session.get(PublishedPrice, spike.product_id)
        assert published_row is not None
        assert published_row.published_at == stored.confirmed_at
        assert published_row.calculation_id == spike.id

    row = await _product_row(demo, PRODUCT_SPIKE)
    confirmation = row["latest_calculation"]["confirmation"]
    assert confirmation["confirmed"] is True
    assert confirmation["confirmed_at"] is not None
    assert (confirmation["can_confirm"], confirmation["refusal"]) == (False, "already_confirmed")
    assert row["latest_calculation"]["state"] == "quarantine"
    assert (row["published"]["price"], row["published"]["origin"]) == ("1490.00", "confirmation")
    assert row["published"]["calculation_id"] == spike.id

    details = (await demo.api.get(f"/api/calculations/{spike.id}")).json()
    assert details["confirmation"] == confirmation


async def test_confirming_twice_is_refused_and_publishes_once(demo: Demo) -> None:
    run = await _spike_run(demo)
    spike = await demo.calculation(run, PRODUCT_SPIKE)
    assert (await _confirm(demo, spike.id)).status_code == 200
    async with demo.session_factory() as session:
        first = await session.scalar(
            select(Calculation.confirmed_at).where(Calculation.id == spike.id)
        )

    await _assert_refused(demo, spike.id, ConfirmRefusal.ALREADY_CONFIRMED)

    async with demo.session_factory() as session:
        again = await session.scalar(
            select(Calculation.confirmed_at).where(Calculation.id == spike.id)
        )
    assert again == first


async def test_confirming_a_first_price_inserts_it(demo: Demo) -> None:
    """Febi has no published price: the confirmation inserts the row."""
    run = await _spike_run(demo)
    febi = await demo.calculation(run, PRODUCT_UNPUBLISHED)
    assert (febi.state, febi.reason, febi.previous_price) == (
        ResultState.QUARANTINE,
        ResultReason.NO_PREVIOUS_PRICE,
        None,
    )
    assert await _published_price(demo, PRODUCT_UNPUBLISHED) is None

    response = await _confirm(demo, febi.id)

    assert response.status_code == 200, response.text
    published = await _published_price(demo, PRODUCT_UNPUBLISHED)
    assert published is not None
    assert (published["price"], published["calculation_id"]) == ("1090.00", febi.id)


async def test_not_quarantined_calculation_is_refused(demo: Demo) -> None:
    run = await _spike_run(demo)
    a = await demo.calculation(run, PRODUCT_A)
    assert a.state is ResultState.PUBLISHED
    await _assert_refused(demo, a.id, ConfirmRefusal.NOT_QUARANTINE)
    row = await _product_row(demo, PRODUCT_A)
    assert row["latest_calculation"]["confirmation"]["refusal"] == "not_quarantine"
    assert row["latest_calculation"]["confirmation"]["can_confirm"] is False


async def test_unknown_calculation_is_404(demo: Demo) -> None:
    response = await _confirm(demo, 999_999)
    assert response.status_code == 404
    assert response.json() == {"detail": "Расчёт не найден"}


async def test_a_newer_finished_run_supersedes_the_recommendation(demo: Demo) -> None:
    first = await _spike_run(demo)
    old_spike = await demo.calculation(first, PRODUCT_SPIKE)
    old_febi = await demo.calculation(first, PRODUCT_UNPUBLISHED)
    second = await demo.run()  # still price_spike: quarantined again
    new_spike = await demo.calculation(second, PRODUCT_SPIKE)
    assert new_spike.state is ResultState.QUARANTINE

    await _assert_refused(demo, old_spike.id, ConfirmRefusal.SUPERSEDED)
    await _assert_refused(demo, old_febi.id, ConfirmRefusal.SUPERSEDED)
    old_details = (await demo.api.get(f"/api/calculations/{old_spike.id}")).json()
    assert old_details["confirmation"]["refusal"] == "superseded"
    assert "требуется новый расчёт" in old_details["confirmation"]["refusal_label"]

    # The recommendation of the newer run can be confirmed.
    assert (await _confirm(demo, new_spike.id)).status_code == 200


async def test_a_partial_run_supersedes_too(demo: Demo) -> None:
    first = await _spike_run(demo)
    old_spike = await demo.calculation(first, PRODUCT_SPIKE)
    await demo.set_mode(Source.HTML_CATALOG, "markup_changed")
    second = await demo.run()
    assert second["status"] == "partial"
    await _assert_refused(demo, old_spike.id, ConfirmRefusal.SUPERSEDED)


async def test_a_failed_run_does_not_supersede(demo: Demo) -> None:
    first = await _spike_run(demo)
    spike = await demo.calculation(first, PRODUCT_SPIKE)
    await demo.set_mode(Source.HTML_CATALOG, "unavailable")
    await demo.set_mode(Source.JSON_API, "unavailable")
    failed = await demo.run()
    assert failed["status"] == "failed"

    assert (await _confirm(demo, spike.id)).status_code == 200


async def test_a_queued_or_running_run_does_not_block(demo: Demo) -> None:
    first = await _spike_run(demo)
    spike = await demo.calculation(first, PRODUCT_SPIKE)
    queued = await demo.api.post("/api/runs")
    assert queued.status_code == 201
    row = await _product_row(demo, PRODUCT_SPIKE)
    assert row["latest_calculation"]["confirmation"]["can_confirm"] is True
    assert await claim_next_run(demo.session_factory) == queued.json()["id"]  # running now

    assert (await _confirm(demo, spike.id)).status_code == 200


async def test_a_run_that_finishes_after_a_confirmation_recalculates_against_it(
    demo: Demo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The confirmation lands between the two transactions of the next run.

    The run read S as 1 990 and found it unchanged (sources back to normal:
    1 990); before it finishes, 1 490 is confirmed. Under the publication lock
    the run calculates S again against 1 490: +33.6 % is a quarantine, so the
    run does not overwrite the confirmed price.
    """
    first = await _spike_run(demo)
    spike = await demo.calculation(first, PRODUCT_SPIKE)
    await demo.set_mode(Source.JSON_API, "normal")
    original = execute.finish_and_publish
    confirmed: list[int] = []

    async def confirm_in_between(*args: Any, **kwargs: Any) -> Any:
        response = await _confirm(demo, spike.id)
        assert response.status_code == 200, response.text
        confirmed.append(spike.id)
        return await original(*args, **kwargs)

    monkeypatch.setattr(execute, "finish_and_publish", confirm_in_between)
    second = await demo.run()

    assert confirmed == [spike.id]
    again = await demo.calculation(second, PRODUCT_SPIKE)
    assert again.previous_price == Decimal("1490.00")
    assert (again.state, again.recommended_price) == (ResultState.QUARANTINE, Decimal("1990.00"))
    assert again.change_percent_display == Decimal("33.6")
    published = await _published_price(demo, PRODUCT_SPIKE)
    assert published is not None
    assert (published["price"], published["calculation_id"]) == ("1490.00", spike.id)
    # Products the confirmation did not touch were not recalculated: A is unchanged.
    a = await demo.calculation(second, PRODUCT_A)
    assert (a.state, a.previous_price) == (ResultState.UNCHANGED, Decimal("1240.00"))


async def test_confirmation_waits_for_a_finishing_run_and_is_superseded(
    demo: Demo, monitor_engine: AsyncEngine
) -> None:
    """Race: a run finishes while the confirmation is under way.

    The run's publishing transaction holds the publication lock (run finished,
    not committed). The confirmation waits for the lock instead of reading the
    old state; once the run commits it sees the newer run and is refused with
    ``superseded`` instead of publishing a stale recommendation.
    """
    first = await _spike_run(demo)
    spike = await demo.calculation(first, PRODUCT_SPIKE)
    queued = await demo.api.post("/api/runs")
    run_id = queued.json()["id"]
    assert await claim_next_run(demo.session_factory) == run_id
    before = await demo.published_rows()

    async with demo.session_factory() as session, session.begin():
        await execute.finish_and_publish(session, run_id, RunStatus.SUCCEEDED, [])
        confirmation = asyncio.create_task(
            confirm_calculation(demo.session_factory, spike.id, max_age=MAX_AGE)
        )
        await _wait_for_advisory_lock_waiter(monitor_engine)
        assert not confirmation.done()
        # Committed: the run is succeeded and newer than the calculation.
    with pytest.raises(ConfirmRefusedError) as refused:
        await asyncio.wait_for(confirmation, timeout=10)
    assert refused.value.refusal is ConfirmRefusal.SUPERSEDED
    assert await demo.published_rows() == before


async def _wait_for_advisory_lock_waiter(engine: AsyncEngine) -> None:
    sql = text("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND NOT granted")
    for _ in range(200):
        async with engine.connect() as conn:
            if await conn.scalar(sql):
                return
        await asyncio.sleep(0.05)
    raise AssertionError("the confirmation never waited for the publication lock")


async def test_too_old_recommendation_is_refused(demo: Demo) -> None:
    run = await _spike_run(demo)
    spike = await demo.calculation(run, PRODUCT_SPIKE)
    async with demo.session_factory() as session, session.begin():
        await session.execute(
            update(Calculation)
            .where(Calculation.id == spike.id)
            .values(calculated_at=text("now() - interval '61 minutes'"))
        )

    await _assert_refused(demo, spike.id, ConfirmRefusal.TOO_OLD)
    row = await _product_row(demo, PRODUCT_SPIKE)
    assert row["latest_calculation"]["confirmation"]["can_confirm"] is False
    assert row["latest_calculation"]["confirmation"]["refusal"] == "too_old"


async def test_age_is_counted_by_the_injected_clock_up_to_the_maximum(demo: Demo) -> None:
    run = await _spike_run(demo)
    spike = await demo.calculation(run, PRODUCT_SPIKE)

    with pytest.raises(ConfirmRefusedError) as refused:
        await confirm_calculation(
            demo.session_factory,
            spike.id,
            max_age=MAX_AGE,
            now=spike.calculated_at + MAX_AGE + timedelta(microseconds=1),
        )
    assert refused.value.refusal is ConfirmRefusal.TOO_OLD

    at_the_limit = spike.calculated_at + MAX_AGE
    confirmed = await confirm_calculation(
        demo.session_factory, spike.id, max_age=MAX_AGE, now=at_the_limit
    )
    assert (confirmed.price, confirmed.published_at) == (Decimal("1490.00"), at_the_limit)


async def test_database_clock_is_used_by_default(demo: Demo) -> None:
    """Without an injected clock the age is measured by now() of PostgreSQL."""
    run = await _spike_run(demo)
    spike = await demo.calculation(run, PRODUCT_SPIKE)
    async with demo.session_factory() as session:
        db_now = await database_now(session)
    # A tiny maximum age relative to the database clock: already too old.
    with pytest.raises(ConfirmRefusedError) as refused:
        await confirm_calculation(
            demo.session_factory,
            spike.id,
            max_age=db_now - spike.calculated_at - timedelta(microseconds=1),
        )
    assert refused.value.refusal is ConfirmRefusal.TOO_OLD
    confirmed = await confirm_calculation(demo.session_factory, spike.id, max_age=MAX_AGE)
    async with demo.session_factory() as session:
        after = await database_now(session)
    assert db_now <= confirmed.published_at <= after


async def _change_product(demo: Demo, item: SeedProduct, **values: Any) -> None:
    async with demo.session_factory() as session, session.begin():
        pid = await product_id(session, item)
        await session.execute(update(Product).where(Product.id == pid).values(**values))


async def test_changed_purchase_is_refused(demo: Demo) -> None:
    run = await _spike_run(demo)
    spike = await demo.calculation(run, PRODUCT_SPIKE)
    await _change_product(demo, PRODUCT_SPIKE, purchase_price=Decimal("1000.01"))
    await _assert_refused(demo, spike.id, ConfirmRefusal.PURCHASE_CHANGED)


async def test_changed_rules_version_is_refused(demo: Demo) -> None:
    run = await _spike_run(demo)
    spike = await demo.calculation(run, PRODUCT_SPIKE)
    async with demo.session_factory() as session, session.begin():
        await session.execute(
            update(Calculation)
            .where(Calculation.id == spike.id)
            .values(rules_version="2026-01-01.1")
        )
    await _assert_refused(demo, spike.id, ConfirmRefusal.RULES_CHANGED)


@pytest.mark.parametrize(
    "change",
    [
        {"min_markup_percent": Decimal("21.00")},
        {"step": Decimal("20.00")},
        {"ceiling": Decimal("5000.00")},
        {"quarantine_threshold_percent": Decimal("30.00")},
    ],
    ids=["markup", "step", "ceiling", "threshold"],
)
async def test_changed_product_settings_are_refused(demo: Demo, change: dict[str, Decimal]) -> None:
    run = await _spike_run(demo)
    spike = await demo.calculation(run, PRODUCT_SPIKE)
    await _change_product(demo, PRODUCT_SPIKE, **change)
    await _assert_refused(demo, spike.id, ConfirmRefusal.SETTINGS_CHANGED)


async def test_changed_published_price_is_refused(demo: Demo) -> None:
    run = await _spike_run(demo)
    spike = await demo.calculation(run, PRODUCT_SPIKE)
    febi = await demo.calculation(run, PRODUCT_UNPUBLISHED)
    async with demo.session_factory() as session, session.begin():
        await session.execute(
            update(PublishedPrice)
            .where(PublishedPrice.product_id == spike.product_id)
            .values(price=Decimal("1980.00"))
        )
        # Febi was calculated without a published price; now one exists.
        session.add(
            PublishedPrice(
                product_id=febi.product_id, price=Decimal("1100.00"), published_at=text("now()")
            )
        )

    await _assert_refused(demo, spike.id, ConfirmRefusal.PUBLISHED_CHANGED)
    await _assert_refused(demo, febi.id, ConfirmRefusal.PUBLISHED_CHANGED)


def _refusals(rows: Sequence[dict[str, Any]]) -> set[str | None]:
    return {
        r["latest_calculation"]["confirmation"]["refusal"] for r in rows if r["latest_calculation"]
    }


async def test_products_before_any_run_have_no_confirmation(demo: Demo) -> None:
    rows = (await demo.api.get("/api/products")).json()["products"]
    assert all(r["latest_calculation"] is None for r in rows)
    await _spike_run(demo)
    rows = (await demo.api.get("/api/products")).json()["products"]
    # Only quarantined rows can be confirmed; every other row says why not.
    can = {
        r["product"]["id"] for r in rows if r["latest_calculation"]["confirmation"]["can_confirm"]
    }
    quarantined = {
        r["product"]["id"] for r in rows if r["latest_calculation"]["state"] == "quarantine"
    }
    assert can == quarantined
    assert _refusals(rows) <= {None, "not_quarantine"}
