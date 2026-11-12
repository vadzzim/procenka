"""The HTML catalog source: search page per article, parsed with selectolax.

Markup contract (fake-sites, normal mode): ``#offers`` wraps the result; each
offer is a ``tr.offer`` row with ``.brand``, ``.article``, ``.price``,
``.stock`` and ``.delivery`` cells; an empty result is ``#offers .no-results``
without rows. Anything else is a structure error.

HTTP outcomes, retries and the time budget: ``app.parsers.http`` (5xx and
connection failures -> network after retries, 403 -> blocked at once, 429 ->
blocked unless Retry-After fits into the budget). Any other non-200 status ->
structure: the site no longer answers the way the parser expects.
"""

from collections.abc import Sequence

import httpx
from selectolax.lexbor import LexborHTMLParser, LexborNode

from app.db.enums import Source
from app.parsers.base import ParsedOffer, SourceError, gather_per_article
from app.parsers.fields import parse_count, parse_price, require_text, structure_error
from app.parsers.http import SYSTEM_CLOCK, Budget, Clock, RetryPolicy, SourceHttp

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

            async def fetch_one(article: str) -> list[ParsedOffer]:
                return await self._fetch_article(http, article)

            return await gather_per_article(articles, fetch_one, concurrency=self._concurrency)

    async def _fetch_article(self, http: SourceHttp, article: str) -> list[ParsedOffer]:
        what = f"артикул {article}"
        response = await http.get(SEARCH_PATH, params={"article": article}, what=what)
        if response.status_code != 200:
            raise structure_error(
                f"неожиданный ответ площадки: HTTP {response.status_code} на {SEARCH_PATH} "
                f"(артикул {article})"
            )
        try:
            return parse_search_page(response.text)
        except SourceError as error:
            raise structure_error(f"{error.message} (артикул {article})") from error
