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

export interface Mt5FetchManyRequest {
  /** Basket of MT5 symbols fetched into one folder for multi-instrument discovery. */
  symbols: string[];
  save_folder: string;
  tf_specs: TfSpec[];
  /** Wipe the folder ONCE before the first symbol; the rest accumulate. */
  clear_existing?: boolean;
}

/** Per-symbol result entry returned by the fetch-many job. */
export interface Mt5SymbolResult {
  symbol: string;
  ok?: boolean;
  terminal?: string;
  save_folder?: string;
  files?: Mt5FileResult[];
  error?: string;
}

export async function fetchMt5DataMany(req: Mt5FetchManyRequest): Promise<JobRef> {
  return api<JobRef>("POST", "/data/mt5/fetch-many", req);
}

export async function getCurrentImport(): Promise<CurrentImport> {
  return api<CurrentImport>("GET", "/data/current-import");
}

export async function clearCurrentImport(): Promise<{ deleted: string[]; kept: string[] }> {
  return api<{ deleted: string[]; kept: string[] }>("DELETE", "/data/current-import");
}

export async function importCsv(path: string): Promise<DataPreview> {
  return api<DataPreview>("POST", "/data/import", { path });
}

export async function getPreview(id: string): Promise<DataPreview> {
  return api<DataPreview>("GET", `/data/preview/${id}`);
}

export interface MarketDataProvider {
  id: "dukascopy";
  name: string;
  venue: string;
  supports_ticks: boolean;
  supports_bars: boolean;
}

export interface DatasetFile {
  kind: "ticks" | "bars" | "discovery_csv";
  symbol: string;
  timeframe?: string | null;
  path: string;
  rows: number;
  sha256: string;
  first_time?: string | null;
  last_time?: string | null;
  quality?: Record<string, unknown>;
}

export interface MarketDataset {
  dataset_id: string;
  state: "building" | "complete" | "failed";
  provider: string;
  venue: string;
  symbols: string[];
  timeframes: string[];
  requested_from: string;
  requested_to: string;
  created_at: string;
  files: DatasetFile[];
  quality: Record<string, unknown>;
  import_options?: {
    include_ticks?: boolean;
    write_discovery_csv?: boolean;
    price_digits?: Record<string, number>;
    storage_layout?: string;
  };
  progress?: Record<string, unknown>;
  error?: string | null;
}

export interface ProviderFetchRequest {
  provider: "dukascopy";
  symbols: string[];
  timeframes: string[];
  date_from: string;
  date_to: string;
  include_ticks: boolean;
  write_discovery_csv: boolean;
  price_digits?: Record<string, number>;
  resume_dataset_id?: string;
}

export function getMarketDataProviders(): Promise<MarketDataProvider[]> {
  return api("GET", "/data/providers");
}

export function listMarketDatasets(): Promise<MarketDataset[]> {
  return api("GET", "/data/datasets");
}

export function deleteMarketDataset(datasetId: string): Promise<{ dataset_id: string; deleted_path: string }> {
  return api("DELETE", `/data/datasets/${encodeURIComponent(datasetId)}`);
}

export function fetchProviderData(req: ProviderFetchRequest): Promise<JobRef> {
  return api("POST", "/data/provider/fetch", req);
}

// ── v0.7.0: MT5 indicator install + auto-chart setup ─────────────────────────

export interface Mt5InstallResult {
  ok: boolean;
  error?: string;
  mt5_paths?: { install: string; data: string; common: string };
  indicators?: { copied: string[]; skipped: string[] };
  helper_ea?: { copied: boolean; path: string };
  compiled?: { name: string; ok: boolean; log: string }[];
  metaeditor?: "found" | "missing";
  next_steps?: string[];
}

export interface Mt5ApplySetupRequest {
  symbol: string;
  /** Optional multi-instrument basket; charts open for the (symbol × tf) cross-product. */
  symbols?: string[];
  timeframes: string[];
  /** Subset of {BD_PinBar, BD_MacdNorm, …} — undefined = all 12 */
  indicators?: string[];
  /** HTF used by BD_HtfDiv (default "M15") */
  htf_for_div?: string;
  /** Seconds to wait for the helper EA's ack file (default 10) */
  wait_for_ack_s?: number;
}

export interface Mt5SetupAck {
  version_acked: number;
  timestamp: number;
  opened: { symbol: string; timeframe: string; chart_id: number; ok: boolean }[];
  errors: string[];
}

export interface Mt5ApplySetupResult {
  ok: boolean;
  error?: string;
  config?: { version: number; config_path: string };
  ack?: Mt5SetupAck;
  acked?: boolean;
}

export async function installMt5Helper(): Promise<Mt5InstallResult> {
  return api<Mt5InstallResult>("POST", "/data/mt5/install-helper");
}

export async function applyMt5Setup(req: Mt5ApplySetupRequest): Promise<Mt5ApplySetupResult> {
  return api<Mt5ApplySetupResult>("POST", "/data/mt5/apply-setup", req);
}
