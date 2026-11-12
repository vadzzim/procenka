"""Creating and claiming runs.

At most one run is queued or running at a time: the partial unique index
``uq_runs_single_active`` enforces it in the database, so concurrent requests
cannot create two active runs.
"""

from dataclasses import dataclass

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.enums import ACTIVE_RUN_STATUSES, RunStatus, RunTrigger
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
