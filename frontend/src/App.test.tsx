import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { App } from "./App";
import type {
  CalculationOut,
  ConfirmRefusalOut,
  DemoResetOut,
  ErrorOut,
  RunCreated,
  ProductsOut,
  PublishedPriceOut,
  SourceModeOut,
  StatusOut,
} from "./api/types";
import { calculationA, finishedRun, modes, products, statusEmpty, statusIdle } from "./test/fixtures";
import { mockApi, renderWithClient } from "./test/server";

const baseRoutes = {
  "GET /api/status": { body: statusIdle },
  "GET /api/products": { body: products },
  "GET /api/demo/modes": { body: modes },
};

function rowOf(productId: number): HTMLElement {
  return screen.getByTestId(`product-${productId}`);
}

describe("products table", () => {
  it("renders prices, state labels and highlights quarantine and retained rows", async () => {
    mockApi(baseRoutes);
    renderWithClient(<App />);

    const a = await screen.findByTestId("product-1");
    expect(within(a).getByText("Bosch")).toBeInTheDocument();
    expect(within(a).getByText("в данных: BOSCH 0 986-452 041")).toBeInTheDocument();
    expect(within(a).getAllByText("1 240,00 ₽")).toHaveLength(2); // published and recommended
    expect(within(a).getByText("1 000,00 ₽")).toBeInTheDocument();
    expect(within(a).getByText("опубликована")).toBeInTheDocument();
    expect(within(a).getByText("−1,6 %")).toBeInTheDocument();
    expect(within(a).getByText(/^по расчёту, /)).toBeInTheDocument();

    const s = rowOf(2);
    expect(s).toHaveClass("state-quarantine");
    expect(within(s).getByText("карантин")).toBeInTheDocument();
    expect(within(s).getByText("скачок цены")).toBeInTheDocument();
    expect(within(s).getByText("1 490,00 ₽")).toBeInTheDocument();
    expect(within(s).getByText("1 990,00 ₽")).toBeInTheDocument();
    expect(within(s).getByText("−25,1 %")).toBeInTheDocument();
    expect(within(s).getByText("цена из исходных данных")).toBeInTheDocument();
    expect(within(s).getByText("по частичным данным")).toBeInTheDocument();

    const febi = rowOf(4);
    expect(febi).toHaveClass("state-quarantine");
    expect(within(febi).getByText("нет предыдущей цены")).toBeInTheDocument();
    expect(within(febi).getByText("нет")).toBeInTheDocument(); // no published price

    const hengst = rowOf(5);
    expect(hengst).toHaveClass("state-retained");
    expect(within(hengst).getByText("оставлена предыдущая")).toBeInTheDocument();
    expect(within(hengst).getByText("нет пригодных предложений")).toBeInTheDocument();
    expect(within(hengst).getByText("690,00 ₽")).toBeInTheDocument();
    expect(within(hengst).getByText("цена из исходных данных")).toBeInTheDocument();
  });

  it("loads the calculation on expand and shows the formula and rejected offers", async () => {
    const requests = mockApi({
      ...baseRoutes,
      "GET /api/calculations/1": { body: calculationA },
    });
    const user = userEvent.setup();
    renderWithClient(<App />);

    const a = await screen.findByTestId("product-1");
    expect(requests.some((r) => r.path === "/api/calculations/1")).toBe(false);

    await user.click(within(a).getByRole("button", { name: "Почему цена такая" }));

    const details = await screen.findByLabelText("Почему цена такая");
    expect(within(details).getByText("Целевая = 1 250,00 − 10,00 = 1 240,00")).toBeInTheDocument();
    expect(
      within(details).getByText(
        "Изменение = |1 240,00 − 1 260,00| / 1 260,00 × 100 = 1,6 % ≤ 20 % → опубликована",
      ),
    ).toBeInTheDocument();
    const rejected = within(details).getByRole("table", { name: "Отброшенные предложения" });
    expect(within(rejected).getByText("другой бренд")).toBeInTheDocument();
    expect(within(rejected).getByText("подозрительно низкая цена")).toBeInTheDocument();
    expect(within(rejected).getByText("1,00 ₽")).toBeInTheDocument();
    const accepted = within(details).getByRole("table", { name: "Учтённые предложения" });
    expect(within(accepted).getAllByText("1 250,00 ₽")).toHaveLength(2);
    expect(within(details).getByText(/Сработавшие ограничения:\s*нет/)).toBeInTheDocument();
    expect(within(details).getByText(/версия правил 2026-11-12\.1/)).toBeInTheDocument();
    expect(requests.filter((r) => r.path === "/api/calculations/1")).toHaveLength(1);

    await user.click(within(a).getByRole("button", { name: "Скрыть" }));
    expect(screen.queryByLabelText("Почему цена такая")).not.toBeInTheDocument();
  });
});

