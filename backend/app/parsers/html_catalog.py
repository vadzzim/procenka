"""The HTML catalog source: search page per article, parsed with selectolax.

Markup contract (fake-sites, normal mode): ``#offers`` wraps the result; each
offer is a ``tr.offer`` row with ``.brand``, ``.article``, ``.price``,
``.stock`` and ``.delivery`` cells; an empty result is ``#offers .no-results``
without rows. Anything else is a structure error.

HTTP outcome mapping (no retries yet): timeout, connection error and 5xx ->
network; 403 and 429 -> blocked; any other non-200 status -> structure (the
site no longer answers the way the parser expects).
"""

from collections.abc import Sequence

import httpx
from selectolax.lexbor import LexborHTMLParser, LexborNode

from app.db.enums import Source, SourceErrorKind
from app.parsers.base import ParsedOffer, SourceError, gather_per_article
from app.parsers.fields import parse_count, parse_price, require_text, structure_error

SEARCH_PATH = "/html/search"

_FIELDS = (
    ("brand", "бренд"),
    ("article", "артикул"),
    ("price", "цена"),
    ("stock", "остаток"),
    ("delivery", "срок поставки"),
)


def _cell_text(row: LexborNode, css_class: str, field: str) -> str:
    cell = row.css_first(f"td.{css_class}")
    if cell is None:
        raise structure_error(f"нет обязательного поля «{field}»")
    return require_text(cell.text(deep=True), field=field)


def _parse_row(row: LexborNode) -> ParsedOffer:
    values = {css_class: _cell_text(row, css_class, field) for css_class, field in _FIELDS}
    return ParsedOffer(
        brand=values["brand"],
        article=values["article"],
        price=parse_price(values["price"]),
        stock=parse_count(values["stock"], field="остаток"),
        delivery_days=parse_count(values["delivery"], field="срок поставки"),
    )


def parse_search_page(html: str) -> list[ParsedOffer]:
    """Offers of one search page; an explicit "nothing found" page gives ``[]``."""
    tree = LexborHTMLParser(html)
    container = tree.css_first("#offers")
    if container is None:
        raise structure_error("на странице поиска нет блока результатов (#offers)")
    rows = container.css("tr.offer")
    no_results = container.css_first(".no-results") is not None
    if rows and no_results:
        raise structure_error("страница одновременно содержит предложения и «ничего не найдено»")
    if not rows:
        if no_results:
            return []
        raise structure_error("в блоке результатов нет ни предложений, ни «ничего не найдено»")
    return [_parse_row(row) for row in rows]


class HtmlCatalogSource:
    source = Source.HTML_CATALOG

    def __init__(
        self,
        base_url: str,
        *,
        proxy: str | None = None,
        timeout_seconds: float = 5.0,
        concurrency: int = 4,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url
        self._proxy = proxy
        self._timeout = httpx.Timeout(timeout_seconds)
        self._concurrency = concurrency
        # Tests pass a transport (an ASGI app or a mock); the proxy is then not used.
        self._transport = transport

    async def fetch_offers(self, articles: Sequence[str]) -> list[ParsedOffer]:
        async with httpx.AsyncClient(
            base_url=self._base_url,
            proxy=None if self._transport is not None else self._proxy,
            transport=self._transport,
            timeout=self._timeout,
            follow_redirects=False,
        ) as client:

            async def fetch_one(article: str) -> list[ParsedOffer]:
                return await self._fetch_article(client, article)

            return await gather_per_article(articles, fetch_one, concurrency=self._concurrency)

    async def _fetch_article(self, client: httpx.AsyncClient, article: str) -> list[ParsedOffer]:
        try:
            response = await client.get(SEARCH_PATH, params={"article": article})
        except httpx.TimeoutException as exc:
            raise SourceError(
                SourceErrorKind.NETWORK, f"таймаут запроса (артикул {article})"
            ) from exc
        except httpx.DecodingError as exc:
            raise structure_error(f"не удалось декодировать ответ (артикул {article})") from exc
        except httpx.RequestError as exc:
            raise SourceError(
                SourceErrorKind.NETWORK,
                f"ошибка соединения: {type(exc).__name__} (артикул {article})",
            ) from exc

        status = response.status_code
        if status in (403, 429):
            raise SourceError(
                SourceErrorKind.BLOCKED, f"площадка заблокировала запрос: HTTP {status}"
            )
        if status >= 500:
            raise SourceError(SourceErrorKind.NETWORK, f"площадка недоступна: HTTP {status}")
        if status != 200:
            raise structure_error(f"неожиданный ответ площадки: HTTP {status}")
        try:
            return parse_search_page(response.text)
        except SourceError as error:
            raise structure_error(f"{error.message} (артикул {article})") from error
