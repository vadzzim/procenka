from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.config import DEFAULT_DATABASE_URL, Settings
from app.db.enums import Source
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
    for name in ("ENABLED_SOURCES", "FAKE_SITES_URL", "HTML_CATALOG_PROXY"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings()
    assert settings.enabled_sources == (Source.HTML_CATALOG,)
    assert settings.fake_sites_url == "http://localhost:8001"
    assert settings.html_catalog_proxy is None
    assert settings.source_concurrency >= 1


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


def test_json_source_is_not_available_yet() -> None:
    settings = Settings(enabled_sources=(Source.JSON_API,))
    with pytest.raises(ValueError, match="step 5"):
        build_sources(settings)
