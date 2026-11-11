from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.config import DEFAULT_DATABASE_URL, Settings


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
