import { ApiError } from "../api/client";
import { useModes, useResetDemo, useSetMode } from "../api/queries";
import { DEMO_MODES, type DemoMode } from "../api/types";
import { ErrorText } from "./ErrorText";

const RESET_CONFIRM =
  "Сбросить демо? Все прогоны и расчёты будут удалены, цены вернутся к исходным данным, " +
  "оба источника — в режим normal.";

function resetError(error: unknown): string {
  if (error instanceof ApiError && error.status === 409) {
    return `Сброс невозможен: идёт прогон. ${error.message}`;
  }
  return error instanceof Error ? error.message : String(error);
}

export function DemoPanel({
  activeRunId,
  hasRuns,
}: {
  activeRunId: number | null;
  /** The reset confirmation is shown only until the next run appears. */
  hasRuns: boolean;
}) {
  const modes = useModes();
  const setMode = useSetMode();
  const reset = useResetDemo();

  const onReset = () => {
    if (window.confirm(RESET_CONFIRM)) reset.mutate();
  };

  return (
    <section className="panel demo" aria-label="Демо">
      <h2>Демо</h2>
      {modes.isError && <ErrorText error={modes.error} />}
      {modes.data?.modes.map((m) => (
        <label key={m.source} className="mode">
          <span className="mode-source">{m.source_label}</span>
          <select
            value={m.mode}
            disabled={setMode.isPending}
            onChange={(e) =>
              setMode.mutate({ source: m.source, mode: e.target.value as DemoMode })
            }
          >
            {DEMO_MODES.map((mode) => (
              <option key={mode} value={mode}>
                {mode}
              </option>
            ))}
          </select>
          <span className="muted">{m.mode_label}</span>
        </label>
      ))}
      {setMode.isError && <ErrorText error={setMode.error} />}

      <div className="reset">
        <button type="button" onClick={onReset} disabled={reset.isPending}>
          Сбросить демо
        </button>
        {activeRunId !== null && (
          <span className="muted">Сброс возможен после завершения прогона №{activeRunId}</span>
        )}
        {reset.isSuccess && !hasRuns && (
          <span className="note">
            Демо сброшено: товаров {reset.data.products}, опубликованных цен{" "}
            {reset.data.published_prices}
          </span>
        )}
        {reset.isError && (
          <p className="error" role="alert">
            {resetError(reset.error)}
          </p>
        )}
      </div>
    </section>
  );
}
