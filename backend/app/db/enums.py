"""Codes of runs and sources stored in the database.

Values are the English codes kept in PostgreSQL enum types. Pricing codes
(result state, reason, constraint, offer rejection) live in
``app.pricing.models`` and are stored as they are.
"""

from enum import StrEnum
from types import MappingProxyType


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"


# At most one run may be in one of these statuses (partial unique index).
ACTIVE_RUN_STATUSES = (RunStatus.QUEUED, RunStatus.RUNNING)


class RunTrigger(StrEnum):
    MANUAL = "manual"
    SCHEDULE = "schedule"


class RunFinishReason(StrEnum):
    """Why a run ended the way it did; empty for a normal finish."""

    INTERRUPTED = "interrupted"
    # Every source failed: nothing was calculated and published prices stay as they were.
    ALL_SOURCES_FAILED = "all_sources_failed"
    # An unexpected error in the worker (a bug), not a source error.
    INTERNAL_ERROR = "internal_error"


class Source(StrEnum):
    HTML_CATALOG = "html_catalog"
    JSON_API = "json_api"


class SourceStatus(StrEnum):
    OK = "ok"
    FAILED = "failed"


class SourceErrorKind(StrEnum):
    NETWORK = "network"
    STRUCTURE = "structure"
    BLOCKED = "blocked"


# The single table of Russian labels the UI shows for source error kinds.
SOURCE_ERROR_LABELS: MappingProxyType[SourceErrorKind, str] = MappingProxyType(
    {
        SourceErrorKind.NETWORK: "недоступен",
        SourceErrorKind.STRUCTURE: "изменилась структура",
        SourceErrorKind.BLOCKED: "заблокирован",
    }
)
