"""The worker on PostgreSQL: recovery of interrupted runs, the schedule, the loop.

``plan.md``, "Прогоны и устойчивость": a run left ``running`` by a stopped
worker becomes ``failed`` / ``interrupted`` at the next start; scheduled runs
are queued by the same worker; the loop survives an error in one iteration.
Sources are the real parsers against fake-sites (``tests.integration.demo``).
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Callable, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.enums import RunFinishReason, RunStatus, RunTrigger, Source
from app.db.models import Calculation, Product, Run, SourceResult
from app.db.runs import claim_next_run, enqueue_run, enqueue_scheduled_run, recover_interrupted_runs
from app.db.seed_data import PRODUCT_A
from app.parsers.base import OfferSource, ParsedOffer
from app.worker import loop
from app.worker.execute import fail_run
from app.worker.loop import process_next_run, run_worker
from tests.integration.demo import SETTINGS, Demo
from tests.logs import capture_json_logs

pytestmark = pytest.mark.integration

WAIT_SECONDS = 15
INTERVAL = 600.0
WORKER_SETTINGS = SETTINGS.model_copy(
    update={"worker_poll_seconds": 0.01, "schedule_interval_seconds": 0.0}
)


async def _run(session_factory: async_sessionmaker[AsyncSession], run_id: int) -> Run:
    async with session_factory() as session:
        run = await session.get(Run, run_id)
    assert run is not None
    return run


async def _runs(session_factory: async_sessionmaker[AsyncSession]) -> list[Run]:
    async with session_factory() as session:
        return list(await session.scalars(select(Run).order_by(Run.id)))


async def _wait_finished(session_factory: async_sessionmaker[AsyncSession], run_id: int) -> Run:
    """Wait until the run exists (it may still have to be scheduled) and is finished."""
    async with asyncio.timeout(WAIT_SECONDS):
        while True:
            async with session_factory() as session:
                run = await session.get(Run, run_id)
            if run is not None and run.finished_at is not None:
                return run
            await asyncio.sleep(0.02)


class Worker:
    """``run_worker`` as a background task, stopped through its ``stop`` event."""

    def __init__(self, task: asyncio.Task[None], stop: asyncio.Event) -> None:
        self.task = task
        self.stop = stop

    async def shutdown(self) -> None:
        self.stop.set()
        await asyncio.wait_for(self.task, WAIT_SECONDS)


@pytest.fixture
async def start_worker(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[Callable[..., Worker]]:
    workers: list[Worker] = []

    def start(
        sources: Sequence[OfferSource],
        *,
        schedule_interval_seconds: float = 0.0,
        now: Callable[[], datetime] | None = None,
    ) -> Worker:
        settings = WORKER_SETTINGS.model_copy(
            update={"schedule_interval_seconds": schedule_interval_seconds}
        )
        stop = asyncio.Event()
        options = {"now": now} if now is not None else {}
        task = asyncio.create_task(
            run_worker(session_factory, list(sources), settings, stop=stop, **options)
        )
        worker = Worker(task, stop)
        workers.append(worker)
        return worker

    yield start
    for worker in workers:
        if not worker.task.done():
            worker.stop.set()
            worker.task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await worker.task


# --- Recovery of interrupted runs ------------------------------------------


async def test_worker_start_fails_a_run_left_running_and_then_runs_normally(
    demo: Demo, start_worker: Callable[..., Worker]
) -> None:
    await demo.run()  # A is published at 1 240
    published = await demo.published_rows()
    # A worker claimed run 2 and stopped before executing it.
    stale = (await demo.api.post("/api/runs")).json()["id"]
    assert await claim_next_run(demo.session_factory) == stale
    # While it is "running", a start returns it instead of a new run.
    assert (await demo.api.post("/api/runs")).json() == {
        "id": stale,
        "status": "running",
        "created": False,
    }

    worker = start_worker(demo.sources)
    interrupted = await _wait_finished(demo.session_factory, stale)

    assert interrupted.status is RunStatus.FAILED
    assert interrupted.finish_reason == RunFinishReason.INTERRUPTED
    assert interrupted.finished_at is not None
    assert interrupted.started_at is not None
    assert interrupted.finished_at >= interrupted.started_at
    assert await demo.published_rows() == published

    # The next start creates a new run, and the worker completes it from scratch.
    response = await demo.api.post("/api/runs")
    assert response.status_code == 201
    fresh = response.json()["id"]
    assert fresh != stale
    finished = await _wait_finished(demo.session_factory, fresh)
    await worker.shutdown()

    assert finished.status is RunStatus.SUCCEEDED
    assert finished.finish_reason is None
    assert await demo.published(PRODUCT_A) == Decimal("1240.00")
    async with demo.session_factory() as session:
        # Nothing was collected or calculated for the interrupted run.
        for model in (SourceResult, Calculation):
            count = await session.scalar(
                select(func.count()).select_from(model).where(model.run_id == stale)
            )
            assert count == 0
        assert await session.scalar(
            select(func.count()).select_from(Calculation).where(Calculation.run_id == fresh)
        )


class _BlockingSource:
    """Signals that collection has started and then never returns."""

    def __init__(self, source: Source) -> None:
        self.source = source
        self.started = asyncio.Event()

    async def fetch_offers(self, articles: Sequence[str]) -> list[ParsedOffer]:
        self.started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


async def test_run_cancelled_during_collection_is_recovered_as_interrupted(demo: Demo) -> None:
    published = await demo.published_rows()
    run_id = (await demo.api.post("/api/runs")).json()["id"]
    blocking = _BlockingSource(Source.JSON_API)
    sources: list[OfferSource] = [demo.sources[0], blocking]

    # The worker process is stopped in the middle of collecting the sources.
    task = asyncio.create_task(process_next_run(demo.session_factory, sources, SETTINGS))
    await asyncio.wait_for(blocking.started.wait(), WAIT_SECONDS)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (await _run(demo.session_factory, run_id)).status is RunStatus.RUNNING

    assert await recover_interrupted_runs(demo.session_factory) == [run_id]

    run = await _run(demo.session_factory, run_id)
    assert (run.status, run.finish_reason) == (RunStatus.FAILED, RunFinishReason.INTERRUPTED)
    assert run.finished_at is not None
    assert await demo.published_rows() == published
    # Recovery is idempotent and a new run proceeds normally.
    assert await recover_interrupted_runs(demo.session_factory) == []
    assert (await demo.run())["status"] == "succeeded"


async def test_recovery_leaves_queued_and_finished_runs_alone(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    first = await enqueue_run(session_factory, RunTrigger.MANUAL)
    await claim_next_run(session_factory)
    async with session_factory() as session, session.begin():
        await session.execute(
            update(Run)
            .where(Run.id == first.id)
            .values(status=RunStatus.SUCCEEDED, finished_at=func.now())
        )
    queued = await enqueue_run(session_factory, RunTrigger.SCHEDULE)

    assert await recover_interrupted_runs(session_factory) == []

    assert [(r.id, r.status) for r in await _runs(session_factory)] == [
        (first.id, RunStatus.SUCCEEDED),
        (queued.id, RunStatus.QUEUED),
    ]


# --- The schedule ----------------------------------------------------------


async def _created_at(session_factory: async_sessionmaker[AsyncSession], run_id: int) -> datetime:
    return (await _run(session_factory, run_id)).created_at


async def _finish(session_factory: async_sessionmaker[AsyncSession], run_id: int) -> None:
    async with session_factory() as session, session.begin():
        await session.execute(
            update(Run)
            .where(Run.id == run_id)
            .values(status=RunStatus.SUCCEEDED, started_at=func.now(), finished_at=func.now())
        )


async def test_schedule_queues_a_run_on_an_empty_database(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    run_id = await enqueue_scheduled_run(
        session_factory, interval_seconds=INTERVAL, now=datetime.now(UTC)
    )

    assert run_id is not None
    run = await _run(session_factory, run_id)
    assert (run.status, run.trigger) == (RunStatus.QUEUED, RunTrigger.SCHEDULE)


@pytest.mark.parametrize("claimed", [False, True], ids=["queued", "running"])
async def test_schedule_adds_nothing_next_to_an_active_run(
    session_factory: async_sessionmaker[AsyncSession], claimed: bool
) -> None:
    active = await enqueue_run(session_factory, RunTrigger.MANUAL)
    if claimed:
        await claim_next_run(session_factory)
    far_future = await _created_at(session_factory, active.id) + timedelta(days=1)

    assert (
        await enqueue_scheduled_run(session_factory, interval_seconds=INTERVAL, now=far_future)
        is None
    )
    assert [r.id for r in await _runs(session_factory)] == [active.id]


async def test_schedule_waits_for_the_interval_since_the_latest_run(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # The latest run is manual: the schedule counts runs of any trigger.
    manual = await enqueue_run(session_factory, RunTrigger.MANUAL)
    await _finish(session_factory, manual.id)
    created = await _created_at(session_factory, manual.id)

    younger = created + timedelta(seconds=INTERVAL - 1)
    assert (
        await enqueue_scheduled_run(session_factory, interval_seconds=INTERVAL, now=younger) is None
    )
    assert len(await _runs(session_factory)) == 1

    older = created + timedelta(seconds=INTERVAL + 1)
    run_id = await enqueue_scheduled_run(session_factory, interval_seconds=INTERVAL, now=older)
    assert run_id is not None
    assert (await _run(session_factory, run_id)).trigger is RunTrigger.SCHEDULE

    # The latest run is now the scheduled one, so the next one waits again.
    await _finish(session_factory, run_id)
    assert (
        await enqueue_scheduled_run(session_factory, interval_seconds=INTERVAL, now=older) is None
    )


async def test_schedule_interval_zero_never_queues(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    far_future = datetime.now(UTC) + timedelta(days=365)
    assert await enqueue_scheduled_run(session_factory, interval_seconds=0, now=far_future) is None
    assert await _runs(session_factory) == []


async def test_schedule_and_manual_start_together_create_one_run(
    session_factory: async_sessionmaker[AsyncSession], api: httpx.AsyncClient
) -> None:
    now = datetime.now(UTC)
    for _ in range(5):
        results = await asyncio.gather(
            api.post("/api/runs"),
            enqueue_scheduled_run(session_factory, interval_seconds=INTERVAL, now=now),
        )
        runs = await _runs(session_factory)
        active = [r for r in runs if r.status is RunStatus.QUEUED]
        assert len(active) == 1
        response, scheduled = results
        assert response.json()["id"] == active[0].id
        assert scheduled in (None, active[0].id)
        await _finish(session_factory, active[0].id)
        now += timedelta(seconds=INTERVAL + 1)


async def test_worker_queues_and_executes_scheduled_runs(
    demo: Demo, start_worker: Callable[..., Worker]
) -> None:
    clock = [datetime.now(UTC)]
    worker = start_worker(demo.sources, schedule_interval_seconds=INTERVAL, now=lambda: clock[0])

    first = await _wait_finished(demo.session_factory, 1)
    assert (first.trigger, first.status) == (RunTrigger.SCHEDULE, RunStatus.SUCCEEDED)
    await asyncio.sleep(0.1)  # several iterations: the interval has not passed yet
    assert len(await _runs(demo.session_factory)) == 1

    clock[0] += timedelta(seconds=INTERVAL + 1)
    second = await _wait_finished(demo.session_factory, 2)
    await worker.shutdown()

    assert (second.trigger, second.status) == (RunTrigger.SCHEDULE, RunStatus.SUCCEEDED)
    assert len(await _runs(demo.session_factory)) == 2


async def test_schedule_survives_a_worker_restart(
    demo: Demo, start_worker: Callable[..., Worker]
) -> None:
    """The decision is taken from the database: a restarted worker does not run at once."""
    manual = await demo.run()
    worker = start_worker(demo.sources, schedule_interval_seconds=INTERVAL)
    await asyncio.sleep(0.1)
    await worker.shutdown()

    assert [r.id for r in await _runs(demo.session_factory)] == [manual["id"]]


# --- The loop survives errors ----------------------------------------------


class _CrashOnceSource:
    """Raises a bug-like error on the first collection, then delegates."""

    def __init__(self, inner: OfferSource) -> None:
        self.source = inner.source
        self._inner = inner
        self.calls = 0

    async def fetch_offers(self, articles: Sequence[str]) -> list[ParsedOffer]:
        self.calls += 1
        if self.calls == 1:
            raise ZeroDivisionError
        return await self._inner.fetch_offers(articles)


async def test_worker_survives_an_error_and_leaves_no_run_running(
    demo: Demo, start_worker: Callable[..., Worker]
) -> None:
    published = await demo.published_rows()
    sources: list[OfferSource] = [_CrashOnceSource(demo.sources[0]), demo.sources[1]]
    worker = start_worker(sources)

    crashed_id = (await demo.api.post("/api/runs")).json()["id"]
    crashed = await _wait_finished(demo.session_factory, crashed_id)
    assert (crashed.status, crashed.finish_reason) == (
        RunStatus.FAILED,
        RunFinishReason.INTERNAL_ERROR,
    )
    assert await demo.published_rows() == published
    assert not worker.task.done()

    next_id = (await demo.api.post("/api/runs")).json()["id"]
    finished = await _wait_finished(demo.session_factory, next_id)
    await worker.shutdown()

    assert finished.status is RunStatus.SUCCEEDED
    assert await demo.published(PRODUCT_A) == Decimal("1240.00")


async def test_crash_whose_failure_could_not_be_written_is_recorded_later(
    demo: Demo, start_worker: Callable[..., Worker], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The database is lost while a run crashes: the run must not stay running for good."""
    attempts: list[int] = []

    async def fail_run_while_db_is_down(
        session_factory: async_sessionmaker[AsyncSession], run_id: int, reason: RunFinishReason
    ) -> None:
        attempts.append(run_id)
        if len(attempts) <= 2:
            raise ConnectionError("database is unreachable")
        await fail_run(session_factory, run_id, reason)

    monkeypatch.setattr(loop, "fail_run", fail_run_while_db_is_down)
    sources: list[OfferSource] = [_CrashOnceSource(demo.sources[0]), demo.sources[1]]
    worker = start_worker(sources)

    crashed_id = (await demo.api.post("/api/runs")).json()["id"]
    crashed = await _wait_finished(demo.session_factory, crashed_id)
    assert (crashed.status, crashed.finish_reason) == (
        RunStatus.FAILED,
        RunFinishReason.INTERNAL_ERROR,
    )
    assert attempts == [crashed_id] * 3  # in the run, then retried by the loop

    next_id = (await demo.api.post("/api/runs")).json()["id"]
    assert next_id != crashed_id
    finished = await _wait_finished(demo.session_factory, next_id)
    await worker.shutdown()
    assert finished.status is RunStatus.SUCCEEDED


