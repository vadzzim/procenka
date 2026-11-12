"""Worker logging: every line carries the run it belongs to (``run_id=-`` if none).

The run id lives in a context variable, so records from any logger emitted
while a run is processed (sources, httpx) get it too, including those from
tasks started inside the run. structlog replaces this in step 7.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s run_id=%(run_id)s %(message)s"

_run_id: ContextVar[int | None] = ContextVar("run_id", default=None)


@contextmanager
def bind_run_id(run_id: int) -> Iterator[None]:
    token = _run_id.set(run_id)
    try:
        yield
    finally:
        _run_id.reset(token)


class RunIdFilter(logging.Filter):
    """Adds ``record.run_id``: the bound run, or ``-``."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "run_id"):
            run_id = _run_id.get()
            record.run_id = "-" if run_id is None else run_id
        return True


class RunIdFormatter(logging.Formatter):
    """``LOG_FORMAT``; continuation lines (tracebacks, multi-line messages) are
    prefixed with the run id too, so every line of the log carries it."""

    def __init__(self) -> None:
        super().__init__(LOG_FORMAT)

    def format(self, record: logging.LogRecord) -> str:
        first, *rest = super().format(record).split("\n")
        prefix = f"run_id={getattr(record, 'run_id', '-')} | "
        return "\n".join([first, *(prefix + line for line in rest)])


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(RunIdFormatter())
    handler.addFilter(RunIdFilter())
    logging.basicConfig(level=level, handlers=[handler], force=True)
