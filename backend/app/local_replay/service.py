from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

import pandas as pd

from ..market_data.catalog import MarketDataCatalog
from ..paths import DEFAULT_RESEARCH
from ..research.models import StrategySpec
from .engine import ENGINE_VERSION, run_replay_stream
from .models import ReplayRequest, ReplayResult


MQL_TIMEFRAMES = {
    "1": "m1", "2": "m2", "3": "m3", "4": "m4", "5": "m5", "6": "m6",
    "10": "m10", "12": "m12", "15": "m15", "20": "m20", "30": "m30",
    "16385": "h1", "16386": "h2", "16387": "h3", "16388": "h4",
    "16390": "h6", "16392": "h8", "16396": "h12", "16408": "d1",
    "PERIOD_M1": "m1", "PERIOD_M5": "m5", "PERIOD_M10": "m10", "PERIOD_M15": "m15",
    "PERIOD_M30": "m30", "PERIOD_H1": "h1", "PERIOD_H2": "h2", "PERIOD_H4": "h4",
    "PERIOD_H6": "h6", "PERIOD_H8": "h8", "PERIOD_H12": "h12", "PERIOD_D1": "d1",
}


class LocalReplayService:
    def __init__(self, catalog: MarketDataCatalog | None = None) -> None:
        self.catalog = catalog or MarketDataCatalog()
        self.output = DEFAULT_RESEARCH / "local_replay"
        self.output.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _read(files: list[str]) -> pd.DataFrame:
        if not files:
            return pd.DataFrame()
        return pd.concat((pd.read_parquet(path) for path in files), ignore_index=True)

    @staticmethod
    def _tick_batches(files: list[str]):
        for path in files:
            yield pd.read_parquet(path, columns=["time", "bid", "ask"])

    def run(self, request: ReplayRequest) -> dict[str, Any]:
        manifest = self.catalog.load(request.dataset_id)
        if manifest.state != "complete":
            raise ValueError(f"dataset {request.dataset_id} is not complete")
        symbol = request.symbol.upper()
        timeframe = request.timeframe.lower()
        strategy = StrategySpec.from_set(request.set_path)
        tick_files = sorted(
            (f for f in manifest.files if f.kind == "ticks" and f.symbol == symbol),
            key=lambda item: (item.first_time or "", item.path),
        )
        tick_paths = [f.path for f in tick_files]
        bar_paths = [
            f.path for f in manifest.files
            if f.kind == "bars" and f.symbol == symbol and f.timeframe == timeframe
        ]
        if not tick_paths:
            raise ValueError("dataset has no retained ticks for this symbol")
        if not bar_paths:
            raise ValueError(f"dataset has no {timeframe} bars for {symbol}")
        bars = self._read(sorted(bar_paths))
        signal_bars: dict[str, pd.DataFrame] = {}
        missing_signals: list[str] = []
        for slot in range(1, 5):
            raw_timeframe = strategy.parameters.get(f"SignalTF{slot}", "0")
            signal_timeframe = MQL_TIMEFRAMES.get(raw_timeframe)
            if not signal_timeframe or signal_timeframe == timeframe:
                continue
            paths = [
                f.path for f in manifest.files
                if f.kind == "bars" and f.symbol == symbol and f.timeframe == signal_timeframe
            ]
            if paths:
                signal_bars[f"tf{slot}"] = self._read(paths)
            else:
                missing_signals.append(signal_timeframe)
        symbol_quality = manifest.quality.get("symbols", {}).get(symbol, {})
        point_size = float(symbol_quality.get("point_size", 0.00001))
        active_mtf = any(
            key in strategy.parameters for key in
            ("mtf_bull_score_lo", "mtf_bull_score_hi", "htf_div_lo", "htf_div_hi")
        )
        if missing_signals and active_mtf:
            raise ValueError(
                "dataset is missing signal timeframe bars required by this strategy: "
                + ", ".join(sorted(set(missing_signals)))
            )
        ledger, metrics, features = run_replay_stream(
            self._tick_batches(tick_paths), bars, strategy, request, point_size,
            signal_bars=signal_bars, total_ticks=sum(item.rows for item in tick_files),
        )

        replay_id = uuid.uuid4().hex
        folder = self.output / replay_id
        folder.mkdir(parents=True, exist_ok=False)
        dataset_fingerprint = hashlib.sha256(
            json.dumps([f.sha256 for f in manifest.files], sort_keys=True).encode("utf-8")
        ).hexdigest()
        metadata = {
            "schema_version": 1, "replay_id": replay_id, "engine_version": ENGINE_VERSION,
            "strategy_fingerprint": strategy.fingerprint, "dataset_id": manifest.dataset_id,
            "dataset_fingerprint": dataset_fingerprint, "provider": manifest.provider,
            "venue": manifest.venue, "symbol": symbol, "timeframe": timeframe,
            "dataset_role": request.dataset_role, "request": request.model_dump(mode="json"),
            "metrics": metrics.model_dump(), "tick_loading_mode": "partition_stream",
        }
        for key, value in {
            "replay_id": replay_id, "engine_version": ENGINE_VERSION,
            "dataset_id": manifest.dataset_id, "dataset_fingerprint": dataset_fingerprint,
            "provider": manifest.provider, "venue": manifest.venue, "symbol": symbol,
            "timeframe": timeframe, "dataset_role": request.dataset_role,
        }.items():
            ledger[key] = value
        drop_columns = ["direction_sign", "max_hold_time"]
        export = ledger.drop(columns=[c for c in drop_columns if c in ledger], errors="ignore")
        csv_path, parquet_path = folder / "closed_trades.csv", folder / "closed_trades.parquet"
        export.to_csv(csv_path, index=False)
        export.to_parquet(parquet_path, index=False, compression="zstd")
        (folder / "replay.json").write_text(json.dumps(metadata, indent=2, default=str), encoding="utf-8")

        stride = max(1, len(bars) // request.chart_max_bars)
        chart_bars = bars.iloc[::stride]
        chart = {
            "bars": chart_bars[["time", "open", "high", "low", "close"]].assign(
                time=lambda frame: frame["time"].astype(str)
            ).to_dict("records"),
            "trades": export[[
                "entry_time", "entry_price", "exit_time", "exit_price", "direction", "net_pnl", "exit_reason"
            ]].assign(
                entry_time=lambda frame: frame["entry_time"].astype(str),
                exit_time=lambda frame: frame["exit_time"].astype(str),
            ).to_dict("records") if not export.empty else [],
        }
        warnings = []
        if missing_signals:
            warnings.append("Unused signal timeframe bars were unavailable: " + ", ".join(sorted(set(missing_signals))))
        result = ReplayResult(
            replay_id=replay_id, strategy_fingerprint=strategy.fingerprint,
            dataset_id=manifest.dataset_id, dataset_role=request.dataset_role,
            ledger_csv=str(csv_path), ledger_parquet=str(parquet_path), metrics=metrics,
            chart=chart, warnings=warnings,
        )
        return result.model_dump(mode="json")


LOCAL_REPLAY = LocalReplayService()
