"""The demo panel (``plan.md``, "Источники и парсеры": modes switched over HTTP;
"Сценарий показа": the reset command) and ``/health``, on PostgreSQL."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import httpx
import pytest
from fake_sites.modes import Mode
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.main import create_app
from app.api.schemas import DemoMode
from app.db.enums import Source
from app.db.models import Calculation, Offer, Run
from app.db.runs import claim_next_run, recover_interrupted_runs
from app.db.seed_data import PRODUCT_A, SEED_PRODUCTS
from app.db.session import create_session_factory
from tests.integration.demo import Demo

pytestmark = pytest.mark.integration

NORMAL = {"mode": "normal", "mode_label": "норма", "retry_after": 60}


def test_demo_modes_are_the_modes_of_fake_sites() -> None:
    assert [m.value for m in DemoMode] == [m.value for m in Mode]


async def test_get_modes_reads_fake_sites(demo: Demo) -> None:
    response = await demo.api.get("/api/demo/modes")
    assert response.status_code == 200, response.text
    assert response.json() == {
        "modes": [
            {"source": "html_catalog", "source_label": "HTML-каталог", **NORMAL},
            {"source": "json_api", "source_label": "JSON API", **NORMAL},
        ]
    }


async def test_put_mode_switches_the_source_the_parsers_read(demo: Demo) -> None:
    response = await demo.api.put("/api/demo/modes/html_catalog", json={"mode": "markup_changed"})
    assert response.status_code == 200, response.text
    assert response.json() == {
        "source": "html_catalog",
        "source_label": "HTML-каталог",
        "mode": "markup_changed",
        "mode_label": "изменилась разметка",
        "retry_after": 60,
    }
    response = await demo.api.put(
        "/api/demo/modes/json_api", json={"mode": "blocked", "retry_after": 5}
    )
    assert response.json()["retry_after"] == 5
    control = (await demo.control.get("/control/modes")).json()
    assert control["html_catalog"]["mode"] == "markup_changed"
    assert control["json_api"] == {"mode": "blocked", "retry_after": 5}

    # The next run sees the switched modes at once.
    run = await demo.run()
    assert [(s["source"], s["error_kind"]) for s in run["sources"]] == [
        ("html_catalog", "structure"),
        ("json_api", "blocked"),
    ]


async def test_put_mode_validates_input(demo: Demo) -> None:
    assert (await demo.api.put("/api/demo/modes/html", json={"mode": "normal"})).status_code == 422
    bad_mode = await demo.api.put("/api/demo/modes/json_api", json={"mode": "broken"})
    assert bad_mode.status_code == 422
    bad_retry = await demo.api.put(
        "/api/demo/modes/json_api", json={"mode": "blocked", "retry_after": -1}
    )
    assert bad_retry.status_code == 422


def _unreachable(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=request)


@pytest.fixture
async def api_without_fake_sites(
    session_factory: async_sessionmaker[AsyncSession], seeded: None
) -> AsyncIterator[httpx.AsyncClient]:
    application = create_app(
        session_factory, fake_sites_transport=httpx.MockTransport(_unreachable)
    )
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="http://api.test") as client:
        yield client
    await application.state.fake_sites.aclose()


async def test_unreachable_fake_sites_is_502(api_without_fake_sites: httpx.AsyncClient) -> None:
    api = api_without_fake_sites
    for response in (
        await api.get("/api/demo/modes"),
        await api.put("/api/demo/modes/html_catalog", json={"mode": "normal"}),
    ):
        assert response.status_code == 502
        assert response.json() == {"detail": "fake-sites недоступен: ConnectError"}


async def test_fake_sites_error_status_is_502(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    application = create_app(
        session_factory,
        fake_sites_transport=httpx.MockTransport(lambda request: httpx.Response(500)),
    )
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="http://api.test") as api:
        response = await api.get("/api/demo/modes")
    await application.state.fake_sites.aclose()
    assert response.status_code == 502
    assert response.json() == {"detail": "fake-sites ответил HTTP 500"}


# --- Reset -------------------------------------------------------------------


async def _count(session_factory: async_sessionmaker[AsyncSession], model: type) -> int:
    async with session_factory() as session:
        return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def test_reset_returns_the_seed_state_and_normal_modes(demo: Demo) -> None:
    seed_rows = await demo.published_rows()
    await demo.run()
    await demo.set_mode(Source.HTML_CATALOG, "markup_changed")
    await demo.set_mode(Source.JSON_API, "price_spike")
    await demo.run()
    assert await demo.published_rows() != seed_rows

    response = await demo.api.post("/api/demo/reset")
    assert response.status_code == 200, response.text
    assert response.json() == {
        "products": len(SEED_PRODUCTS),
        "published_prices": len(seed_rows),
        "modes": [
            {"source": "html_catalog", "source_label": "HTML-каталог", **NORMAL},
            {"source": "json_api", "source_label": "JSON API", **NORMAL},
        ],
    }
    # Byte for byte the published prices of the seed; no runs, offers or calculations.
    assert await demo.published_rows() == seed_rows
    for model in (Run, Offer, Calculation):
        assert await _count(demo.session_factory, model) == 0
    control = (await demo.control.get("/control/modes")).json()
    assert {source: state["mode"] for source, state in control.items()} == {
        "html_catalog": "normal",
        "json_api": "normal",
    }
    # Exactly the state of a fresh seed: the first run publishes A again, ids from 1.
    run = await demo.run()
    assert run["id"] == 1
    assert (await demo.calculation(run, PRODUCT_A)).recommended_price == 1240
    status = (await demo.api.get("/api/status")).json()
    assert status["last_finished_run"]["id"] == 1


@pytest.mark.parametrize("claim", [False, True], ids=["queued", "running"])
async def test_reset_with_an_active_run_is_409(demo: Demo, claim: bool) -> None:
    await demo.run()
    await demo.set_mode(Source.HTML_CATALOG, "blocked")
    published = await demo.published_rows()
    active = (await demo.api.post("/api/runs")).json()["id"]
    if claim:
        assert await claim_next_run(demo.session_factory) == active

    response = await demo.api.post("/api/demo/reset")
    assert response.status_code == 409
    assert str(active) in response.json()["detail"]
    # Nothing was reset: runs, prices and modes are as they were.
    assert await _count(demo.session_factory, Run) == 2
    assert await demo.published_rows() == published
    control = (await demo.control.get("/control/modes")).json()
    assert control["html_catalog"]["mode"] == "blocked"


async def test_reset_with_unreachable_fake_sites_resets_nothing(
    api_without_fake_sites: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    run_id = (await api_without_fake_sites.post("/api/runs")).json()["id"]
    await claim_next_run(session_factory)
    await recover_interrupted_runs(session_factory)

    response = await api_without_fake_sites.post("/api/demo/reset")
    assert response.status_code == 502
    assert response.json() == {"detail": "Ничего не сброшено: fake-sites недоступен: ConnectError"}
    async with session_factory() as session:
        assert list(await session.scalars(select(Run.id))) == [run_id]


class _DuringModesReset(httpx.AsyncBaseTransport):
    """fake-sites, but ``hook`` runs when the API asks it to reset the modes."""

    def __init__(
        self, inner: httpx.AsyncBaseTransport, hook: Callable[[], Awaitable[None]]
    ) -> None:
        self._inner = inner
        self._hook = hook

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/control/reset":
            await self._hook()
        return await self._inner.handle_async_request(request)


@asynccontextmanager
async def _api_with(
    demo: Demo, transport: httpx.AsyncBaseTransport
) -> AsyncIterator[httpx.AsyncClient]:
    application = create_app(demo.session_factory, fake_sites_transport=transport)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://api.test"
        ) as client:
            yield client
    finally:
        await application.state.fake_sites.aclose()


async def test_run_executed_while_modes_are_reset_is_wiped_by_the_reset(
    demo: Demo, fake_sites_transport: httpx.ASGITransport
) -> None:
    """A run with the old modes that slips in during the reset never survives a 200."""
    seed_rows = await demo.published_rows()
    await demo.set_mode(Source.HTML_CATALOG, "markup_changed")
    executed: list[str] = []

    async def run_with_old_modes() -> None:
        executed.append((await demo.run())["status"])

    async with _api_with(demo, _DuringModesReset(fake_sites_transport, run_with_old_modes)) as api:
        response = await api.post("/api/demo/reset")

    assert executed == ["partial"]
    assert response.status_code == 200, response.text
    assert await _count(demo.session_factory, Run) == 0
    assert await _count(demo.session_factory, Calculation) == 0
    assert await demo.published_rows() == seed_rows
    control = (await demo.control.get("/control/modes")).json()
    assert {state["mode"] for state in control.values()} == {"normal"}


async def test_run_queued_while_modes_are_reset_is_409_and_keeps_the_database(
    demo: Demo, fake_sites_transport: httpx.ASGITransport
) -> None:
    await demo.run()
    await demo.set_mode(Source.HTML_CATALOG, "markup_changed")
    published = await demo.published_rows()
    queued: list[int] = []

    async def queue_a_run() -> None:
        queued.append((await demo.api.post("/api/runs")).json()["id"])

    async with _api_with(demo, _DuringModesReset(fake_sites_transport, queue_a_run)) as api:
        response = await api.post("/api/demo/reset")

    assert response.status_code == 409
    assert response.json()["detail"] == (
        f"Прогон {queued[0]} поставлен во время сброса: режимы источников сброшены, "
        "база — нет; повторите сброс после его завершения"
    )
    assert await _count(demo.session_factory, Run) == 2
    assert await demo.published_rows() == published
    # The queued run will collect with normal modes.
    control = (await demo.control.get("/control/modes")).json()
    assert {state["mode"] for state in control.values()} == {"normal"}


# --- /health -------------------------------------------------------------------


async def test_health_is_ok_with_a_live_database(api: httpx.AsyncClient) -> None:
    response = await api.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_health_is_503_without_a_database() -> None:
    # Nothing listens on port 1: the connection is refused at once.
    engine = create_async_engine(
        "postgresql+asyncpg://procenka:procenka@127.0.0.1:1/procenka",
        connect_args={"timeout": 2},
    )
    application = create_app(create_session_factory(engine))
    transport = httpx.ASGITransport(app=application)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://api.test") as api:
            response = await api.get("/health")
    finally:
        await application.state.fake_sites.aclose()
        await engine.dispose()
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
