import { api } from "./client";
import type { JobRef } from "./discovery";

export interface DataPreview {
  id: string;
  path: string;
  n_rows: number;
  columns: string[];
  sample: Record<string, unknown>[];
}

export interface TfSpec {
  prefix: "m" | "h" | "d" | "W" | "M";
  time_value: number;
  trading_days: number;
}

export interface Mt5FetchRequest {
  symbol: string;
  save_folder: string;
  tf_specs: TfSpec[];
  /** Wipe recognized hist_data CSVs before fetching. Set only after user confirms. */
  clear_existing?: boolean;
}

export interface CurrentImportTf {
  label: string;
  filename: string;
  path: string;
  size_bytes: number;
  modified_at: string;
}

export interface CurrentImport {
  exists: boolean;
  /** Single shared symbol if every file matches; null if none or mixed. */
  symbol: string | null;
  timeframes: CurrentImportTf[];
  modified_at: string | null;
}

export interface Mt5CheckResult {
  ok: boolean;
  terminal?: string;
  account?: string;
  error?: string;
}

export interface Mt5FileResult {
  label: string;
  ok: boolean;
  candles: number;
  path: string;
  error: string | null;
}

export async function checkMt5(): Promise<Mt5CheckResult> {
  return api<Mt5CheckResult>("GET", "/data/mt5/check");
}

export async function getDefaultFolder(): Promise<string> {
  const r = await api<{ folder: string }>("GET", "/data/mt5/default_folder");
  return r.folder;
}

export async function calcCandles(
  prefix: string,
  time_value: number,
  trading_days: number,
): Promise<number> {
  const r = await api<{ candles: number }>(
    "GET",
    `/data/mt5/candles?prefix=${prefix}&time_value=${time_value}&trading_days=${trading_days}`,
  );
  return r.candles;
}

export async function fetchMt5Data(req: Mt5FetchRequest): Promise<JobRef> {
  return api<JobRef>("POST", "/data/mt5/fetch", req);
}

export async function getCurrentImport(): Promise<CurrentImport> {
  return api<CurrentImport>("GET", "/data/current-import");
}

export async function importCsv(path: string): Promise<DataPreview> {
  return api<DataPreview>("POST", "/data/import", { path });
}

export async function getPreview(id: string): Promise<DataPreview> {
  return api<DataPreview>("GET", `/data/preview/${id}`);
}
