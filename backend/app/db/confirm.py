"""Confirmation of a quarantined recommendation (``plan.md``, extension 1).

The manager confirms a recommendation that stayed in quarantine; it is then
published. A confirmation is allowed only if the calculation is still the
latest word and its inputs still hold; otherwise a new calculation is needed.
The conditions, checked in this order, give the code of the refusal:

1. ``not_quarantine``: the state of the calculation is not "quarantine";
2. ``already_confirmed``: it was confirmed already (nothing is published twice);
3. ``superseded``: the calculation is not from the latest finished run. A
   finished run is ``succeeded`` or ``partial``: a ``failed`` run calculates
   nothing that is shown and supersedes nothing; a queued or running run does
   not block a confirmation (see ``app.db.publication`` for how a run that
   finishes later takes the confirmed price into account);
4. ``too_old``: it is older than ``QUARANTINE_CONFIRM_MAX_AGE_MINUTES`` by the
   database clock (exactly the maximum age is still allowed);
5. ``purchase_changed``: the purchase price of the product changed;
6. ``rules_changed``: the calculation was made by another ``RULES_VERSION``;
7. ``settings_changed``: markup, step, ceiling or quarantine threshold of the
   product changed (beyond ``plan.md``, in its spirit);
8. ``published_changed``: the published price is not the one the calculation
   compared against (``previous_price``).

The check and the write are one transaction under the publication lock, so a
run cannot finish (and supersede the recommendation) between them.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.enums import RunStatus
from app.db.models import Calculation, Product, PublishedPrice, Run
from app.db.publication import Publication, lock_publication, upsert_published_prices
from app.pricing.models import RULES_VERSION, ResultState

# Runs whose calculations are the latest word: they finished and wrote calculations.
FINISHED_RUN_STATUSES = (RunStatus.SUCCEEDED, RunStatus.PARTIAL)


class ConfirmRefusal(StrEnum):
    NOT_QUARANTINE = "not_quarantine"
    ALREADY_CONFIRMED = "already_confirmed"
    SUPERSEDED = "superseded"
    TOO_OLD = "too_old"
    PURCHASE_CHANGED = "purchase_changed"
    RULES_CHANGED = "rules_changed"
    SETTINGS_CHANGED = "settings_changed"
    PUBLISHED_CHANGED = "published_changed"


# The single table of Russian labels of refusals; the API and the UI show them as they are.
CONFIRM_REFUSAL_LABELS: MappingProxyType[ConfirmRefusal, str] = MappingProxyType(
    {
        ConfirmRefusal.NOT_QUARANTINE: "рекомендация не в карантине: подтверждать нечего",
        ConfirmRefusal.ALREADY_CONFIRMED: "рекомендация уже подтверждена и опубликована",
        ConfirmRefusal.SUPERSEDED: (
            "рекомендация не из последнего завершённого прогона: требуется новый расчёт"
        ),
        ConfirmRefusal.TOO_OLD: "рекомендация устарела: требуется новый расчёт",
        ConfirmRefusal.PURCHASE_CHANGED: (
            "закупка изменилась с момента расчёта: требуется новый расчёт"
        ),
        ConfirmRefusal.RULES_CHANGED: (
            "версия правил изменилась с момента расчёта: требуется новый расчёт"
        ),
        ConfirmRefusal.SETTINGS_CHANGED: (
            "настройки товара изменились с момента расчёта: требуется новый расчёт"
        ),
        ConfirmRefusal.PUBLISHED_CHANGED: (
            "опубликованная цена изменилась с момента расчёта: требуется новый расчёт"
        ),
    }
)


@dataclass(frozen=True, slots=True)
class ConfirmContext:
    """What a calculation is checked against: the state of the database now."""

    latest_finished_run_id: int | None
    now: datetime
    max_age: timedelta
    rules_version: str = RULES_VERSION


def confirm_refusal(
    calculation: Calculation,
    product: Product,
    published_price: Decimal | None,
    context: ConfirmContext,
) -> ConfirmRefusal | None:
    """Why ``calculation`` cannot be confirmed now; ``None`` if it can."""
    if calculation.state is not ResultState.QUARANTINE:
        return ConfirmRefusal.NOT_QUARANTINE
    if calculation.confirmed_at is not None:
        return ConfirmRefusal.ALREADY_CONFIRMED
    if calculation.run_id != context.latest_finished_run_id:
        return ConfirmRefusal.SUPERSEDED
    if context.now - calculation.calculated_at > context.max_age:
        return ConfirmRefusal.TOO_OLD
    if product.purchase_price != calculation.purchase_price:
        return ConfirmRefusal.PURCHASE_CHANGED
    if calculation.rules_version != context.rules_version:
        return ConfirmRefusal.RULES_CHANGED
    if (
        product.min_markup_percent,
        product.step,
        product.ceiling,
        product.quarantine_threshold_percent,
    ) != (
        calculation.min_markup_percent,
        calculation.step,
        calculation.ceiling,
        calculation.quarantine_threshold_percent,
    ):
        return ConfirmRefusal.SETTINGS_CHANGED
    if published_price != calculation.previous_price:
        return ConfirmRefusal.PUBLISHED_CHANGED
    return None


async def latest_finished_run_id(session: AsyncSession) -> int | None:
    run_id: int | None = await session.scalar(
        select(func.max(Run.id)).where(Run.status.in_(FINISHED_RUN_STATUSES))
    )
    return run_id


async def database_now(session: AsyncSession) -> datetime:
    """The database clock at this moment (not the start of the transaction)."""
    now: datetime | None = await session.scalar(select(func.clock_timestamp()))
    assert now is not None
    return now


async def confirm_context(
    session: AsyncSession, *, max_age: timedelta, now: datetime | None = None
) -> ConfirmContext:
    """The current state; ``now`` (tests) replaces the database clock."""
    return ConfirmContext(
        latest_finished_run_id=await latest_finished_run_id(session),
        now=await database_now(session) if now is None else now,
        max_age=max_age,
    )


class CalculationNotFoundError(LookupError):
    pass


class ConfirmRefusedError(RuntimeError):
    def __init__(self, refusal: ConfirmRefusal) -> None:
        super().__init__(refusal.value)
        self.refusal = refusal

    @property
    def label(self) -> str:
        return CONFIRM_REFUSAL_LABELS[self.refusal]


@dataclass(frozen=True, slots=True)
class Confirmed:
    """The price published by a confirmation."""

    product_id: int
    brand: str
    article: str
    name: str
    calculation_id: int
    price: Decimal
    published_at: datetime


async def confirm_calculation(
    session_factory: async_sessionmaker[AsyncSession],
    calculation_id: int,
    *,
    max_age: timedelta,
    now: datetime | None = None,
) -> Confirmed:
    """Check and publish in one transaction; raise ``ConfirmRefusedError`` or
    ``CalculationNotFoundError`` and write nothing otherwise.

    ``now`` (tests) replaces the database clock for the age and the timestamps.
    """
    async with session_factory() as session, session.begin():
        # Taken before anything is read: a run finishing concurrently is either
        # fully visible to the checks below or waits until this commits.
        await lock_publication(session)
        row = (
            await session.execute(
                select(Calculation, Product)
                .join(Product, Product.id == Calculation.product_id)
                .where(Calculation.id == calculation_id)
                .with_for_update()
            )
        ).one_or_none()
        if row is None:
            raise CalculationNotFoundError(calculation_id)
        calculation, product = row
        published_price = await session.scalar(
            select(PublishedPrice.price).where(PublishedPrice.product_id == product.id)
        )
        context = await confirm_context(session, max_age=max_age, now=now)
        refusal = confirm_refusal(calculation, product, published_price, context)
        if refusal is not None:
            raise ConfirmRefusedError(refusal)
        assert calculation.recommended_price is not None  # quarantine always recommends
        await session.execute(
            update(Calculation)
            .where(Calculation.id == calculation.id, Calculation.confirmed_at.is_(None))
            .values(confirmed_at=context.now)
        )
        await upsert_published_prices(
            session,
            [Publication(product.id, calculation.id, calculation.recommended_price)],
            published_at=context.now,
        )
        return Confirmed(
            product_id=product.id,
            brand=product.brand,
            article=product.article,
            name=product.name,
            calculation_id=calculation.id,
            price=calculation.recommended_price,
            published_at=context.now,
        )
