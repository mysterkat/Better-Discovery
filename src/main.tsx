import React from "react";
import ReactDOM from "react-dom/client";
import "./styles/globals.css";

/**
 * Route to the appropriate component based on the `?window=` query parameter.
 *
 * Main window:            /?              → <App>
 * MC results window:      /?window=mc-results&jobId=…   → <MonteCarloResults>
 * Discovery results:      /?window=discovery-results&jobId=…  → <DiscoveryResults>
 * MQL results:            /?window=mql-results&path=…  → <MqlExportResults>
 */
async function mount() {
  const p = new URLSearchParams(window.location.search);
  const wType = p.get("window");

  let Component: React.ComponentType;

  if (wType === "mc-results") {
    Component = (await import("./windows/MonteCarloResults")).default;
  } else if (wType === "discovery-results") {
    Component = (await import("./windows/DiscoveryResults")).default;
  } else if (wType === "mql-results") {
    Component = (await import("./windows/MqlExportResults")).default;
  } else {
    Component = (await import("./App")).default;
  }

  ReactDOM.createRoot(document.getElementById("root")!).render(
    <React.StrictMode>
      <Component />
    </React.StrictMode>,
  );
}

mount();
