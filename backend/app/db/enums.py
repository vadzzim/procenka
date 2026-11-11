"""Codes of runs and sources stored in the database.

Values are the English codes kept in PostgreSQL enum types. Pricing codes
(result state, reason, constraint, offer rejection) live in
``app.pricing.models`` and are stored as they are.
"""

from enum import StrEnum


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
    """Why a run ended the way it did. Only ``interrupted`` is fixed so far."""

    INTERRUPTED = "interrupted"


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
