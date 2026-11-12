"""The worker: take queued runs one by one and execute them.

Recovery of runs left ``running`` after a crash and the schedule come in step 6.
"""

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.enums import RunFinishReason
from app.db.runs import claim_next_run
from app.parsers.base import OfferSource
from app.worker.execute import RunOutcome, execute_run, fail_run

logger = logging.getLogger("app.worker")


async def process_next_run(
    session_factory: async_sessionmaker[AsyncSession],
    sources: list[OfferSource],
    settings: Settings,
) -> RunOutcome | None:
    """Claim and execute one queued run; ``None`` if nothing is queued.

    An unexpected exception (a bug, not a source error) fails the run with
    ``internal_error`` and is raised again.
    """
    run_id = await claim_next_run(session_factory)
    if run_id is None:
        return None
    logger.info("run %s started", run_id)
    try:
        outcome = await execute_run(
            session_factory, run_id, sources, global_settings=settings.global_settings()
        )
    except Exception:
        await fail_run(session_factory, run_id, RunFinishReason.INTERNAL_ERROR)
        raise
    logger.info(
        "run %s finished: %s, %s calculations, %s published",
        run_id,
        outcome.status.value,
        outcome.calculations,
        outcome.published,
    )
    return outcome


async def run_worker(
    session_factory: async_sessionmaker[AsyncSession],
    sources: list[OfferSource],
    settings: Settings,
) -> None:
    while True:
        try:
            outcome = await process_next_run(session_factory, sources, settings)
        except Exception:
            logger.exception("run failed with an unexpected error")
            outcome = None
        if outcome is None:
            await asyncio.sleep(settings.worker_poll_interval_seconds)
