import type { StatusOut } from "../api/types";
import { formatDateTime } from "../format";

export function SourcesStatus({ status }: { status: StatusOut | undefined }) {
  const run = status?.last_finished_run ?? null;
  return (
    <section className="panel" aria-label="Источники">
      <h2>Источники{run ? ` — прогон №${run.id}` : ""}</h2>
      {!run && <p className="muted">Нет завершённых прогонов</p>}
      {run && run.sources.length === 0 && (
        <p className="muted">
          Прогон завершён до сбора источников
          {run.finish_reason_label ? ` (${run.finish_reason_label})` : ""}
        </p>
      )}
      {run && run.sources.length > 0 && (
        <ul className="sources">
          {run.sources.map((s) => (
            <li key={s.source} className={`source source-${s.status}`}>
              <div>
                <strong>{s.source_label}</strong>{" "}
                {s.status === "ok" ? (
                  <span className="badge ok">ok</span>
                ) : (
                  <span className="badge failed">ошибка: {s.error_label}</span>
                )}
              </div>
              <div className="muted">
                предложений: {s.offer_count} · {formatDateTime(s.finished_at)}
              </div>
              {s.error_message && <div className="source-message">{s.error_message}</div>}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
