import type { UseQueryResult } from "@tanstack/react-query";

import { useStartRun } from "../api/queries";
import type { RunOut, StatusOut } from "../api/types";
import { formatDateTime } from "../format";
import { ErrorText } from "./ErrorText";

function runTime(run: RunOut): string {
  if (run.finished_at) return `завершён ${formatDateTime(run.finished_at)}`;
  if (run.started_at) return `начат ${formatDateTime(run.started_at)}`;
  return `поставлен ${formatDateTime(run.created_at)}`;
}

export function RunHeader({ status }: { status: UseQueryResult<StatusOut> }) {
  const startRun = useStartRun();
  const active = status.data?.active_run ?? null;
  const latest = status.data?.latest_run ?? null;
  const created = startRun.data;

  return (
    <header className="header">
      <div className="header-title">
        <h1>Проценка</h1>
        <a href="/api/published-prices" target="_blank" rel="noreferrer">
          JSON API опубликованных цен
        </a>
      </div>

      <div className="run-control">
        <button
          type="button"
          className="primary"
          onClick={() => startRun.mutate()}
          disabled={startRun.isPending || active !== null}
        >
          {active ? `Прогон №${active.id} идёт…` : "Запустить прогон"}
        </button>
        {created && !created.created && (
          <span className="note">Прогон №{created.id} уже в очереди или выполняется</span>
        )}
        {startRun.isError && <ErrorText error={startRun.error} />}
      </div>

      <div className="run-status" aria-label="Последний прогон">
        {status.isError && <ErrorText error={status.error} />}
        {status.data && !latest && <span className="muted">Прогонов ещё не было</span>}
        {latest && (
          <>
            <span>Последний прогон №{latest.id}:</span>{" "}
            <span className={`badge run-${latest.status}`}>{latest.status_label}</span>{" "}
            <span className="muted">{runTime(latest)}</span>
            {latest.finish_reason_label && (
              <span className="finish-reason"> — причина: {latest.finish_reason_label}</span>
            )}
          </>
        )}
      </div>
    </header>
  );
}
