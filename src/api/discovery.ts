import { api } from "./client";

export interface JobRef {
  job_id: string;
  status: "pending" | "running" | "done" | "failed" | "cancelled";
  result?: unknown;
  error?: string;
}

export async function getDefaults(): Promise<Record<string, unknown>> {
  return api<Record<string, unknown>>("GET", "/discovery/defaults");
}

export async function startDiscovery(
  overrides: Record<string, unknown> = {},
): Promise<JobRef> {
  return api<JobRef>("POST", "/discovery/start", { overrides });
}

export async function getDiscoveryResults(jobId: string): Promise<JobRef> {
  return api<JobRef>("GET", `/discovery/results/${jobId}`);
}
