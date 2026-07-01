import { lazy, type ComponentType } from "react";

export type TabId =
  | "data-import"
  | "discovery"
  | "strategy-library"
  | "strategy-merger"
  | "evolution-lab"
  | "research-lab"
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
    label: "Strategy Discovery",
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
    id: "evolution-lab",
    label: "Evolution Lab",
    icon: "↯",
    Component: lazy(() => import("./tabs/EvolutionLabTab")),
  },
  {
    id: "research-lab",
    label: "Research Lab",
    icon: "▦",
    Component: lazy(() => import("./tabs/ResearchLabTab")),
  },
  {
    id: "monte-carlo",
    label: "Monte Carlo",
    icon: "📈",
    Component: lazy(() => import("./tabs/MonteCarloTab")),
  },
];
