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
        if number == 2:
            raise RuntimeError("database is unreachable")
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
        "iteration 3: touched=True",  # after iteration 2, although it raised
    ]
    assert heartbeat.is_fresh(path, 60)  # after the last iteration too


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
