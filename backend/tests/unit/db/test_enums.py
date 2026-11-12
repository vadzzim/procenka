from app.api.schemas import DEMO_MODE_LABELS, PRICE_ORIGIN_LABELS, DemoMode, PriceOrigin
from app.db.enums import (
    RUN_FINISH_REASON_LABELS,
    RUN_STATUS_LABELS,
    SOURCE_ERROR_LABELS,
    SOURCE_LABELS,
    RunFinishReason,
    RunStatus,
    Source,
    SourceErrorKind,
)


def test_every_source_error_kind_has_a_russian_label() -> None:
    assert dict(SOURCE_ERROR_LABELS) == {
        SourceErrorKind.NETWORK: "недоступен",
        SourceErrorKind.STRUCTURE: "изменилась структура",
        SourceErrorKind.BLOCKED: "заблокирован",
    }
    assert set(SOURCE_ERROR_LABELS) == set(SourceErrorKind)


def test_every_run_status_finish_reason_and_source_has_a_label() -> None:
    assert set(RUN_STATUS_LABELS) == set(RunStatus)
    assert set(RUN_FINISH_REASON_LABELS) == set(RunFinishReason)
    assert set(SOURCE_LABELS) == set(Source)
    assert all(RUN_STATUS_LABELS.values())
    assert RUN_FINISH_REASON_LABELS[RunFinishReason.INTERRUPTED].startswith("прерван")


def test_every_demo_mode_and_price_origin_has_a_label() -> None:
    assert set(DEMO_MODE_LABELS) == set(DemoMode)
    assert set(PRICE_ORIGIN_LABELS) == set(PriceOrigin)
