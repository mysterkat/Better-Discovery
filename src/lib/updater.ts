import { check, type Update } from "@tauri-apps/plugin-updater";
import { relaunch } from "@tauri-apps/plugin-process";
import { getVersion } from "@tauri-apps/api/app";

export type UpdateState =
  | { status: "idle" }
  | { status: "checking" }
  | { status: "up-to-date" }
  | { status: "available"; version: string; notes: string | null }
  | { status: "downloading"; downloaded: number; total: number | null }
  | { status: "installing" }
  | { status: "error"; message: string };

export async function getCurrentVersion(): Promise<string> {
  try {
    return await getVersion();
  } catch {
    return "unknown";
  }
}

export async function checkForUpdate(): Promise<Update | null> {
  return await check();
}

export async function downloadAndInstall(
  update: Update,
  onProgress: (downloaded: number, total: number | null) => void,
): Promise<void> {
  let downloaded = 0;
  let total: number | null = null;

  await update.downloadAndInstall((event) => {
    switch (event.event) {
      case "Started":
        total = event.data.contentLength ?? null;
        onProgress(0, total);
        break;
      case "Progress":
        downloaded += event.data.chunkLength;
        onProgress(downloaded, total);
        break;
      case "Finished":
        onProgress(downloaded, total);
        break;
    }
  });

  await relaunch();
}

/**
 * Silent check-on-launch. Returns the Update if one is available, null otherwise.
 * Errors are swallowed — a failed check on startup must never block the app.
 */
export async function silentCheckOnLaunch(): Promise<Update | null> {
  try {
    return await check();
  } catch {
    return null;
  }
}
