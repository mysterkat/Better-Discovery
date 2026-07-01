import { lazy, type ComponentType } from "react";

export type TabId =
  | "data-import"
  | "discovery"
  | "strategy-library"
  | "strategy-merger"
  | "strategy-compare"
  | "research-lab"
  | "set-to-mql"
  | "monte-carlo";

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
    id: "strategy-library",
    label: "Strategy Library",
    icon: "★",
    Component: lazy(() => import("./tabs/StrategyLibraryTab")),
  },
  {
    id: "strategy-merger",
    label: "Strategy Merger",
    icon: "⇄",
    Component: lazy(() => import("./tabs/StrategyMergerTab")),
  },
  {
    id: "research-lab",
    label: "Research Lab",
    icon: "▦",
    Component: lazy(() => import("./tabs/ResearchLabTab")),
  },
  {
    id: "strategy-compare",
    label: "Strategy Compare",
    icon: "≡",
    Component: lazy(() => import("./tabs/StrategyCompareTab")),
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
