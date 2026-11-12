"""Runs API: one active run at a time; repeated starts return the same run."""

import asyncio

import httpx
import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.enums import RunStatus, RunTrigger
from app.db.models import Run
from app.db.runs import claim_next_run, enqueue_run

pytestmark = pytest.mark.integration


async def test_repeated_start_returns_the_same_run(
    session_factory: async_sessionmaker[AsyncSession], api: httpx.AsyncClient
) -> None:
    first = await api.post("/api/runs")
    second = await api.post("/api/runs")

    assert first.status_code == 201
    assert first.json() == {"id": first.json()["id"], "status": "queued", "created": True}
    assert second.status_code == 200
    assert second.json() == {"id": first.json()["id"], "status": "queued", "created": False}

    # Still the same run once the worker has taken it.
    assert await claim_next_run(session_factory) == first.json()["id"]
    third = await api.post("/api/runs")
    assert third.status_code == 200
    assert third.json() == {"id": first.json()["id"], "status": "running", "created": False}

    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Run)) == 1


async def test_concurrent_starts_create_one_run(
    session_factory: async_sessionmaker[AsyncSession], api: httpx.AsyncClient
) -> None:
    responses = await asyncio.gather(*(api.post("/api/runs") for _ in range(8)))

    ids = {response.json()["id"] for response in responses}
    assert len(ids) == 1
    assert sorted(response.status_code for response in responses) == [200] * 7 + [201]
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Run)) == 1


async def test_new_run_after_the_active_one_finished(
    session_factory: async_sessionmaker[AsyncSession], api: httpx.AsyncClient
) -> None:
    first_id = (await api.post("/api/runs")).json()["id"]
    await claim_next_run(session_factory)
    async with session_factory() as session, session.begin():
        await session.execute(
            update(Run)
            .where(Run.id == first_id)
            .values(status=RunStatus.SUCCEEDED, finished_at=func.now())
        )

    response = await api.post("/api/runs")

    assert response.status_code == 201
    assert response.json()["id"] != first_id
    assert response.json()["created"] is True


async def test_get_run(api: httpx.AsyncClient) -> None:
    run_id = (await api.post("/api/runs")).json()["id"]

    body = (await api.get(f"/api/runs/{run_id}")).json()

    assert body["id"] == run_id
    assert body["status"] == "queued"
    assert body["trigger"] == "manual"
    assert body["started_at"] is None
    assert body["finished_at"] is None
    assert body["sources"] == []


async def test_unknown_run_is_404(api: httpx.AsyncClient) -> None:
    response = await api.get("/api/runs/999999")
    assert response.status_code == 404


async def test_claim_takes_the_queued_run_once(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    assert await claim_next_run(session_factory) is None
    enqueued = await enqueue_run(session_factory, RunTrigger.SCHEDULE)

    assert await claim_next_run(session_factory) == enqueued.id
    assert await claim_next_run(session_factory) is None
    async with session_factory() as session:
        run = await session.get(Run, enqueued.id)
        assert run is not None
        assert run.status is RunStatus.RUNNING
        assert run.started_at is not None


async def test_claim_skips_a_locked_run(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    enqueued = await enqueue_run(session_factory, RunTrigger.MANUAL)
    async with session_factory() as session, session.begin():
        await session.execute(select(Run).where(Run.id == enqueued.id).with_for_update())
        # Another worker holds the row: SKIP LOCKED returns nothing instead of waiting.
        assert await asyncio.wait_for(claim_next_run(session_factory), timeout=5) is None
    assert await claim_next_run(session_factory) == enqueued.id


@pytest.mark.usefixtures("seeded")
async def test_published_prices_of_the_seed(api: httpx.AsyncClient) -> None:
    body = (await api.get("/api/published-prices")).json()

    prices = body["prices"]
    assert len(prices) == 24  # 25 products, one without a published price
    first = prices[0]
    assert set(first) == {
        "product_id",
        "brand",
        "article",
        "name",
        "price",
        "published_at",
        "calculation_id",
    }
    assert (first["brand"], first["article"], first["price"]) == ("Bosch", "0986452041", "1260.00")
    assert first["calculation_id"] is None
    assert first["published_at"].startswith("2026-11-01T09:00:00")
