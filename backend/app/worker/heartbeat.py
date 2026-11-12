"""Liveness of the worker loop for the docker compose healthcheck.

The worker removes a stale heartbeat file at start, touches it once the
recovery of interrupted runs is done and after every loop iteration that got
through to the database (``WORKER_HEARTBEAT_FILE``; unset — no file): with the
database unreachable the file ages and the worker becomes unhealthy. The healthcheck
``python -m app.worker.heartbeat <file> --max-age <seconds>`` exits 0 while the
file is fresh and 1 when it is missing or older than ``max_age``.

``max_age`` must exceed the longest run: during a run the loop does not
iterate (a run is bounded by the per-source time budget, 30 s in the demo).
The module imports nothing from the application, so the check stays cheap.
"""

import argparse
import sys
import time
from pathlib import Path


def beat(path: Path) -> None:
    """Create the file or update its modification time to now."""
    path.touch()


def clear(path: Path) -> None:
    """Remove a heartbeat left by a previous process (the container's /tmp survives a restart)."""
    path.unlink(missing_ok=True)


def is_fresh(path: Path, max_age_seconds: float, *, now: float | None = None) -> bool:
    """True if the file exists and was touched at most ``max_age_seconds`` ago."""
    try:
        modified = path.stat().st_mtime
    except FileNotFoundError:
        return False
    current = time.time() if now is None else now
    return current - modified <= max_age_seconds


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Exit 0 if the worker heartbeat is fresh.")
    parser.add_argument("path", type=Path)
    parser.add_argument("--max-age", type=float, default=120.0, help="seconds")
    args = parser.parse_args(argv)
    if is_fresh(args.path, args.max_age):
        return 0
    print(f"worker heartbeat {args.path} is missing or older than {args.max_age} s")
    return 1


if __name__ == "__main__":
    sys.exit(main())