describe("run status and sources", () => {
  it("shows the last run with its status and source errors", async () => {
    mockApi(baseRoutes);
    renderWithClient(<App />);

    const run = await screen.findByLabelText("Последний прогон");
    await within(run).findByText("частично: часть источников с ошибкой");
    const sources = screen.getByLabelText("Источники");
    expect(within(sources).getByText("ошибка: изменилась структура")).toBeInTheDocument();
    expect(within(sources).getByText(/нет блока результатов/)).toBeInTheDocument();
    expect(within(sources).getByText(/предложений: 24/)).toBeInTheDocument();
  });

  it("starts a run and shows that a run is in progress", async () => {
    let active = false;
    const created: RunCreated = { id: 4, status: "queued", created: true };
    const requests = mockApi({
      ...baseRoutes,
      "GET /api/status": () => ({
        status: 200,
        body: active
          ? {
              ...statusIdle,
              active_run: {
                ...finishedRun,
                id: 4,
                status: "queued",
                status_label: "в очереди",
                finished_at: null,
                sources: [],
              },
            }
          : statusEmpty,
      }),
      "POST /api/runs": () => {
        active = true;
        return { status: 201, body: created };
      },
    });
    const user = userEvent.setup();
    renderWithClient(<App />);

    await screen.findByText("Прогонов ещё не было");
    await user.click(screen.getByRole("button", { name: "Запустить прогон" }));

    expect(await screen.findByRole("button", { name: "Прогон №4 идёт…" })).toBeDisabled();
    expect(requests.filter((r) => r.method === "POST" && r.path === "/api/runs")).toHaveLength(1);
  });
});

