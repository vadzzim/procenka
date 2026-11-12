from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.config import DEFAULT_DATABASE_URL, Settings
from app.db.enums import Source
from app.parsers.html_catalog import HtmlCatalogSource
from app.parsers.http import RetryPolicy
from app.parsers.json_api import JsonApiSource
from app.parsers.registry import build_sources


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("DATABASE_URL", "SUSPICIOUS_PRICE_FACTOR", "MAX_DELIVERY_DAYS"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings()
    assert settings.database_url == DEFAULT_DATABASE_URL
    assert settings.source_time_budget_seconds == 30
    global_settings = settings.global_settings()
    assert global_settings.suspicious_price_factor == Decimal("0.5")
    assert global_settings.max_delivery_days == 14


def test_environment_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@db:5432/x")
    monkeypatch.setenv("SUSPICIOUS_PRICE_FACTOR", "0.45")
    settings = Settings()
    assert settings.database_url == "postgresql+asyncpg://u:p@db:5432/x"
    assert settings.suspicious_price_factor == Decimal("0.45")


@pytest.mark.parametrize("factor", ["-0.1", "0.12345", "123.5"])
def test_factor_must_fit_the_snapshot_column(monkeypatch: pytest.MonkeyPatch, factor: str) -> None:
    monkeypatch.setenv("SUSPICIOUS_PRICE_FACTOR", factor)
    with pytest.raises(ValidationError):
        Settings()


def test_source_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "ENABLED_SOURCES",
        "FAKE_SITES_URL",
        "HTML_CATALOG_PROXY",
        "JSON_API_PROXY",
        "SOURCE_RETRY_ATTEMPTS",
        "SOURCE_RETRY_PAUSE_SECONDS",
        "SOURCE_RATE_LIMIT_RETRIES",
    ):
        monkeypatch.delenv(name, raising=False)
    settings = Settings()
    assert settings.enabled_sources == (Source.HTML_CATALOG, Source.JSON_API)
    assert settings.fake_sites_url == "http://localhost:8001"
    assert settings.html_catalog_proxy is None
    assert settings.json_api_proxy is None
    assert settings.source_concurrency >= 1
    assert settings.retry_policy() == RetryPolicy(
        attempts=3, pause_seconds=1.0, rate_limit_retries=2
    )
    sources = build_sources(settings)
    assert [type(source) for source in sources] == [HtmlCatalogSource, JsonApiSource]


def test_sources_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENABLED_SOURCES", '["html_catalog"]')
    monkeypatch.setenv("FAKE_SITES_URL", "http://fake-sites:8001")
    monkeypatch.setenv("HTML_CATALOG_PROXY", "http://proxy:3128")
    settings = Settings()
    sources = build_sources(settings)
    assert [source.source for source in sources] == [Source.HTML_CATALOG]
    assert settings.html_catalog_proxy == "http://proxy:3128"


@pytest.mark.parametrize("value", ["[]", '["ftp_site"]'])
def test_invalid_sources_are_rejected(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("ENABLED_SOURCES", value)
    with pytest.raises(ValidationError):
        Settings()


def test_retry_settings_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOURCE_RETRY_ATTEMPTS", "5")
    monkeypatch.setenv("SOURCE_RETRY_PAUSE_SECONDS", "0.5")
    monkeypatch.setenv("SOURCE_RATE_LIMIT_RETRIES", "0")
    monkeypatch.setenv("SOURCE_TIME_BUDGET_SECONDS", "10")
    monkeypatch.setenv("JSON_API_PROXY", "http://proxy:3128")
    monkeypatch.setenv("ENABLED_SOURCES", '["json_api"]')
    settings = Settings()
    assert settings.retry_policy() == RetryPolicy(
        attempts=5, pause_seconds=0.5, rate_limit_retries=0
    )
    [source] = build_sources(settings)
    assert isinstance(source, JsonApiSource)
    assert source._budget_seconds == 10
    assert source._retry_policy == settings.retry_policy()
    assert source._proxy == "http://proxy:3128"


@pytest.mark.parametrize(
    ("name", "value"),
    [("SOURCE_RETRY_ATTEMPTS", "0"), ("SOURCE_RETRY_PAUSE_SECONDS", "-1")],
)
def test_invalid_retry_settings_are_rejected(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError):
        Settings()


def test_worker_defaults_and_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("WORKER_POLL_SECONDS", "SCHEDULE_INTERVAL_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings()
    assert settings.worker_poll_seconds == 2
    assert settings.schedule_interval_seconds == 600

    monkeypatch.setenv("SCHEDULE_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("WORKER_POLL_SECONDS", "0.5")
    settings = Settings()
    assert settings.schedule_interval_seconds == 0
    assert settings.worker_poll_seconds == 0.5

    monkeypatch.setenv("SCHEDULE_INTERVAL_SECONDS", "-1")
    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.parametrize("value", ["", "  "])
def test_empty_proxy_means_no_proxy(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    """compose passes ``${HTML_CATALOG_PROXY:-}``: an unset variable arrives as ""."""
    monkeypatch.setenv("HTML_CATALOG_PROXY", value)
    monkeypatch.setenv("JSON_API_PROXY", value)
    monkeypatch.setenv("ENABLED_SOURCES", '["html_catalog","json_api"]')
    settings = Settings()
    assert settings.html_catalog_proxy is None
    assert settings.json_api_proxy is None
    html, json_api = build_sources(settings)
    assert isinstance(html, HtmlCatalogSource)
    assert isinstance(json_api, JsonApiSource)
    assert html._proxy is None
    assert json_api._proxy is None


def test_proxy_values_reach_their_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTML_CATALOG_PROXY", "http://html-proxy:3128")
    monkeypatch.setenv("JSON_API_PROXY", "http://json-proxy:3128")
    monkeypatch.setenv("ENABLED_SOURCES", '["html_catalog","json_api"]')
    html, json_api = build_sources(Settings())
    assert isinstance(html, HtmlCatalogSource)
    assert isinstance(json_api, JsonApiSource)
    assert html._proxy == "http://html-proxy:3128"
    assert json_api._proxy == "http://json-proxy:3128"


def test_quarantine_confirm_max_age(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("QUARANTINE_CONFIRM_MAX_AGE_MINUTES", raising=False)
    assert Settings().quarantine_confirm_max_age_minutes == 60
    monkeypatch.setenv("QUARANTINE_CONFIRM_MAX_AGE_MINUTES", "5")
    assert Settings().quarantine_confirm_max_age_minutes == 5
    monkeypatch.setenv("QUARANTINE_CONFIRM_MAX_AGE_MINUTES", "0")
    with pytest.raises(ValidationError):
        Settings()
