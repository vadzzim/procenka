import { useEffect, useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./client";
import type { DemoMode, DemoModesOut, Source } from "./types";

export const queryKeys = {
  status: ["status"] as const,
  products: ["products"] as const,
  modes: ["modes"] as const,
  calculation: (id: number) => ["calculation", id] as const,
};

/** While a run is queued or running, poll every second; otherwise rarely. */
export const ACTIVE_POLL_MS = 1_000;
export const IDLE_POLL_MS = 15_000;

export function useStatus() {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: queryKeys.status,
    queryFn: api.status,
    refetchInterval: (q) => (q.state.data?.active_run ? ACTIVE_POLL_MS : IDLE_POLL_MS),
  });

  // When the last finished run changes (a run completed, or the demo was reset),
  // the products table is stale. The first successful status counts as a change too:
  // the initial /api/products may have been read before a run that this status
  // already reports as finished, so it is refetched. An in-flight request is cancelled
  // explicitly first: invalidateQueries does not cancel an initial load (no data yet)
  // and would let its stale response win.
  const finishedId = query.data ? (query.data.last_finished_run?.id ?? null) : undefined;
  const synced = useRef<number | null | undefined>(undefined);
  useEffect(() => {
    if (finishedId === undefined || synced.current === finishedId) return;
    synced.current = finishedId;
    void (async () => {
      await queryClient.cancelQueries({ queryKey: queryKeys.products });
      await queryClient.invalidateQueries({ queryKey: queryKeys.products });
      // Whether a calculation can still be confirmed depends on the latest run.
      await queryClient.invalidateQueries({ queryKey: ["calculation"] });
    })();
  }, [finishedId, queryClient]);

  return query;
}

export function useProducts() {
  return useQuery({ queryKey: queryKeys.products, queryFn: api.products });
}

export function useCalculation(id: number) {
  return useQuery({
    queryKey: queryKeys.calculation(id),
    queryFn: () => api.calculation(id),
    // The numbers never change; the confirmation part is refreshed explicitly
    // (after a confirmation and when a run finishes, see useStatus).
    staleTime: Infinity,
  });
}

/** Confirm a quarantined recommendation; then the table, status and details are refetched. */
export function useConfirm() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.confirm(id),
    onSettled: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.products }),
        queryClient.invalidateQueries({ queryKey: queryKeys.status }),
        queryClient.invalidateQueries({ queryKey: ["calculation"] }),
      ]);
    },
  });
}

export function useStartRun() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: api.startRun,
    onSettled: () => queryClient.invalidateQueries({ queryKey: queryKeys.status }),
  });
}

export function useModes() {
  return useQuery({ queryKey: queryKeys.modes, queryFn: api.modes });
}

export function useSetMode() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ source, mode }: { source: Source; mode: DemoMode }) =>
      api.setMode(source, mode),
    onSuccess: (updated) => {
      queryClient.setQueryData<DemoModesOut>(queryKeys.modes, (old) =>
        old
          ? { modes: old.modes.map((m) => (m.source === updated.source ? updated : m)) }
          : old,
      );
    },
    onError: () => queryClient.invalidateQueries({ queryKey: queryKeys.modes }),
  });
}

export function useResetDemo() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: api.resetDemo,
    onSuccess: (result) => {
      queryClient.setQueryData<DemoModesOut>(queryKeys.modes, { modes: result.modes });
      queryClient.removeQueries({ queryKey: ["calculation"] });
      void queryClient.invalidateQueries({ queryKey: queryKeys.status });
      void queryClient.invalidateQueries({ queryKey: queryKeys.products });
    },
  });
}
