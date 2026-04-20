import { useEffect, useState } from "react";
import { listen } from "@tauri-apps/api/event";
import { invoke } from "@tauri-apps/api/core";

type BackendStatus = "waiting" | "ready" | "standalone";

// Phase 4: blank shell that surfaces sidecar readiness. UI comes in Phase 5+.
export default function App() {
  const [port, setPort] = useState<number | null>(null);
  const [status, setStatus] = useState<BackendStatus>("waiting");

  useEffect(() => {
    let unlisten: (() => void) | null = null;
    (async () => {
      try {
        unlisten = await listen<{ port: number }>("backend-ready", (e) => {
          setPort(e.payload.port);
          setStatus("ready");
        });
        // If the event already fired before we mounted, pull the current port.
        const current = await invoke<number | null>("get_backend_port");
        if (current != null) {
          setPort(current);
          setStatus("ready");
        }
      } catch {
        // Running outside Tauri (plain `vite dev`); no sidecar.
        setStatus("standalone");
      }
    })();
    return () => {
      if (unlisten) unlisten();
    };
  }, []);

  return (
    <main
      style={{
        fontFamily: "system-ui, sans-serif",
        background: "#0b0f17",
        color: "#e6edf3",
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        flexDirection: "column",
        gap: "0.5rem",
      }}
    >
      <h1 style={{ margin: 0, fontSize: "1.25rem", letterSpacing: "0.05em" }}>
        BETTER DISCOVERY
      </h1>
      <p style={{ margin: 0, fontSize: "0.9rem", opacity: 0.7 }}>
        {status === "waiting" && "waiting for backend..."}
        {status === "ready" && `backend on port ${port}`}
        {status === "standalone" &&
          "running in browser (no Tauri shell); start via `npm run tauri dev`"}
      </p>
    </main>
  );
}
