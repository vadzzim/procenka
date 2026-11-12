import { useConfirm } from "../api/queries";
import type { ConfirmationOut } from "../api/types";
import { formatDateTime, formatMoney } from "../format";
import { ErrorText } from "./ErrorText";

/** The question asked before publishing: "Опубликовать 1 490,00 ₽ вместо 1 990,00 ₽?" */
export function confirmQuestion(recommended: string, published: string | null): string {
  return published
    ? `Опубликовать ${formatMoney(recommended)} вместо ${formatMoney(published)}?`
    : `Опубликовать ${formatMoney(recommended)}? Опубликованной цены ещё нет.`;
}

/**
 * «Подтвердить» for a quarantined recommendation: publishes it after a question.
 * Disabled with the server's reason when it cannot be confirmed now; replaced by
 * a mark once confirmed. The server checks everything again: a 409 shows its reason.
 */
export function ConfirmQuarantine({
  calculationId,
  recommended,
  published,
  confirmation,
}: {
  calculationId: number;
  recommended: string;
  /** The price the recommendation replaces; null when nothing is published yet. */
  published: string | null;
  confirmation: ConfirmationOut;
}) {
  const confirm = useConfirm();

  if (confirmation.confirmed) {
    return (
      <div className="confirm">
        <span className="badge confirmed">
          подтверждена
          {confirmation.confirmed_at && `, ${formatDateTime(confirmation.confirmed_at)}`}
        </span>
      </div>
    );
  }

  const onClick = () => {
    if (!window.confirm(confirmQuestion(recommended, published))) return;
    confirm.mutate(calculationId);
  };

  const hint = confirmation.can_confirm ? undefined : (confirmation.refusal_label ?? undefined);
  return (
    <div className="confirm">
      <button
        type="button"
        onClick={onClick}
        disabled={!confirmation.can_confirm || confirm.isPending}
        title={hint}
      >
        {confirm.isPending ? "Публикация…" : "Подтвердить"}
      </button>
      {hint && <div className="muted small">{hint}</div>}
      {confirm.isError && <ErrorText error={confirm.error} />}
    </div>
  );
}
