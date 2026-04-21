/**
 * Helper to open a named result window via Tauri WebviewWindow.
 * Silently no-ops when running outside the Tauri shell.
 */

export async function openResultWindow(
  label: string,
  title: string,
  params: Record<string, string>,
): Promise<void> {
  try {
    const { WebviewWindow } = await import("@tauri-apps/api/webviewWindow");
    const query = new URLSearchParams(params).toString();
    const win = new WebviewWindow(label, {
      url: `/?${query}`,
      title,
      width: 1200,
      height: 800,
      minWidth: 900,
      minHeight: 600,
      center: true,
      resizable: true,
    });
    win.once("tauri://error", (e) => {
      console.error(`[window:${label}] error:`, e);
    });
  } catch {
    // Not running inside Tauri (plain vite dev) — open in a new browser tab.
    const query = new URLSearchParams(params).toString();
    window.open(`/?${query}`, "_blank");
  }
}
