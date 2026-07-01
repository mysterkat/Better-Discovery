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

export interface StrategyValidationRequest {
  dataset_id: string;
  pattern_id: string;
  date_from: string;
  date_to: string;
  initial_balance: number;
  lot_size: number;
  contract_size: number;
  commission_per_lot_round_turn: number;
  slippage_price_units: number;
  oos_fraction: number;
  walk_train_months: number;
  walk_test_months: number;
  walk_mutation_samples: number;
  stability_samples: number;
  stability_seed: number;
  min_profit_factor: number;
  min_sharpe: number;
  max_drawdown_pct: number;
  min_walk_forward_pass_rate: number;
  min_stability_pass_rate: number;
  min_trades: number;
}

export interface ValidationGate {
  decision: "pass" | "reject";
  checks: Record<string, boolean>;
}

export interface ValidationSegment {
  from: string;
  to: string;
  metrics: ReplayMetrics & Record<string, number | null>;
  gate: ValidationGate;
  ledger_csv?: string;
  ledger_parquet?: string | null;
}

export interface StrategyValidationResult {
  experiment_id: string;
  pattern_id: string;
  strategy_id: string;
  library_name: string;
  dataset_id: string;
  artifact_folder: string;
  is_oos: {
    split_time: string;
    in_sample: ValidationSegment;
    out_of_sample: ValidationSegment;
  };
  walk_forward: {
    train_months: number;
    test_months: number;
    mutation_samples_per_fold: number;
    fold_count: number;
    pass_count: number;
    pass_rate: number;
    decision: "pass" | "reject";
    folds: Array<{
      fold: number;
      train_from: string;
      train_to: string;
      test_from: string;
      test_to: string;
      selected_strategy_id: string;
      selected_parent?: string | null;
      train_metrics: ReplayMetrics & Record<string, number | null>;
      test_metrics: ReplayMetrics & Record<string, number | null>;
      gate: ValidationGate;
    }>;
  };
  parameter_stability: {
    samples: number;
    pass_count: number;
    pass_rate: number;
    decision: "pass" | "reject";
    variants: Array<{
      strategy_id: string;
      parent?: string | null;
      metrics: ReplayMetrics & Record<string, number | null>;
      gate: ValidationGate;
    }>;
  };
  regime_breakdown: Record<string, Array<{ bucket: string; metrics: ReplayMetrics & Record<string, number | null> }>>;
  overall: {
    decision: "pass" | "reject";
    checks: Record<string, boolean>;
    runtime_seconds: number;
  };
}

export function runStrategyValidation(request: StrategyValidationRequest): Promise<JobRef> {
  return api("POST", "/research/strategy-validation", request);
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