describe("polling", () => {
  it("polls status every second during a run and refetches products when it finishes", async () => {
    const running: StatusOut = {
      latest_run: { ...finishedRun, status: "running", status_label: "выполняется", finished_at: null, sources: [] },
      active_run: { ...finishedRun, status: "running", status_label: "выполняется", finished_at: null, sources: [] },
      last_finished_run: null,
    };
    let statusCalls = 0;
    const requests = mockApi({
      ...baseRoutes,
      "GET /api/status": () => {
        statusCalls += 1;
        return { status: 200, body: statusCalls === 1 ? running : statusIdle };
      },
    });
    renderWithClient(<App />);

    expect(await screen.findByRole("button", { name: "Прогон №3 идёт…" })).toBeDisabled();
    const productCalls = () => requests.filter((r) => r.path === "/api/products").length;
    await screen.findByTestId("product-1");
    const beforeFinish = productCalls();

    // The next poll (ACTIVE_POLL_MS later) sees the run finished.
    await screen.findByRole("button", { name: "Запустить прогон" }, { timeout: 3000 });
    await waitFor(() => expect(productCalls()).toBe(beforeFinish + 1));
    expect(statusCalls).toBe(2);
  });

  it("refetches products read before a run that the first status already reports finished", async () => {
    // Page opened while a run is finishing: /api/products answers with the state before
    // the run, the first /api/status already sees the run finished.
    const stale: ProductsOut = {
      products: products.products.map((row) =>
        row.product.id === 1
          ? {
              ...row,
              published: { ...row.published!, price: "1260.00", origin: "seed", origin_label: "цена из исходных данных" },
              latest_calculation: null,
            }
          : row,
      ),
    };
    let productCalls = 0;
    let releaseStatus: () => void = () => {};
    const statusGate = new Promise<void>((resolve) => {
      releaseStatus = resolve;
    });
    let statusCalls = 0;
    const requests = mockApi({
      ...baseRoutes,
      "GET /api/products": () => {
        productCalls += 1;
        return { status: 200, body: productCalls === 1 ? stale : products };
      },
      "GET /api/status": async () => {
        statusCalls += 1;
        if (statusCalls === 1) await statusGate;
        return { status: 200, body: statusIdle };
      },
    });
    renderWithClient(<App />);

    // The stale table is rendered first, then the status arrives.
    const staleRow = await screen.findByTestId("product-1");
    expect(within(staleRow).getByText("1 260,00 ₽")).toBeInTheDocument();
    expect(within(staleRow).getByText("расчётов не было")).toBeInTheDocument();
    releaseStatus();

    await waitFor(() => expect(within(rowOf(1)).getByText("опубликована")).toBeInTheDocument());
    expect(within(rowOf(1)).getAllByText("1 240,00 ₽")).toHaveLength(2);
    expect(requests.filter((r) => r.path === "/api/products")).toHaveLength(2);
    expect(statusCalls).toBe(1);
  });
  it("refetches products whose stale initial response arrives after the finished status", async () => {
    // Initial /api/products read the database before the run but answers late,
    // after the first /api/status (run already finished) has been received.
    const stale: ProductsOut = {
      products: products.products.map((row) =>
        row.product.id === 1 ? { ...row, latest_calculation: null } : row,
      ),
    };
    let productCalls = 0;
    let releaseProducts: () => void = () => {};
    const productsGate = new Promise<void>((resolve) => {
      releaseProducts = resolve;
    });
    let statusCalls = 0;
    mockApi({
      ...baseRoutes,
      "GET /api/products": async () => {
        productCalls += 1;
        if (productCalls === 1) {
          await productsGate;
          return { status: 200, body: stale };
        }
        return { status: 200, body: products };
      },
      "GET /api/status": () => {
        statusCalls += 1;
        return { status: 200, body: statusIdle };
      },
    });
    renderWithClient(<App />);

    await screen.findByText("Последний прогон №3:");
    releaseProducts();

    await waitFor(() => expect(within(rowOf(1)).getByText("опубликована")).toBeInTheDocument());
    // The stale response is discarded and never replaces the fresh table.
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(within(rowOf(1)).queryByText("расчётов не было")).not.toBeInTheDocument();
    expect(productCalls).toBe(2);
    expect(statusCalls).toBe(1);
  });
});

