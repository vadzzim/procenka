from app.db.enums import SOURCE_ERROR_LABELS, SourceErrorKind


def test_every_source_error_kind_has_a_russian_label() -> None:
    assert dict(SOURCE_ERROR_LABELS) == {
        SourceErrorKind.NETWORK: "недоступен",
        SourceErrorKind.STRUCTURE: "изменилась структура",
        SourceErrorKind.BLOCKED: "заблокирован",
    }
    assert set(SOURCE_ERROR_LABELS) == set(SourceErrorKind)
