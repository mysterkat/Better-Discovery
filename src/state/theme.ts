/**
 * Theme type + DOM helper.
 * Imported by settings.ts and any component that needs to apply a theme.
 */

export type Theme = "dark" | "light" | "midnight-blue" | "custom";

export const THEME_LIST: { id: Theme; label: string; swatches: string[] }[] = [
  {
    id: "dark",
    label: "Dark",
    swatches: ["#0b0f17", "#161b22", "#238636", "#1f6feb"],
  },
  {
    id: "light",
    label: "Light",
    swatches: ["#ffffff", "#f6f8fa", "#2da44e", "#0969da"],
  },
  {
    id: "midnight-blue",
    label: "Midnight Blue",
    swatches: ["#0a0e1a", "#152040", "#3d8bff", "#00c9a7"],
  },
  {
    id: "custom",
    label: "Custom",
    swatches: ["var(--custom-bg,#0b0f17)", "var(--custom-bg3,#21262d)", "var(--custom-accent,#238636)", "var(--custom-accent2,#1f6feb)"],
  },
];

/** Write data-theme onto <html> so CSS variables take effect immediately. */
export function applyTheme(theme: Theme): void {
  document.documentElement.setAttribute("data-theme", theme);
}
