"""Bridge to import_hist_data (MT5 historical data downloader)."""
from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

from .. import paths  # ensures toolkit is on sys.path  # noqa: F401
from ..paths import DEFAULT_HIST_DATA


DEFAULT_HIST_FOLDER = str(DEFAULT_HIST_DATA)


def _run_toolkit_call(
    fn_name: str,
    payload: dict[str, Any],
    *,
    timeout: int,
) -> dict[str, Any]:
    """Run import_hist_data.<fn_name> in a subprocess and decode its JSON result.

    MT5's Python bindings can take down the hosting interpreter when the
    terminal/runtime setup is bad. Keeping those calls in a child process lets
    the backend survive and return the real failure details to the UI.
    """
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
        "    print(json.dumps({'ok': True, 'result': result}, default=str))\n"
        "except Exception as exc:\n"
        "    print(json.dumps({\n"
        "        'ok': False,\n"
        "        'error': f'{type(exc).__name__}: {exc}',\n"
        "        'traceback': traceback.format_exc(),\n"
        "    }))\n"
    )

    try:
        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                script,
                str(paths.TOOLKIT_DIR),
                fn_name,
                json.dumps(payload),
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"MT5 {fn_name} timed out after {timeout}s. "
            "Make sure MetaTrader 5 is running and logged in."
        ) from exc
    except Exception as exc:
        raise RuntimeError(f"Could not launch MT5 {fn_name}: {exc}") from exc

    stdout = proc.stdout.strip()
    stderr = proc.stderr.strip()
    if not stdout:
        extra = f" stderr: {stderr}" if stderr else ""
        raise RuntimeError(
            f"MT5 {fn_name} process exited with code {proc.returncode} and produced no output.{extra}"
        )

    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"MT5 {fn_name} returned invalid JSON (exit {proc.returncode}). "
            f"stdout: {stdout!r} stderr: {stderr!r}"
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
) -> dict[str, Any]:
    folder = save_folder or DEFAULT_HIST_FOLDER
    result = _run_toolkit_call(
        "main",
        {"symbol": symbol, "save_folder": folder, "tf_specs": tf_specs},
        timeout=180,
    )
    if not result.get("ok", False) and result.get("error") and not result.get("files"):
        raise RuntimeError(str(result["error"]))
    return result


def candles_for_days(prefix: str, time_value: int, trading_days: int) -> int:
    import import_hist_data as _ih  # type: ignore[import-not-found]
    return _ih.trading_days_to_candles(prefix, time_value, trading_days)
