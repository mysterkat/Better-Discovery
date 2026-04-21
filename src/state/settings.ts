/**
 * Zustand settings store.
 *
 * Persistence: on load, fetches from GET /settings (→ userdata/settings.json).
 * On every change, debounced PUT /settings writes back.
 *
 * The theme is also written directly to the <html> data-theme attribute so
 * CSS variables apply before React re-renders.
 */

import { create } from "zustand";
import { api } from "../api/client";
import { applyTheme, type Theme } from "./theme";

export type { Theme };

export interface Settings {
  theme: Theme;
  sidebarCollapsed: boolean;
}

interface SettingsStore extends Settings {
  /** True once the initial GET /settings has returned. */
  loaded: boolean;
  load: () => Promise<void>;
  setTheme: (theme: Theme) => void;
  setSidebarCollapsed: (v: boolean) => void;
}

const DEFAULTS: Settings = {
  theme: "dark",
  sidebarCollapsed: false,
};

// Simple debounce for the PUT call.
let _saveTimer: ReturnType<typeof setTimeout> | null = null;
function scheduleSave(state: Settings) {
  if (_saveTimer != null) clearTimeout(_saveTimer);
  _saveTimer = setTimeout(() => {
    const { theme, sidebarCollapsed } = state;
    api("PUT", "/settings", { theme, sidebarCollapsed }).catch(console.warn);
  }, 400);
}

export const useSettings = create<SettingsStore>((set, get) => ({
  ...DEFAULTS,
  loaded: false,

  load: async () => {
    // Apply the default theme immediately so the page isn't unstyled.
    applyTheme(get().theme);
    try {
      const remote = await api<Partial<Settings>>("GET", "/settings");
      const merged: Settings = { ...DEFAULTS, ...remote };
      applyTheme(merged.theme);
      set({ ...merged, loaded: true });
    } catch {
      set({ loaded: true });
    }
  },

  setTheme: (theme) => {
    applyTheme(theme);
    set({ theme });
    scheduleSave({ ...get(), theme });
  },

  setSidebarCollapsed: (v) => {
    set({ sidebarCollapsed: v });
    scheduleSave({ ...get(), sidebarCollapsed: v });
  },
}));
