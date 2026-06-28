"""Bridge to import_hist_data (MT5 historical data downloader)."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .. import paths  # ensures toolkit is on sys.path  # noqa: F401
from ..paths import DEFAULT_HIST_DATA
from ..market_data.catalog import MarketDataCatalog
from ..market_data.models import DatasetManifest


DEFAULT_HIST_FOLDER = str(DEFAULT_HIST_DATA)

# Filename pattern produced by import_hist_data.main():
# `{symbol_lowercase}_{label}.csv`  e.g. `xauusd_m5.csv`, `eurusd_h1.csv`
_FILENAME_RE = re.compile(r"^([a-z0-9]+)_([a-z]+\d*)\.csv$", re.IGNORECASE)

# Map of timeframe label to minutes. Used to sort imports smallest to largest
# so discovery's auto-fill picks the most granular timeframes first.
_TF_MINUTES: dict[str, int] = {
    "m1": 1, "m2": 2, "m3": 3, "m4": 4, "m5": 5, "m6": 6, "m10": 10, "m12": 12,
    "m15": 15, "m20": 20, "m30": 30,
    "h1": 60, "h2": 120, "h3": 180, "h4": 240, "h6": 360, "h8": 480, "h12": 720,
    "d1": 1440, "w1": 10080, "mn1": 43200,
}

_KNOWN_CLOSED_MONTH_DAYS = {(1, 1), (12, 25)}


def _parse_filename(name: str) -> tuple[str, str] | None:
    """Return (symbol, tf_label) for a recognized hist_data CSV, else None."""
    m = _FILENAME_RE.match(name)
    if not m:
        return None
    return m.group(1).upper(), m.group(2).lower()


def _easter_date(year: int) -> date:
    """Gregorian Easter date using Meeus/Jones/Butcher algorithm."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _is_known_market_closed_day(value: date) -> bool:
    if value.weekday() >= 5:
        return True
    if (value.month, value.day) in _KNOWN_CLOSED_MONTH_DAYS:
        return True
    return value == (_easter_date(value.year) - timedelta(days=2))


def _trading_days_between(start: date, end: date) -> list[date]:
    days: list[date] = []
    current = start
    while current <= end:
        if not _is_known_market_closed_day(current):
            days.append(current)
        current += timedelta(days=1)
    return days


def _gap_crosses_only_closed_days(start: pd.Timestamp, end: pd.Timestamp) -> bool:
    start_date = start.date()
    end_date = end.date()
    if start_date == end_date:
        return False
    middle = start_date + timedelta(days=1)
    while middle < end_date:
        if not _is_known_market_closed_day(middle):
            return False
        middle += timedelta(days=1)
    return _is_known_market_closed_day(start_date) or _is_known_market_closed_day(end_date) or start.weekday() == 4


