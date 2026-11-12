"""The worker process: stop signals; JSON log lines with run_id, source and product_id."""

import asyncio
import io
import json
import logging

from app.logs import bind_product_id, bind_run_id, bind_source, configure_logging, get_logger
from app.worker.__main__ import StopSignals
from tests.logs import capture_json_logs


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


async def test_log_lines_are_json_with_the_bound_context_also_in_tasks() -> None:
    worker = get_logger("app.worker")
    library = logging.getLogger("httpx")
    with capture_json_logs() as logs:
        worker.info("idle")
        with bind_run_id(42):
            worker.info("run started")

            async def inside_task() -> None:
                with bind_source("html_catalog"):
                    library.info("HTTP Request: GET %s", "/html/search")
                with bind_product_id(7):
                    worker.info("price calculated", state="published")

            await asyncio.create_task(inside_task())
            worker.info("after the task")
        worker.info("after the run")

    records = logs.records
    events = [(r["event"], r["run_id"], r.get("source"), r.get("product_id")) for r in records]
    assert events == [
        ("idle", None, None, None),
        ("run started", 42, None, None),
        ("HTTP Request: GET /html/search", 42, "html_catalog", None),
        ("price calculated", 42, None, 7),
        ("after the task", 42, None, None),
        ("after the run", None, None, None),
    ]
    first = records[0]
    assert (first["level"], first["logger"], first["service"]) == ("info", "app.worker", "worker")
    assert first["timestamp"].endswith("Z")
    assert records[3]["state"] == "published"
    assert records[2]["logger"] == "httpx"


def test_a_traceback_is_a_field_of_its_json_line() -> None:
    worker = get_logger("app.worker")
    with capture_json_logs() as logs, bind_run_id(7):
        try:
            raise ZeroDivisionError("boom")
        except ZeroDivisionError:
            worker.exception("run failed" + chr(10) + "second line")

    assert len(logs.raw) == 1
    (record,) = logs.records
    assert record["run_id"] == 7
    assert record["level"] == "error"
    assert record["event"] == "run failed" + chr(10) + "second line"
    assert "ZeroDivisionError: boom" in record["exception"]


def test_configure_logging_routes_uvicorn_and_skips_health_access_lines() -> None:
    root = logging.getLogger()
    saved = (root.handlers[:], root.level)
    uvicorn_loggers = [logging.getLogger(n) for n in ("uvicorn", "uvicorn.error", "uvicorn.access")]
    saved_uvicorn = [(lg.handlers[:], lg.propagate, lg.filters[:]) for lg in uvicorn_loggers]
    stream = io.StringIO()
    try:
        configure_logging("api", stream=stream)
        access = logging.getLogger("uvicorn.access")
        fmt = '%s - "%s %s HTTP/%s" %d'
        access.info(fmt, "127.0.0.1:5000", "GET", "/health", "1.1", 200)
        access.info(fmt, "127.0.0.1:5000", "GET", "/api/products", "1.1", 200)
        logging.getLogger("uvicorn.error").info("Application startup complete.")
    finally:
        root.handlers[:] = saved[0]
        root.setLevel(saved[1])
        for lg, (handlers, propagate, filters) in zip(uvicorn_loggers, saved_uvicorn, strict=True):
            lg.handlers[:] = handlers
            lg.propagate = propagate
            lg.filters[:] = filters

    records = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert [r["event"] for r in records] == [
        '127.0.0.1:5000 - "GET /api/products HTTP/1.1" 200',
        "Application startup complete.",
    ]
    assert {r["service"] for r in records} == {"api"}
    assert "run_id" not in records[0]
