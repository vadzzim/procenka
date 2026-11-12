"""The worker heartbeat behind the compose healthcheck."""

import asyncio
import os
import time
from pathlib import Path
from typing import Any

import pytest

from app.config import Settings
from app.worker import heartbeat, loop


def test_beat_creates_the_file_and_refreshes_it(tmp_path: Path) -> None:
    path = tmp_path / "heartbeat"
    assert not heartbeat.is_fresh(path, 120)

    heartbeat.beat(path)
    assert heartbeat.is_fresh(path, 120)

    old = time.time() - 600
    os.utime(path, (old, old))
    assert not heartbeat.is_fresh(path, 120)
    heartbeat.beat(path)
    assert heartbeat.is_fresh(path, 120)


def test_freshness_is_bounded_by_max_age(tmp_path: Path) -> None:
    path = tmp_path / "heartbeat"
    heartbeat.beat(path)
    modified = path.stat().st_mtime
    assert heartbeat.is_fresh(path, 120, now=modified + 120)
    assert not heartbeat.is_fresh(path, 120, now=modified + 120.5)


def test_clear_removes_the_file_and_ignores_a_missing_one(tmp_path: Path) -> None:
    path = tmp_path / "heartbeat"
    heartbeat.clear(path)
    heartbeat.beat(path)
    heartbeat.clear(path)
    assert not path.exists()


def test_healthcheck_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "heartbeat"
    assert heartbeat.main([str(path), "--max-age", "120"]) == 1
    assert "missing or older" in capsys.readouterr().out

    heartbeat.beat(path)
    assert heartbeat.main([str(path), "--max-age", "120"]) == 0

    old = time.time() - 121
    os.utime(path, (old, old))
    assert heartbeat.main([str(path), "--max-age", "120"]) == 1


async def test_worker_touches_the_heartbeat_after_recovery_and_every_iteration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "heartbeat"
    # A file left by the previous process (a restarted container keeps /tmp).
    heartbeat.beat(path)
    seen: list[str] = []
    stop = asyncio.Event()

    async def fake_recover(_: Any) -> list[int]:
        seen.append(f"recover: exists={path.exists()}")
        return []

    async def fake_iteration(*_: Any) -> None:
        number = sum(event.startswith("iteration") for event in seen) + 1
        # Make every touch observable: the previous one is pushed into the past.
        mtime = path.stat().st_mtime
        seen.append(f"iteration {number}: touched={mtime > time.time() - 60}")
        old = time.time() - 600
        os.utime(path, (old, old))
        if number == 3:
            stop.set()

    monkeypatch.setattr(loop, "recover", fake_recover)
    monkeypatch.setattr(loop, "worker_iteration", fake_iteration)
    settings = Settings(worker_poll_seconds=0.01, worker_heartbeat_file=path)

    await loop.run_worker(None, [], settings, stop=stop)  # type: ignore[arg-type]

    assert seen == [
        # Stale heartbeat removed before the recovery: not healthy until it is done.
        "recover: exists=False",
        "iteration 1: touched=True",  # touched after the recovery
        "iteration 2: touched=True",  # after iteration 1
        "iteration 3: touched=True",  # after iteration 2
    ]
    assert heartbeat.is_fresh(path, 60)  # after the last iteration too


async def test_heartbeat_is_not_touched_while_the_database_is_unreachable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only an iteration that got through to the database touches the heartbeat.

    Iterations: 1 ok; 2 and 3 fail as with an unreachable database (OSError from
    asyncpg, a crashed run whose failure could not be written); 4 is a crashed
    run whose failure was written (the database answered); 5 ok.
    """
    path = tmp_path / "heartbeat"
    seen: list[str] = []
    stop = asyncio.Event()

    async def fake_recover(_: Any) -> list[int]:
        return []

    async def fake_record_crash(_: Any, run_id: int) -> None:
        seen.append(f"crash of run {run_id} recorded")

    async def fake_iteration(*_: Any) -> None:
        number = sum(event.startswith("iteration") for event in seen) + 1
        mtime = path.stat().st_mtime
        seen.append(f"iteration {number}: touched={mtime > time.time() - 60}")
        old = time.time() - 600
        os.utime(path, (old, old))
        if number == 2:
            raise ConnectionRefusedError("database is unreachable")
        if number == 3:
            raise loop.RunCrashedError(7, recorded=False)
        if number == 4:
            raise loop.RunCrashedError(8, recorded=True)
        if number == 5:
            stop.set()

    monkeypatch.setattr(loop, "recover", fake_recover)
    monkeypatch.setattr(loop, "record_crash", fake_record_crash)
    monkeypatch.setattr(loop, "worker_iteration", fake_iteration)
    settings = Settings(worker_poll_seconds=0.01, worker_heartbeat_file=path)

    await loop.run_worker(None, [], settings, stop=stop)  # type: ignore[arg-type]

    assert seen == [
        "iteration 1: touched=True",  # after the recovery
        "iteration 2: touched=True",  # after iteration 1
        "iteration 3: touched=False",  # iteration 2 could not reach the database
        "crash of run 7 recorded",
        "iteration 4: touched=False",  # iteration 3: its crash could not be written
        "iteration 5: touched=True",  # iteration 4: the crash was written
    ]
    assert heartbeat.is_fresh(path, 60)


async def test_worker_becomes_unhealthy_when_the_database_stays_unreachable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "heartbeat"
    stop = asyncio.Event()
    iterations = 0

    async def fake_recover(_: Any) -> list[int]:
        return []

    async def fake_iteration(*_: Any) -> None:
        nonlocal iterations
        iterations += 1
        if iterations == 1:
            # The last good iteration was long ago.
            old = time.time() - 600
            os.utime(path, (old, old))
            raise ConnectionRefusedError("database is unreachable")
        if iterations < 4:
            raise ConnectionRefusedError("database is unreachable")
        stop.set()
        raise ConnectionRefusedError("database is unreachable")

    monkeypatch.setattr(loop, "recover", fake_recover)
    monkeypatch.setattr(loop, "worker_iteration", fake_iteration)
    settings = Settings(worker_poll_seconds=0.01, worker_heartbeat_file=path)

    await loop.run_worker(None, [], settings, stop=stop)  # type: ignore[arg-type]

    assert iterations == 4
    assert heartbeat.main([str(path), "--max-age", "120"]) == 1


async def test_worker_without_a_heartbeat_file_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stop = asyncio.Event()

    async def fake_recover(_: Any) -> list[int]:
        return []

    async def fake_iteration(*_: Any) -> None:
        stop.set()

    monkeypatch.setattr(loop, "recover", fake_recover)
    monkeypatch.setattr(loop, "worker_iteration", fake_iteration)
    monkeypatch.chdir(tmp_path)

    await loop.run_worker(None, [], Settings(worker_poll_seconds=0.01), stop=stop)  # type: ignore[arg-type]

    assert os.listdir(tmp_path) == []
