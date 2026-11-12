"""Failure modes of the synthetic sources, kept in memory per application.

Each source has its own mode; switching it with ``PUT /control/modes/{source}``
takes effect on the very next request, without a restart. ``POST
/control/reset`` puts both sources back to ``normal``.

| Mode           | HTML catalog                         | JSON API                              |
|----------------|--------------------------------------|---------------------------------------|
| normal         | correct page                         | correct JSON                          |
| markup_changed | other markup, no required fields     | other schema (renamed/missing fields) |
| unavailable    | 503                                  | 503                                   |
| blocked        | 403 with a captcha page              | 429 with ``Retry-After``              |
| price_spike    | the control product S costs 1 500    | the control product S costs 1 500     |

``unavailable`` and ``blocked`` apply to every page of the source (for the
JSON API also to the page that issues the session cookie).
"""

from dataclasses import dataclass
from enum import StrEnum


class SourceCode(StrEnum):
    """The same codes as the backend uses for its sources."""

    HTML_CATALOG = "html_catalog"
    JSON_API = "json_api"


class Mode(StrEnum):
    NORMAL = "normal"
    MARKUP_CHANGED = "markup_changed"
    UNAVAILABLE = "unavailable"
    BLOCKED = "blocked"
    PRICE_SPIKE = "price_spike"


# Seconds the JSON API asks to wait in ``blocked`` (more than the backend's 30 s budget).
DEFAULT_RETRY_AFTER = 60


@dataclass(frozen=True, slots=True)
class SourceMode:
    mode: Mode = Mode.NORMAL
    retry_after: int = DEFAULT_RETRY_AFTER


class ModeStore:
    """Current mode of every source; one store per application instance."""

    def __init__(self) -> None:
        self._modes: dict[SourceCode, SourceMode] = {}
        self.reset()

    def get(self, source: SourceCode) -> SourceMode:
        return self._modes[source]

    def set(self, source: SourceCode, mode: SourceMode) -> None:
        self._modes[source] = mode

    def reset(self) -> None:
        self._modes = {source: SourceMode() for source in SourceCode}

    def all(self) -> dict[SourceCode, SourceMode]:
        return dict(self._modes)
