import { api } from "./client";

export interface JobRef {
  job_id: string;
  status: "pending" | "running" | "done" | "failed" | "cancelled";
  result?: unknown;
  error?: string;
}

export interface ParamDef {
  key: string;
  value: unknown;
  label: string;
  group: string;
  type: "int" | "float" | "bool" | "str" | "folder";
  description: string;
  min?: number;
  max?: number;
  step?: number;
  options?: string[];
}

export async function getDefaults(): Promise<Record<string, unknown>> {
  return api<Record<string, unknown>>("GET", "/discovery/defaults");
}

export async function getParams(): Promise<ParamDef[]> {
  return api<ParamDef[]>("GET", "/discovery/params");
}

export async function startDiscovery(
  overrides: Record<string, unknown> = {},
): Promise<JobRef> {
  return api<JobRef>("POST", "/discovery/start", { overrides });
}

export async function getDiscoveryResults(jobId: string): Promise<JobRef> {
  return api<JobRef>("GET", `/discovery/results/${jobId}`);
}
