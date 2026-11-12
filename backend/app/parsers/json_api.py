"""The JSON API source: the storefront's hidden API behind a session cookie.

The storefront page ``/json/`` loads prices by XHR. The parser opens the page
once per collection to get the session cookie (kept in the client's cookie
jar) and then calls the API directly in the same session:
``/json/api/offers?article=...``.

API contract (fake-sites, normal mode): ``{"offers": [{"brand": str,
"article": str, "price": "1250.00", "stock": int, "delivery_days": int}]}``;
``"offers": []`` is "nothing found". Prices are strings; a JSON number, more
than two decimals, a missing or renamed field is a structure error. JSON is
decoded with ``parse_float=Decimal``, so no float ever appears.

Session: a 401 from the API reopens the session once per collection; another
401 afterwards is "access denied" (blocked). HTTP outcomes, retries and the
budget: ``app.parsers.http``; other non-200 statuses are structure errors.
"""

import asyncio
import json
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

import httpx

from app.db.enums import Source
from app.parsers.base import ParsedOffer, SourceError, gather_per_article
from app.parsers.fields import parse_api_count, parse_api_price, require_text, structure_error
from app.parsers.http import SYSTEM_CLOCK, Budget, Clock, RetryPolicy, SourceHttp, blocked_error

SESSION_PAGE_PATH = "/json/"
OFFERS_PATH = "/json/api/offers"
SESSION_COOKIE = "session"


def _reject_constant(name: str) -> Any:
    raise ValueError(f"not a JSON number: {name}")


def _required(item: dict[str, Any], key: str, field: str) -> object:
    if key not in item or item[key] is None:
        raise structure_error(f"нет обязательного поля «{field}» ({key})")
    return item[key]


def _text(item: dict[str, Any], key: str, field: str) -> str:
    value = _required(item, key, field)
    if not isinstance(value, str):
        raise structure_error(f"поле «{field}» ({key}) должно быть строкой")
    return require_text(value, field=field)


def _parse_offer(item: object) -> ParsedOffer:
    if not isinstance(item, dict):
        raise structure_error("предложение в ответе API — не объект")
    return ParsedOffer(
        brand=_text(item, "brand", "бренд"),
        article=_text(item, "article", "артикул"),
        price=parse_api_price(_required(item, "price", "цена")),
        stock=parse_api_count(_required(item, "stock", "остаток"), field="остаток"),
        delivery_days=parse_api_count(
            _required(item, "delivery_days", "срок поставки"), field="срок поставки"
        ),
    )


def parse_offers_payload(content: bytes) -> list[ParsedOffer]:
    """Offers of one API response; ``{"offers": []}`` gives ``[]``."""
    try:
        payload = json.loads(content, parse_float=Decimal, parse_constant=_reject_constant)
    except (ValueError, UnicodeDecodeError) as exc:
        raise structure_error("ответ API — не JSON") from exc
    if not isinstance(payload, dict):
        raise structure_error("ответ API — не объект")
    offers = payload.get("offers")
    if offers is None:
        raise structure_error("в ответе API нет обязательного поля «offers»")
    if not isinstance(offers, list):
        raise structure_error("поле «offers» в ответе API — не список")
    return [_parse_offer(item) for item in offers]


class _Session:
    """The session of one collection, shared by concurrent article requests."""

    def __init__(self, http: SourceHttp) -> None:
        self._http = http
        self._lock = asyncio.Lock()
        self._reopened = False
        # Bumped on every (re)open, so concurrent 401s reopen the session only once.
        self.generation = 0

    async def open(self) -> None:
        self._http.client.cookies.clear()
        response = await self._http.get(SESSION_PAGE_PATH, what="страница сессии")
        if response.status_code != 200:
            raise structure_error(
                f"неожиданный ответ площадки: HTTP {response.status_code} на {SESSION_PAGE_PATH}"
            )
        if SESSION_COOKIE not in self._http.client.cookies:
            raise structure_error(
                f"страница {SESSION_PAGE_PATH} не выдала сессионную cookie «{SESSION_COOKIE}»"
            )
        self.generation += 1

    async def reopen(self, seen_generation: int, where: str) -> None:
        """Reopen after a 401 seen with ``seen_generation``; allowed once per collection."""
        async with self._lock:
            if self.generation != seen_generation:
                return  # another request has already reopened it
            if self._reopened:
                raise blocked_error(
                    f"доступ отклонён: HTTP 401 на {where} после переоткрытия сессии"
                )
            self._reopened = True
            await self.open()


class JsonApiSource:
    source = Source.JSON_API

    def __init__(
        self,
        base_url: str,
        *,
        proxy: str | None = None,
        timeout_seconds: float = 5.0,
        concurrency: int = 4,
        budget_seconds: float = 30.0,
        retry_policy: RetryPolicy | None = None,
        clock: Clock = SYSTEM_CLOCK,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url
        self._proxy = proxy
        self._timeout_seconds = timeout_seconds
        self._concurrency = concurrency
        self._budget_seconds = budget_seconds
        self._retry_policy = retry_policy or RetryPolicy()
        self._clock = clock
        # Tests pass a transport (an ASGI app or a mock); the proxy is then not used.
        self._transport = transport

    async def fetch_offers(self, articles: Sequence[str]) -> list[ParsedOffer]:
        if not articles:
            return []
        budget = Budget(self._budget_seconds, self._clock)
        async with httpx.AsyncClient(
            base_url=self._base_url,
            proxy=None if self._transport is not None else self._proxy,
            transport=self._transport,
            timeout=httpx.Timeout(self._timeout_seconds),
            follow_redirects=False,
        ) as client:
            http = SourceHttp(
                client,
                budget=budget,
                policy=self._retry_policy,
                clock=self._clock,
                request_timeout_seconds=self._timeout_seconds,
            )
            session = _Session(http)
            await session.open()

            async def fetch_one(article: str) -> list[ParsedOffer]:
                return await self._fetch_article(http, session, article)

            return await gather_per_article(articles, fetch_one, concurrency=self._concurrency)

    async def _fetch_article(
        self, http: SourceHttp, session: _Session, article: str
    ) -> list[ParsedOffer]:
        what = f"артикул {article}"
        where = f"{OFFERS_PATH} ({what})"
        generation = session.generation
        response = await http.get(OFFERS_PATH, params={"article": article}, what=what)
        if response.status_code == 401:
            await session.reopen(generation, where)
            response = await http.get(OFFERS_PATH, params={"article": article}, what=what)
            if response.status_code == 401:
                raise blocked_error(
                    f"доступ отклонён: HTTP 401 на {where} после переоткрытия сессии"
                )
        if response.status_code != 200:
            raise structure_error(
                f"неожиданный ответ площадки: HTTP {response.status_code} на {where}"
            )
        try:
            return parse_offers_payload(response.content)
        except SourceError as error:
            raise structure_error(f"{error.message} ({what})") from error
