"""The configured set of sources of a run."""

import httpx

from app.config import Settings
from app.db.enums import Source
from app.parsers.base import OfferSource
from app.parsers.html_catalog import HtmlCatalogSource


def build_sources(
    settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
) -> list[OfferSource]:
    """Sources listed in ``settings.enabled_sources``; ``transport`` is for tests."""
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
                        transport=transport,
                    )
                )
            case Source.JSON_API:
                raise ValueError("the JSON API source is not implemented yet (step 5)")
    return sources
