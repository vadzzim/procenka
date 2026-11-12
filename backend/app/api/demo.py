"""The demo panel: failure modes of the synthetic sources and the reset.

Modes are proxied to the control endpoints of fake-sites; a failure to reach
fake-sites is a 502 with a message the manager can read.
"""

from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, status

from app.api.deps import FakeSites, SessionFactory
from app.api.schemas import (
    DEMO_MODE_LABELS,
    DemoMode,
    DemoModesOut,
    DemoResetOut,
    ErrorOut,
    ModeChange,
    SourceModeOut,
)
from app.db.enums import SOURCE_LABELS, Source
from app.db.reset import ActiveRunError, active_run_id, reset_when_idle
from app.logs import get_logger

router = APIRouter(prefix="/api/demo")
logger = get_logger("app.api")

FAKE_SITES_ERRORS: dict[int | str, dict[str, Any]] = {
    502: {"model": ErrorOut, "description": "fake-sites недоступен или ответил ошибкой"}
}


def _mode_out(source: Source, state: dict[str, Any]) -> SourceModeOut:
    mode = DemoMode(state["mode"])
    return SourceModeOut(
        source=source,
        source_label=SOURCE_LABELS[source],
        mode=mode,
        mode_label=DEMO_MODE_LABELS[mode],
        retry_after=int(state["retry_after"]),
    )


def _modes_out(payload: dict[str, Any]) -> list[SourceModeOut]:
    return [_mode_out(source, payload[source.value]) for source in Source]


async def _call(client: httpx.AsyncClient, method: str, path: str, **kwargs: Any) -> Any:
    """A control request to fake-sites; any failure is a 502 with a readable reason."""
    try:
        response = await client.request(method, path, **kwargs)
    except httpx.HTTPError as exc:
        logger.warning("fake-sites unreachable", path=path, error=repr(exc))
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail=f"fake-sites недоступен: {type(exc).__name__}",
        ) from exc
    if response.status_code != 200:
        logger.warning("fake-sites error", path=path, status_code=response.status_code)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail=f"fake-sites ответил HTTP {response.status_code}",
        )
    try:
        return response.json()
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, detail="fake-sites вернул не JSON"
        ) from exc


@router.get("/modes", responses=FAKE_SITES_ERRORS)
async def get_modes(fake_sites: FakeSites) -> DemoModesOut:
    """Текущий режим каждого источника."""
    return DemoModesOut(modes=_modes_out(await _call(fake_sites, "GET", "/control/modes")))


@router.put("/modes/{source}", responses=FAKE_SITES_ERRORS)
async def put_mode(source: Source, change: ModeChange, fake_sites: FakeSites) -> SourceModeOut:
    """Переключить режим источника; действует со следующего запроса парсера."""
    payload = await _call(
        fake_sites,
        "PUT",
        f"/control/modes/{source.value}",
        json=change.model_dump(mode="json", exclude_none=True),
    )
    logger.info("source mode changed", source=source.value, mode=change.mode.value)
    return _mode_out(source, payload)


@router.post(
    "/reset",
    responses={
        409: {"model": ErrorOut, "description": "Есть прогон в очереди или в работе"},
        **FAKE_SITES_ERRORS,
    },
)
async def reset_demo(session_factory: SessionFactory, fake_sites: FakeSites) -> DemoResetOut:
    """Вернуть демо в исходное состояние: оба источника — normal, БД — как после seed.

    Порядок: проверка активного прогона → сброс режимов fake-sites → в одной
    транзакции под блокировкой ``runs`` повторная проверка и сброс БД. Режимы
    сбрасываются первыми, поэтому прогон, поставленный во время сброса, либо
    стирается сбросом БД, либо (если он ещё в очереди или выполняется) даёт 409,
    но никогда не выполняется со старыми режимами после ответа 200.
    """
    async with session_factory() as session:
        active = await active_run_id(session)
    if active is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Прогон {active} в очереди или выполняется: сброс после его завершения",
        )
    # No database transaction is open during the HTTP request.
    try:
        modes = _modes_out(await _call(fake_sites, "POST", "/control/reset"))
    except HTTPException as exc:
        raise HTTPException(exc.status_code, detail=f"Ничего не сброшено: {exc.detail}") from exc
    try:
        async with session_factory() as session, session.begin():
            report = await reset_when_idle(session)
    except ActiveRunError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=(
                f"Прогон {exc.run_id} поставлен во время сброса: режимы источников сброшены, "
                "база — нет; повторите сброс после его завершения"
            ),
        ) from exc
    logger.info("demo reset", products=report.products_inserted)
    return DemoResetOut(
        products=report.products_inserted,
        published_prices=report.published_prices_inserted,
        modes=modes,
    )
