from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from ..market_data.catalog import MarketDataCatalog
from ..paths import DEFAULT_RESEARCH
from ..research.store import ExperimentStore
from .bar_engine import run_bar_replay
from .models import HypothesisBarRequest, HypothesisBarResult
from .signals import build_signal_frame


class HypothesisResearchService:
    def __init__(
        self,
        catalog: MarketDataCatalog | None = None,
        store: ExperimentStore | None = None,
    ) -> None:
        self.catalog = catalog or MarketDataCatalog()
        self.store = store or ExperimentStore()
        self.output = DEFAULT_RESEARCH / "hypothesis"
        self.output.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _read(paths: list[str]) -> pd.DataFrame:
        return pd.concat((pd.read_parquet(path) for path in paths), ignore_index=True) if paths else pd.DataFrame()

    @staticmethod
    def _range(frame: pd.DataFrame, date_from, date_to) -> pd.DataFrame:
        if frame.empty:
            return frame
        times = pd.to_datetime(frame["time"], utc=True)
        mask = (times >= pd.Timestamp(date_from)) & (times < pd.Timestamp(date_to))
        return frame.loc[mask].copy().reset_index(drop=True)

    @staticmethod
    def _gate(
        metrics: dict[str, Any],
        role: str,
        override: dict[str, int | float] | None = None,
    ) -> dict[str, Any]:
        policy = {
            "fit": {"min_trades": 100, "min_profit_factor": 1.05, "max_drawdown_pct": 20.0},
            "internal_oos": {"min_trades": 60, "min_profit_factor": 1.15, "max_drawdown_pct": 12.0},
            "development": {"min_trades": 80, "min_profit_factor": 1.10, "max_drawdown_pct": 15.0},
            "validation": {"min_trades": 30, "min_profit_factor": 1.20, "max_drawdown_pct": 12.0},
            "variant_retest": {"min_trades": 30, "min_profit_factor": 1.20, "max_drawdown_pct": 12.0},
            "lockbox": {"min_trades": 30, "min_profit_factor": 1.15, "max_drawdown_pct": 12.0},
            "five_year_confirmation": {"min_trades": 100, "min_profit_factor": 1.15, "max_drawdown_pct": 15.0},
        }[role].copy()
        policy.update(override or {})
        stability_key = "positive_quarter_fraction" if role in {"fit", "internal_oos"} else "positive_month_fraction"
        checks = {
            "minimum_trades": metrics["trades"] >= policy["min_trades"],
            "profit_factor": metrics["profit_factor"] is not None and metrics["profit_factor"] >= policy["min_profit_factor"],
            "positive_expectancy": metrics["expected_payoff"] is not None and metrics["expected_payoff"] > 0,
            "drawdown": metrics["max_drawdown_pct"] <= policy["max_drawdown_pct"],
            "chronological_stability": metrics[stability_key] >= float(
                policy.get(
                    "min_positive_quarter_fraction" if stability_key == "positive_quarter_fraction"
                    else "min_positive_month_fraction",
                    0.5,
                )
            ),
        }
        if role == "development":
            checks["minimum_positive_years"] = metrics["positive_years"] >= int(
                policy.get("min_positive_years", 3)
            )
        return {"decision": "promote" if all(checks.values()) else "reject", "checks": checks, "policy": policy}

    def _bars(self, manifest, symbol: str, timeframe: str, date_from, date_to) -> pd.DataFrame:
        paths = sorted(
            item.path for item in manifest.files
            if item.kind == "bars" and item.symbol == symbol and item.timeframe == timeframe
        )
        return self._range(self._read(paths), date_from, date_to)

    def run(self, request: HypothesisBarRequest) -> dict[str, Any]:
        manifest = self.catalog.load(request.dataset_id)
        if manifest.state != "complete":
            raise ValueError(f"dataset {request.dataset_id} is not complete")
        symbol = "XAUUSD"
        bars = self._bars(manifest, symbol, request.strategy.timeframe, request.date_from, request.date_to)
        if bars.empty:
            raise ValueError("no primary bars in requested range")
        warmup_from = pd.Timestamp(request.date_from) - pd.Timedelta(days=120)
        warm_bars = self._bars(manifest, symbol, request.strategy.timeframe, warmup_from, request.date_to)
        contexts = {
            timeframe: self._bars(manifest, symbol, timeframe, warmup_from, request.date_to)
            for timeframe in request.strategy.context_timeframes
        }
        signals = build_signal_frame(warm_bars, contexts, request.strategy)
        signals = signals.loc[(signals.index >= pd.Timestamp(request.date_from)) & (signals.index < pd.Timestamp(request.date_to))]
        return self.run_preloaded(request, bars, signals)

    def run_preloaded(
        self,
        request: HypothesisBarRequest,
        bars: pd.DataFrame,
        signals: pd.DataFrame,
    ) -> dict[str, Any]:
        if (
            request.dataset_role == "lockbox"
            and self.store.has_completed_lockbox(
                request.strategy.fingerprint, kind="hypothesis_bar_replay"
            )
        ):
            raise ValueError("lockbox already consumed for this strategy fingerprint")
        experiment_id = self.store.create(
            "hypothesis_bar_replay",
            request.model_dump(mode="json"),
            strategy_fingerprint=request.strategy.fingerprint,
            dataset_role=request.dataset_role,
        )
        try:
            ledger, metrics = run_bar_replay(bars, signals, request)
            gate = self._gate(metrics, request.dataset_role, request.promotion_policy)
            folder = self.output / experiment_id
            folder.mkdir(parents=True, exist_ok=False)
            ledger_csv = folder / "closed_trades.csv"
            ledger_parquet = folder / "closed_trades.parquet"
            ledger.to_csv(ledger_csv, index=False)
            ledger.to_parquet(ledger_parquet, index=False, compression="zstd")
            result = HypothesisBarResult(
                experiment_id=experiment_id,
                strategy_fingerprint=request.strategy.fingerprint,
                dataset_id=request.dataset_id,
                dataset_role=request.dataset_role,
                ledger_parquet=str(ledger_parquet),
                ledger_csv=str(ledger_csv),
                metrics=metrics,
                gate=gate,
            ).model_dump(mode="json")
            metadata = {
                "schema_version": 1,
                "strategy": request.strategy.model_dump(mode="json"),
                "request": request.model_dump(mode="json"),
                **result,
            }
            (folder / "result.json").write_text(
                json.dumps(metadata, indent=2, default=str), encoding="utf-8"
            )
            self.store.finish(experiment_id, result)
            return result
        except Exception as exc:
            self.store.fail(experiment_id, str(exc))
            raise
