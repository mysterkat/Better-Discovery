"""Bridge to import_hist_data (MT5 historical data downloader)."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import paths  # ensures toolkit is on sys.path  # noqa: F401
from ..paths import DEFAULT_HIST_DATA


DEFAULT_HIST_FOLDER = str(DEFAULT_HIST_DATA)

# Filename pattern produced by import_hist_data.main():
# `{symbol_lowercase}_{label}.csv`  e.g. `xauusd_m5.csv`, `eurusd_h1.csv`
_FILENAME_RE = re.compile(r"^([a-z0-9]+)_([a-z]+\d*)\.csv$", re.IGNORECASE)

# Map of timeframe label → minutes. Used to sort imports smallest → largest
# so discovery's auto-fill picks the most granular timeframes first.
_TF_MINUTES: dict[str, int] = {
    "m1": 1, "m2": 2, "m3": 3, "m4": 4, "m5": 5, "m6": 6, "m10": 10, "m12": 12,
    "m15": 15, "m20": 20, "m30": 30,
    "h1": 60, "h2": 120, "h3": 180, "h4": 240, "h6": 360, "h8": 480, "h12": 720,
    "d1": 1440, "w1": 10080, "mn1": 43200,
}


def _parse_filename(name: str) -> tuple[str, str] | None:
    """Return (symbol, tf_label) for a recognized hist_data CSV, else None."""
    m = _FILENAME_RE.match(name)
    if not m:
        return None
    return m.group(1).upper(), m.group(2).lower()


def list_current_import() -> dict[str, Any]:
    """Inspect DEFAULT_HIST_FOLDER and return a structured summary.

    Returns:
        {
          "exists": bool,
          "symbol": str | None,    # only if files share a symbol
          "timeframes": [
            {"label": "M5", "filename": "xauusd_m5.csv", "path": "...",
             "size_bytes": int, "modified_at": "ISO-8601 UTC"}
          ],
          "modified_at": "ISO-8601 UTC" | None,  # latest mtime
        }
    """
    folder = Path(DEFAULT_HIST_FOLDER)
    if not folder.is_dir():
        return {"exists": False, "symbol": None, "timeframes": [], "modified_at": None}

    entries: list[dict[str, Any]] = []
    symbols: set[str] = set()
    latest_mtime = 0.0

    for f in folder.iterdir():
        if not f.is_file():
            continue
        parsed = _parse_filename(f.name)
        if parsed is None:
            continue
        symbol, label = parsed
        symbols.add(symbol)
        stat = f.stat()
        latest_mtime = max(latest_mtime, stat.st_mtime)
        entries.append({
            "label": label.upper(),
            "filename": f.name,
            "path": str(f),
            "size_bytes": stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            # Internal sort key — strip from response below
            "_minutes": _TF_MINUTES.get(label, 99_999_999),
        })

    entries.sort(key=lambda e: e["_minutes"])
    for e in entries:
        e.pop("_minutes", None)

    return {
        "exists": len(entries) > 0,
        "symbol": next(iter(symbols)) if len(symbols) == 1 else None,
        "timeframes": entries,
        "modified_at": (
            datetime.fromtimestamp(latest_mtime, tz=timezone.utc).isoformat()
            if latest_mtime else None
        ),
    }


def clear_data_folder() -> dict[str, Any]:
    """Delete every recognized hist_data CSV (matching {sym}_{tf}.csv).

    Non-matching files are left alone — we never delete unfamiliar content.
    Returns {"deleted": [filenames], "kept": [filenames]}.
    """
    folder = Path(DEFAULT_HIST_FOLDER)
    deleted: list[str] = []
    kept: list[str] = []
    if not folder.is_dir():
        return {"deleted": deleted, "kept": kept}

    for f in folder.iterdir():
        if not f.is_file():
            continue
        if _parse_filename(f.name) is not None:
            try:
                f.unlink()
                deleted.append(f.name)
            except OSError:
                kept.append(f.name)
        else:
            kept.append(f.name)

    return {"deleted": deleted, "kept": kept}


_PROGRESS_RE = re.compile(r"^\[(\d+)/(\d+)\]\s*(.+)$")


def _run_toolkit_call(
    fn_name: str,
    payload: dict[str, Any],
    *,
    timeout: int,
) -> dict[str, Any]:
    """Run import_hist_data.<fn_name> in a subprocess and decode its JSON result.

    Streams stdout line-by-line so we can:
    1. Update the current job's stage (parses `[i/N] msg` markers).
    2. Honour cancellation: if the worker thread's job is cancel-requested,
       terminate the subprocess.

    The toolkit prints a final `RESULT_JSON: {...}` line containing the
    structured result; everything before that is progress / log noise.
    """
    # Lazy import to avoid a circular import at module load — runners imports
    # nothing from bridge; bridge does NOT import runners at top level.
    from ..jobs.runners import get_current_job  # noqa: WPS433

    script = (
        "import json, sys, traceback\n"
        "from pathlib import Path\n"
        "toolkit_dir = Path(sys.argv[1])\n"
        "fn_name = sys.argv[2]\n"
        "payload = json.loads(sys.argv[3])\n"
        "sys.path.insert(0, str(toolkit_dir))\n"
        "try:\n"
        "    import import_hist_data as mod\n"
        "    result = getattr(mod, fn_name)(**payload)\n"
        "    print('RESULT_JSON:', json.dumps({'ok': True, 'result': result}, default=str), flush=True)\n"
        "except Exception as exc:\n"
        "    print('RESULT_JSON:', json.dumps({\n"
        "        'ok': False,\n"
        "        'error': f'{type(exc).__name__}: {exc}',\n"
        "        'traceback': traceback.format_exc(),\n"
        "    }), flush=True)\n"
    )

    try:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-u",  # unbuffered stdout so progress lines arrive promptly
                "-c",
                script,
                str(paths.TOOLKIT_DIR),
                fn_name,
                json.dumps(payload),
            ],
            stdout=subprocess.PIPE,
            # Merge stderr INTO stdout so we only have one pipe to drain.
            # Two separate PIPEs without concurrent draining is a classic
            # deadlock — stderr's buffer (4-8 KB on Windows) fills, the child
            # blocks on its next stderr.write(), and our stdout iterator hangs.
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,  # line-buffered
        )
    except Exception as exc:
        raise RuntimeError(f"Could not launch MT5 {fn_name}: {exc}") from exc

    job = get_current_job()
    # Register subprocess on the job so the cancel endpoint can terminate it.
    if job is not None:
        job.meta["_subprocess"] = proc

    deadline = time.monotonic() + timeout
    result_json: str | None = None
    cancelled_via_flag = False

    try:
        if proc.stdout is None:
            raise RuntimeError("subprocess stdout is None — cannot stream progress")
        # Drain the merged stdout/stderr stream.
        for line in proc.stdout:
            line = line.rstrip("\r\n")
            if not line:
                continue
            if line.startswith("RESULT_JSON:"):
                result_json = line[len("RESULT_JSON:"):].strip()
                continue
            # Update job stage from `[i/N] msg`
            m = _PROGRESS_RE.match(line)
            if m and job is not None:
                idx, total, msg = int(m.group(1)), int(m.group(2)), m.group(3).strip()
                job.mark_stage(msg, idx, total)
            elif job is not None:
                job.append_log(line)
            # Cooperative cancel + timeout checks
            if job is not None and job.is_cancel_requested():
                cancelled_via_flag = True
                proc.terminate()
                break
            if time.monotonic() > deadline:
                proc.terminate()
                raise RuntimeError(
                    f"MT5 {fn_name} timed out after {timeout}s. "
                    "Make sure MetaTrader 5 is running and logged in."
                )
        proc.wait(timeout=5)
    finally:
        if job is not None:
            job.meta.pop("_subprocess", None)
        if proc.stdout: proc.stdout.close()

    if cancelled_via_flag:
        raise RuntimeError(f"MT5 {fn_name} cancelled by user")

    if result_json is None:
        raise RuntimeError(
            f"MT5 {fn_name} process exited with code {proc.returncode} and produced no result."
        )

    try:
        envelope = json.loads(result_json)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"MT5 {fn_name} returned invalid JSON (exit {proc.returncode}). "
            f"result line: {result_json!r}"
        ) from exc

    if not envelope.get("ok", False):
        trace = envelope.get("traceback")
        detail = str(envelope.get("error") or "unknown MT5 error")
        if trace:
            detail = f"{detail}\n{trace}"
        raise RuntimeError(detail)

    return envelope["result"]


def check_connection() -> dict[str, Any]:
    return _run_toolkit_call("check_connection", {}, timeout=20)


def fetch_historical(
    symbol: str,
    save_folder: str,
    tf_specs: list[dict[str, Any]],
    *,
    clear_existing: bool = False,
) -> dict[str, Any]:
    folder = save_folder or DEFAULT_HIST_FOLDER
    cleared: dict[str, Any] | None = None
    if clear_existing and folder == DEFAULT_HIST_FOLDER:
        # Only auto-clean the canonical hist_data folder. If the user pointed
        # at a custom folder via overrideOnce, leave it alone — they're in
        # control of that location.
        cleared = clear_data_folder()
    result = _run_toolkit_call(
        "main",
        {"symbol": symbol, "save_folder": folder, "tf_specs": tf_specs},
        timeout=180,
    )
    if not result.get("ok", False) and result.get("error") and not result.get("files"):
        raise RuntimeError(str(result["error"]))
    if cleared is not None:
        result["cleared"] = cleared
    return result


def fetch_many_symbols(
    symbols: list[str],
    save_folder: str,
    tf_specs: list[dict[str, Any]],
    *,
    clear_existing: bool = False,
) -> dict[str, Any]:
    """Fetch several symbols into the SAME folder so a multi-instrument basket
    can coexist for cross-instrument discovery.

    ``clear_existing`` wipes the folder ONCE before the first symbol; subsequent
    symbols accumulate (filenames are ``{symbol}_{tf}.csv`` so they never
    collide). One symbol failing does not abort the batch — its slot carries an
    ``error`` and the loop continues. Runs on the current job thread, so each
    symbol's per-TF progress streams to the same job.
    """
    folder = save_folder or DEFAULT_HIST_FOLDER
    per_symbol: list[dict[str, Any]] = []
    for i, sym in enumerate(symbols):
        try:
            res = fetch_historical(
                sym, folder, tf_specs,
                clear_existing=(clear_existing and i == 0),
            )
            per_symbol.append({"symbol": sym, **(res if isinstance(res, dict) else {})})
        except Exception as exc:  # noqa: BLE001 - isolate one symbol's failure
            per_symbol.append({
                "symbol": sym, "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            })
    return {
        "ok": any(r.get("ok", False) for r in per_symbol),
        "save_folder": folder,
        "symbols": list(symbols),
        "per_symbol": per_symbol,
    }


def candles_for_days(prefix: str, time_value: int, trading_days: int) -> int:
    import import_hist_data as _ih  # type: ignore[import-not-found]
    return _ih.trading_days_to_candles(prefix, time_value, trading_days)
