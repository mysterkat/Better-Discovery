import { lazy, type ComponentType } from "react";

export type TabId = "data-import" | "discovery" | "set-to-mql" | "monte-carlo";

export interface TabDef {
  id: TabId;
  label: string;
  icon: string;
  Component: ComponentType;
}

export const TABS: TabDef[] = [
  {
    id: "data-import",
    label: "Data Import",
    icon: "⬆",
    Component: lazy(() => import("./tabs/DataImportTab")),
  },
  {
    id: "discovery",
    label: "Pattern Discovery",
    icon: "🔍",
    Component: lazy(() => import("./tabs/DiscoveryTab")),
  },
  {
    id: "set-to-mql",
    label: "Set → MQL",
    icon: "⚙",
    Component: lazy(() => import("./tabs/SetToMqlTab")),
  },
  {
    id: "monte-carlo",
    label: "Monte Carlo",
    icon: "📈",
    Component: lazy(() => import("./tabs/MonteCarloTab")),
  },
];
