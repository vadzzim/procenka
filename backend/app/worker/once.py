"""``python -m app.worker.once``: queue a manual run and execute it right here.

For debugging without the worker process. If a run is already queued, the
oldest queued run is executed; if one is running elsewhere, nothing is done.
"""

import asyncio
import sys

from app.config import get_settings
from app.db.enums import RunStatus, RunTrigger
from app.db.runs import enqueue_run
from app.db.session import create_engine, create_session_factory
from app.parsers.registry import build_sources
from app.worker.loop import process_next_run


async def _main() -> int:
    settings = get_settings()
    engine = create_engine(settings.database_url)
    try:
        session_factory = create_session_factory(engine)
        enqueued = await enqueue_run(session_factory, RunTrigger.MANUAL)
        if enqueued.status is RunStatus.RUNNING:
            print(f"run {enqueued.id} is already running elsewhere")
            return 1
        outcome = await process_next_run(session_factory, build_sources(settings), settings)
    finally:
        await engine.dispose()
    if outcome is None:
        print("no queued run")
        return 1
    print(
        f"run {outcome.run_id}: {outcome.status.value}, "
        f"{outcome.calculations} calculations, {outcome.published} published"
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
