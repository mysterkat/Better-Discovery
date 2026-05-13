/**
 * Zustand store for the user's saved Monte Carlo run history.
 *
 * Persistence lives on the backend (`/mc/runs`); this store is just an
 * in-memory cache + the load/save/remove actions that wrap the HTTP calls.
 * The list contains lightweight summaries so the RunHistory panel can render
 * without fetching every full result blob.
 */

import { create } from "zustand";
import { listMcRuns, saveMcRun, deleteMcRun, type McRunSummary } from "../api/mc";

export type { McRunSummary };

interface McRunsStore {
  runs: McRunSummary[];
  loading: boolean;
  load: () => Promise<void>;
  save: (jobId: string, name: string) => Promise<void>;
  remove: (jobId: string) => Promise<void>;
}

export const useMcRuns = create<McRunsStore>((set, get) => ({
  runs: [],
  loading: false,

  load: async () => {
    if (get().loading) return;
    set({ loading: true });
    try {
      const runs = await listMcRuns();
      // Newest first.
      runs.sort((a, b) => b.timestamp - a.timestamp);
      set({ runs, loading: false });
    } catch {
      // Backend may not yet implement /mc/runs — treat as empty list.
      set({ loading: false });
    }
  },

  save: async (jobId, name) => {
    try {
      await saveMcRun(jobId, name);
    } catch {
      // swallow — surface error via UI separately if needed
    }
    // Refresh the list so the new entry appears (with backend-derived
    // headline metrics).
    await get().load();
  },

  remove: async (jobId) => {
    // Optimistic removal — refresh on failure.
    const before = get().runs;
    set({ runs: before.filter((r) => r.jobId !== jobId) });
    try {
      await deleteMcRun(jobId);
    } catch {
      set({ runs: before });
    }
  },
}));
