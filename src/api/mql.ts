import { api } from "./client";
import type { HypothesisStrategySpec } from "./discovery";

export interface MqlExportResult {
  ok: boolean;
  path: string;
  inputs_present: number;
  inputs_required: number;
  missing_inputs: string[];
  has_commission_r: boolean;
  has_swap_r_per_bar: boolean;
}

export interface HypothesisMqlExportRequest {
  strategy: HypothesisStrategySpec;
  output_name?: string | null;
  risk_fraction?: number;
  daily_loss_pct?: number;
  max_loss_pct?: number;
  max_trades_per_day?: number;
  max_spread_points?: number;
}

export interface HypothesisMqlExportResult {
  ok: boolean;
  mq5_path: string;
  set_path: string;
  spec_path: string;
  preferred_mq5_path?: string;
  mt5_installed?: boolean;
  mt5_mq5_path?: string | null;
  mt5_set_path?: string | null;
  mt5_spec_path?: string | null;
  mt5_data_path?: string | null;
  mt5_experts_folder?: string | null;
  strategy_id: string;
  lineage: string;
  magic_number: number;
  warnings: string[];
}

export async function getTemplate(): Promise<{ path: string }> {
  return api("GET", "/mql/template");
}

export async function exportMql(
  setContent: string,
  templatePath?: string | null,
  outputName?: string | null,
): Promise<MqlExportResult> {
  return api<MqlExportResult>("POST", "/mql/export", {
    set_content: setContent,
    template_path: templatePath ?? null,
    output_name: outputName ?? null,
  });
}

export async function exportHypothesisEa(
  request: HypothesisMqlExportRequest,
): Promise<HypothesisMqlExportResult> {
  return api<HypothesisMqlExportResult>("POST", "/mql/hypothesis-export", request);
}
