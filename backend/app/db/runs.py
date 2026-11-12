"""Creating, claiming and recovering runs.

At most one run is queued or running at a time: the partial unique index
``uq_runs_single_active`` enforces it in the database, so concurrent requests
cannot create two active runs.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import ColumnElement, exists, func, literal, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.enums import ACTIVE_RUN_STATUSES, RunFinishReason, RunStatus, RunTrigger
from app.db.models import Run

# The active run may finish between the failed insert and the select; then try again.
_ENQUEUE_ATTEMPTS = 5


@dataclass(frozen=True, slots=True)
class EnqueuedRun:
    id: int
    status: RunStatus
    created: bool


async def enqueue_run(
    session_factory: async_sessionmaker[AsyncSession], trigger: RunTrigger
) -> EnqueuedRun:
    """Queue a new run, or return the run that is already queued or running."""
    for _ in range(_ENQUEUE_ATTEMPTS):
        async with session_factory() as session, session.begin():
            run_id = await session.scalar(
                insert(Run)
                .values(status=RunStatus.QUEUED, trigger=trigger)
                # The only unique constraints of runs are the identity key and
                # uq_runs_single_active, so a conflict means "an active run exists".
                .on_conflict_do_nothing()
                .returning(Run.id)
            )
            if run_id is not None:
                return EnqueuedRun(id=run_id, status=RunStatus.QUEUED, created=True)
            active = (
                await session.execute(
                    select(Run.id, Run.status).where(Run.status.in_(ACTIVE_RUN_STATUSES))
                )
            ).one_or_none()
            if active is not None:
                return EnqueuedRun(id=active.id, status=active.status, created=False)
    raise RuntimeError("could not enqueue a run: the active run kept changing")


async def claim_next_run(session_factory: async_sessionmaker[AsyncSession]) -> int | None:
    """Move the oldest queued run to running and commit; ``None`` if nothing is queued."""
    async with session_factory() as session, session.begin():
        run_id = await session.scalar(
            select(Run.id)
            .where(Run.status == RunStatus.QUEUED)
            .order_by(Run.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if run_id is None:
            return None
        await session.execute(
            update(Run)
            .where(Run.id == run_id)
            .values(status=RunStatus.RUNNING, started_at=text("now()"))
        )
        return run_id


async def enqueue_scheduled_run(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    interval_seconds: float,
    now: datetime | None = None,
) -> int | None:
    """Queue a scheduled run if it is due; return its id, or ``None`` if it is not due.

    The decision is made from the database, not from a timer in memory, so the
    schedule survives a restart of the worker and does not add a run next to a
    manual one: a run is due when no run is queued or running and the latest
    run of any trigger was created ``interval_seconds`` or more ago (or there
    are no runs at all). ``interval_seconds = 0`` turns the schedule off.

    "Now" is the database clock, the one that sets ``runs.created_at``; tests
    pass ``now`` instead, and the scheduled run is then created at that moment,
    so one clock is used throughout.

    One statement decides and inserts; a run queued concurrently by the API
    is caught by ``uq_runs_single_active`` (``ON CONFLICT DO NOTHING``).
    """
    if interval_seconds <= 0:
        return None
    current: ColumnElement[datetime] = (
        func.now() if now is None else literal(now, Run.created_at.type)
    )
    due = ~exists().where(Run.status.in_(ACTIVE_RUN_STATUSES)) & ~exists().where(
        Run.created_at > current - timedelta(seconds=interval_seconds)
    )
    async with session_factory() as session, session.begin():
        run_id: int | None = await session.scalar(
            insert(Run)
            .from_select(
                ["status", "trigger", "created_at"],
                select(
                    literal(RunStatus.QUEUED.value).cast(Run.status.type),
                    literal(RunTrigger.SCHEDULE.value).cast(Run.trigger.type),
                    current,
                ).where(due),
            )
            .on_conflict_do_nothing()
            .returning(Run.id)
        )
        return run_id


async def recover_interrupted_runs(session_factory: async_sessionmaker[AsyncSession]) -> list[int]:
    """Fail every run left ``running`` by a stopped worker; called once at worker start.

    Such a run becomes ``failed`` with ``finish_reason = interrupted``. Its source
    results, offers and calculations, if any were written, stay as history;
    nothing of it is published, because publication happens only in the
    transaction that finishes a run that is still ``running``. Queued runs are
    left alone: the worker takes them as usual.
    """
    async with session_factory() as session, session.begin():
        ids = await session.scalars(
            update(Run)
            .where(Run.status == RunStatus.RUNNING)
            .values(
                status=RunStatus.FAILED,
                finished_at=func.now(),
                finish_reason=RunFinishReason.INTERRUPTED,
            )
            .returning(Run.id)
        )
        return sorted(ids)
