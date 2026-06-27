"""Provider import orchestration, aggregation, and integrity checks."""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .catalog import MarketDataCatalog
from .models import DatasetManifest, MarketDataImportRequest
from .providers import DukascopyProvider, normalize_range


_FREQUENCIES = {
    "m1": "1min", "m2": "2min", "m3": "3min", "m4": "4min", "m5": "5min",
    "m10": "10min", "m15": "15min", "m20": "20min", "m30": "30min",
    "h1": "1h", "h2": "2h", "h4": "4h", "h6": "6h", "h8": "8h", "h12": "12h",
    "d1": "1D",
}


def aggregate_ticks(ticks: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    frequency = _FREQUENCIES.get(timeframe.lower())
    if frequency is None:
        raise ValueError(f"unsupported local aggregation timeframe: {timeframe}")
    if ticks.empty:
        return pd.DataFrame(columns=[
            "time", "open", "high", "low", "close", "bid_open", "bid_high", "bid_low",
            "bid_close", "ask_open", "ask_high", "ask_low", "ask_close", "tick_volume",
            "real_volume", "spread_open", "spread_mean", "spread_max",
        ])
    indexed = ticks.set_index("time").sort_index()
    mid = indexed["mid"].resample(frequency, label="left", closed="left").ohlc()
    bid = indexed["bid"].resample(frequency, label="left", closed="left").ohlc().add_prefix("bid_")
    ask = indexed["ask"].resample(frequency, label="left", closed="left").ohlc().add_prefix("ask_")
    counts = indexed["mid"].resample(frequency).count().rename("tick_volume")
    volume = (indexed["bid_volume"] + indexed["ask_volume"]).resample(frequency).sum().rename("real_volume")
    spreads = indexed["spread"].resample(frequency).agg(["first", "mean", "max"])
    spreads.columns = ["spread_open", "spread_mean", "spread_max"]
    bars = pd.concat([mid, bid, ask, counts, volume, spreads], axis=1).dropna(subset=["open"])
    return bars.reset_index()


def _tick_quality(ticks: pd.DataFrame) -> dict[str, Any]:
    duplicate_ticks = int(ticks["time"].duplicated().sum()) if not ticks.empty else 0
    crossed = int((ticks["ask"] < ticks["bid"]).sum()) if not ticks.empty else 0
    nonpositive = int((ticks[["bid", "ask"]] <= 0).any(axis=1).sum()) if not ticks.empty else 0
    return {
        "tick_rows": len(ticks), "duplicate_tick_times": duplicate_ticks,
        "crossed_quotes": crossed, "nonpositive_quotes": nonpositive,
        "missing_hour_files": len(ticks.attrs.get("missing_hours", [])),
        "missing_hours": list(ticks.attrs.get("missing_hours", [])),
    }


def _month_key(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


class MarketDataService:
    def __init__(self, catalog: MarketDataCatalog | None = None) -> None:
        self.catalog = catalog or MarketDataCatalog()

    def providers(self) -> list[dict[str, Any]]:
        return [{
            "id": "dukascopy", "name": "Dukascopy", "supports_ticks": True,
            "supports_bars": True, "venue": DukascopyProvider.venue,
        }]

    def import_data(self, request: MarketDataImportRequest) -> dict[str, Any]:
        from ..jobs.runners import check_cancelled, get_current_job

        start, end = normalize_range(request.date_from, request.date_to)
        if request.resume_dataset_id:
            manifest = self.catalog.load(request.resume_dataset_id)
            expected = (request.provider, request.symbols, request.timeframes, start.isoformat(), end.isoformat())
            actual = (
                manifest.provider, manifest.symbols, manifest.timeframes,
                manifest.requested_from, manifest.requested_to,
            )
            if actual != expected:
                raise ValueError("resume request does not match the existing dataset manifest")
            expected_options = {
                "include_ticks": request.include_ticks,
                "write_discovery_csv": request.write_discovery_csv,
                "price_digits": request.price_digits,
            }
            if any(manifest.import_options.get(key) != value for key, value in expected_options.items()):
                raise ValueError("resume options do not match the existing dataset manifest")
            if manifest.state == "complete":
                return manifest.model_dump()
            manifest.state = "building"
            manifest.error = None
            manifest.completed_at = None
        else:
            dataset_id = f"{request.provider}_{start:%Y%m%d}_{end:%Y%m%d}_{uuid.uuid4().hex[:8]}"
            manifest = DatasetManifest(
                dataset_id=dataset_id, provider=request.provider, venue=DukascopyProvider.venue,
                symbols=request.symbols, timeframes=request.timeframes,
                requested_from=start.isoformat(), requested_to=end.isoformat(),
                created_at=datetime.now(timezone.utc).isoformat(),
                import_options={
                    "include_ticks": request.include_ticks,
                    "write_discovery_csv": request.write_discovery_csv,
                    "price_digits": request.price_digits,
                    "storage_layout": "daily_ticks_monthly_bars",
                },
                progress={"completed_days": [], "day_quality": {}},
            )
        self.catalog.save_manifest(manifest)
        provider = DukascopyProvider(request.price_digits)
        all_quality: dict[str, Any] = {}
        try:
            days = list(provider.days(start, end))
            total_days = len(days) * len(request.symbols)
            completed_days = 0
            completed = set(manifest.progress.get("completed_days", []))
            day_quality = dict(manifest.progress.get("day_quality", {}))
            for symbol in request.symbols:
                months: dict[str, list[date]] = defaultdict(list)
                for day in days:
                    months[_month_key(day)].append(day)
                for month, month_days in months.items():
                    month_days_complete = all(
                        f"{symbol}:{day.isoformat()}" in completed for day in month_days
                    )
                    month_bars_complete = all(
                        any(
                            item.kind == "bars"
                            and item.symbol == symbol
                            and item.timeframe == timeframe
                            and Path(item.path).stem == month
                            and Path(item.path).is_file()
                            for item in manifest.files
                        )
                        for timeframe in request.timeframes
                    )
                    if month_days_complete and month_bars_complete:
                        completed_days += len(month_days)
                        continue
                    monthly_bars: dict[str, list[pd.DataFrame]] = {
                        timeframe: [] for timeframe in request.timeframes
                    }
                    for day_index, day in enumerate(month_days, start=1):
                        check_cancelled()
                        completed_days += 1
                        current_job = get_current_job()
                        if current_job is not None:
                            current_job.mark_stage(f"{symbol} {day.isoformat()}", completed_days, total_days)
                        print(
                            f"[{completed_days}/{total_days}] {symbol} {month} day "
                            f"{day_index}/{len(month_days)} {day}", flush=True,
                        )
                        day_id = f"{symbol}:{day.isoformat()}"
                        tick_path = self.catalog.folder(manifest.dataset_id) / "ticks" / symbol / f"{day}.parquet"
                        if tick_path.is_file():
                            ticks = pd.read_parquet(tick_path)
                            stats = _tick_quality(ticks)
                            day_quality[day_id] = stats
                            if not any(Path(item.path) == tick_path for item in manifest.files):
                                item = self.catalog.write_parquet(
                                    manifest, ticks, kind="ticks", symbol=symbol,
                                    relative_path=Path("ticks") / symbol / f"{day}.parquet",
                                )
                                item.quality = stats
                            completed.add(day_id)
                        elif day_id in completed and day_quality.get(day_id, {}).get("tick_rows") == 0:
                            ticks = pd.DataFrame()
                        else:
                            ticks = provider.fetch_day(symbol, day, start, end)
                            stats = _tick_quality(ticks)
                            day_quality[day_id] = stats
                            if request.include_ticks and not ticks.empty:
                                item = self.catalog.write_parquet(
                                    manifest, ticks, kind="ticks", symbol=symbol,
                                    relative_path=Path("ticks") / symbol / f"{day}.parquet",
                                )
                                item.quality = stats
                            completed.add(day_id)
                            manifest.progress = {
                                "completed_days": sorted(completed), "day_quality": day_quality,
                            }
                        if ticks.empty:
                            continue
                        for timeframe in request.timeframes:
                            monthly_bars[timeframe].append(aggregate_ticks(ticks, timeframe))
                    manifest.progress = {
                        "completed_days": sorted(completed), "day_quality": day_quality,
                    }
                    for timeframe, frames in monthly_bars.items():
                        check_cancelled()
                        frame = pd.concat(frames, ignore_index=True) if frames else aggregate_ticks(pd.DataFrame(), timeframe)
                        self.catalog.write_parquet(
                            manifest, frame, kind="bars", symbol=symbol, timeframe=timeframe,
                            relative_path=Path("bars") / symbol / timeframe / f"{month}.parquet",
                        )
                        self.catalog.save_manifest(manifest)

                symbol_stats = [day_quality.get(f"{symbol}:{day.isoformat()}", {}) for day in days]
                bar_rows = {
                    timeframe: sum(
                        item.rows for item in manifest.files
                        if item.kind == "bars" and item.symbol == symbol and item.timeframe == timeframe
                    ) for timeframe in request.timeframes
                }
                totals = {
                    key: sum(int(stats.get(key, 0)) for stats in symbol_stats)
                    for key in (
                        "tick_rows", "duplicate_tick_times", "crossed_quotes",
                        "nonpositive_quotes", "missing_hour_files",
                    )
                }
                missing_hour_details = [
                    {"day": day.isoformat(), **gap}
                    for day in days
                    for gap in day_quality.get(f"{symbol}:{day.isoformat()}", {}).get(
                        "missing_hours", []
                    )
                ]
                all_quality[symbol] = {
                    **totals, "missing_hour_details": missing_hour_details, "bar_rows": bar_rows,
                    "passed": totals["tick_rows"] > 0 and not any(
                        totals[key] for key in (
                            "duplicate_tick_times", "crossed_quotes", "nonpositive_quotes",
                            "missing_hour_files",
                        )
                    ),
                    "price_digits": provider.digits(symbol),
                    "point_size": 10 ** (-provider.digits(symbol)),
                }
                self.catalog.save_manifest(manifest)
            passed = all(value["passed"] for value in all_quality.values())
            if not passed:
                raise RuntimeError("market-data integrity checks failed; discovery CSVs were not published")
            if request.write_discovery_csv:
                for symbol in request.symbols:
                    for timeframe in request.timeframes:
                        paths = [
                            Path(item.path) for item in manifest.files
                            if item.kind == "bars" and item.symbol == symbol and item.timeframe == timeframe
                        ]
                        self.catalog.write_discovery_csv_from_parquet(
                            manifest, paths, symbol, timeframe
                        )
                        self.catalog.save_manifest(manifest)
            published = self.catalog.publish_discovery_csvs(manifest) if request.write_discovery_csv else []
            result = self.catalog.complete(
                manifest, {"symbols": all_quality, "passed": passed, "published_discovery_csvs": published}
            )
            return result.model_dump()
        except Exception as exc:
            manifest.state = "failed"
            manifest.error = f"{type(exc).__name__}: {exc}"
            manifest.completed_at = datetime.now(timezone.utc).isoformat()
            self.catalog.save_manifest(manifest)
            raise
        finally:
            close = getattr(provider, "close", None)
            if close is not None:
                close()

    def list_datasets(self) -> list[dict[str, Any]]:
        return [item.model_dump() for item in self.catalog.list()]

    def get_dataset(self, dataset_id: str) -> dict[str, Any]:
        return self.catalog.load(dataset_id).model_dump()

    def delete_dataset(self, dataset_id: str) -> dict[str, str]:
        return self.catalog.delete(dataset_id)


MARKET_DATA = MarketDataService()
