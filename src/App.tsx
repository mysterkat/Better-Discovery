import { Suspense, useEffect, useState } from "react";
import { listen } from "@tauri-apps/api/event";
import { invoke } from "@tauri-apps/api/core";

import Sidebar from "./components/Sidebar";
import SettingsPanel from "./components/SettingsPanel";
import { TABS, type TabId } from "./router";
import { useSettings } from "./state/settings";
import { resetPort } from "./api/client";

export default function App() {
  const [activeTab, setActiveTab] = useState<TabId>("data-import");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [backendReady, setBackendReady] = useState(false);

  const loadSettings = useSettings((s) => s.load);

  useEffect(() => {
    let unlisten: (() => void) | null = null;

    const init = async () => {
      try {
        unlisten = await listen<{ port: number }>("backend-ready", (e) => {
          resetPort(e.payload.port);
          setBackendReady(true);
          loadSettings();
        });
        // Already running before we mounted — pick up the live port.
        const port = await invoke<number | null>("get_backend_port");
        if (port != null) {
          resetPort(port);
          setBackendReady(true);
          loadSettings();
        }
      } catch {
        // Running outside the Tauri shell (plain `vite dev`).
        // Connect via fallback port defined in api/client.ts.
        setBackendReady(true);
        loadSettings();
      }
    };

    init();
    return () => {
      if (unlisten) unlisten();
    };
  }, [loadSettings]);

  const activeTabDef = TABS.find((t) => t.id === activeTab);

  return (
    <div className="app-shell">
      <Sidebar
        activeTab={activeTab}
        onTabChange={setActiveTab}
        onSettings={() => setSettingsOpen(true)}
      />

      <main className="main-content">
        {!backendReady && (
          <p className="backend-wait">Connecting to backend…</p>
        )}

        {backendReady && activeTabDef && (
          <Suspense fallback={<p className="tab-loading">Loading…</p>}>
            <activeTabDef.Component />
          </Suspense>
        )}
      </main>

      <SettingsPanel
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
      />
    </div>
  );
}
