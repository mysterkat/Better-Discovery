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


def candles_to_trading_days(prefix: str, time_value: int, candle_count: int) -> int:
    """
    Inverse of ``trading_days_to_candles``: derive a calendar-span estimate (in
    days) from a raw candle count for the given TF. Used only as a fallback when
    a caller supplies ``candle_count`` but no explicit ``trading_days`` span.
    Always rounds UP so the derived span never falls short of the candle count.
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
    return int(math.ceil(candle_count / per_day))


def _download_one(mt5: Any, symbol: str, tf_const: int, label: str,
                  span_days: int, folder: str) -> dict[str, Any]:
    """
    Pull historical bars for one symbol/timeframe using ``copy_rates_range``.

    ``copy_rates_from_pos`` only returns bars already in the terminal's LOCAL
    cache, so deep-history requests silently truncate (e.g. 1000d M15 → 372d).
    ``copy_rates_range`` forces the terminal to serve the full requested window
    from the server, so the broker's true depth is honoured.

    ``span_days`` is the requested CALENDAR span: ``date_from`` is set to
    ``now_utc - span_days`` and ``date_to`` to ``now_utc``. The warm-up
    ``copy_rates_from_pos`` call is kept so the symbol is primed before the
    range request. The on-disk CSV format (datetime ``time`` index +
    ``open,high,low,close[,volume]`` columns) is preserved exactly.
    """
    import time as _time
    from datetime import datetime, timedelta, timezone
    import pandas as pd

    mt5.copy_rates_from_pos(symbol, tf_const, 0, 99999)   # warm-up cache
    _time.sleep(2)
    # MT5 interprets range datetimes as UTC; use timezone-aware UTC bounds.
    date_to   = datetime.now(timezone.utc)
    date_from = date_to - timedelta(days=max(int(span_days), 1))
    rates = mt5.copy_rates_range(symbol, tf_const, date_from, date_to)
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
    # Surface the broker's TRUE depth when the window came back short of ask.
    actual_from = df.index[0]
    actual_to   = df.index[-1]
    actual_days = (actual_to - actual_from).days
    if actual_days < int(span_days) - 1:
        print(
            f"    {label.upper()}: requested {int(span_days)}d but broker "
            f"returned {actual_days}d ({actual_from} → {actual_to}, "
            f"{len(df)} bars)",
            flush=True,
        )
    return {"label": label, "ok": True, "error": None, "candles": len(df), "path": path}


def main(
    symbol: str,
    save_folder: str,
    tf_specs: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Connect to MT5 and download historical data for each requested timeframe.

    tf_specs: list of {prefix: str, time_value: int, trading_days: int}
        ``trading_days`` is the requested calendar span and is threaded straight
        down to the range fetch. If a spec omits ``trading_days`` but supplies
        ``candle_count``, the span is derived from it via the per-TF rate.

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
            # Prefer an explicit calendar span; otherwise derive one from a
            # supplied candle_count so callers passing either shape still work.
            if spec.get("trading_days") is not None:
                days = int(spec["trading_days"])
            elif spec.get("candle_count") is not None:
                days = candles_to_trading_days(
                    prefix, time_value, int(spec["candle_count"])
                )
            else:
                raise ValueError("spec needs 'trading_days' or 'candle_count'")
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
            res = _download_one(mt5, symbol, tf_const, label, days, save_folder)
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


def fetch_many(
    symbols: list[str],
    tf_specs: list[dict[str, Any]],
    save_folder: str,
) -> dict[str, Any]:
    """
    Download the same set of timeframes for a basket of symbols.

    Thin loop over ``main`` — one MT5 connect/shutdown cycle per symbol — so the
    multi-instrument basket reuses the identical range-fetch path (and therefore
    the identical CSV format). A failure for one symbol does not abort the rest.

    tf_specs: same shape as ``main`` (see its docstring).

    Returns:
        {ok, symbols: {<symbol>: <result-from-main>}}
        ``ok`` is True only if every symbol's pull fully succeeded.
    """
    results: dict[str, Any] = {}
    for symbol in symbols:
        try:
            results[symbol] = main(symbol, save_folder, tf_specs)
        except Exception as exc:  # pragma: no cover - defensive; main rarely raises
            results[symbol] = {"ok": False, "error": str(exc), "files": []}
    return {
        "ok": all(r.get("ok") for r in results.values()) if results else False,
        "symbols": results,
    }
