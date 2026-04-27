import { api } from "./client";

/** Fetch the user's persistent parameter defaults (stored in userdata/param_defaults.json). */
export async function getParamDefaults(): Promise<Record<string, unknown>> {
  return api<Record<string, unknown>>("GET", "/param-defaults");
}

/** Persist the user's parameter defaults. */
export async function putParamDefaults(defaults: Record<string, unknown>): Promise<void> {
  await api("PUT", "/param-defaults", defaults);
}
