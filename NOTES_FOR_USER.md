# Notes for the user

Collected during build. None of these required edits to `MONTE CARLO\`.

## 1. `pattern_discovery_v6.main()` is parameterless

The entry point reads module-level constants (`RANDOM_SEED`, `OUTPUT_FOLDER`,
`TRAIN_RATIO`, etc.) rather than taking arguments. To expose parameters to the
UI without editing the source file, `backend/app/bridge/discovery.py`
monkey-patches the imported module's attributes at runtime before calling
`main()`, snapshots originals, and restores them after the run.

- **Today:** works, zero edits to the read-only tree.
- **Nice-to-have upstream change (optional):** add a signature like
  `def main(**overrides) -> dict` that accepts the same names and applies them
  internally, so the bridge can stop monkey-patching. Not required to ship.

## 2. Set → MQL converter — now available ✅

The delivery brief mentioned no exporter in `MONTE CARLO/src`. However,
`MONTE CARLO/ea/` contains:

- **`PatternDiscoveryEA.mq5`** — the universal v3.03 EA template (READ-ONLY).
- **`PatternDiscovery_Converter.html`** — a standalone browser-based converter.

`backend/app/bridge/set_to_mql.py` now ports the full converter logic to Python
(faithfully mirroring `PatternDiscovery_Converter.html`). The HTTP 501 stub has
been replaced with the real implementation:

```
POST /mql/export  { set_content, template_path?, output_name? }
  → { ok: true, path: "…/userdata/mql/pattern_XX_CYY_DIRECTION.mq5" }

GET  /mql/template
  → { path: "…MONTE CARLO\\ea\\PatternDiscoveryEA.mq5" }
```

The Set→MQL tab UI (Phase 6) will use these endpoints.

## 3. `import_hist_data.main()` requires MetaTrader 5

The module imports `MetaTrader5` and talks to a running MT5 terminal. Useful
to know for the Data Import tab: histdata import will only work on a machine
with MT5 installed and logged in. CSV import works without MT5.

## 4. Windows SDK — ✅ RESOLVED (Phase 4 checkpoint passed)

The Phase 4 blocker (missing Windows SDK → `link.exe` failure) has been
resolved. After installing the **Desktop development with C++** workload in
the Visual Studio Installer, `cargo check` completed successfully:

```
Finished `dev` profile [unoptimized + debuginfo] target(s) in 1m 12s
```

**To launch the app in dev mode:**

```powershell
# Open a Developer Command Prompt (or source vcvars64.bat first), then:
npm run tauri -- dev
```

Or use the convenience script:
```powershell
scripts\dev.ps1
```

This opens a 1280×800 window titled "BETTER DISCOVERY" and starts the FastAPI
sidecar automatically. The sidebar should appear immediately; tabs show
placeholder content (Phase 5). Full tab implementations arrive in Phase 6.

## 5. Python version

The embedded runtime target is Python 3.11 (per brief). Dev smoke runs used
the user's system Python 3.14 in a local `.venv` with fastapi/uvicorn/pydantic
only. `backend/requirements.txt` keeps the 3.11-targeted pins for the Phase 8
embedded install. numpy 1.26.4 / pandas 2.2.3 pins won't build on 3.14; that's
expected — the embedded runtime will be true 3.11.
