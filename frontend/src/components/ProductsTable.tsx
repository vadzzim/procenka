import { Fragment, useState } from "react";

import { useProducts } from "../api/queries";
import type { ProductRowOut } from "../api/types";
import { formatDateTime, formatMoney, formatPercent } from "../format";
import { CalculationDetails } from "./CalculationDetails";
import { ErrorText } from "./ErrorText";

const COLUMNS = 10;

function Published({ row }: { row: ProductRowOut }) {
  const p = row.published;
  if (!p) return <span className="muted">нет</span>;
  return (
    <>
      <div className="money">{formatMoney(p.price)}</div>
      <div className="muted small">
        {p.origin === "seed" ? p.origin_label : `${p.origin_label}, ${formatDateTime(p.published_at)}`}
      </div>
    </>
  );
}

function ProductRow({
  row,
  expanded,
  onToggle,
}: {
  row: ProductRowOut;
  expanded: boolean;
  onToggle: () => void;
}) {
  const { product } = row;
  const calc = row.latest_calculation;
  const rawDiffers = product.brand_raw !== product.brand || product.article_raw !== product.article;

  return (
    <Fragment>
      <tr className={calc ? `state-${calc.state}` : undefined} data-testid={`product-${product.id}`}>
        <td>
          <div>
            <strong>{product.brand}</strong> {product.article}
          </div>
          <div className="muted small">{product.name}</div>
          {rawDiffers && (
            <div className="muted small">
              в данных: {product.brand_raw} {product.article_raw}
            </div>
          )}
        </td>
        <td className="money">{formatMoney(row.purchase_price)}</td>
        <td>
          <Published row={row} />
        </td>
        <td className="money">
          {calc?.recommended_price ? formatMoney(calc.recommended_price) : "—"}
        </td>
        <td>
          {calc ? (
            <span className={`badge state-badge-${calc.state}`}>{calc.state_label}</span>
          ) : (
            <span className="muted">расчётов не было</span>
          )}
        </td>
        <td>{calc?.reason_label ?? ""}</td>
        <td className="money">
          {calc?.change_percent_display ? formatPercent(calc.change_percent_display) : "—"}
        </td>
        <td>{calc?.partial_data && <span className="badge partial">по частичным данным</span>}</td>
        <td className="small">{calc ? formatDateTime(calc.calculated_at) : "—"}</td>
        <td>
          {calc && (
            <button type="button" className="link" aria-expanded={expanded} onClick={onToggle}>
              {expanded ? "Скрыть" : "Почему цена такая"}
            </button>
          )}
        </td>
      </tr>
      {expanded && calc && (
        <tr className="details-row">
          <td colSpan={COLUMNS}>
            <CalculationDetails id={calc.id} />
          </td>
        </tr>
      )}
    </Fragment>
  );
}

export function ProductsTable() {
  const products = useProducts();
  // Expanded rows are keyed by calculation id: a new run collapses stale details.
  const [expanded, setExpanded] = useState<ReadonlySet<number>>(new Set());

  const toggle = (id: number) =>
    setExpanded((old) => {
      const next = new Set(old);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  return (
    <section className="products">
      <h2>Товары</h2>
      {products.isPending && <p className="muted">Загрузка…</p>}
      {products.isError && <ErrorText error={products.error} />}
      {products.data && (
        <table>
          <thead>
            <tr>
              <th>Товар</th>
              <th>Закупка</th>
              <th>Опубликованная цена</th>
              <th>Новая рекомендация</th>
              <th>Состояние</th>
              <th>Причина</th>
              <th>Изменение</th>
              <th>Данные</th>
              <th>Время расчёта</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {products.data.products.map((row) => {
              const calcId = row.latest_calculation?.id;
              return (
                <ProductRow
                  key={row.product.id}
                  row={row}
                  expanded={calcId !== undefined && expanded.has(calcId)}
                  onToggle={() => calcId !== undefined && toggle(calcId)}
                />
              );
            })}
          </tbody>
        </table>
      )}
    </section>
  );
}
