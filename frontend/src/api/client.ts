import createClient from "openapi-fetch";

import type { paths } from "./schema";
import type {
  CalculationOut,
  DemoMode,
  DemoModesOut,
  DemoResetOut,
  ProductsOut,
  PublishedPriceOut,
  RunCreated,
  Source,
  SourceModeOut,
  StatusOut,
} from "./types";

const client = createClient<paths>({
  // Same origin: nginx (or the Vite dev proxy) forwards /api to the backend.
  baseUrl: globalThis.location?.origin ?? "",
  // Resolve fetch on every call, so tests can stub it.
  fetch: (request) => globalThis.fetch(request),
});

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

function errorMessage(error: unknown, status: number): string {
  if (error && typeof error === "object" && "detail" in error) {
    const { detail } = error as { detail: unknown };
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) return "Некорректный запрос";
  }
  return `Ошибка сервера: HTTP ${status}`;
}

async function unwrap<T>(
  call: Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<T> {
  const { data, error, response } = await call;
  if (data === undefined) {
    throw new ApiError(response.status, errorMessage(error, response.status));
  }
  return data;
}

export const api = {
  status: (): Promise<StatusOut> => unwrap(client.GET("/api/status")),
  products: (): Promise<ProductsOut> => unwrap(client.GET("/api/products")),
  calculation: (id: number): Promise<CalculationOut> =>
    unwrap(
      client.GET("/api/calculations/{calculation_id}", {
        params: { path: { calculation_id: id } },
      }),
    ),
  startRun: (): Promise<RunCreated> => unwrap(client.POST("/api/runs")),
  /** 409 carries the Russian reason in `detail`: it becomes the error message. */
  confirm: (id: number): Promise<PublishedPriceOut> =>
    unwrap(
      client.POST("/api/calculations/{calculation_id}/confirm", {
        params: { path: { calculation_id: id } },
      }),
    ),
  modes: (): Promise<DemoModesOut> => unwrap(client.GET("/api/demo/modes")),
  setMode: (source: Source, mode: DemoMode): Promise<SourceModeOut> =>
    unwrap(
      client.PUT("/api/demo/modes/{source}", {
        params: { path: { source } },
        body: { mode },
      }),
    ),
  resetDemo: (): Promise<DemoResetOut> => unwrap(client.POST("/api/demo/reset")),
};
