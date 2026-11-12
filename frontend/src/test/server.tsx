import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { vi } from "vitest";

export interface RecordedRequest {
  method: string;
  path: string;
  body: unknown;
}

type Reply = { status: number; body: unknown };
type Handler = (request: RecordedRequest) => Reply | Promise<Reply>;

/**
 * Stub `fetch` with a route table: "METHOD /path" → JSON response.
 * Returns the list of requests the app made.
 */
export function mockApi(routes: Record<string, Handler | { status?: number; body: unknown }>) {
  const requests: RecordedRequest[] = [];
  const fetchMock = vi.fn(async (input: Request) => {
    const url = new URL(input.url);
    const text = await input.text();
    const request: RecordedRequest = {
      method: input.method,
      path: url.pathname,
      body: text ? (JSON.parse(text) as unknown) : undefined,
    };
    requests.push(request);
    const route = routes[`${request.method} ${request.path}`];
    if (!route) {
      return new Response(JSON.stringify({ detail: "not mocked" }), { status: 404 });
    }
    const { status = 200, body } = typeof route === "function" ? await route(request) : route;
    return new Response(JSON.stringify(body), {
      status,
      headers: { "content-type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return requests;
}

export function renderWithClient(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}
