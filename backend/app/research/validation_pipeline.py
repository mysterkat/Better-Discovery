from __future__ import annotations

import json
import math
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field, model_validator

from ..jobs.runners import check_cancelled, get_current_job
from ..paths import DEFAULT_RESEARCH
from ..hypothesis.bar_engine import run_bar_replay
from ..hypothesis.grammar import mutate_hypothesis
from ..hypothesis.models import HypothesisBarRequest, HypothesisSpec
from ..hypothesis.service import HypothesisResearchService
from ..hypothesis.signals import align_signal_timeframe, apply_signal_rules, build_base_frame
from .store import ExperimentStore


class StrategyValidationRequest(BaseModel):
    dataset_id: str
    pattern_id: str
    strategy: HypothesisSpec
    library_name: str | None = None
    date_from: datetime
    date_to: datetime
    initial_balance: float = Field(default=10_000.0, gt=0)
    lot_size: float = Field(default=0.1, gt=0)
    contract_size: float = Field(default=100.0, gt=0)
    commission_per_lot_round_turn: float = Field(default=7.0, ge=0)
    slippage_price_units: float = Field(default=0.10, ge=0)
    oos_fraction: float = Field(default=0.30, ge=0.10, le=0.60)
    walk_train_months: int = Field(default=24, ge=3, le=120)
    walk_test_months: int = Field(default=6, ge=1, le=24)
    walk_mutation_samples: int = Field(default=12, ge=0, le=100)
    stability_samples: int = Field(default=24, ge=0, le=200)
    stability_seed: int = Field(default=42, ge=0, le=2_147_483_647)
    min_profit_factor: float = Field(default=1.30, ge=1.0, le=5.0)
    min_sharpe: float = Field(default=1.0, ge=-5.0, le=10.0)
    max_drawdown_pct: float = Field(default=12.0, gt=0.0, le=100.0)
    min_walk_forward_pass_rate: float = Field(default=0.60, ge=0.0, le=1.0)
    min_stability_pass_rate: float = Field(default=0.60, ge=0.0, le=1.0)
    min_trades: int = Field(default=100, ge=1)

    @model_validator(mode="after")
    def validate_range(self) -> "StrategyValidationRequest":
        if self.date_to <= self.date_from:
            raise ValueError("date_to must be after date_from")
        return self


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        if not np.isfinite(value):
            return None
        return float(value)
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if pd.isna(value) if not isinstance(value, (str, bytes, bytearray, dict, list, tuple)) else False:
        return None
    return value


def _session(hour: int) -> str:
    if 0 <= hour < 7:
        return "asia"
    if 7 <= hour < 12:
        return "london"
    if 12 <= hour < 17:
        return "ny_overlap"
    if 17 <= hour < 22:
        return "ny_late"
    return "rollover"


def _extended_metrics(ledger: pd.DataFrame, metrics: dict[str, Any], initial_balance: float) -> dict[str, Any]:
    out = dict(metrics)
    if ledger.empty:
        out.update({
            "sharpe": None,
            "sortino": None,
            "trades_per_week": 0.0,
            "largest_trade_concentration_pct": 0.0,
            "largest_month_concentration_pct": 0.0,
        })
        return out
    pnl = ledger["net_pnl"].to_numpy(dtype=float)
    mean = float(np.mean(pnl))
    std = float(np.std(pnl, ddof=1)) if len(pnl) > 1 else 0.0
    downside = pnl[pnl < 0]
    downside_std = float(np.std(downside, ddof=1)) if len(downside) > 1 else 0.0
    exits = pd.to_datetime(ledger["exit_time"], utc=True)
    span_days = max(1.0, (exits.max() - exits.min()).total_seconds() / 86400.0)
    trades_per_week = float(len(ledger) / (span_days / 7.0))
    abs_total = float(np.abs(pnl).sum())
    largest_trade = float(np.max(np.abs(pnl))) if len(pnl) else 0.0
    monthly = ledger.assign(month=exits.dt.strftime("%Y-%m")).groupby("month")["net_pnl"].sum()
    net_total = abs(float(np.sum(pnl)))
    largest_month = float(monthly.abs().max()) if len(monthly) else 0.0
    out.update({
        "sharpe": (mean / std) * math.sqrt(252.0) if std > 0 else None,
        "sortino": (mean / downside_std) * math.sqrt(252.0) if downside_std > 0 else None,
        "trades_per_week": trades_per_week,
        "largest_trade_concentration_pct": (largest_trade / abs_total * 100.0) if abs_total > 0 else 0.0,
        "largest_month_concentration_pct": (largest_month / net_total * 100.0) if net_total > 0 else 0.0,
        "return_pct": float(np.sum(pnl) / initial_balance * 100.0),
    })
    return out


