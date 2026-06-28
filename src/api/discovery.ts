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
  /** "core" = always visible in the per-run accordion.
   *  "advanced" = hidden behind a per-group "Show advanced (N)" collapse.
   *  Defaults to "core" when the backend omits the field (older builds). */
  tier?: "core" | "advanced";
  /** When present, this param's entire group is only active when the gating
   *  param equals the given value. The frontend dims/collapses the group
   *  otherwise. */
  gated_by?: { key: string; value: string };
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

export type HypothesisFamily =
  | "strategy_grammar"
  | "time_series_breakout"
  | "session_range_breakout"
  | "trend_pullback"
  | "volatility_expansion"
  | "regime_mean_reversion"
  | "liquidity_sweep_reclaim"
  | "failed_breakout_reversal"
  | "prior_day_level_continuation"
  | "volatility_spike_reversal"
  | "opening_range_continuation_reversal"
  | "trend_day_pullback"
  | "day_time_regime_filter"
  | "inside_bar_expansion";

export interface HypothesisStrategySpec {
  schema_version?: number;
  strategy_id: string;
  lineage: HypothesisFamily;
  hypothesis: string;
  timeframe: "m1" | "m5" | "m10" | "m15";
  context_timeframes?: ("h1" | "h4")[];
  parameters: Record<string, unknown>;
}

export interface HypothesisDiscoveryRequest {
  dataset_id: string;
  symbol: "XAUUSD";
  timeframe: "m1" | "m5" | "m10" | "m15";
  date_from: string;
  date_to: string;
  families?: HypothesisFamily[];
  max_variants: number;
  min_closed_trades: number;
  min_trades_per_week?: number;
  parallel_workers?: number;
  lot_size?: number;
  contract_size?: number;
  commission_per_lot_round_turn?: number;
  slippage_price_units?: number;
  challenge: {
    initial_balance?: number;
    target_profit_pct: number;
    daily_loss_pct: number;
    max_loss_pct: number;
    max_attempt_days: number;
    start_frequency: string;
    risk_fractions: number[];
    internal_daily_stop_pcts: number[];
    max_trades_per_day_options: number[];
  };
}

export async function startHypothesisDiscovery(
  request: HypothesisDiscoveryRequest,
): Promise<JobRef> {
  return startDiscovery({ engine: "hypothesis", ...request });
}

export async function getDiscoveryResults(jobId: string): Promise<JobRef> {
  return api<JobRef>("GET", `/discovery/results/${jobId}`);
}

export interface SoftFail {
  name: string;
  value: number;
  threshold: number;
  mode: "min" | "max";
}

export interface PatternSummary {
  rank: number;
  pattern_id: string;
  cluster: number;
  direction: string;
  seed: number;
  bidir_mode: string;
  marginal: boolean;
  soft_fail: SoftFail | null;
  composite_score: number;
  // Train (in-sample) metrics
  train_wr: number;
  train_wilson_wr: number;
  train_pf: number;
  train_trades: number;
  train_per_day: number;
  // Test (out-of-sample) metrics
  test_score: number;
  test_wr: number;
  test_pf: number;
  test_trades: number;
  ea_test_wr: number;
  ea_test_wilson_wr: number;
  ea_test_pf: number;
  ea_test_trades: number;
  ea_test_expectancy_r: number;
  ea_test_breakeven_wr: number;
  overall_wr: number;
  recent_wr: number;
  consistency: number;
  implied_rr: number;
  sl_pct: number;
  tp_pct: number;
  set_file: string | null;
  /** v0.6.0: rule conditions { indicator_name: [lower_bound, upper_bound] }.
   *  Used by the results UI to render an Indicators table per pattern. */
  genetic_rule?: Record<string, [number, number]>;
}

export interface DiscoveryOverview {
  avg_ea_test_wr?: number | null;
  avg_ea_test_pf?: number | null;
  avg_ea_test_expectancy_r?: number | null;
  total_ea_test_trades?: number;
  avg_test_wr?: number | null;
  avg_test_pf?: number | null;
  avg_train_wr?: number | null;
  avg_train_pf?: number | null;
  total_test_trades?: number;
}

export interface SetFileResponse {
  path: string;
  name: string;
  content: string;
}

export async function getSetFileContent(path: string): Promise<SetFileResponse> {
  return api<SetFileResponse>(
    "GET",
    `/discovery/set-file?path=${encodeURIComponent(path)}`,
  );
}
