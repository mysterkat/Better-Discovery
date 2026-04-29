"""MT5 historical data downloader — parameterized version.

Lives in backend/toolkit/ and is called exclusively through the
backend bridge (bridge/mt5_import.py). All parameters are passed
explicitly; no module-level constants are read at runtime.
"""
from __future__ import annotations

import math
import os
from typing import Any

# Maps user-facing TF string (prefix+value) → MT5 attribute name.
_TF_ATTR: dict[str, str] = {
    "m1": "TIMEFRAME_M1",   "m2": "TIMEFRAME_M2",   "m3": "TIMEFRAME_M3",
    "m4": "TIMEFRAME_M4",   "m5": "TIMEFRAME_M5",   "m6": "TIMEFRAME_M6",
    "m10": "TIMEFRAME_M10", "m12": "TIMEFRAME_M12", "m15": "TIMEFRAME_M15",
    "m20": "TIMEFRAME_M20", "m30": "TIMEFRAME_M30",
    "h1": "TIMEFRAME_H1",   "h2": "TIMEFRAME_H2",   "h3": "TIMEFRAME_H3",
    "h4": "TIMEFRAME_H4",   "h6": "TIMEFRAME_H6",   "h8": "TIMEFRAME_H8",
    "h12": "TIMEFRAME_H12",
    "d1": "TIMEFRAME_D1",
    "W1": "TIMEFRAME_W1",
    "M1": "TIMEFRAME_MN1",
}


def tf_label(prefix: str, time_value: int) -> str:
    """Canonical timeframe label, e.g. 'm5', 'h4', 'd1', 'W1', 'M1'."""
    if prefix in ("d", "D"):
        return "d1"
    if prefix == "W":
        return "W1"
    if prefix == "M":
        return "M1"
    return f"{prefix}{time_value}"


def trading_days_to_candles(prefix: str, time_value: int, trading_days: int) -> int:
    """
    Convert requested trading days into a candle count for the given TF.
    Always rounds UP so the pull never falls short of the requested period.
    """
    if prefix == "m":
        per_day = 1440.0 / time_value
    elif prefix == "h":
        per_day = 24.0 / time_value
    elif prefix in ("d", "D"):
        per_day = 1.0
    elif prefix == "W":
        per_day = 1.0 / 5.0
    elif prefix == "M":
        per_day = 1.0 / 21.0
    else:
        per_day = 1.0
    return math.ceil(trading_days * per_day)


def check_connection() -> dict[str, Any]:
    """Test MT5 connection. Returns {ok, terminal, account} or {ok: false, error}."""
    try:
        import MetaTrader5 as mt5
    except ImportError:
        return {
            "ok": False,
            "error": "MetaTrader5 Python package is not installed. "
                     "Run: pip install MetaTrader5",
        }
    if not mt5.initialize():
        err = mt5.last_error()
        return {"ok": False, "error": (
            f"MT5 init failed (code {err[0]}): {err[1]}. "
            "Make sure MetaTrader 5 is open, logged in, and "
            "'Allow DLL imports' is enabled in Tools → Options → Expert Advisors."
        )}
    info    = mt5.terminal_info()
    account = mt5.account_info()
    mt5.shutdown()
    return {
        "ok": True,
        "terminal": f"{info.name} build {info.build}" if info else "connected",
        "account":  f"#{account.login} {account.server}" if account else "",
    }


def _download_one(mt5: Any, symbol: str, tf_const: int, label: str,
                  candle_count: int, folder: str) -> dict[str, Any]:
    import time as _time
    import pandas as pd

    mt5.copy_rates_from_pos(symbol, tf_const, 0, 99999)   # warm-up cache
    _time.sleep(2)
    rates = mt5.copy_rates_from_pos(symbol, tf_const, 0, candle_count)
    if rates is None or len(rates) == 0:
        return {
            "label": label, "ok": False,
            "error": f"No data — {mt5.last_error()}",
            "candles": 0, "path": "",
        }
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df = df.set_index("time")
    if "tick_volume" in df.columns:
        df = df.rename(columns={"tick_volume": "volume"})
    keep_cols = ["open", "high", "low", "close"] + (
        ["volume"] if "volume" in df.columns else []
    )
    df = df[keep_cols]
    filename = f"{symbol.lower()}_{label}.csv"
    path     = os.path.join(folder, filename)
    df.to_csv(path)
    return {"label": label, "ok": True, "error": None, "candles": len(df), "path": path}


def main(
    symbol: str,
    save_folder: str,
    tf_specs: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Connect to MT5 and download historical data for each requested timeframe.

    tf_specs: list of {prefix: str, time_value: int, trading_days: int}

    Returns:
        {ok, terminal, save_folder, files: [{label, ok, candles, path, error}]}
    """
    try:
        import MetaTrader5 as mt5
    except ImportError:
        return {
            "ok": False,
            "error": "MetaTrader5 Python package not installed. pip install MetaTrader5",
            "files": [],
        }

    if not mt5.initialize():
        err = str(mt5.last_error())
        return {
            "ok": False,
            "error": f"MT5 init failed: {err}. Make sure MT5 is running and logged in.",
            "files": [],
        }

    info = mt5.terminal_info()
    terminal_str = f"{info.name} build {info.build}" if info else "unknown"
    os.makedirs(save_folder, exist_ok=True)

    files: list[dict[str, Any]] = []
    n_total = len(tf_specs)
    for idx, spec in enumerate(tf_specs, start=1):
        try:
            prefix     = str(spec["prefix"])
            time_value = int(spec["time_value"])
            days       = int(spec["trading_days"])
            label      = tf_label(prefix, time_value)
            # Progress marker the bridge parses to update job.stage_*
            # Format must match `^\[(\d+)/(\d+)\]\s*(.+)$` — keep it stable.
            print(f"[{idx}/{n_total}] Fetching {label.upper()}", flush=True)
            attr_name  = _TF_ATTR.get(label)
            if attr_name is None:
                raise ValueError(f"Unsupported timeframe: {prefix}{time_value}")
            tf_const = getattr(mt5, attr_name, None)
            if tf_const is None:
                raise ValueError(f"MT5 has no attribute {attr_name}")
            candles = trading_days_to_candles(prefix, time_value, days)
            res = _download_one(mt5, symbol, tf_const, label, candles, save_folder)
        except Exception as exc:
            res = {
                "label": spec.get("prefix", "?") + str(spec.get("time_value", "")),
                "ok": False, "error": str(exc), "candles": 0, "path": "",
            }
        files.append(res)

    mt5.shutdown()
    return {
        "ok": all(r["ok"] for r in files),
        "terminal": terminal_str,
        "save_folder": save_folder,
        "files": files,
    }