def _audit_mt5_bars(symbol: str, timeframe: str, bars: pd.DataFrame) -> dict[str, Any]:
    expected_minutes = _TF_MINUTES.get(timeframe.lower())
    issues: list[str] = []
    if bars.empty:
        return {
            "passed": False,
            "symbol": symbol,
            "timeframe": timeframe,
            "rows": 0,
            "issues": ["no bars"],
        }

    frame = bars.copy()
    frame["time"] = pd.to_datetime(frame["time"], utc=True)
    frame = frame.sort_values("time").reset_index(drop=True)
    duplicates = int(frame["time"].duplicated().sum())
    if duplicates:
        issues.append(f"{duplicates} duplicate timestamps")
    non_monotonic = int((frame["time"].diff().dt.total_seconds().dropna() <= 0).sum())
    if non_monotonic:
        issues.append(f"{non_monotonic} non-increasing timestamps")

    ohlc = frame[["open", "high", "low", "close"]].apply(pd.to_numeric, errors="coerce")
    null_ohlc = int(ohlc.isna().any(axis=1).sum())
    invalid_ohlc = int(((ohlc["high"] < ohlc[["open", "close", "low"]].max(axis=1)) |
                        (ohlc["low"] > ohlc[["open", "close", "high"]].min(axis=1)) |
                        (ohlc <= 0).any(axis=1)).sum())
    if null_ohlc:
        issues.append(f"{null_ohlc} rows with null OHLC")
    if invalid_ohlc:
        issues.append(f"{invalid_ohlc} rows with invalid OHLC")

    first_time = pd.Timestamp(frame["time"].iloc[0])
    last_time = pd.Timestamp(frame["time"].iloc[-1])
    present_days = {pd.Timestamp(value).date() for value in frame["time"]}
    expected_days = _trading_days_between(first_time.date(), last_time.date())
    missing_days = [day.isoformat() for day in expected_days if day not in present_days]
    if missing_days:
        issues.append(f"{len(missing_days)} missing trading days")

    large_gaps: list[dict[str, Any]] = []
    if expected_minutes:
        threshold_seconds = max(expected_minutes * 60 * 18, 90 * 60)
        for previous, current in zip(frame["time"].iloc[:-1], frame["time"].iloc[1:]):
            previous_ts = pd.Timestamp(previous)
            current_ts = pd.Timestamp(current)
            gap_seconds = (current_ts - previous_ts).total_seconds()
            if gap_seconds <= threshold_seconds:
                continue
            if _gap_crosses_only_closed_days(previous_ts, current_ts):
                continue
            large_gaps.append({
                "from": previous_ts.isoformat(),
                "to": current_ts.isoformat(),
                "minutes": round(gap_seconds / 60, 2),
            })
    if large_gaps:
        issues.append(f"{len(large_gaps)} suspicious large gaps")

    return {
        "passed": not issues,
        "symbol": symbol,
        "timeframe": timeframe,
        "rows": int(len(frame)),
        "first_time": first_time.isoformat(),
        "last_time": last_time.isoformat(),
        "duplicate_timestamps": duplicates,
        "non_monotonic_timestamps": non_monotonic,
        "null_ohlc_rows": null_ohlc,
        "invalid_ohlc_rows": invalid_ohlc,
        "missing_trading_days_count": len(missing_days),
        "missing_trading_days": missing_days[:50],
        "large_gaps_count": len(large_gaps),
        "large_gaps": large_gaps[:50],
        "known_closed_days_excluded": "weekends, Jan 1, Dec 25, Good Friday",
        "issues": issues,
    }


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
            # Internal sort key - strip from response below
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

    Non-matching files are left alone - we never delete unfamiliar content.
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


def _format_rate(bytes_per_second: float | int | None) -> str:
    value = float(bytes_per_second or 0.0)
    units = ["B/s", "KB/s", "MB/s", "GB/s"]
    unit = units[0]
    for unit in units:
        if value < 1024 or unit == units[-1]:
            break
        value /= 1024
    return f"{value:.1f} {unit}"


def _update_import_metrics(job: Any, metric: dict[str, Any], total: int) -> None:
    metrics = dict(job.meta.get("import_metrics") or {})
    completed = list(metrics.get("completed") or [])
    completed.append(metric)
    elapsed = sum(
        float(item.get("download_seconds") or 0.0) + float(item.get("write_seconds") or 0.0)
        for item in completed
    )
    done = len(completed)
    remaining = max(0, total - done)
    avg_seconds = elapsed / done if done else None
    eta_seconds = (avg_seconds * remaining) if avg_seconds is not None else None
    job.meta["import_metrics"] = {
        "completed": completed[-20:],
        "completed_timeframes": done,
        "total_timeframes": total,
        "last_symbol": metric.get("symbol"),
        "last_timeframe": str(metric.get("timeframe", "")).upper(),
        "last_rows": metric.get("rows"),
        "last_file_bytes": metric.get("file_bytes"),
        "download_bytes_per_second": metric.get("download_bytes_per_second"),
        "write_bytes_per_second": metric.get("write_bytes_per_second"),
        "download_rate_label": _format_rate(metric.get("download_bytes_per_second")),
        "write_rate_label": _format_rate(metric.get("write_bytes_per_second")),
        "eta_seconds": eta_seconds,
    }
    if eta_seconds is not None:
        job.eta_seconds = eta_seconds


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
    # Lazy import to avoid a circular import at module load - runners imports
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
        env = os.environ.copy()
        env.setdefault("PYTHONIOENCODING", "utf-8")
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
            # deadlock - stderr's buffer (4-8 KB on Windows) fills, the child
            # blocks on its next stderr.write(), and our stdout iterator hangs.
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,  # line-buffered
            env=env,
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
            raise RuntimeError("subprocess stdout is None - cannot stream progress")
        # Drain the merged stdout/stderr stream.
        for line in proc.stdout:
            line = line.rstrip("\r\n")
            if not line:
                continue
            if line.startswith("RESULT_JSON:"):
                result_json = line[len("RESULT_JSON:"):].strip()
                continue
            if line.startswith("METRIC_JSON:"):
                if job is not None:
                    try:
                        metric = json.loads(line[len("METRIC_JSON:"):].strip())
                        tf_specs = payload.get("tf_specs")
                        total = int(job.stage_total or (len(tf_specs) if isinstance(tf_specs, list) else 1))
                        _update_import_metrics(job, metric, total)
                    except Exception as exc:
                        job.append_log(f"metric parse failed: {exc}")
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


