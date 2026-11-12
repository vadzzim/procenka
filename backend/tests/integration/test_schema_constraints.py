"""Database constraints the run and publication logic relies on."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import ACTIVE_RUN_STATUSES, RunFinishReason, RunStatus, RunTrigger
from app.db.models import Calculation, Product, PublishedPrice, Run
from app.db.seed import seed
from app.pricing.models import RULES_VERSION, ResultReason, ResultState

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)


def _run(status: RunStatus) -> Run:
    return Run(
        status=status,
        trigger=RunTrigger.MANUAL,
        started_at=None if status is RunStatus.QUEUED else NOW,
        finished_at=None if status in ACTIVE_RUN_STATUSES else NOW,
    )


def _calculation(run_id: int, product_id: int, **overrides: Any) -> Calculation:
    values: dict[str, Any] = {
        "run_id": run_id,
        "product_id": product_id,
        "rules_version": RULES_VERSION,
        "purchase_price": Decimal("1000.00"),
        "min_markup_percent": Decimal("20"),
        "step": Decimal("10.00"),
        "ceiling": None,
        "quarantine_threshold_percent": Decimal("20"),
        "suspicious_price_factor": Decimal("0.5"),
        "max_delivery_days": 14,
        "previous_price": Decimal("1260.00"),
        "partial_data": False,
        "accepted_offers": [],
        "rejected_offers": [],
        "min_competitor_price": Decimal("1250.00"),
        "target_price_raw": Decimal("1240.00"),
        "target_price": Decimal("1240.00"),
        "lower_bound": Decimal("1200.00"),
        "upper_bound": None,
        "applied_constraints": [],
        "recommended_price": Decimal("1240.00"),
        "state": ResultState.PUBLISHED,
        "reason": None,
        "change_percent": Decimal("-1.587301587301587301587301587"),
        "change_percent_display": Decimal("-1.6"),
    }
    values.update(overrides)
    return Calculation(**values)


async def _seeded_product_id(session: AsyncSession) -> int:
    await seed(session)
    product_id = await session.scalar(select(Product.id).order_by(Product.id).limit(1))
    assert product_id is not None
    return product_id


async def _finished_run_id(session: AsyncSession) -> int:
    run = _run(RunStatus.SUCCEEDED)
    session.add(run)
    await session.flush()
    return run.id


@pytest.mark.parametrize("first", ACTIVE_RUN_STATUSES)
@pytest.mark.parametrize("second", ACTIVE_RUN_STATUSES)
async def test_only_one_queued_or_running_run(
    db_session: AsyncSession, first: RunStatus, second: RunStatus
) -> None:
    db_session.add(_run(first))
    await db_session.commit()

    db_session.add(_run(second))
    with pytest.raises(IntegrityError, match="uq_runs_single_active"):
        await db_session.commit()


async def test_many_finished_runs_next_to_one_active(db_session: AsyncSession) -> None:
    statuses = [
        RunStatus.SUCCEEDED,
        RunStatus.SUCCEEDED,
        RunStatus.PARTIAL,
        RunStatus.PARTIAL,
        RunStatus.FAILED,
        RunStatus.FAILED,
        RunStatus.RUNNING,
    ]
    for status in statuses:
        db_session.add(_run(status))
        await db_session.commit()
    assert await db_session.scalar(select(func.count()).select_from(Run)) == len(statuses)


async def test_finished_run_frees_the_active_slot(db_session: AsyncSession) -> None:
    run = _run(RunStatus.RUNNING)
    db_session.add(run)
    await db_session.commit()

    run.status = RunStatus.FAILED
    run.finished_at = NOW
    run.finish_reason = RunFinishReason.INTERRUPTED
    await db_session.commit()

    db_session.add(_run(RunStatus.QUEUED))
    await db_session.commit()
    assert await db_session.scalar(select(func.count()).select_from(Run)) == 2


@pytest.mark.parametrize(
    ("status", "finished_at"),
    [(RunStatus.SUCCEEDED, None), (RunStatus.RUNNING, NOW), (RunStatus.QUEUED, NOW)],
)
async def test_finished_at_must_match_status(
    db_session: AsyncSession, status: RunStatus, finished_at: datetime | None
) -> None:
    db_session.add(Run(status=status, trigger=RunTrigger.SCHEDULE, finished_at=finished_at))
    with pytest.raises(IntegrityError, match="ck_runs_finished_at_matches_status"):
        await db_session.commit()


async def test_one_calculation_per_run_and_product(db_session: AsyncSession) -> None:
    product_id = await _seeded_product_id(db_session)
    first_run_id = await _finished_run_id(db_session)
    second_run_id = await _finished_run_id(db_session)

    # The same product in two runs is fine.
    db_session.add(_calculation(first_run_id, product_id))
    db_session.add(_calculation(second_run_id, product_id))
    await db_session.commit()

    db_session.add(_calculation(first_run_id, product_id))
    with pytest.raises(IntegrityError, match="uq_calculations_run_id_product_id"):
        await db_session.commit()


async def test_calculation_round_trips_exact_values(db_session: AsyncSession) -> None:
    product_id = await _seeded_product_id(db_session)
    run_id = await _finished_run_id(db_session)
    offers = [{"source": "json_api", "price": "1250.00"}]
    calculation = _calculation(run_id, product_id, accepted_offers=offers)
    db_session.add(calculation)
    await db_session.commit()
    db_session.expunge_all()

    stored = await db_session.get_one(Calculation, calculation.id)
    assert isinstance(stored.recommended_price, Decimal)
    assert stored.recommended_price == Decimal("1240.00")
    assert stored.change_percent == Decimal("-1.587301587301587301587301587")
    assert stored.accepted_offers == offers
    assert stored.state is ResultState.PUBLISHED


async def test_money_columns_keep_two_decimals(db_session: AsyncSession) -> None:
    product_id = await _seeded_product_id(db_session)
    run_id = await _finished_run_id(db_session)
    calculation = _calculation(run_id, product_id, recommended_price=Decimal("1240"))
    db_session.add(calculation)
    await db_session.commit()
    db_session.expunge_all()

    stored = await db_session.get_one(Calculation, calculation.id)
    assert str(stored.recommended_price) == "1240.00"


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"recommended_price": None}, "ck_calculations_recommended_price_matches_state"),
        ({"reason": ResultReason.PRICE_SPIKE}, "ck_calculations_reason_matches_state"),
        (
            {"state": ResultState.QUARANTINE, "reason": None},
            "ck_calculations_reason_matches_state",
        ),
        (
            {"state": ResultState.RETAINED, "recommended_price": None},
            "ck_calculations_reason_matches_state",
        ),
        # Only a quarantined recommendation is confirmed.
        (
            {"confirmed_at": datetime(2026, 11, 12, tzinfo=UTC)},
            "ck_calculations_confirmed_only_quarantine",
        ),
    ],
)
async def test_calculation_state_consistency(
    db_session: AsyncSession, overrides: dict[str, Any], constraint: str
) -> None:
    product_id = await _seeded_product_id(db_session)
    run_id = await _finished_run_id(db_session)
    db_session.add(_calculation(run_id, product_id, **overrides))
    with pytest.raises(IntegrityError, match=constraint):
        await db_session.commit()


async def test_published_price_is_one_row_per_product_via_on_conflict(
    db_session: AsyncSession,
) -> None:
    product_id = await _seeded_product_id(db_session)
    for price in (Decimal("1240.00"), Decimal("1230.00")):
        statement = insert(PublishedPrice).values(
            product_id=product_id, price=price, published_at=NOW, calculation_id=None
        )
        await db_session.execute(
            statement.on_conflict_do_update(
                index_elements=[PublishedPrice.product_id],
                set_={"price": statement.excluded.price},
            )
        )
    await db_session.commit()

    prices = (
        await db_session.scalars(
            select(PublishedPrice.price).where(PublishedPrice.product_id == product_id)
        )
    ).all()
    assert prices == [Decimal("1230.00")]
