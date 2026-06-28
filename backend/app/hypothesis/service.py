from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import pandas as pd

from ..jobs.runners import check_cancelled, get_current_job
from ..market_data.catalog import MarketDataCatalog
from ..paths import DEFAULT_RESEARCH
from ..research.store import ExperimentStore
from .bar_engine import run_bar_replay, run_bar_replay_fast_metrics
from .challenge import evaluate_challenge_grid
from .grammar import generate_hypotheses
from .models import (
    HypothesisBarRequest,
    HypothesisBarResult,
    HypothesisDiscoveryRequest,
    HypothesisDiscoveryResult,
    HypothesisSpec,
)
from .signals import apply_signal_rules, build_base_frame, build_signal_frame


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

    def _replay_request(
        self,
        request: HypothesisDiscoveryRequest,
        strategy: HypothesisSpec,
    ) -> HypothesisBarRequest:
        return HypothesisBarRequest(
            dataset_id=request.dataset_id,
            strategy=strategy,
            date_from=request.date_from,
            date_to=request.date_to,
            dataset_role="development",
            initial_balance=request.challenge.initial_balance,
            lot_size=request.lot_size,
            contract_size=request.contract_size,
            commission_per_lot_round_turn=request.commission_per_lot_round_turn,
            slippage_price_units=request.slippage_price_units,
            promotion_policy={
                "min_trades": request.min_closed_trades,
                "min_profit_factor": 1.0,
                "max_drawdown_pct": 100.0,
                "min_positive_month_fraction": 0.0,
                "min_positive_years": 0,
            },
        )

    @staticmethod
    def _candidate_row(
        strategy: HypothesisSpec,
        metrics: dict[str, Any],
        challenge: dict[str, Any],
    ) -> dict[str, Any]:
        best = challenge["best"]["summary"]
        compact_challenge = {
            "best": best,
            "grid_summaries": [item["summary"] for item in challenge["grid"]],
        }
        return {
            "strategy_id": strategy.strategy_id,
            "strategy_fingerprint": strategy.fingerprint,
            "lineage": strategy.lineage,
            "hypothesis": strategy.hypothesis,
            "parameters": strategy.parameters,
            "trades": metrics["trades"],
            "net_profit": metrics["net_profit"],
            "profit_factor": metrics["profit_factor"],
            "expected_payoff": metrics["expected_payoff"],
            "max_drawdown_pct": metrics["max_drawdown_pct"],
            "challenge_score": best["score"],
            "challenge_start_windows": best["start_windows"],
            "challenge_active_starts": best["active_starts"],
            "challenge_pass_count": best["pass_count"],
            "challenge_pass_rate": best["pass_rate"],
            "challenge_active_pass_rate": best["active_pass_rate"],
            "challenge_prop_fail_count": best["prop_fail_count"],
            "challenge_prop_fail_rate": best["prop_fail_rate"],
            "median_days_to_target": best["median_days_to_target"],
            "best_days_to_target": best["best_days_to_target"],
            "median_trades_to_target": best["median_trades_to_target"],
            "risk_fraction": best["risk_fraction"],
            "internal_daily_stop_pct": best["internal_daily_stop_pct"],
            "max_trades_per_day": best["max_trades_per_day"],
            "challenge": compact_challenge,
        }

    def _evaluate_candidate(
        self,
        request: HypothesisDiscoveryRequest,
        strategy: HypothesisSpec,
        bars: pd.DataFrame,
        base_frame: pd.DataFrame,
    ) -> tuple[dict[str, Any], pd.DataFrame] | None:
        replay_request = self._replay_request(request, strategy)
        signals = apply_signal_rules(base_frame, strategy)
        signals = signals.loc[
            (signals.index >= pd.Timestamp(request.date_from))
            & (signals.index < pd.Timestamp(request.date_to))
        ]
        signal_count = int((signals["signal_direction"] != 0).sum())
        if signal_count < request.min_closed_trades:
            return None

        fast_metrics = run_bar_replay_fast_metrics(bars, signals, replay_request)
        if fast_metrics["trades"] < request.min_closed_trades:
            return None
        profit_factor = fast_metrics["profit_factor"]
        expected_payoff = fast_metrics["expected_payoff"]
        if profit_factor is None or profit_factor < 0.90 or expected_payoff is None or expected_payoff <= 0:
            return None

        ledger, metrics = run_bar_replay(bars, signals, replay_request)
        if metrics["trades"] < request.min_closed_trades:
            return None
        if metrics["profit_factor"] is None or metrics["profit_factor"] < 0.90 or metrics["expected_payoff"] is None or metrics["expected_payoff"] <= 0:
            return None
        challenge = evaluate_challenge_grid(ledger, request.challenge)
        return self._candidate_row(strategy, metrics, challenge), ledger

    def run_discovery(self, request: HypothesisDiscoveryRequest) -> dict[str, Any]:
        manifest = self.catalog.load(request.dataset_id)
        if manifest.state != "complete":
            raise ValueError(f"dataset {request.dataset_id} is not complete")
        experiment_id = self.store.create(
            "hypothesis_discovery",
            request.model_dump(mode="json"),
            dataset_role="development",
        )
        try:
            bars = self._bars(
                manifest,
                request.symbol,
                request.timeframe,
                request.date_from,
                request.date_to,
            )
            if bars.empty:
                raise ValueError("no primary bars in requested range")
            warmup_from = pd.Timestamp(request.date_from) - pd.Timedelta(days=120)
            warm_bars = self._bars(
                manifest,
                request.symbol,
                request.timeframe,
                warmup_from,
                request.date_to,
            )
            contexts = {
                timeframe: self._bars(manifest, request.symbol, timeframe, warmup_from, request.date_to)
                for timeframe in ("h1", "h4")
            }
            base_frame = build_base_frame(warm_bars, contexts)
            specs = generate_hypotheses(request)
            rows: list[dict[str, Any]] = []
            top_ledgers: dict[str, pd.DataFrame] = {}
            current_job = get_current_job()
            variant_started_at = time.time()

            def record(result: tuple[dict[str, Any], pd.DataFrame] | None) -> None:
                if result is None:
                    return
                row, ledger = result
                rows.append(row)
                top_ledgers[row["strategy_id"]] = ledger

            workers = min(request.parallel_workers, len(specs) or 1)
            def update_variant_progress(index: int, total: int, workers_used: int) -> None:
                if current_job is None:
                    return
                current_job.mark_stage(
                    f"Hypothesis variants ({workers_used} workers)",
                    index,
                    total,
                )
                elapsed = max(0.001, time.time() - variant_started_at)
                completed = max(1, index)
                rate = completed / elapsed
                remaining = max(0, total - index)
                current_job.eta_seconds = remaining / rate if rate > 0 else None
                current_job.meta["hypothesis_progress"] = {
                    "completed_variants": index,
                    "total_variants": total,
                    "accepted_variants": len(rows),
                    "variants_per_hour": rate * 3600.0,
                    "eta_seconds": current_job.eta_seconds,
                }

            if workers <= 1:
                for index, strategy in enumerate(specs, start=1):
                    check_cancelled()
                    record(self._evaluate_candidate(request, strategy, bars, base_frame))
                    update_variant_progress(index, len(specs), workers)
            else:
                executor = ThreadPoolExecutor(max_workers=workers)
                futures = {
                    executor.submit(self._evaluate_candidate, request, strategy, bars, base_frame): strategy
                    for strategy in specs
                }
                cancel_futures = False
                try:
                    for index, future in enumerate(as_completed(futures), start=1):
                        check_cancelled()
                        record(future.result())
                        update_variant_progress(index, len(specs), workers)
                except Exception:
                    cancel_futures = True
                    for future in futures:
                        future.cancel()
                    raise
                finally:
                    executor.shutdown(wait=not cancel_futures, cancel_futures=cancel_futures)

            rows.sort(
                key=lambda item: (
                    item["challenge_score"],
                    item["challenge_pass_count"],
                    -(item["median_days_to_target"] or 999.0),
                    item["profit_factor"] or 0.0,
                ),
                reverse=True,
            )

            folder = self.output / "hypothesis_discovery" / experiment_id
            folder.mkdir(parents=True, exist_ok=False)
            summary_json = folder / "summary.json"
            summary_csv = folder / "summary.csv"
            request_path = folder / "request.json"
            request_path.write_text(
                json.dumps(request.model_dump(mode="json"), indent=2, default=str),
                encoding="utf-8",
            )
            compact_rows = [
                {key: value for key, value in row.items() if key != "challenge"}
                for row in rows
            ]
            pd.DataFrame(compact_rows).to_csv(summary_csv, index=False)
            top_candidates = rows[: request.top_n]
            top_folder = folder / "top_ledgers"
            top_folder.mkdir(exist_ok=True)
            for row in top_candidates:
                ledger = top_ledgers[row["strategy_id"]]
                ledger.to_csv(top_folder / f"{row['strategy_id']}.csv", index=False)
            summary_payload = {
                "experiment_id": experiment_id,
                "request": request.model_dump(mode="json"),
                "variants_generated": len(specs),
                "variants_tested": len(rows),
                "parallel_workers": workers,
                "top_candidates": top_candidates,
            }
            summary_json.write_text(
                json.dumps(summary_payload, indent=2, default=str),
                encoding="utf-8",
            )
            result = HypothesisDiscoveryResult(
                experiment_id=experiment_id,
                dataset_id=request.dataset_id,
                symbol=request.symbol,
                timeframe=request.timeframe,
                variants_generated=len(specs),
                variants_tested=len(rows),
                parallel_workers=workers,
                artifact_folder=str(folder),
                summary_csv=str(summary_csv),
                summary_json=str(summary_json),
                top_candidates=top_candidates,
            ).model_dump(mode="json")
            self.store.finish(experiment_id, result)
            return result
        except Exception as exc:
            self.store.fail(experiment_id, str(exc))
            raise