def _gate(metrics: dict[str, Any], request: StrategyValidationRequest, *, allow_lower_trades: bool = False) -> dict[str, Any]:
    trades_required = max(1, int(request.min_trades * (0.35 if allow_lower_trades else 1.0)))
    checks = {
        "minimum_trades": int(metrics.get("trades") or 0) >= trades_required,
        "net_profit_positive": float(metrics.get("net_profit") or 0.0) > 0,
        "profit_factor": metrics.get("profit_factor") is not None and float(metrics["profit_factor"]) >= request.min_profit_factor,
        "sharpe": metrics.get("sharpe") is not None and float(metrics["sharpe"]) >= request.min_sharpe,
        "drawdown": float(metrics.get("max_drawdown_pct") or 0.0) <= request.max_drawdown_pct,
        "positive_months": float(metrics.get("positive_month_fraction") or 0.0) >= 0.50,
        "concentration": float(metrics.get("largest_month_concentration_pct") or 0.0) <= 65.0,
    }
    return {"decision": "pass" if all(checks.values()) else "reject", "checks": checks}


class StrategyValidationPipeline:
    def __init__(
        self,
        hypothesis: HypothesisResearchService | None = None,
        store: ExperimentStore | None = None,
    ) -> None:
        self.hypothesis = hypothesis or HypothesisResearchService()
        self.store = store or ExperimentStore()
        self.output = DEFAULT_RESEARCH / "strategy_validation"
        self.output.mkdir(parents=True, exist_ok=True)

    def _load_frames(self, request: StrategyValidationRequest) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, pd.DataFrame]]:
        manifest = self.hypothesis.catalog.load(request.dataset_id)
        if manifest.state != "complete":
            raise ValueError(f"dataset {request.dataset_id} is not complete")
        warmup_from = pd.Timestamp(request.date_from) - pd.Timedelta(days=120)
        bars = self.hypothesis._bars(manifest, "XAUUSD", request.strategy.timeframe, request.date_from, request.date_to)
        if bars.empty:
            raise ValueError("no primary bars in requested validation range")
        warm_bars = self.hypothesis._bars(manifest, "XAUUSD", request.strategy.timeframe, warmup_from, request.date_to)
        contexts = {
            timeframe: self.hypothesis._bars(manifest, "XAUUSD", timeframe, warmup_from, request.date_to)
            for timeframe in request.strategy.context_timeframes
        }
        base_frame = build_base_frame(warm_bars, contexts)
        grammar_frames: dict[str, pd.DataFrame] = {request.strategy.timeframe: base_frame}
        grammar_timeframes = self.hypothesis._grammar_timeframes(request.strategy)
        for timeframe in grammar_timeframes:
            if timeframe == request.strategy.timeframe:
                continue
            warm_tf_bars = self.hypothesis._bars(manifest, "XAUUSD", timeframe, warmup_from, request.date_to)
            if warm_tf_bars.empty:
                raise ValueError(f"missing grammar timeframe bars: {timeframe.upper()}")
            grammar_frames[timeframe] = align_signal_timeframe(
                base_frame,
                build_base_frame(warm_tf_bars, contexts),
                timeframe,
                request.strategy.timeframe,
            )
        return bars, base_frame, grammar_frames

    def _request(
        self,
        request: StrategyValidationRequest,
        strategy: HypothesisSpec,
        date_from: pd.Timestamp,
        date_to: pd.Timestamp,
        role: Literal["validation", "walk_forward", "variant_retest"] = "validation",
    ) -> HypothesisBarRequest:
        return HypothesisBarRequest(
            dataset_id=request.dataset_id,
            strategy=strategy,
            date_from=date_from.to_pydatetime(),
            date_to=date_to.to_pydatetime(),
            dataset_role=role,
            initial_balance=request.initial_balance,
            lot_size=request.lot_size,
            contract_size=request.contract_size,
            commission_per_lot_round_turn=request.commission_per_lot_round_turn,
            slippage_price_units=request.slippage_price_units,
            promotion_policy={"min_trades": 1, "min_profit_factor": 1.0, "max_drawdown_pct": 100.0},
        )

    def _evaluate(
        self,
        request: StrategyValidationRequest,
        strategy: HypothesisSpec,
        bars: pd.DataFrame,
        base_frame: pd.DataFrame,
        grammar_frames: dict[str, pd.DataFrame],
        date_from: pd.Timestamp,
        date_to: pd.Timestamp,
        *,
        role: Literal["validation", "walk_forward", "variant_retest"] = "validation",
    ) -> tuple[pd.DataFrame, dict[str, Any]]:
        segment_bars = self.hypothesis._range(bars, date_from, date_to)
        if segment_bars.empty:
            return pd.DataFrame(), _extended_metrics(pd.DataFrame(), {"trades": 0, "net_profit": 0.0, "profit_factor": None, "expected_payoff": None, "max_drawdown_pct": 0.0}, request.initial_balance)
        signals = apply_signal_rules(base_frame, strategy, grammar_frames)
        signals = signals.loc[(signals.index >= date_from) & (signals.index < date_to)]
        ledger, metrics = run_bar_replay(segment_bars, signals, self._request(request, strategy, date_from, date_to, role))
        return ledger, _extended_metrics(ledger, metrics, request.initial_balance)

    def _is_oos(
        self,
        request: StrategyValidationRequest,
        bars: pd.DataFrame,
        base_frame: pd.DataFrame,
        grammar_frames: dict[str, pd.DataFrame],
        folder: Path,
    ) -> dict[str, Any]:
        start = pd.Timestamp(request.date_from)
        end = pd.Timestamp(request.date_to)
        split = start + (end - start) * (1.0 - request.oos_fraction)
        train_ledger, train_metrics = self._evaluate(request, request.strategy, bars, base_frame, grammar_frames, start, split)
        oos_ledger, oos_metrics = self._evaluate(request, request.strategy, bars, base_frame, grammar_frames, split, end)
        train_ledger.to_csv(folder / "is_trades.csv", index=False)
        oos_ledger.to_csv(folder / "oos_trades.csv", index=False)
        if not oos_ledger.empty:
            oos_ledger.to_parquet(folder / "oos_trades.parquet", index=False, compression="zstd")
        return {
            "split_time": split.isoformat(),
            "in_sample": {
                "from": start.isoformat(),
                "to": split.isoformat(),
                "metrics": train_metrics,
                "gate": _gate(train_metrics, request),
                "ledger_csv": str(folder / "is_trades.csv"),
            },
            "out_of_sample": {
                "from": split.isoformat(),
                "to": end.isoformat(),
                "metrics": oos_metrics,
                "gate": _gate(oos_metrics, request),
                "ledger_csv": str(folder / "oos_trades.csv"),
                "ledger_parquet": str(folder / "oos_trades.parquet") if (folder / "oos_trades.parquet").is_file() else None,
            },
        }

    def _walk_forward(
        self,
        request: StrategyValidationRequest,
        bars: pd.DataFrame,
        base_frame: pd.DataFrame,
        grammar_frames: dict[str, pd.DataFrame],
    ) -> dict[str, Any]:
        folds: list[dict[str, Any]] = []
        start = pd.Timestamp(request.date_from)
        end = pd.Timestamp(request.date_to)
        fold_start = start
        fold_index = 1
        while True:
            check_cancelled()
            train_from = fold_start
            train_to = train_from + pd.DateOffset(months=request.walk_train_months)
            test_to = train_to + pd.DateOffset(months=request.walk_test_months)
            if test_to > end:
                break
            candidates = [request.strategy]
            for idx in range(request.walk_mutation_samples):
                candidates.append(mutate_hypothesis(request.strategy, child_index=idx, generation=1, seed=request.stability_seed + fold_index * 1000))
            ranked: list[tuple[float, HypothesisSpec, dict[str, Any]]] = []
            for candidate in candidates:
                _ledger, metrics = self._evaluate(request, candidate, bars, base_frame, grammar_frames, train_from, train_to, role="walk_forward")
                pf = float(metrics.get("profit_factor") or 0.0)
                score = pf * 1000.0 + float(metrics.get("net_profit") or 0.0) - float(metrics.get("max_drawdown_pct") or 0.0) * 10.0
                ranked.append((score, candidate, metrics))
            ranked.sort(key=lambda item: item[0], reverse=True)
            selected = ranked[0][1]
            _test_ledger, test_metrics = self._evaluate(request, selected, bars, base_frame, grammar_frames, train_to, test_to, role="walk_forward")
            gate = _gate(test_metrics, request, allow_lower_trades=True)
            folds.append({
                "fold": fold_index,
                "train_from": train_from.isoformat(),
                "train_to": train_to.isoformat(),
                "test_from": train_to.isoformat(),
                "test_to": test_to.isoformat(),
                "selected_strategy_id": selected.strategy_id,
                "selected_parent": selected.parameters.get("guided_parent"),
                "train_metrics": ranked[0][2],
                "test_metrics": test_metrics,
                "gate": gate,
            })
            fold_index += 1
            fold_start = fold_start + pd.DateOffset(months=request.walk_test_months)
        pass_count = sum(1 for fold in folds if fold["gate"]["decision"] == "pass")
        pass_rate = pass_count / len(folds) if folds else 0.0
        return {
            "train_months": request.walk_train_months,
            "test_months": request.walk_test_months,
            "mutation_samples_per_fold": request.walk_mutation_samples,
            "folds": folds,
            "fold_count": len(folds),
            "pass_count": pass_count,
            "pass_rate": pass_rate,
            "decision": "pass" if folds and pass_rate >= request.min_walk_forward_pass_rate else "reject",
        }

    def _stability(
        self,
        request: StrategyValidationRequest,
        bars: pd.DataFrame,
        base_frame: pd.DataFrame,
        grammar_frames: dict[str, pd.DataFrame],
    ) -> dict[str, Any]:
        start = pd.Timestamp(request.date_from)
        end = pd.Timestamp(request.date_to)
        variants: list[dict[str, Any]] = []
        for idx in range(request.stability_samples):
            check_cancelled()
            child = mutate_hypothesis(request.strategy, child_index=idx, generation=1, seed=request.stability_seed)
            _ledger, metrics = self._evaluate(request, child, bars, base_frame, grammar_frames, start, end, role="variant_retest")
            gate = _gate(metrics, request, allow_lower_trades=True)
            variants.append({
                "strategy_id": child.strategy_id,
                "parent": child.parameters.get("guided_parent"),
                "metrics": metrics,
                "gate": gate,
            })
        pass_count = sum(1 for item in variants if item["gate"]["decision"] == "pass")
        pass_rate = pass_count / len(variants) if variants else 0.0
        return {
            "samples": len(variants),
            "pass_count": pass_count,
            "pass_rate": pass_rate,
            "decision": "pass" if (not variants or pass_rate >= request.min_stability_pass_rate) else "reject",
            "variants": variants,
        }

    def _regime_breakdown(self, ledger: pd.DataFrame) -> dict[str, Any]:
        if ledger.empty:
            return {"sessions": [], "weekdays": [], "directions": [], "exit_reasons": []}
        frame = ledger.copy()
        entries = pd.to_datetime(frame["entry_time"], utc=True)
        frame["session"] = entries.dt.hour.map(_session)
        frame["weekday"] = entries.dt.day_name()
        groups: dict[str, str] = {
            "sessions": "session",
            "weekdays": "weekday",
            "directions": "direction",
            "exit_reasons": "exit_reason",
        }
        result: dict[str, Any] = {}
        for name, column in groups.items():
            rows = []
            for key, subset in frame.groupby(column):
                metrics = _extended_metrics(subset, {"trades": len(subset), "net_profit": float(subset["net_pnl"].sum()), "profit_factor": None, "expected_payoff": float(subset["net_pnl"].mean()), "max_drawdown_pct": 0.0}, 10_000.0)
                pnl = subset["net_pnl"]
                gross_profit = float(pnl[pnl > 0].sum())
                gross_loss = float(abs(pnl[pnl < 0].sum()))
                metrics["profit_factor"] = gross_profit / gross_loss if gross_loss else None
                rows.append({"bucket": str(key), "metrics": metrics})
            result[name] = sorted(rows, key=lambda item: float(item["metrics"].get("net_profit") or 0.0), reverse=True)
        return result

    def run(self, request: StrategyValidationRequest) -> dict[str, Any]:
        experiment_id = self.store.create(
            "strategy_validation",
            request.model_dump(mode="json"),
            strategy_fingerprint=request.strategy.fingerprint,
            dataset_role="overall",
        )
        folder = self.output / experiment_id
        folder.mkdir(parents=True, exist_ok=False)
        started = time.time()
        try:
            job = get_current_job()
            if job:
                job.mark_stage("Loading bars and signals", 0, 5)
            bars, base_frame, grammar_frames = self._load_frames(request)
            if job:
                job.mark_stage("IS/OOS validation", 1, 5)
            is_oos = self._is_oos(request, bars, base_frame, grammar_frames, folder)
            if job:
                job.mark_stage("Walk-forward validation", 2, 5)
            walk_forward = self._walk_forward(request, bars, base_frame, grammar_frames)
            if job:
                job.mark_stage("Parameter stability", 3, 5)
            stability = self._stability(request, bars, base_frame, grammar_frames)
            if job:
                job.mark_stage("Regime breakdown", 4, 5)
            oos_path = Path(str(is_oos["out_of_sample"].get("ledger_csv")))
            oos_ledger = pd.read_csv(oos_path) if oos_path.is_file() else pd.DataFrame()
            breakdown = self._regime_breakdown(oos_ledger)
            checks = {
                "out_of_sample": is_oos["out_of_sample"]["gate"]["decision"] == "pass",
                "walk_forward": walk_forward["decision"] == "pass",
                "parameter_stability": stability["decision"] == "pass",
            }
            result = {
                "experiment_id": experiment_id,
                "pattern_id": request.pattern_id,
                "strategy_id": request.strategy.strategy_id,
                "library_name": request.library_name or request.pattern_id,
                "dataset_id": request.dataset_id,
                "artifact_folder": str(folder),
                "settings": request.model_dump(mode="json"),
                "is_oos": is_oos,
                "walk_forward": walk_forward,
                "parameter_stability": stability,
                "regime_breakdown": breakdown,
                "overall": {
                    "decision": "pass" if all(checks.values()) else "reject",
                    "checks": checks,
                    "runtime_seconds": time.time() - started,
                },
            }
            (folder / "validation_result.json").write_text(
                json.dumps(_json_safe(result), indent=2),
                encoding="utf-8",
            )
            self.store.finish(experiment_id, _json_safe(result))
            if job:
                job.mark_stage("Done", 5, 5)
            return _json_safe(result)
        except Exception as exc:
            self.store.fail(experiment_id, str(exc))
            raise
