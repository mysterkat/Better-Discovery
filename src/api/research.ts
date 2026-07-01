import { api } from "./client";
import type { JobRef } from "./discovery";

export interface SavedStrategyReplayRequest {
  dataset_id: string;
  pattern_id: string;
  date_from: string;
  date_to: string;
  dataset_role: "validation" | "walk_forward" | "lockbox";
  initial_balance: number;
  lot_size: number;
  contract_size: number;
  commission_per_lot_round_turn: number;
  slippage_price_units: number;
}

export interface ReplayMetrics {
  trades: number;
  wins: number;
  win_rate_pct: number | null;
  net_profit: number;
  gross_profit: number;
  gross_loss: number;
  profit_factor: number | null;
  expected_payoff: number | null;
  max_drawdown: number;
  max_drawdown_pct: number;
}

export interface SavedStrategyReplayResult {
  experiment_id: string;
  pattern_id?: string;
  strategy_id?: string;
  library_name?: string;
  strategy_fingerprint: string;
  dataset_id: string;
  dataset_role: string;
  ledger_csv: string;
  ledger_parquet: string;
  metrics: ReplayMetrics & Record<string, number | null>;
  gate: {
    decision: "promote" | "reject";
    checks: Record<string, boolean>;
    policy?: Record<string, number>;
  };
}

export function runSavedStrategyReplay(request: SavedStrategyReplayRequest): Promise<JobRef> {
  return api("POST", "/research/saved-strategy-replay", request);
}

export interface RobustnessResult {
  artifact: string;
  method: string;
  warning: string;
  overall: { trades: number; observed_net_profit: number; p_value: number; z_score: number | null; null_p95: number };
  walk_forward: {
    positive_folds: number; significant_folds: number; required_positive_folds: number;
    folds: Array<{ fold: number; from: string; to: string; trades: number; net_profit: number; profit_factor: number | null; permutation_p_value: number }>;
  };
  gate: { decision: "pass" | "reject"; checks: Record<string, boolean> };
}

export function runLocalRobustness(ledgerPath: string): Promise<JobRef> {
  return api("POST", "/research/local-robustness", {
    ledger_path: ledgerPath, permutations: 5000, seed: 42, block_size: 5,
    walk_forward_folds: 5, significance_level: 0.05, min_positive_fold_fraction: 0.6,
  });
}

export interface ReplayExperiment {
  id: string;
  kind: "hypothesis_bar_replay";
  status: "completed" | "failed" | "running";
  created_at: string;
  request: SavedStrategyReplayRequest | Record<string, unknown>;
  result: SavedStrategyReplayResult | null;
}

export async function listSavedReplayExperiments(): Promise<ReplayExperiment[]> {
  const rows = await api<Array<ReplayExperiment | Record<string, unknown>>>("GET", "/research/experiments?limit=100");
  return rows.filter((row): row is ReplayExperiment =>
    (row as ReplayExperiment).kind === "hypothesis_bar_replay" &&
    (row as ReplayExperiment).status === "completed" &&
    (row as ReplayExperiment).result != null
  );
}
