// Display formatting. Money and percentages come from the API as decimal strings
// and are formatted with string operations only: they never become a JS number.

const MONEY_RE = /^(-?)(\d+)\.(\d{2})$/;
const DECIMAL_RE = /^(-?)(\d+)(?:\.(\d+))?$/;
const NBSP = " ";
const MINUS = "−";

function groupThousands(digits: string): string {
  return digits.replace(/\B(?=(\d{3})+(?!\d))/g, NBSP);
}

/** "12900.00" → "12 900,00 ₽" (non-breaking spaces). Unknown input is returned as is. */
export function formatMoney(value: string): string {
  const match = MONEY_RE.exec(value);
  if (!match) return value;
  const [, sign, whole = "", cents = ""] = match;
  return `${sign ? MINUS : ""}${groupThousands(whole)},${cents}${NBSP}₽`;
}

/** "-25.1" → "−25,1 %", "1.6" → "1,6 %". Unknown input is returned as is. */
export function formatPercent(value: string): string {
  const match = DECIMAL_RE.exec(value);
  if (!match) return value;
  const [, sign, whole = "", fraction] = match;
  const number = fraction === undefined ? whole : `${whole},${fraction}`;
  return `${sign ? MINUS : ""}${number}${NBSP}%`;
}

const dateTime = new Intl.DateTimeFormat("ru-RU", {
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
});

/** ISO 8601 → local date and time, "12.11.2026, 06:31:05". */
export function formatDateTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : dateTime.format(date);
}
