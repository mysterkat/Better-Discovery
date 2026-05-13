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

/** Delete generated .set/.mq5/.csv artifacts from userdata/discovery and userdata/mql. */
export async function clearCache(): Promise<ClearCacheResponse> {
  return api<ClearCacheResponse>("POST", "/system/clear-cache");
}
