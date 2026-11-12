"""Capturing JSON log lines in tests without touching pytest's own handlers."""

import io
import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from app.logs import WORKER_LOG_KEYS, json_formatter


class JsonLines:
    def __init__(self, stream: io.StringIO) -> None:
        self._stream = stream

    @property
    def raw(self) -> list[str]:
        return self._stream.getvalue().splitlines()

    @property
    def records(self) -> list[dict[str, Any]]:
        """Every line parsed as JSON (fails if a line is not one JSON object)."""
        records = [json.loads(line) for line in self.raw]
        assert all(isinstance(record, dict) for record in records)
        return records


@contextmanager
def capture_json_logs(service: str = "worker", level: int = logging.INFO) -> Iterator[JsonLines]:
    """The worker's JSON formatter on the root logger for the duration of the block."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(json_formatter(service, always=WORKER_LOG_KEYS))
    root = logging.getLogger()
    previous = root.level
    root.addHandler(handler)
    root.setLevel(level)
    try:
        yield JsonLines(stream)
    finally:
        root.removeHandler(handler)
        root.setLevel(previous)