async def test_worker_survives_an_error_outside_a_run(
    demo: Demo, start_worker: Callable[..., Worker]
) -> None:
    calls = 0

    def flaky_now() -> datetime:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionError("database is unreachable")
        return datetime.now(UTC)

    worker = start_worker(demo.sources, schedule_interval_seconds=INTERVAL, now=flaky_now)

    run = await _wait_finished(demo.session_factory, 1)
    await worker.shutdown()
    assert (run.trigger, run.status) == (RunTrigger.SCHEDULE, RunStatus.SUCCEEDED)
    assert calls > 1


# --- Logs ------------------------------------------------------------------


async def test_every_worker_log_line_is_json_with_its_run_source_and_product(
    demo: Demo, start_worker: Callable[..., Worker]
) -> None:
    source_logger = logging.getLogger("tests.source")

    class _LoggingSource:
        """A source that logs from inside the run, as httpx does."""

        def __init__(self, inner: OfferSource) -> None:
            self.source = inner.source
            self._inner = inner

        async def fetch_offers(self, articles: Sequence[str]) -> list[ParsedOffer]:
            source_logger.warning("collecting %s articles", len(articles))
            return await self._inner.fetch_offers(articles)

    with capture_json_logs() as logs:
        stale = (await demo.api.post("/api/runs")).json()["id"]
        await claim_next_run(demo.session_factory)
        worker = start_worker([_LoggingSource(demo.sources[0]), demo.sources[1]])
        await _wait_finished(demo.session_factory, stale)
        fresh = (await demo.api.post("/api/runs")).json()["id"]
        await _wait_finished(demo.session_factory, fresh)
        await worker.shutdown()

    records = logs.records  # every line is one JSON object
    assert records
    assert all("run_id" in r for r in records)
    # The recovered run, the new run and its outcome.
    assert any(r["run_id"] == stale and "interrupted" in r["event"] for r in records)
    assert any(r["run_id"] == fresh and r["event"] == "run started" for r in records)
    finished = [r for r in records if r["event"] == "run finished"]
    assert [(r["run_id"], r["status"]) for r in finished] == [(fresh, "succeeded")]
    # Lines from inside a source (our own, the parsers' and httpx's) carry run and source.
    # (httpx lines of the test's own API client are not part of a run.)
    from_source = [
        r
        for r in records
        if r["logger"] in {"tests.source", "httpx", "app.parsers"} and "api.test" not in r["event"]
    ]
    assert {r["logger"] for r in from_source} == {"tests.source", "httpx", "app.parsers"}
    assert all(
        r["run_id"] == fresh and r["source"] in {"html_catalog", "json_api"} for r in from_source
    )
    assert {r["source"] for r in from_source if r["logger"] == "httpx"} == {
        "html_catalog",
        "json_api",
    }
    assert any(r["logger"] == "tests.source" and r["source"] == "html_catalog" for r in records)
    # One line per calculated product with the run and the product.
    calculated = [r for r in records if r["event"] == "price calculated"]
    async with demo.session_factory() as session:
        product_ids = set(await session.scalars(select(Product.id)))
    assert {r["product_id"] for r in calculated} == product_ids
    assert all(r["run_id"] == fresh and "source" not in r for r in calculated)
    # Lines that do not belong to a run say so explicitly.
    assert any(r["run_id"] is None and r["event"] == "worker started" for r in records)
