/**
 * Thin HTTP client that resolves the backend port via Tauri IPC.
 * Falls back to a hard-coded dev port when running outside the Tauri shell.
 */

import { invoke } from "@tauri-apps/api/core";

const DEV_FALLBACK_PORT = 8765;

let _port: number | null = null;

export async function getBaseUrl(): Promise<string> {
  if (_port != null) return `http://127.0.0.1:${_port}`;
  try {
    const port = await invoke<number | null>("get_backend_port");
    if (port != null) {
      _port = port;
      return `http://127.0.0.1:${port}`;
    }
  } catch {
    // Not running inside Tauri — use the dev fallback.
  }
  _port = DEV_FALLBACK_PORT;
  return `http://127.0.0.1:${DEV_FALLBACK_PORT}`;
}

/** Invalidate the cached port (called when a fresh backend-ready event fires). */
export function resetPort(port: number): void {
  _port = port;
}

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly detail: string,
    method: string,
    path: string,
  ) {
    super(`${method} ${path} → ${status}: ${detail}`);
    this.name = "ApiError";
  }
}

export async function api<T = unknown>(
  method: "GET" | "POST" | "PUT" | "DELETE",
  path: string,
  body?: unknown,
): Promise<T> {
  const base = await getBaseUrl();
  const res = await fetch(`${base}${path}`, {
    method,
    headers: body != null ? { "Content-Type": "application/json" } : undefined,
    body: body != null ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let detail: string;
    try {
      const j = await res.json() as { detail?: string };
      detail = j.detail ?? res.statusText;
    } catch {
      detail = res.statusText;
    }
    throw new ApiError(res.status, detail, method, path);
  }
  return res.json() as Promise<T>;
}