def _mt5_csv_to_bars(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    if "time" not in frame.columns:
        raise ValueError(f"MT5 CSV missing time column: {path}")
    required = {"open", "high", "low", "close"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"MT5 CSV missing columns {sorted(missing)}: {path}")
    bars = frame[["time", "open", "high", "low", "close"]].copy()
    bars["time"] = pd.to_datetime(bars["time"], utc=True)
    volume = frame["volume"] if "volume" in frame.columns else pd.Series(0, index=frame.index)
    bars["tick_volume"] = pd.to_numeric(volume, errors="coerce").fillna(0)
    bars["real_volume"] = bars["tick_volume"]
    for prefix in ("bid", "ask"):
        bars[f"{prefix}_open"] = bars["open"]
        bars[f"{prefix}_high"] = bars["high"]
        bars[f"{prefix}_low"] = bars["low"]
        bars[f"{prefix}_close"] = bars["close"]
    bars["spread_open"] = 0.0
    bars["spread_mean"] = 0.0
    bars["spread_max"] = 0.0
    return bars.sort_values("time").reset_index(drop=True)


def _tf_label_from_spec(spec: dict[str, Any]) -> str:
    prefix = str(spec["prefix"])
    time_value = int(spec["time_value"])
    if prefix in ("d", "D"):
        return "d1"
    if prefix == "W":
        return "W1"
    if prefix == "M":
        return "M1"
    return f"{prefix.lower()}{time_value}"


def _validate_mt5_result(tf_specs: list[dict[str, Any]], result: dict[str, Any]) -> None:
    """Reject partial MT5 imports instead of publishing incomplete datasets."""
    expected = [_tf_label_from_spec(spec).lower() for spec in tf_specs]
    files = result.get("files") or []
    by_label = {
        str(item.get("label", "")).lower(): item
        for item in files
        if isinstance(item, dict)
    }
    problems: list[str] = []
    for label in expected:
        item = by_label.get(label)
        display = label.upper()
        if item is None:
            problems.append(f"{display}: missing from MT5 result")
            continue
        if not item.get("ok", False):
            problems.append(f"{display}: {item.get('error') or 'download failed'}")
            continue
        if not item.get("path"):
            problems.append(f"{display}: no output file path")
            continue
        if int(item.get("candles") or 0) <= 0:
            problems.append(f"{display}: no bars returned")
    if problems:
        raise RuntimeError("MT5 import incomplete: " + "; ".join(problems))


def _publish_mt5_dataset(
    symbols: list[str],
    mt5_result: dict[str, Any],
    *,
    catalog: MarketDataCatalog | None = None,
) -> dict[str, Any] | None:
    """Register MT5 broker CSV bars as an immutable market-data dataset."""
    file_entries: list[tuple[str, dict[str, Any]]] = []
    if "per_symbol" in mt5_result:
        for symbol_result in mt5_result.get("per_symbol", []):
            symbol = str(symbol_result.get("symbol", "")).upper()
            for file_result in symbol_result.get("files", []) or []:
                if file_result.get("ok") and file_result.get("path"):
                    file_entries.append((symbol, file_result))
    else:
        symbol = symbols[0].upper() if symbols else "XAUUSD"
        for file_result in mt5_result.get("files", []) or []:
            if file_result.get("ok") and file_result.get("path"):
                file_entries.append((symbol, file_result))

    if not file_entries:
        return None

    parsed: list[tuple[str, str, Path, pd.DataFrame, dict[str, Any]]] = []
    quality_by_symbol: dict[str, dict[str, Any]] = {}
    for symbol, file_result in file_entries:
        path = Path(str(file_result["path"]))
        if not path.is_file():
            continue
        timeframe = str(file_result.get("label") or path.stem.split("_")[-1]).lower()
        bars = _mt5_csv_to_bars(path)
        if not bars.empty:
            audit = _audit_mt5_bars(symbol, timeframe, bars)
            quality_by_symbol.setdefault(symbol, {})[timeframe] = audit
            parsed.append((symbol, timeframe, path, bars, audit))
    if not parsed:
        return None
    failed_audits = [
        f"{symbol} {timeframe.upper()}: {', '.join(audit.get('issues') or ['quality audit failed'])}"
        for symbol, timeframe, _, _, audit in parsed
        if not audit.get("passed", False)
    ]
    if failed_audits:
        raise RuntimeError("MT5 bar quality audit failed: " + "; ".join(failed_audits))

    start = min(pd.Timestamp(frame["time"].min()).to_pydatetime() for _, _, _, frame, _ in parsed)
    end = max(pd.Timestamp(frame["time"].max()).to_pydatetime() for _, _, _, frame, _ in parsed)
    dataset_id = f"mt5_{start:%Y%m%d}_{end:%Y%m%d}_{uuid.uuid4().hex[:8]}"
    catalog = catalog or MarketDataCatalog()
    timeframes = sorted({timeframe for _, timeframe, _, _, _ in parsed}, key=lambda value: _TF_MINUTES.get(value, 99_999))
    manifest = DatasetManifest(
        dataset_id=dataset_id,
        provider="mt5",
        venue="MetaTrader 5 broker history",
        symbols=sorted({symbol for symbol, _, _, _, _ in parsed}),
        timeframes=timeframes,
        requested_from=start.isoformat(),
        requested_to=end.isoformat(),
        created_at=datetime.now(timezone.utc).isoformat(),
        import_options={
            "source": "mt5_import",
            "write_discovery_csv": True,
            "bid_ask_mode": "bar_ohlc_midpoint_proxy",
        },
        quality={"source": "mt5", "passed": True, "symbols": quality_by_symbol},
    )
    for symbol, timeframe, source_path, bars, audit in parsed:
        item = catalog.write_parquet(
            manifest,
            bars,
            kind="bars",
            symbol=symbol,
            timeframe=timeframe,
            relative_path=Path("bars") / symbol / timeframe / f"{source_path.stem}.parquet",
        )
        item.quality = {
            "source_csv": str(source_path),
            "rows": len(bars),
            "bid_ask_mode": "bar_ohlc_midpoint_proxy",
            "bar_audit": audit,
        }
        catalog.write_discovery_csv(manifest, bars, symbol, timeframe)
    published = catalog.publish_discovery_csvs(manifest)
    completed = catalog.complete(
        manifest,
        {
            "source": "mt5",
            "passed": True,
            "symbols": quality_by_symbol,
            "published_discovery_csvs": published,
            "bid_ask_mode": "bar_ohlc_midpoint_proxy",
            "note": "MT5 bar history has no historical bid/ask OHLC in this import path; bid/ask fields are proxied from bar OHLC.",
        },
    )
    return completed.model_dump(mode="json")


def fetch_historical(
    symbol: str,
    save_folder: str,
    tf_specs: list[dict[str, Any]],
    *,
    clear_existing: bool = False,
    publish_dataset: bool = True,
) -> dict[str, Any]:
    folder = save_folder or DEFAULT_HIST_FOLDER
    cleared: dict[str, Any] | None = None
    if clear_existing and folder == DEFAULT_HIST_FOLDER:
        # Only auto-clean the canonical hist_data folder. If the user pointed
        # at a custom folder via overrideOnce, leave it alone - they're in
        # control of that location.
        cleared = clear_data_folder()
    result = _run_toolkit_call(
        "main",
        {"symbol": symbol, "save_folder": folder, "tf_specs": tf_specs},
        timeout=180,
    )
    if not result.get("ok", False) and result.get("error") and not result.get("files"):
        raise RuntimeError(str(result["error"]))
    _validate_mt5_result(tf_specs, result)
    if cleared is not None:
        result["cleared"] = cleared
    if publish_dataset:
        dataset = _publish_mt5_dataset([symbol], result)
        if dataset is not None:
            result["dataset"] = dataset
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
    collide). One symbol failing does not abort the batch - its slot carries an
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
                publish_dataset=False,
            )
            per_symbol.append({"symbol": sym, **(res if isinstance(res, dict) else {})})
        except Exception as exc:  # noqa: BLE001 - isolate one symbol's failure
            per_symbol.append({
                "symbol": sym, "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            })
    failures = [
        f"{item['symbol']}: {item.get('error')}"
        for item in per_symbol
        if not item.get("ok", False)
    ]
    if failures:
        raise RuntimeError("MT5 basket import incomplete: " + "; ".join(failures))
    return {
        "ok": any(r.get("ok", False) for r in per_symbol),
        "save_folder": folder,
        "symbols": list(symbols),
        "per_symbol": per_symbol,
        "dataset": _publish_mt5_dataset(symbols, {"per_symbol": per_symbol}),
    }


def candles_for_days(prefix: str, time_value: int, trading_days: int) -> int:
    import import_hist_data as _ih  # type: ignore[import-not-found]
    return _ih.trading_days_to_candles(prefix, time_value, trading_days)
