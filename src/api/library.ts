import { api, getBaseUrl } from "./client";
import type { PatternSummary } from "./discovery";

export type AttachKind = "mt5_html" | "mt5_csv";

export interface LibraryEntry {
  pattern_id: string;
  saved_at: string;
  lib_path: string;
  set_path: string | null;
  csv_path: string | null;
  mt5_html_path: string | null;
  mt5_csv_path: string | null;
  /** Full PatternSummary at save time. Stored verbatim so the compare view
   *  keeps working even after the originating discovery run is gone. */
  metadata: PatternSummary | Record<string, unknown>;
}

export interface LibrarySaveResponse {
  entry: LibraryEntry;
  duplicate: boolean;
}

export interface LibrarySaveRequest {
  pattern_id: string;
  set_file: string;
  metadata: PatternSummary | Record<string, unknown>;
}

export interface LibraryAttachRequest {
  pattern_id: string;
  kind: AttachKind;
  content_b64: string;
}

export async function saveToLibrary(req: LibrarySaveRequest): Promise<LibrarySaveResponse> {
  return api<LibrarySaveResponse>("POST", "/library/save", req);
}

export async function listLibrary(): Promise<LibraryEntry[]> {
  return api<LibraryEntry[]>("GET", "/library/list");
}

export async function deleteLibraryEntry(patternId: string): Promise<void> {
  await api("DELETE", `/library/${encodeURIComponent(patternId)}`);
}

/** Read a File/Blob and resolve to its base64 contents (without the data: prefix). */
function readAsBase64(file: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error ?? new Error("FileReader failed"));
    reader.onload = () => {
      const result = reader.result;
      if (typeof result !== "string") {
        reject(new Error("FileReader returned non-string"));
        return;
      }
      const comma = result.indexOf(",");
      resolve(comma >= 0 ? result.slice(comma + 1) : result);
    };
    reader.readAsDataURL(file);
  });
}

export async function attachToLibrary(
  patternId: string,
  kind: AttachKind,
  file: Blob,
): Promise<LibraryEntry> {
  const content_b64 = await readAsBase64(file);
  return api<LibraryEntry>("POST", "/library/attach", {
    pattern_id: patternId,
    kind,
    content_b64,
  });
}

/** Absolute URL the Compare tab feeds into <iframe src> for the MT5 HTML report. */
export async function getMt5HtmlUrl(patternId: string): Promise<string> {
  const base = await getBaseUrl();
  return `${base}/library/${encodeURIComponent(patternId)}/mt5_html`;
}

async function fetchCsvText(patternId: string, slug: "trades_csv" | "mt5_csv"): Promise<string | null> {
  const base = await getBaseUrl();
  const r = await fetch(`${base}/library/${encodeURIComponent(patternId)}/${slug}`);
  if (r.status === 404) return null;
  if (!r.ok) throw new Error(`CSV fetch failed (${r.status})`);
  return r.text();
}

/** Discovery trades CSV (entry_time, exit_time, pnl_pts, ...). Null if not saved. */
export const getDiscoveryTradesCsv = (id: string) => fetchCsvText(id, "trades_csv");

/** Attached MT5 trades CSV. Null if not attached. */
export const getMt5TradesCsv = (id: string) => fetchCsvText(id, "mt5_csv");
