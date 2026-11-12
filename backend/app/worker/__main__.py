"""``python -m app.worker``: the worker process."""

import asyncio
import logging

from app.config import get_settings
from app.db.session import create_engine, create_session_factory
from app.parsers.registry import build_sources
from app.worker.loop import run_worker


async def _main() -> None:
    settings = get_settings()
    engine = create_engine(settings.database_url)
    try:
        await run_worker(create_session_factory(engine), build_sources(settings), settings)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(_main())