describe("demo panel", () => {
  it("sends PUT when a mode is selected", async () => {
    const updated: SourceModeOut = {
      source: "html_catalog",
      source_label: "HTML-каталог",
      mode: "markup_changed",
      mode_label: "изменилась разметка",
      retry_after: 60,
    };
    const requests = mockApi({
      ...baseRoutes,
      "PUT /api/demo/modes/html_catalog": { body: updated },
    });
    const user = userEvent.setup();
    renderWithClient(<App />);

    const panel = await screen.findByLabelText("Демо");
    const select = await within(panel).findByRole("combobox", { name: /HTML-каталог/ });
    await user.selectOptions(select, "markup_changed");

    await within(panel).findByText("изменилась разметка");
    expect(requests).toContainEqual({
      method: "PUT",
      path: "/api/demo/modes/html_catalog",
      body: { mode: "markup_changed" },
    });
    expect(select).toHaveValue("markup_changed");
  });

  it("shows a clear message when reset is refused with 409", async () => {
    const conflict: ErrorOut = {
      detail: "Прогон 7 в очереди или выполняется: сброс после его завершения",
    };
    mockApi({ ...baseRoutes, "POST /api/demo/reset": { status: 409, body: conflict } });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();
    renderWithClient(<App />);

    const panel = await screen.findByLabelText("Демо");
    await user.click(within(panel).getByRole("button", { name: "Сбросить демо" }));

    const alert = await within(panel).findByRole("alert");
    expect(alert).toHaveTextContent("Сброс невозможен: идёт прогон.");
    expect(alert).toHaveTextContent("Прогон 7 в очереди или выполняется");
  });

  it("does not reset without confirmation and reports a successful reset", async () => {
    const result: DemoResetOut = { products: 25, published_prices: 24, modes: modes.modes };
    let wasReset = false;
    const requests = mockApi({
      ...baseRoutes,
      "GET /api/status": () => ({ status: 200, body: wasReset ? statusEmpty : statusIdle }),
      "POST /api/demo/reset": () => {
        wasReset = true;
        return { status: 200, body: result };
      },
    });
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    const user = userEvent.setup();
    renderWithClient(<App />);

    const panel = await screen.findByLabelText("Демо");
    const button = within(panel).getByRole("button", { name: "Сбросить демо" });
    await user.click(button);
    expect(requests.some((r) => r.path === "/api/demo/reset")).toBe(false);

    await user.click(button);
    await within(panel).findByText(/Демо сброшено: товаров 25, опубликованных цен 24/);
    expect(confirm).toHaveBeenCalledTimes(2);
    await waitFor(() =>
      expect(requests.filter((r) => r.path === "/api/demo/reset")).toHaveLength(1),
    );
  });
});

