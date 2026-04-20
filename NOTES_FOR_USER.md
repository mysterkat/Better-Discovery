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

## 2. No Set->MQL exporter in `MONTE CARLO/src`

The delivery brief specifies a "Set -> MQL" tab, but no matching module exists
in the read-only toolkit. `backend/app/bridge/set_to_mql.py` is a stub that
raises `SetToMqlNotAvailable` (HTTP 501 from `/mql/export`).

- **Needed upstream:** a module with
  `def export(pattern_id: str, template: str) -> str` that returns the path to
  a generated `.mq5`/`.mq4` file.
- Until then the Set->MQL tab will surface a clear "not available" state; it
  will not silently fail.

## 3. `import_hist_data.main()` requires MetaTrader 5

The module imports `MetaTrader5` and talks to a running MT5 terminal. Useful
to know for the Data Import tab: histdata import will only work on a machine
with MT5 installed and logged in. CSV import works without MT5.

## 4. Python version

The embedded runtime target is Python 3.11 (per brief). Dev smoke runs used
the user's system Python 3.14 in a local `.venv` with fastapi/uvicorn/pydantic
only. `backend/requirements.txt` keeps the 3.11-targeted pins for the Phase 8
embedded install. numpy 1.26.4 / pandas 2.2.3 pins won't build on 3.14; that's
expected — the embedded runtime will be true 3.11.
