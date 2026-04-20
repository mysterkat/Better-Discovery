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

## 4. **BLOCKER: Windows SDK not installed (needed for `tauri dev`)**

Phase 4 code is complete — Rust sources parse, `tauri.conf.json` validates,
`cargo check` pulls the dep graph — but the final link step fails:

```
error: linking with `link.exe` failed: exit code: 1
note: in the Visual Studio installer, ensure the "C++ build tools" workload
      is selected
```

Diagnosis:
- Rust toolchain: `stable-x86_64-pc-windows-msvc` (default).
- Visual Studio 2022 Community is installed at
  `C:\Program Files\Microsoft Visual Studio\2022\Community\`.
- MSVC toolset present: `VC\Tools\MSVC\14.44.35207\bin\Hostx64\x64\link.exe`.
- But no Windows SDK is installed — neither `C:\Program Files (x86)\Windows Kits\10\`
  nor the matching registry keys exist. Without the SDK, `link.exe` cannot find
  `kernel32.lib`, `user32.lib`, etc., which every Tauri build needs.
- A stray `C:\Program Files\Git\usr\bin\link.exe` (Git's symlink tool, not
  Microsoft's linker) sits earlier on PATH than MSVC — not the root cause, but
  will need `vcvars64.bat` to be sourced first even after the SDK lands.

**Fix (one-time, user action required):**
1. Open the Visual Studio Installer.
2. Modify the VS 2022 Community install.
3. Under "Workloads" select **Desktop development with C++** — this pulls in
   the Windows 10/11 SDK + MSVC build tools + CMake as a set.
4. Install.
5. Verify: `where.exe cl.exe` in a new shell should resolve to
   `...\VC\Tools\MSVC\14.x\bin\Hostx64\x64\cl.exe`.
6. Re-run Phase 4 check: from `src-tauri/` run
   `cmd /c '"C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat" && cargo check'`.
   Should succeed. Then `npm run tauri -- dev` from the repo root.

Until then Phase 4 cannot finish its own acceptance check
("`pnpm tauri dev` opens a blank window"). Everything else is written,
committed, and verified as far as possible without a linker:
- `npm install` succeeded (71 packages, Tauri CLI 2.10.1).
- `npm run build` succeeds (TS strict + Vite) — the frontend bundle builds.
- Rust sources parse cleanly; unresolved-import errors when rustc is invoked
  standalone are expected (tauri/serde are cargo deps).
- `cargo check` failed only at link-time.

## 5. Python version

The embedded runtime target is Python 3.11 (per brief). Dev smoke runs used
the user's system Python 3.14 in a local `.venv` with fastapi/uvicorn/pydantic
only. `backend/requirements.txt` keeps the 3.11-targeted pins for the Phase 8
embedded install. numpy 1.26.4 / pandas 2.2.3 pins won't build on 3.14; that's
expected — the embedded runtime will be true 3.11.
