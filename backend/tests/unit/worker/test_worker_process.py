"""The worker process: stop signals and log lines with the run id."""

import asyncio
import logging
import sys

from app.worker.__main__ import StopSignals
from app.worker.logs import RunIdFilter, RunIdFormatter, bind_run_id


async def test_first_signal_stops_between_iterations_second_cancels() -> None:
    stop = asyncio.Event()

    async def forever() -> None:
        await asyncio.Event().wait()

    task = asyncio.create_task(forever())
    handler = StopSignals(stop, task)

    handler(15)
    await asyncio.sleep(0)
    assert stop.is_set()
    assert not task.done()

    handler(2)
    await asyncio.sleep(0)
    assert task.cancelled()


def _format(record: logging.LogRecord) -> str:
    RunIdFilter().filter(record)
    return RunIdFormatter().format(record)


def _record(message: str) -> logging.LogRecord:
    return logging.LogRecord("app.worker", logging.INFO, __file__, 1, message, None, None)


async def test_log_lines_carry_the_bound_run_id_also_in_tasks() -> None:
    assert " run_id=- idle" in _format(_record("idle"))
    with bind_run_id(42):
        assert " run_id=42 run started" in _format(_record("run started"))

        async def inside_task() -> str:
            return _format(_record("from a source"))

        assert " run_id=42 from a source" in await asyncio.create_task(inside_task())
    assert " run_id=- after" in _format(_record("after"))


def test_every_line_of_a_traceback_carries_the_run_id() -> None:
    try:
        raise ZeroDivisionError("boom")
    except ZeroDivisionError:
        exc_info = sys.exc_info()
    record = logging.LogRecord(
        "app.worker", logging.ERROR, __file__, 1, "run failed\nsecond line", None, exc_info
    )
    with bind_run_id(7):
        lines = _format(record).splitlines()

    assert len(lines) > 3
    assert " run_id=7 run failed" in lines[0]
    assert all(line.startswith("run_id=7 | ") for line in lines[1:])
    assert lines[-1] == "run_id=7 | ZeroDivisionError: boom"
