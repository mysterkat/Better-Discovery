import { api } from "./client";
import type { JobRef, ParamDef } from "./discovery";

export type McPhase = "phase1" | "phase2" | "funded" | "longterm";

export interface McRunRequest {
  phase: McPhase;
  pnl?: number[];
  pnl_csv_path?: string;
  pnl_split?: string;
  params?: Record<string, unknown>;
}

export interface McRunAllRequest {
  pnl?: number[];
  data_source?: "tradingview" | "mt5_html";
  pnl_csv_path?: string;       // TradingView CSV
  file_path_html?: string;     // MT5 Strategy Tester HTML
  pnl_split?: string;
  global_params?: Record<string, unknown>;
  phase1_params?: Record<string, unknown>;
  phase2_params?: Record<string, unknown>;
  funded_params?: Record<string, unknown>;
  longterm_params?: Record<string, unknown>;
}

export async function getMetrics(): Promise<{ phases: string[]; advanced: string[] }> {
  return api("GET", "/mc/metrics");
}

export async function getMcParams(): Promise<ParamDef[]> {
  return api<ParamDef[]>("GET", "/mc/params");
}

export async function getMcDefaults(): Promise<Record<string, unknown>> {
  return api<Record<string, unknown>>("GET", "/mc/defaults");
}

export async function runMc(req: McRunRequest): Promise<JobRef> {
  return api<JobRef>("POST", "/mc/run", req);
}

export async function runAllPhases(req: McRunAllRequest): Promise<JobRef> {
  return api<JobRef>("POST", "/mc/run_all", req);
}

export async function getMcResults(jobId: string): Promise<JobRef> {
  return api<JobRef>("GET", `/mc/results/${jobId}`);
}

export type { JobRef };
