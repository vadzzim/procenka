"""Reading runs with their source results for the API."""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import RunOut, SourceResultOut
from app.db.enums import (
    RUN_FINISH_REASON_LABELS,
    RUN_STATUS_LABELS,
    SOURCE_ERROR_LABELS,
    SOURCE_LABELS,
    RunFinishReason,
)
from app.db.models import Run, SourceResult


def source_result_out(result: SourceResult) -> SourceResultOut:
    return SourceResultOut(
        source=result.source,
        source_label=SOURCE_LABELS[result.source],
        status=result.status,
        error_kind=result.error_kind,
        error_label=None if result.error_kind is None else SOURCE_ERROR_LABELS[result.error_kind],
        error_message=result.error_message,
        offer_count=result.offer_count,
        started_at=result.started_at,
        finished_at=result.finished_at,
    )


def run_out(run: Run, sources: Sequence[SourceResult]) -> RunOut:
    reason = None if run.finish_reason is None else RunFinishReason(run.finish_reason)
    return RunOut(
        id=run.id,
        status=run.status,
        status_label=RUN_STATUS_LABELS[run.status],
        trigger=run.trigger,
        created_at=run.created_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
        finish_reason=reason,
        finish_reason_label=None if reason is None else RUN_FINISH_REASON_LABELS[reason],
        sources=[source_result_out(source) for source in sources],
    )


async def load_run_out(session: AsyncSession, run: Run | None) -> RunOut | None:
    if run is None:
        return None
    sources = await session.scalars(
        select(SourceResult).where(SourceResult.run_id == run.id).order_by(SourceResult.source)
    )
    return run_out(run, list(sources))
