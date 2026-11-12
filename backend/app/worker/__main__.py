"""``python -m app.worker``: the worker process.

SIGTERM or SIGINT stops the worker between iterations; a second signal
cancels the run in progress at once (it stays ``running`` and the next start
recovers it as interrupted). The same happens when the process is killed.
"""

import asyncio
import contextlib
import signal

from app.config import get_settings
from app.db.session import create_engine, create_session_factory
from app.logs import WORKER_LOG_KEYS, configure_logging, get_logger
from app.parsers.registry import build_sources
from app.worker.loop import run_worker

logger = get_logger("app.worker")


class StopSignals:
    """First signal: stop between iterations. Second: cancel the main task."""

    def __init__(self, stop: asyncio.Event, task: asyncio.Task[None]) -> None:
        self._stop = stop
        self._task = task

    def __call__(self, signum: int) -> None:
        if not self._stop.is_set():
            logger.info("signal received: stopping after the current iteration", signal=signum)
            self._stop.set()
        else:
            logger.warning("signal received again: cancelling the current run", signal=signum)
            self._task.cancel()


def install_stop_signals(stop: asyncio.Event, task: asyncio.Task[None]) -> None:
    loop = asyncio.get_running_loop()
    handler = StopSignals(stop, task)
    for signum in (signal.SIGTERM, signal.SIGINT):
        # Windows event loops cannot do this; Ctrl+C still interrupts there.
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(signum, handler, signum)


async def _main() -> None:
    settings = get_settings()
    engine = create_engine(settings.database_url)
    stop = asyncio.Event()
    try:
        task = asyncio.create_task(
            run_worker(create_session_factory(engine), build_sources(settings), settings, stop=stop)
        )
        install_stop_signals(stop, task)
        try:
            await task
        except asyncio.CancelledError:
            logger.warning("worker cancelled; a run left running is recovered at the next start")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    configure_logging("worker", always=WORKER_LOG_KEYS)
    asyncio.run(_main())
