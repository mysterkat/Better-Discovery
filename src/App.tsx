import { Suspense, useEffect, useState } from "react";
import { listen } from "@tauri-apps/api/event";
import { invoke } from "@tauri-apps/api/core";

import Sidebar from "./components/Sidebar";
import SettingsPanel from "./components/SettingsPanel";
import { TABS, type TabId } from "./router";
import { useSettings } from "./state/settings";
import { useParamDefaults } from "./state/paramDefaults";
import { resetPort } from "./api/client";
import { silentCheckOnLaunch } from "./lib/updater";

export default function App() {
  const [activeTab, setActiveTab] = useState<TabId>("data-import");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [backendReady, setBackendReady] = useState(false);
  const [updateAvailableVersion, setUpdateAvailableVersion] = useState<
    string | null
  >(null);

  // Keep visited tabs mounted so their state survives navigation.
  // tabKeys is incremented on reset to force a fresh remount of that tab.
  const [mountedTabs, setMountedTabs] = useState<Set<TabId>>(
    () => new Set(["data-import" as TabId])
  );
  const [tabKeys, setTabKeys] = useState<Partial<Record<TabId, number>>>({});

  const loadSettings = useSettings((s) => s.load);
  const loadParamDefaults = useParamDefaults((s) => s.load);

  useEffect(() => {
    silentCheckOnLaunch().then((update) => {
      if (update) setUpdateAvailableVersion(update.version);
    });
  }, []);

  useEffect(() => {
    let unlisten: (() => void) | null = null;

    const bootStores = () => {
      loadSettings();
      loadParamDefaults();
    };

    const init = async () => {
      try {
        unlisten = await listen<{ port: number }>("backend-ready", (e) => {
          resetPort(e.payload.port);
          setBackendReady(true);
          bootStores();
        });
        // Already running before we mounted — pick up the live port.
        const port = await invoke<number | null>("get_backend_port");
        if (port != null) {
          resetPort(port);
          setBackendReady(true);
          bootStores();
        }
      } catch {
        // Running outside the Tauri shell (plain `vite dev`).
        // Connect via fallback port defined in api/client.ts.
        setBackendReady(true);
        bootStores();
      }
    };

    init();
    return () => {
      if (unlisten) unlisten();
    };
  }, [loadSettings, loadParamDefaults]);

  // Mount the tab on first visit so it stays alive for the rest of the session.
  useEffect(() => {
    setMountedTabs((prev) => {
      if (prev.has(activeTab)) return prev;
      const next = new Set(prev);
      next.add(activeTab);
      return next;
    });
  }, [activeTab]);

  const handleResetTab = () => {
    setTabKeys((prev) => ({ ...prev, [activeTab]: (prev[activeTab] ?? 0) + 1 }));
  };

  return (
    <div className="app-shell">
      <Sidebar
        activeTab={activeTab}
        onTabChange={setActiveTab}
        onSettings={() => setSettingsOpen(true)}
        onResetTab={handleResetTab}
      />

      {updateAvailableVersion && (
        <button
          className="update-toast"
          onClick={() => setSettingsOpen(true)}
          title="Open settings to install"
        >
          Update available: v{updateAvailableVersion} — click to install
        </button>
      )}

      <main className="main-content">
        {!backendReady && (
          <p className="backend-wait">Connecting to backend…</p>
        )}

        {backendReady && TABS.filter((t) => mountedTabs.has(t.id)).map((t) => (
          <div
            key={t.id}
            style={{ display: t.id === activeTab ? undefined : "none", height: "100%" }}
          >
            <Suspense fallback={<p className="tab-loading">Loading…</p>}>
              <t.Component key={tabKeys[t.id] ?? 0} />
            </Suspense>
          </div>
        ))}
      </main>

      <SettingsPanel
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
      />
    </div>
  );
}
