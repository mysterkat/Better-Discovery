import { api } from "./client";

export interface OpenFolderResponse {
  ok: boolean;
  opened: string;
}

export interface ClearCacheResponse {
  ok: boolean;
  total_files: number;
  total_bytes: number;
  folders: Record<string, { files_removed: number; bytes_removed: number }>;
}

/** Reveal a userdata folder (or the parent of a file) in the OS file manager. */
export async function openFolder(path: string): Promise<OpenFolderResponse> {
  return api<OpenFolderResponse>("POST", "/system/open-folder", { path });
}

// fix 4b: known cache type IDs (must match backend _ALL_CACHE_TYPES)
export type CacheType = "discovery" | "mql" | "library_reports";

export const CACHE_TYPE_LABELS: Record<CacheType, string> = {
  discovery:       "Discovery artifacts (.set/.csv/.png)",
  mql:             "MQL export files (.mq5/.set)",
  library_reports: "Library MT5 attachments (.htm/.csv per strategy)",
};

/** fix 4b: Delete selected cache types (or all if types is empty/omitted). */
export async function clearCache(types?: CacheType[]): Promise<ClearCacheResponse> {
  return api<ClearCacheResponse>("POST", "/system/clear-cache", types && types.length ? { types } : {});
}
