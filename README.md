# BETTER DISCOVERY

Standalone Windows desktop app (Tauri v2 + React + FastAPI sidecar with embedded
Python) that wraps the existing Monte Carlo / pattern-discovery toolkit at
`C:\Users\micha\Desktop\MONTE CARLO\`.

## Hard rule

`C:\Users\micha\Desktop\MONTE CARLO\` is READ-ONLY. This app imports from it via
`sys.path` insertion. No file in that tree may be created, edited, renamed,
moved, or deleted.

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

## Status

Phase 1 (scaffold) in progress. See the delivery brief for the full phased plan.
