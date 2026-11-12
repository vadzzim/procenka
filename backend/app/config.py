"""Application configuration from environment variables.

Global pricing settings are configuration, not a database table: they are
the same for every product and change only with a redeploy.
"""

from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.db.enums import Source
from app.parsers.http import RetryPolicy
from app.pricing.models import GlobalSettings

DEFAULT_DATABASE_URL = "postgresql+asyncpg://procenka:procenka@localhost:54329/procenka"
DEFAULT_FAKE_SITES_URL = "http://localhost:8001"


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

    # Sources collected in every run, in this order (ENABLED_SOURCES='["html_catalog"]').
    enabled_sources: tuple[Source, ...] = Field(
        default=(Source.HTML_CATALOG, Source.JSON_API), min_length=1
    )
    # Base URL of the fake-sites service that hosts both synthetic sources.
    fake_sites_url: str = DEFAULT_FAKE_SITES_URL
    # Optional proxy of each source, passed to httpx.AsyncClient.
    html_catalog_proxy: str | None = None
    json_api_proxy: str | None = None
    # Timeout of one HTTP request to a source and the number of concurrent requests per source.
    source_request_timeout_seconds: float = Field(default=5.0, gt=0)
    source_concurrency: int = Field(default=4, ge=1)
    # Network failures (timeout, connection error, 5xx): attempts in total and the pause.
    source_retry_attempts: int = Field(default=3, ge=1)
    source_retry_pause_seconds: float = Field(default=1.0, ge=0)
    # Retries after HTTP 429 (each after Retry-After, if it fits into the time budget).
    source_rate_limit_retries: int = Field(default=2, ge=0)

    # The worker's pause between iterations (schedule check, claim, execute).
    worker_poll_seconds: float = Field(default=2.0, gt=0)
    # A scheduled run is queued when no run is active and the latest run is at
    # least this old (decided from the database); 0 turns the schedule off.
    schedule_interval_seconds: float = Field(default=600.0, ge=0)
    # Touched by the worker at start and after every iteration, for the compose
    # healthcheck (see app.worker.heartbeat); None writes no file.
    worker_heartbeat_file: Path | None = None

    def retry_policy(self) -> RetryPolicy:
        return RetryPolicy(
            attempts=self.source_retry_attempts,
            pause_seconds=self.source_retry_pause_seconds,
            rate_limit_retries=self.source_rate_limit_retries,
        )

    def global_settings(self) -> GlobalSettings:
        return GlobalSettings(
            suspicious_price_factor=self.suspicious_price_factor,
            max_delivery_days=self.max_delivery_days,
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
