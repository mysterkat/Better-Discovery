"""Download the first XAUUSD Dukascopy tick batch for prop-firm research.

Defaults:
- symbol: XAUUSD
- range: 2018-01-01 UTC through 2026-01-01 UTC
- retained data: daily bid/ask tick Parquet plus m1/m5/m15/h1 bars
- legacy discovery CSV export: off

The script auto-resumes the newest matching incomplete dataset. If a failed run
contains open-market missing-hour gaps, it deletes only those affected day/month
artifacts before resuming so the importer refetches them.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.market_data.catalog import MarketDataCatalog  # noqa: E402
from app.market_data.models import MarketDataImportRequest  # noqa: E402
from app.research.service import RESEARCH  # noqa: E402


DEFAULT_FROM = "2018-01-01T00:00:00Z"
DEFAULT_TO = "2026-01-01T00:00:00Z"
DEFAULT_TIMEFRAMES = ("m1", "m5", "m15", "h1")
RESULT_PATH = ROOT / "userdata" / "research" / "xauusd_dukascopy_first_batch.json"


def parse_utc(value: str) -> datetime:
    normalized = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def same_request(manifest: Any, request: MarketDataImportRequest) -> bool:
    return (
        manifest.provider == request.provider
        and manifest.symbols == request.symbols
        and manifest.timeframes == request.timeframes
        and parse_utc(manifest.requested_from) == request.date_from
        and parse_utc(manifest.requested_to) == request.date_to
        and bool(manifest.import_options.get("include_ticks")) == request.include_ticks
        and bool(manifest.import_options.get("write_discovery_csv")) == request.write_discovery_csv
    )


def find_resume_dataset(catalog: MarketDataCatalog, request: MarketDataImportRequest) -> str | None:
    for manifest in catalog.list():
        if manifest.state != "complete" and same_request(manifest, request):
            return manifest.dataset_id
    return None


def repair_missing_hour_artifacts(catalog: MarketDataCatalog, dataset_id: str) -> list[str]:
    manifest = catalog.load(dataset_id)
    day_quality = dict(manifest.progress.get("day_quality", {}))
    bad_day_ids = [
        key for key, stats in day_quality.items()
        if int(stats.get("missing_hour_files", 0) or 0) > 0
    ]
    if not bad_day_ids:
        return []

    folder = catalog.folder(dataset_id)
    completed = set(manifest.progress.get("completed_days", []))
    affected = set()
    for day_id in bad_day_ids:
        symbol, day = day_id.split(":", 1)
        affected.add((symbol, day[:7]))
        completed.discard(day_id)
        day_quality.pop(day_id, None)
        (folder / "ticks" / symbol / f"{day}.parquet").unlink(missing_ok=True)

    kept = []
    for item in manifest.files:
        path = Path(item.path)
        remove = False
        if item.kind == "ticks":
            remove = any(
                item.symbol == symbol and path.name == f"{day_id.split(':', 1)[1]}.parquet"
                for symbol, _month in affected
                for day_id in bad_day_ids
                if day_id.startswith(f"{symbol}:")
            )
        elif item.kind == "bars":
            remove = (item.symbol, path.stem) in affected
        if remove:
            path.unlink(missing_ok=True)
        else:
            kept.append(item)

    manifest.files = kept
    manifest.progress = {
        "completed_days": sorted(completed),
        "day_quality": day_quality,
    }
    manifest.state = "building"
    manifest.error = None
    manifest.completed_at = None
    catalog.save_manifest(manifest)
    return bad_day_ids


def compact_result(result: dict[str, Any]) -> dict[str, Any]:
    dataset_id = result["dataset_id"]
    return {
        "experiment_id": result.get("experiment_id"),
        "dataset_id": dataset_id,
        "state": result["state"],
        "provider": result["provider"],
        "symbols": result["symbols"],
        "timeframes": result["timeframes"],
        "requested_from": result["requested_from"],
        "requested_to": result["requested_to"],
        "include_ticks": result["import_options"]["include_ticks"],
        "write_discovery_csv": result["import_options"]["write_discovery_csv"],
        "quality": result["quality"],
        "manifest_path": str(ROOT / "userdata" / "market_data" / dataset_id / "manifest.json"),
    }


def write_result(payload: dict[str, Any]) -> None:
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = RESULT_PATH.with_suffix(".json.tmp")
    temp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    temp.replace(RESULT_PATH)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--date-from", default=DEFAULT_FROM)
    parser.add_argument("--date-to", default=DEFAULT_TO)
    parser.add_argument("--timeframes", default=",".join(DEFAULT_TIMEFRAMES))
    parser.add_argument("--workers", type=int, default=12, help="Dukascopy hourly parallel requests.")
    parser.add_argument("--attempts", type=int, default=5, help="Retries per hourly file.")
    parser.add_argument("--timeout", type=float, default=30.0, help="HTTP timeout per hourly file.")
    parser.add_argument("--max-runs", type=int, default=20, help="Import retry/resume attempts.")
    parser.add_argument("--resume-dataset-id", default=None)
    parser.add_argument("--fresh", action="store_true", help="Do not auto-resume matching incomplete datasets.")
    parser.add_argument("--write-discovery-csv", action="store_true")
    args = parser.parse_args()

    os.environ["BD_DUKASCOPY_HOURLY_WORKERS"] = str(args.workers)
    os.environ["BD_DUKASCOPY_HOURLY_ATTEMPTS"] = str(args.attempts)
    os.environ["BD_DUKASCOPY_HTTP_TIMEOUT"] = str(args.timeout)

    timeframes = [item.strip().lower() for item in args.timeframes.split(",") if item.strip()]
    request = MarketDataImportRequest(
        provider="dukascopy",
        symbols=[args.symbol],
        timeframes=timeframes,
        date_from=parse_utc(args.date_from),
        date_to=parse_utc(args.date_to),
        include_ticks=True,
        write_discovery_csv=bool(args.write_discovery_csv),
    )

    catalog = MarketDataCatalog()
    resume_id = args.resume_dataset_id
    if resume_id is None and not args.fresh:
        resume_id = find_resume_dataset(catalog, request)
    if resume_id:
        repaired = repair_missing_hour_artifacts(catalog, resume_id)
        if repaired:
            print(f"Repaired {len(repaired)} missing-hour day(s) before resume.", flush=True)
        request.resume_dataset_id = resume_id
        print(f"Resuming dataset: {resume_id}", flush=True)
    else:
        print("Starting a new dataset.", flush=True)

    failures: list[dict[str, str | int]] = []
    for run_no in range(1, args.max_runs + 1):
        try:
            result = RESEARCH.import_market_data(request)
            payload = compact_result(result)
            payload["failures"] = failures
            write_result(payload)
            print(json.dumps(payload, indent=2, default=str), flush=True)
            return
        except (httpx.TransportError, httpx.HTTPStatusError, RuntimeError) as exc:
            message = str(exc)
            failures.append({
                "run": run_no,
                "type": type(exc).__name__,
                "message": message,
            })
            if run_no >= args.max_runs:
                write_result({"status": "failed", "failures": failures})
                raise
            if request.resume_dataset_id is None:
                request.resume_dataset_id = find_resume_dataset(catalog, request)
            if request.resume_dataset_id:
                repaired = repair_missing_hour_artifacts(catalog, request.resume_dataset_id)
                if repaired:
                    print(f"Repaired {len(repaired)} missing-hour day(s) before retry.", flush=True)
            delay = min(120, 10 * run_no)
            print(
                f"Import run {run_no}/{args.max_runs} failed: {type(exc).__name__}: "
                f"{message}. Retrying in {delay}s.",
                flush=True,
            )
            time.sleep(delay)


if __name__ == "__main__":
    main()
