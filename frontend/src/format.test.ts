import { describe, expect, it } from "vitest";

import { formatMoney, formatPercent } from "./format";

const NBSP = " ";

describe("formatMoney", () => {
  it.each([
    ["0.50", "0,50 ₽"],
    ["0.00", "0,00 ₽"],
    ["1.00", "1,00 ₽"],
    ["999.99", "999,99 ₽"],
    ["1240.00", "1 240,00 ₽"],
    ["12900.00", "12 900,00 ₽"],
    ["100000.05", "100 000,05 ₽"],
    ["1234567.89", "1 234 567,89 ₽"],
  ])("%s → %s", (input, expected) => {
    expect(formatMoney(input)).toBe(expected.replaceAll(" ", NBSP));
  });

  it("keeps all digits of a large amount (no float conversion)", () => {
    expect(formatMoney("9007199254740993.01")).toBe(
      "9 007 199 254 740 993,01 ₽".replaceAll(" ", NBSP),
    );
  });

  it.each(["", "12", "12.5", "12.345", "1,00", "abc"])(
    "returns unexpected input %j unchanged",
    (input) => {
      expect(formatMoney(input)).toBe(input);
    },
  );
});

describe("formatPercent", () => {
  it.each([
    ["-25.1", "−25,1 %"],
    ["-1.6", "−1,6 %"],
    ["0.0", "0,0 %"],
    ["20", "20 %"],
    ["20.04", "20,04 %"],
  ])("%s → %s", (input, expected) => {
    expect(formatPercent(input)).toBe(expected.replaceAll(" ", NBSP));
  });
});
