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
  data_source?: "tradingview" | "mt5_html" | "local_ledger";
  pnl_csv_path?: string;       // TradingView CSV
  file_path_html?: string;     // MT5 Strategy Tester HTML
  local_ledger_path?: string;  // Local replay closed-trade ledger
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

export interface McCompareRequest {
  local_ledger_path: string;
  mt5_report_path: string;
  global_params?: Record<string, unknown>;
  phase1_params?: Record<string, unknown>;
  phase2_params?: Record<string, unknown>;
  funded_params?: Record<string, unknown>;
  longterm_params?: Record<string, unknown>;
}

export interface McCompareResult {
  parity: {
    decision: "pass" | "block"; local_trades: number; mt5_trades: number;
    trade_count_delta_pct: number; local_net_profit: number; mt5_net_profit: number;
    net_profit_delta_pct: number;
  };
  headlines: { local: Record<string, number | null>; mt5: Record<string, number | null>; delta: Record<string, number | null> };
}

export function compareMc(req: McCompareRequest): Promise<JobRef> {
  return api("POST", "/mc/compare", req);
}

export async function getMcResults(jobId: string): Promise<JobRef> {
  return api<JobRef>("GET", `/mc/results/${jobId}`);
}

// ── Result shapes (mirrors backend bridge return values) ────────────────

export interface PhaseResultsDF {
  columns: string[];
  records: Record<string, unknown>[];
}

export interface EvalPhaseResult {
  pass_rate: number;
  n_passed: number;
  n_failed: number;
  fail_pcts: { daily_dd: number; total_dd: number };
  daily_dd_breach_pct: number;
  total_dd_breach_pct: number;
  profit_shortfall_pct: number;
  avg_days: number;
  days_p10: number;
  days_p50: number;
  days_p90: number;
  days_worst: number;
  results_df: PhaseResultsDF;
  equity_curves?: number[][];
  // Echoed parameters used by the dashboard KPI table.
  balance: number;
  profit_pct: number;
  daily_dd_pct: number;
  total_dd_pct: number;
  min_days: number;
  // Phase-2 only — funnel & combined rate.
  n_p1_passed?: number;
  combined_pass_rate?: number;
  phase: "phase1" | "phase2";
}

export interface FundedResult {
  breach_rate: number;
  breach_before_payout_rate?: number;
  payout_rate: number;
  breach_pcts: { daily_dd: number; total_dd: number };
  avg_total_earnings: number;
  avg_payout_count: number;
  avg_first_payout_day: number;
  avg_days_active: number;
  results_df: PhaseResultsDF;
  equity_curves?: number[][];
  floor_curves?: number[][];
  survival?: number[];
  max_sim_days?: number;
  balance: number;
  daily_dd_pct: number;
  total_dd_pct: number;
  months: number;
}

export interface LongtermBenchmark {
  ticker?: string;
  start_price?: number;
  end_price?: number;
  annualized_return?: number;
  sharpe?: number;
  final_equity?: number;
  error?: string;
}

export interface LongtermResult {
  pass_rate: number;
  median_equity: number;
  p10_equity: number;
  p90_equity: number;
  median_max_dd: number;
  median_sharpe: number;
  annualized_return: number;
  benchmark: LongtermBenchmark | null;
  n_days: number;
  ruin_floor: number;
  balance: number;
  equity_paths: number[][];
  max_dd: number[];
  final_equity: number[];
  sharpe: number[];
}

export interface RegimeData {
  trans_matrix: number[][];     // 5x5 row-normalised transition probabilities
  stationary_dist: number[];    // length 5
  labels: string[];             // length 5 (e.g. TrendUp, TrendDn, …)
}

// ── Verdict / advanced result blocks (Tier-3 dashboard) ────────────────
//
// These mirror the shapes produced by the backend's verdict aggregator and
// the advanced sweep helpers. Every field is optional so the dashboard can
// degrade gracefully when an older backend doesn't emit them.

export interface VerdictBlock {
  pass_rate?: number;
  pass_rate_ci_low?: number;
  pass_rate_ci_high?: number;
  median_days?: number;
  dominant_fail?: string;
  dominant_fail_pct?: number;
  // Funded-phase variants
  payout_rate?: number;
  expected_monthly_usd?: number;
  expected_lifetime_months?: number;
  breach_rate?: number;
  breach_before_payout_rate?: number;
  dominant_breach?: string;
  // Long-term variants
  p_ruin_1y?: number;
  p_ruin_5y?: number;
  median_equity?: number;
  median_sharpe?: number;
}

export interface VerdictGlobal {
  challenge_fee?: number;
  fee_refunded_on_first_payout?: boolean;
  /** v0.5.0: replaces ``roi_pass_rate``. Real expected return per challenge
   *  attempt (refund-aware). Negative = expect to lose money. */
  avg_roi_pct?: number;
  kelly_fraction?: number;
  kelly_verdict?: string;
  intraday_dd_factor?: number;
}

export interface AllPhasesResult {
  phase1: EvalPhaseResult;
  phase2: EvalPhaseResult;
  funded: FundedResult;
  longterm: LongtermResult;
  regime: RegimeData | null;
  // ── Tier-3 verdict / advanced blocks (all optional) ──
  verdict?: {
    phase1?: VerdictBlock;
    phase2?: VerdictBlock;
    funded?: VerdictBlock;
    longterm?: VerdictBlock;
    global?: VerdictGlobal;
    combined_days_to_funded?: number;
  };
  lot_sweep?: Array<{ lot: number; pass_rate: number; median_earnings: number }>;
  payout_cadence_sweep?: Array<{
    cadence_days: number;
    total_earnings: number;
    breach_rate: number;
  }>;
  kelly?: {
    kelly_fraction: number;
    half_kelly: number;
    expected_log_growth: number;
  };
  ruin_horizons?: Array<{ days: number; p_ruin: number }>;
  funded_lifetime?: {
    median_months: number;
    p10_months: number;
    p90_months: number;
  };
}

// ── Run history (saved MC results) ─────────────────────────────────────

export interface McRunSummary {
  jobId: string;
  name: string;
  timestamp: number;
  params: Record<string, unknown>;
  pass_rate?: number;
  payout_rate?: number;
  preset_id?: string;
}

export async function listMcRuns(): Promise<McRunSummary[]> {
  return api<McRunSummary[]>("GET", "/mc/runs");
}

export async function saveMcRun(jobId: string, name: string): Promise<void> {
  await api<void>("POST", `/mc/runs/${jobId}`, { name });
}

export async function deleteMcRun(jobId: string): Promise<void> {
  await api<void>("DELETE", `/mc/runs/${jobId}`);
}

export type { JobRef };
