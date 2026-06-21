# BETTER DISCOVERY

Autonomous research orchestration is documented in
[`docs/RESEARCH_ORCHESTRATOR.md`](docs/RESEARCH_ORCHESTRATOR.md). The dedicated
Codex operating prompt is in
[`docs/AUTONOMOUS_RESEARCH_PROMPT.md`](docs/AUTONOMOUS_RESEARCH_PROMPT.md).

The Data Import tab builds canonical Dukascopy tick datasets and publishes
compatible discovery bars. Research Lab performs local bid/ask replay, exports
Monte Carlo ledgers, and compares local results with native MT5 reports. MCP is
optional and exists only for autonomous Codex control of these same services.

Standalone Windows desktop app (Tauri v2 + React + FastAPI sidecar with embedded
Python) containing the Monte Carlo and pattern-discovery toolkit under
`backend/toolkit`.

## Stack

- Shell: Tauri v2 (Rust)
- Frontend: React 18 + TypeScript + Vite + TailwindCSS + shadcn/ui + Zustand +
  TanStack Query + Plotly.js (2D and 3D)
- Backend: FastAPI on embedded CPython 3.11, spawned as a Tauri sidecar on a
  random localhost port
- Storage: portable under `userdata/` (not `%APPDATA%`)

## Layout

See the delivery brief. High level:

```
BETTER DISCOVERY/
  src/          React frontend
  src-tauri/    Rust shell + embedded Python binary
  backend/      FastAPI app + bridge modules to MONTE CARLO/src
  scripts/      PowerShell setup / dev / build scripts
  userdata/     Portable config, themes, recent files, cache (gitignored)
```

## Dev loop

Once Phase 2+ are in:

```
scripts\dev.ps1
```

Starts the backend on port 8765, Vite on 5173, and `pnpm tauri dev`.

## Build

```
scripts\build.ps1
```

Produces a Windows installer under `src-tauri\target\release\bundle\`.
