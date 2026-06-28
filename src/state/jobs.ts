/**
 * Zustand store for tracking in-flight and completed jobs.
 * Each job is subscribed via SSE at /jobs/{job_id}/events.
 *
 * `activeByKind` maps a tab/kind name (e.g. "discovery", "mt5_fetch") to the
 * id of the most recently created job of that kind. Tabs use this to recover
 * an in-flight job after they remount (e.g. after the user switched tabs).
 */

import { create } from "zustand";
import { api, getBaseUrl } from "../api/client";

export type JobStatus = "pending" | "running" | "done" | "failed" | "cancelled";

export interface Job {
  job_id: string;
  kind: string;
  status: JobStatus;
  progress?: number;
  stage_name?: string | null;
  stage_index?: number | null;
  stage_total?: number | null;
  eta_seconds?: number | null;
  seed_index?: number | null;
  seed_total?: number | null;
  seed_value?: number | null;
  started_at?: number | null;
  finished_at?: number | null;
  cancel_requested?: boolean;
  meta?: {
    import_metrics?: {
      completed_timeframes?: number;
      total_timeframes?: number;
      last_symbol?: string;
      last_timeframe?: string;
      last_rows?: number;
      last_file_bytes?: number;
      download_rate_label?: string;
      write_rate_label?: string;
      eta_seconds?: number | null;
    };
    [key: string]: unknown;
  };
  result?: unknown;
  error?: string;
}

interface JobsStore {
  jobs: Record<string, Job>;
  /** kind → most-recent jobId of that kind. Survives tab unmount. */
  activeByKind: Record<string, string>;
  upsert: (job: Job) => void;
  /** Register a newly-created job as the active one for its kind. */
  setActive: (kind: string, jobId: string) => void;
  /** Subscribe to SSE for a job; returns an unsubscribe function. */
  subscribe: (jobId: string) => () => void;
  /** Request cancellation of a running job. */
  cancel: (jobId: string) => Promise<void>;
}

export const useJobs = create<JobsStore>((set, get) => ({
  jobs: {},
  activeByKind: {},

  upsert: (job) =>
    set((s) => ({ jobs: { ...s.jobs, [job.job_id]: job } })),

  setActive: (kind, jobId) =>
    set((s) => ({ activeByKind: { ...s.activeByKind, [kind]: jobId } })),

  subscribe: (jobId) => {
    let sse: EventSource | null = null;
    let closed = false;

    const connect = async () => {
      if (closed) return;
      const base = await getBaseUrl();
      sse = new EventSource(`${base}/jobs/${jobId}/events`);

      sse.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data as string) as Job;
          get().upsert(data);
          if (data.status === "done" || data.status === "failed" || data.status === "cancelled") {
            sse?.close();
          }
        } catch {
          /* ignore malformed events */
        }
      };

      sse.onerror = () => {
        sse?.close();
      };
    };

    connect();

    return () => {
      closed = true;
      sse?.close();
    };
  },

  cancel: async (jobId) => {
    try {
      await api("POST", `/jobs/${jobId}/cancel`);
      // Optimistically reflect the cancel request — the SSE stream will
      // confirm with the actual terminal status shortly.
      const cur = get().jobs[jobId];
      if (cur) get().upsert({ ...cur, cancel_requested: true });
    } catch {
      // Cancel request itself failed; silently ignore — user can try again.
    }
  },
}));
