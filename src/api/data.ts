import { api } from "./client";

export interface DataPreview {
  id: string;
  path: string;
  n_rows: number;
  columns: string[];
  sample: Record<string, unknown>[];
}

export async function importCsv(path: string): Promise<DataPreview> {
  return api<DataPreview>("POST", "/data/import", { path });
}

export async function getPreview(id: string): Promise<DataPreview> {
  return api<DataPreview>("GET", `/data/preview/${id}`);
}
