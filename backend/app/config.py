"""Application configuration from environment variables.

Global pricing settings are configuration, not a database table: they are
the same for every product and change only with a redeploy.
"""

from decimal import Decimal
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.pricing.models import GlobalSettings

DEFAULT_DATABASE_URL = "postgresql+asyncpg://procenka:procenka@localhost:54329/procenka"


class Settings(BaseSettings):
    """Read from the process environment only; ``.env`` is for docker compose."""

    model_config = SettingsConfigDict(extra="ignore", frozen=True)

    database_url: str = DEFAULT_DATABASE_URL

    # Pricing settings shared by all products (see GlobalSettings).
    # Bounded by calculations.suspicious_price_factor NUMERIC(6,4): stored without rounding.
    suspicious_price_factor: Decimal = Field(
        default=Decimal("0.5"), ge=0, max_digits=6, decimal_places=4
    )
    max_delivery_days: int = Field(default=14, ge=0)

    # Time budget for collecting one source, including pauses between retries.
    source_time_budget_seconds: float = Field(default=30.0, gt=0)

    def global_settings(self) -> GlobalSettings:
        return GlobalSettings(
            suspicious_price_factor=self.suspicious_price_factor,
            max_delivery_days=self.max_delivery_days,
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