describe("quarantine confirmation", () => {
  const spikeConfirmed: PublishedPriceOut = {
    product_id: 2,
    brand: "MANN-FILTER",
    article: "W71275",
    name: "Фильтр масляный",
    price: "1490.00",
    published_at: "2026-11-12T04:11:00.000000Z",
    calculation_id: 27,
  };

  function confirmedProducts(): ProductsOut {
    return {
      products: products.products.map((row) =>
        row.product.id === 2 && row.latest_calculation
          ? {
              ...row,
              published: {
                price: "1490.00",
                published_at: spikeConfirmed.published_at,
                origin: "confirmation",
                origin_label: "подтверждена из карантина",
                calculation_id: 27,
                calculation_run_id: 2,
                calculated_at: row.latest_calculation.calculated_at,
              },
              latest_calculation: {
                ...row.latest_calculation,
                confirmation: {
                  confirmed: true,
                  confirmed_at: spikeConfirmed.published_at,
                  can_confirm: false,
                  refusal: "already_confirmed",
                  refusal_label: "рекомендация уже подтверждена и опубликована",
                },
              },
            }
          : row,
      ),
    };
  }

  it("shows the button only for quarantined rows, disabled with the reason when not allowed", async () => {
    mockApi(baseRoutes);
    renderWithClient(<App />);

    const s = await screen.findByTestId("product-2");
    expect(within(s).getByRole("button", { name: "Подтвердить" })).toBeEnabled();
    expect(within(rowOf(1)).queryByRole("button", { name: "Подтвердить" })).not.toBeInTheDocument();
    expect(within(rowOf(5)).queryByRole("button", { name: "Подтвердить" })).not.toBeInTheDocument();

    const febiButton = within(rowOf(4)).getByRole("button", { name: "Подтвердить" });
    expect(febiButton).toBeDisabled();
    const reason = "рекомендация не из последнего завершённого прогона: требуется новый расчёт";
    expect(febiButton).toHaveAttribute("title", reason);
    expect(within(rowOf(4)).getByText(reason)).toBeInTheDocument();
  });

  it("asks before publishing and does nothing when cancelled", async () => {
    const requests = mockApi(baseRoutes);
    const ask = vi.spyOn(window, "confirm").mockReturnValue(false);
    const user = userEvent.setup();
    renderWithClient(<App />);

    const s = await screen.findByTestId("product-2");
    await user.click(within(s).getByRole("button", { name: "Подтвердить" }));

    expect(ask).toHaveBeenCalledWith("Опубликовать 1 490,00 ₽ вместо 1 990,00 ₽?");
    expect(requests.some((r) => r.method === "POST")).toBe(false);
  });

  it("publishes on confirmation and refetches products and status", async () => {
    let confirmed = false;
    const requests = mockApi({
      ...baseRoutes,
      "GET /api/products": () => ({
        status: 200,
        body: confirmed ? confirmedProducts() : products,
      }),
      "POST /api/calculations/27/confirm": () => {
        confirmed = true;
        return { status: 200, body: spikeConfirmed };
      },
    });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();
    renderWithClient(<App />);

    const s = await screen.findByTestId("product-2");
    const productsBefore = requests.filter((r) => r.path === "/api/products").length;
    const statusBefore = requests.filter((r) => r.path === "/api/status").length;
    await user.click(within(s).getByRole("button", { name: "Подтвердить" }));

    await within(rowOf(2)).findByText(/^подтверждена, /);
    expect(requests.filter((r) => r.method === "POST")).toEqual([
      { method: "POST", path: "/api/calculations/27/confirm", body: undefined },
    ]);
    expect(requests.filter((r) => r.path === "/api/products").length).toBeGreaterThan(
      productsBefore,
    );
    expect(requests.filter((r) => r.path === "/api/status").length).toBeGreaterThan(statusBefore);
    const row = rowOf(2);
    expect(within(row).queryByRole("button", { name: "Подтвердить" })).not.toBeInTheDocument();
    expect(within(row).getByText("карантин")).toBeInTheDocument(); // the state stays
    expect(within(row).getAllByText("1 490,00 ₽")).toHaveLength(2); // published and recommended
    expect(within(row).getByText(/^подтверждена из карантина, /)).toBeInTheDocument();
  });

  it("shows the Russian reason when the server refuses with 409", async () => {
    const refusal: ConfirmRefusalOut = {
      code: "superseded",
      detail: "рекомендация не из последнего завершённого прогона: требуется новый расчёт",
    };
    mockApi({
      ...baseRoutes,
      "POST /api/calculations/27/confirm": { status: 409, body: refusal },
    });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();
    renderWithClient(<App />);

    const s = await screen.findByTestId("product-2");
    await user.click(within(s).getByRole("button", { name: "Подтвердить" }));

    const alert = await within(rowOf(2)).findByRole("alert");
    expect(alert).toHaveTextContent(refusal.detail);
  });

  it("offers the button in «Почему цена такая» of a quarantined calculation", async () => {
    const calculationS: CalculationOut = {
      ...calculationA,
      id: 27,
      run_id: 2,
      recommended_price: "1490.00",
      previous_price: "1990.00",
      state: "quarantine",
      state_label: "карантин",
      reason: "price_spike",
      reason_label: "скачок цены",
      confirmation: {
        confirmed: false,
        confirmed_at: null,
        can_confirm: true,
        refusal: null,
        refusal_label: null,
      },
    };
    const requests = mockApi({
      ...baseRoutes,
      "GET /api/calculations/27": { body: calculationS },
      "POST /api/calculations/27/confirm": { body: spikeConfirmed },
    });
    const ask = vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();
    renderWithClient(<App />);

    const s = await screen.findByTestId("product-2");
    await user.click(within(s).getByRole("button", { name: "Почему цена такая" }));
    const details = await screen.findByLabelText("Почему цена такая");
    await user.click(within(details).getByRole("button", { name: "Подтвердить" }));

    expect(ask).toHaveBeenCalledWith("Опубликовать 1 490,00 ₽ вместо 1 990,00 ₽?");
    await waitFor(() =>
      expect(requests.filter((r) => r.path === "/api/calculations/27/confirm")).toHaveLength(1),
    );
    // The details are refetched after the confirmation.
    await waitFor(() =>
      expect(requests.filter((r) => r.path === "/api/calculations/27")).toHaveLength(2),
    );
  });
});
