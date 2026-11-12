"""The worker: recover, schedule, take queued runs one by one and execute them.

``run_worker`` (``plan.md``, "Прогоны и устойчивость"):

1. once, at start: runs left ``running`` by a stopped worker become ``failed``
   with ``finish_reason = interrupted``;
2. every iteration: queue a scheduled run if it is due (decided from the
   database), claim the oldest queued run with ``FOR UPDATE SKIP LOCKED`` and
   execute it, touch the heartbeat file (``WORKER_HEARTBEAT_FILE``, for the
   compose healthcheck), then pause ``WORKER_POLL_SECONDS``.

An exception in one iteration is logged and the loop goes on; a run whose
execution raised is failed with ``internal_error`` (retried in the next
iterations if the database was unreachable). ``stop`` ends the
loop between iterations (set by SIGTERM / SIGINT in ``__main__``).
"""

import asyncio
import contextlib
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.enums import RunFinishReason
from app.db.runs import claim_next_run, enqueue_scheduled_run, recover_interrupted_runs
from app.logs import bind_run_id, get_logger
from app.parsers.base import OfferSource
from app.worker.execute import RunOutcome, execute_run, fail_run
from app.worker.heartbeat import beat, clear

logger = get_logger("app.worker")

# A clock for the schedule; ``None`` means the database clock (see enqueue_scheduled_run).
type Now = Callable[[], datetime] | None


class RunCrashedError(RuntimeError):
    """Executing a run raised unexpectedly; the cause is already logged with the run id.

    ``recorded``: the run is failed as ``internal_error`` in the database. If not
    (the database was unreachable too), the run is still ``running`` and the
    worker loop records the failure as soon as it can.
    """

    def __init__(self, run_id: int, *, recorded: bool) -> None:
        super().__init__(f"run {run_id} failed with an unexpected error")
        self.run_id = run_id
        self.recorded = recorded


async def process_next_run(
    session_factory: async_sessionmaker[AsyncSession],
    sources: list[OfferSource],
    settings: Settings,
) -> RunOutcome | None:
    """Claim and execute one queued run; ``None`` if nothing is queued.

    An unexpected exception (a bug, a lost database connection; not a source
    error) is logged, fails the run with ``internal_error`` and is raised as
    ``RunCrashedError``.
    Cancellation is not caught: the run stays ``running`` until the next worker
    start recovers it as interrupted.
    """
    run_id = await claim_next_run(session_factory)
    if run_id is None:
        return None
    with bind_run_id(run_id):
        logger.info("run started")
        try:
            outcome = await execute_run(
                session_factory, run_id, sources, global_settings=settings.global_settings()
            )
        except Exception as exc:
            logger.exception("run failed with an unexpected error; failing it as internal_error")
            try:
                await fail_run(session_factory, run_id, RunFinishReason.INTERNAL_ERROR)
            except Exception:
                logger.exception("could not record the failure of the run; it stays running")
                raise RunCrashedError(run_id, recorded=False) from exc
            raise RunCrashedError(run_id, recorded=True) from exc
        logger.info(
            "run finished",
            status=outcome.status.value,
            calculations=outcome.calculations,
            published=outcome.published,
        )
    return outcome


async def schedule_run(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings, now: Now
) -> int | None:
    """Queue a scheduled run if it is due (see ``enqueue_scheduled_run``)."""
    run_id = await enqueue_scheduled_run(
        session_factory,
        interval_seconds=settings.schedule_interval_seconds,
        now=None if now is None else now(),
    )
    if run_id is not None:
        with bind_run_id(run_id):
            logger.info("scheduled run queued")
    return run_id


async def worker_iteration(
    session_factory: async_sessionmaker[AsyncSession],
    sources: list[OfferSource],
    settings: Settings,
    now: Now,
) -> RunOutcome | None:
    await schedule_run(session_factory, settings, now)
    return await process_next_run(session_factory, sources, settings)


async def record_crash(session_factory: async_sessionmaker[AsyncSession], run_id: int) -> None:
    """Fail a crashed run whose failure could not be written before."""
    with bind_run_id(run_id):
        await fail_run(session_factory, run_id, RunFinishReason.INTERNAL_ERROR)
        logger.warning("failure of the run recorded: failed as internal_error")


async def recover(session_factory: async_sessionmaker[AsyncSession]) -> list[int]:
    run_ids = await recover_interrupted_runs(session_factory)
    for run_id in run_ids:
        with bind_run_id(run_id):
            logger.warning("run was left running by a stopped worker: failed as interrupted")
    return run_ids


def heartbeat(path: Path | None, *, stale: bool = False) -> None:
    """Touch the heartbeat file (``stale``: remove it); an OS error is logged, the loop goes on."""
    if path is None:
        return
    try:
        if stale:
            clear(path)
        else:
            beat(path)
    except OSError:
        logger.exception("could not update the heartbeat file", path=str(path))


async def run_worker(
    session_factory: async_sessionmaker[AsyncSession],
    sources: list[OfferSource],
    settings: Settings,
    *,
    stop: asyncio.Event,
    now: Now = None,
) -> None:
    # Not healthy until the recovery is done: a file left by the previous process goes first.
    heartbeat(settings.worker_heartbeat_file, stale=True)
    await recover(session_factory)
    heartbeat(settings.worker_heartbeat_file)
    logger.info(
        "worker started",
        schedule_interval_seconds=settings.schedule_interval_seconds,
        poll_seconds=settings.worker_poll_seconds,
    )
    # A crashed run still ``running`` because its failure could not be written:
    # it would block every new run until a restart, so it is retried first.
    unrecorded: int | None = None
    while not stop.is_set():
        try:
            if unrecorded is not None:
                await record_crash(session_factory, unrecorded)
                unrecorded = None
            await worker_iteration(session_factory, sources, settings, now)
        except RunCrashedError as crash:
            # Already logged with its run id.
            if not crash.recorded:
                unrecorded = crash.run_id
        except Exception:
            # E.g. the database is unreachable; the next iteration tries again.
            logger.exception("worker iteration failed")
        heartbeat(settings.worker_heartbeat_file)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=settings.worker_poll_seconds)
    logger.info("worker stopped")
