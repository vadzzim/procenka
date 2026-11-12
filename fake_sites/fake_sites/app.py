"""The fake-sites application: synthetic competitor sources of the demo.

Run: ``uvicorn fake_sites.app:app --port 8001``.

* ``GET /html/search?article=...`` — the HTML catalog (``html_catalog``);
* ``GET /json/`` and ``GET /json/api/offers?article=...`` — the storefront
  page that issues the session cookie and the API behind it (``json_api``);
* ``GET /control/modes``, ``PUT /control/modes/{source}``,
  ``POST /control/reset`` — failure modes (see ``fake_sites.modes``).

Modes and sessions live in memory of one application instance.
"""

from typing import Annotated

from fastapi import Cookie, FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from fake_sites.catalog import Site, search
from fake_sites.html_catalog import (
    render_captcha_page,
    render_changed_search_page,
    render_search_page,
)
from fake_sites.json_api import (
    SESSION_COOKIE,
    SESSION_COOKIE_PATH,
    STOREFRONT_PAGE,
    SessionStore,
    changed_offers_payload,
    offers_payload,
)
from fake_sites.modes import DEFAULT_RETRY_AFTER, Mode, ModeStore, SourceCode, SourceMode

Article = Annotated[str, Query(max_length=100)]


class ModeState(BaseModel):
    mode: Mode
    retry_after: int


class ModeChange(BaseModel):
    mode: Mode
    # Seconds of Retry-After the JSON API sends in ``blocked``; omitted -> the default.
    retry_after: int | None = Field(default=None, ge=0, le=3600)


def _state(mode: SourceMode) -> ModeState:
    return ModeState(mode=mode.mode, retry_after=mode.retry_after)


def _unavailable() -> Response:
    return HTMLResponse("<h1>503 Service Unavailable</h1>", status_code=503)


def _json_failure(mode: SourceMode) -> Response | None:
    """The response of the JSON source in a failing mode, for any of its pages."""
    if mode.mode is Mode.UNAVAILABLE:
        return _unavailable()
    if mode.mode is Mode.BLOCKED:
        return JSONResponse(
            {"error": "too_many_requests"},
            status_code=429,
            headers={"Retry-After": str(mode.retry_after)},
        )
    return None


def create_app() -> FastAPI:
    application = FastAPI(title="procenka fake-sites", description="Синтетические площадки демо")
    modes = ModeStore()
    sessions = SessionStore()

    @application.get("/html/search", response_class=HTMLResponse)
    async def html_search(article: Article = "") -> Response:
        mode = modes.get(SourceCode.HTML_CATALOG).mode
        if mode is Mode.UNAVAILABLE:
            return _unavailable()
        if mode is Mode.BLOCKED:
            return HTMLResponse(render_captcha_page(), status_code=403)
        listings = search(Site.HTML, article, price_spike=mode is Mode.PRICE_SPIKE)
        if mode is Mode.MARKUP_CHANGED:
            return HTMLResponse(render_changed_search_page(article, listings))
        return HTMLResponse(render_search_page(article, listings))

    @application.get("/json/", response_class=HTMLResponse)
    async def json_storefront() -> Response:
        failure = _json_failure(modes.get(SourceCode.JSON_API))
        if failure is not None:
            return failure
        response = HTMLResponse(STOREFRONT_PAGE)
        response.set_cookie(
            SESSION_COOKIE,
            sessions.issue(),
            path=SESSION_COOKIE_PATH,
            httponly=True,
            samesite="lax",
        )
        return response

    @application.get("/json/api/offers")
    async def json_offers(
        article: Article = "",
        session: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> Response:
        mode = modes.get(SourceCode.JSON_API)
        failure = _json_failure(mode)
        if failure is not None:
            return failure
        if not sessions.is_valid(session):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        listings = search(Site.JSON, article, price_spike=mode.mode is Mode.PRICE_SPIKE)
        if mode.mode is Mode.MARKUP_CHANGED:
            return JSONResponse(changed_offers_payload(article, listings))
        return JSONResponse(offers_payload(article, listings))

    @application.get("/control/modes")
    async def get_modes() -> dict[SourceCode, ModeState]:
        return {source: _state(mode) for source, mode in modes.all().items()}

    @application.put("/control/modes/{source}")
    async def put_mode(source: SourceCode, change: ModeChange) -> ModeState:
        retry_after = DEFAULT_RETRY_AFTER if change.retry_after is None else change.retry_after
        modes.set(source, SourceMode(mode=change.mode, retry_after=retry_after))
        return _state(modes.get(source))

    @application.post("/control/reset")
    async def reset_modes() -> dict[SourceCode, ModeState]:
        modes.reset()
        return {source: _state(mode) for source, mode in modes.all().items()}

    return application


app = create_app()
