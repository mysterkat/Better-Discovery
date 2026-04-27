/**
 * Zustand store for persistent parameter defaults.
 *
 * On load:  GET /param-defaults  →  userdata/param_defaults.json
 * On every change: debounced PUT /param-defaults writes back.
 *
 * Both Discovery and MC Sim tabs read from this store to pre-fill
 * their form fields, so the user's preferred starting values are
 * always reflected without manual re-entry.
 */

import { create } from "zustand";
import { getParamDefaults, putParamDefaults } from "../api/paramDefaults";

export interface ParamDefaultsStore {
  /** True once the initial GET has returned. */
  loaded: boolean;
  /** Map of param key → persisted default value. */
  defaults: Record<string, unknown>;
  /** Load from backend (call once at app start). */
  load: () => Promise<void>;
  /** Update a single key and schedule a save. */
  set: (key: string, value: unknown) => void;
  /** Replace all defaults at once and save immediately. */
  setAll: (defaults: Record<string, unknown>) => void;
  /** Reset a key back to its code-level default (removes the entry). */
  reset: (key: string) => void;
}

let _saveTimer: ReturnType<typeof setTimeout> | null = null;

function scheduleSave(defaults: Record<string, unknown>) {
  if (_saveTimer != null) clearTimeout(_saveTimer);
  _saveTimer = setTimeout(() => {
    putParamDefaults(defaults).catch(console.warn);
  }, 400);
}

export const useParamDefaults = create<ParamDefaultsStore>((set, get) => ({
  loaded: false,
  defaults: {},

  load: async () => {
    try {
      const remote = await getParamDefaults();
      set({ defaults: remote ?? {}, loaded: true });
    } catch {
      set({ loaded: true });
    }
  },

  set: (key, value) => {
    const next = { ...get().defaults, [key]: value };
    set({ defaults: next });
    scheduleSave(next);
  },

  setAll: (defaults) => {
    set({ defaults });
    putParamDefaults(defaults).catch(console.warn);
  },

  reset: (key) => {
    const next = { ...get().defaults };
    delete next[key];
    set({ defaults: next });
    scheduleSave(next);
  },
}));
