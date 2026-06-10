import { api } from "./client";

export interface MqlExportResult {
  ok: boolean;
  path: string;
  inputs_present: number;
  inputs_required: number;
  missing_inputs: string[];
  has_commission_r: boolean;
  has_swap_r_per_bar: boolean;
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
