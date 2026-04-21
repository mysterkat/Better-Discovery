import { api } from "./client";
import type { JobRef } from "./discovery";

export type McPhase = "phase1" | "phase2" | "funded" | "longterm";

export interface McRunRequest {
  phase: McPhase;
  pnl?: number[];
  pnl_csv_path?: string;
  pnl_split?: string;
  params?: Record<string, unknown>;
}

export async function getMetrics(): Promise<{ phases: string[]; advanced: string[] }> {
  return api("GET", "/mc/metrics");
}

export async function runMc(req: McRunRequest): Promise<JobRef> {
  return api<JobRef>("POST", "/mc/run", req);
}

export async function getMcResults(jobId: string): Promise<JobRef> {
  return api<JobRef>("GET", `/mc/results/${jobId}`);
}

export type { JobRef };
