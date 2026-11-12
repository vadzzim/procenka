import { useCalculation } from "../api/queries";
import type { CalculationOut, OfferOut, RejectedOfferOut } from "../api/types";
import { formatDateTime, formatMoney, formatPercent } from "../format";
import { ErrorText } from "./ErrorText";

function OffersTable({
  offers,
  caption,
}: {
  offers: ReadonlyArray<OfferOut | RejectedOfferOut>;
  caption: string;
}) {
  const withReason = offers.some((o) => "reason_label" in o);
  return (
    <table className="offers">
      <caption>{caption}</caption>
      <thead>
        <tr>
          <th>Источник</th>
          <th>Бренд и артикул в источнике</th>
          <th>После нормализации</th>
          <th>Цена</th>
          <th>Остаток</th>
          <th>Срок, дн.</th>
          {withReason && <th>Причина</th>}
        </tr>
      </thead>
      <tbody>
        {offers.map((o, i) => (
          <tr key={i}>
            <td>{o.source_label}</td>
            <td>
              {o.brand_raw} {o.article_raw}
            </td>
            <td>
              {o.brand} {o.article}
            </td>
            <td className="money">{formatMoney(o.price)}</td>
            <td>{o.stock}</td>
            <td>{o.delivery_days}</td>
            {withReason && <td>{"reason_label" in o ? o.reason_label : ""}</td>}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Inputs({ calc }: { calc: CalculationOut }) {
  const i = calc.inputs;
  return (
    <dl className="inputs">
      <dt>Закупка</dt>
      <dd>{formatMoney(i.purchase_price)}</dd>
      <dt>Мин. наценка</dt>
      <dd>{formatPercent(i.min_markup_percent)}</dd>
      <dt>Шаг</dt>
      <dd>{formatMoney(i.step)}</dd>
      <dt>Потолок</dt>
      <dd>{i.ceiling ? formatMoney(i.ceiling) : "не задан"}</dd>
      <dt>Порог карантина</dt>
      <dd>{formatPercent(i.quarantine_threshold_percent)}</dd>
      <dt>Коэффициент подозрительно низкой цены</dt>
      <dd>{i.suspicious_price_factor.replace(".", ",")}</dd>
      <dt>Макс. срок поставки</dt>
      <dd>{i.max_delivery_days} дн.</dd>
    </dl>
  );
}

export function CalculationDetails({ id }: { id: number }) {
  const query = useCalculation(id);
  if (query.isPending) return <p className="muted">Загрузка расчёта…</p>;
  if (query.isError) return <ErrorText error={query.error} />;
  const calc = query.data;

  return (
    <div className="details" aria-label="Почему цена такая">
      <h3>Почему цена такая</h3>
      <p className="muted small">
        Расчёт №{calc.id}, прогон №{calc.run_id}, {formatDateTime(calc.calculated_at)}; версия
        правил {calc.rules_version}
        {calc.partial_data && " · по частичным данным"}
      </p>

      <div className="details-grid">
        <div>
          <h4>Расчёт</h4>
          <ol className="formula">
            {calc.formula.map((line, i) => (
              <li key={i}>{line}</li>
            ))}
          </ol>
          <p>
            Сработавшие ограничения:{" "}
            {calc.applied_constraints.length
              ? calc.applied_constraints.map((c) => c.label).join(", ")
              : "нет"}
          </p>
          <p>
            Итог:{" "}
            <strong>{calc.recommended_price ? formatMoney(calc.recommended_price) : "нет"}</strong>
            {" · "}решение: <strong>{calc.state_label}</strong>
            {calc.reason_label && ` (${calc.reason_label})`}
          </p>
        </div>
        <div>
          <h4>Настройки на момент расчёта</h4>
          <Inputs calc={calc} />
        </div>
      </div>

      {calc.accepted_offers.length > 0 ? (
        <OffersTable offers={calc.accepted_offers} caption="Учтённые предложения" />
      ) : (
        <p className="muted">Учтённых предложений нет</p>
      )}
      {calc.rejected_offers.length > 0 && (
        <OffersTable offers={calc.rejected_offers} caption="Отброшенные предложения" />
      )}
    </div>
  );
}
