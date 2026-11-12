"""structlog: JSON lines on stdout for the api and the worker.

Every log line is one JSON object with ``timestamp``, ``level``, ``logger``,
``event`` and the bound context: ``run_id``, ``source`` and ``product_id``
where they apply. The context lives in context variables
(``structlog.contextvars``), so it reaches every line logged while it is bound,
including lines of tasks started inside (a source collected in a task) and
lines of standard-library loggers such as httpx and uvicorn: those go through
the same handler and the same processors.

The worker always writes ``run_id`` (``null`` outside a run), so the log of a
run can be filtered by one key. A traceback is a field of its line
(``exception``), so it never produces lines without the run id.
"""

import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TextIO

import structlog
from structlog.contextvars import bound_contextvars
from structlog.typing import EventDict, Processor, WrappedLogger

# Keys present in every line of the worker log, null when not bound.
WORKER_LOG_KEYS = ("run_id",)

# Processors shared by structlog loggers and standard-library records.
_SHARED_PROCESSORS: list[Processor] = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_log_level,
    structlog.stdlib.add_logger_name,
    structlog.processors.TimeStamper(fmt="iso", utc=True),
]

structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        *_SHARED_PROCESSORS,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.StackInfoRenderer(),
        structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
    ],
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=False,
)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.stdlib.get_logger(name)
    return logger


@contextmanager
def bind_run_id(run_id: int) -> Iterator[None]:
    with bound_contextvars(run_id=run_id):
        yield


@contextmanager
def bind_source(source: str) -> Iterator[None]:
    with bound_contextvars(source=source):
        yield


@contextmanager
def bind_product_id(product_id: int) -> Iterator[None]:
    with bound_contextvars(product_id=product_id):
        yield


class _DefaultKeys:
    """Adds ``key: None`` for every listed key missing from the line."""

    def __init__(self, keys: tuple[str, ...]) -> None:
        self._keys = keys

    def __call__(self, logger: WrappedLogger, method_name: str, event_dict: EventDict) -> EventDict:
        for key in self._keys:
            event_dict.setdefault(key, None)
        return event_dict


def _add_service(service: str) -> Processor:
    def processor(logger: WrappedLogger, method_name: str, event_dict: EventDict) -> EventDict:
        event_dict["service"] = service
        return event_dict

    return processor


def json_formatter(service: str, *, always: tuple[str, ...] = ()) -> logging.Formatter:
    """The formatter that renders every record as one JSON line."""
    return structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=_SHARED_PROCESSORS,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            _add_service(service),
            _DefaultKeys(always),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(ensure_ascii=False, default=str),
        ],
    )


class _SkipHealthAccess(logging.Filter):
    """Drops uvicorn access lines of ``/health``: compose polls it every 2 seconds."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        return not (isinstance(args, tuple) and len(args) >= 3 and args[2] == "/health")


def configure_logging(
    service: str,
    *,
    always: tuple[str, ...] = (),
    level: int = logging.INFO,
    stream: TextIO | None = None,
) -> logging.Handler:
    """Route all logging (structlog and the standard library) to JSON lines.

    ``always``: keys present in every line (``null`` if not bound). Uvicorn's
    own handlers are removed so its lines go through the same JSON handler.
    """
    handler = logging.StreamHandler(sys.stdout if stream is None else stream)
    handler.setFormatter(json_formatter(service, always=always))
    logging.basicConfig(level=level, handlers=[handler], force=True)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, _SkipHealthAccess) for f in access.filters):
        access.addFilter(_SkipHealthAccess())
    return handler
