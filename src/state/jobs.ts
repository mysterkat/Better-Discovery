/**
 * Zustand store for tracking in-flight and completed jobs.
 * Each job is subscribed via SSE at /jobs/{job_id}/events.
 */

import { create } from "zustand";
import { getBaseUrl } from "../api/client";

export type JobStatus = "pending" | "running" | "done" | "failed" | "cancelled";

export interface Job {
  job_id: string;
  kind: string;
  status: JobStatus;
  result?: unknown;
  error?: string;
}

interface JobsStore {
  jobs: Record<string, Job>;
  upsert: (job: Job) => void;
  /** Subscribe to SSE for a job; returns an unsubscribe function. */
  subscribe: (jobId: string) => () => void;
}

export const useJobs = create<JobsStore>((set, get) => ({
  jobs: {},

  upsert: (job) =>
    set((s) => ({ jobs: { ...s.jobs, [job.job_id]: job } })),

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
          if (data.status === "done" || data.status === "failed") {
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
}));
