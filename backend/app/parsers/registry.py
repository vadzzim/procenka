"""The configured set of sources of a run."""

import httpx

from app.config import Settings
from app.db.enums import Source
from app.parsers.base import OfferSource
from app.parsers.html_catalog import HtmlCatalogSource
from app.parsers.http import SYSTEM_CLOCK, Clock
from app.parsers.json_api import JsonApiSource


def build_sources(
    settings: Settings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    clock: Clock = SYSTEM_CLOCK,
) -> list[OfferSource]:
    """Sources listed in ``settings.enabled_sources``; ``transport`` and ``clock`` are for tests.

    Every source gets its own client, budget, concurrency limit and timeouts.
    """
    sources: list[OfferSource] = []
    for source in settings.enabled_sources:
        match source:
            case Source.HTML_CATALOG:
                sources.append(
                    HtmlCatalogSource(
                        settings.fake_sites_url,
                        proxy=settings.html_catalog_proxy,
                        timeout_seconds=settings.source_request_timeout_seconds,
                        concurrency=settings.source_concurrency,
                        budget_seconds=settings.source_time_budget_seconds,
                        retry_policy=settings.retry_policy(),
                        clock=clock,
                        transport=transport,
                    )
                )
            case Source.JSON_API:
                sources.append(
                    JsonApiSource(
                        settings.fake_sites_url,
                        proxy=settings.json_api_proxy,
                        timeout_seconds=settings.source_request_timeout_seconds,
                        concurrency=settings.source_concurrency,
                        budget_seconds=settings.source_time_budget_seconds,
                        retry_policy=settings.retry_policy(),
                        clock=clock,
                        transport=transport,
                    )
                )
    return sources
