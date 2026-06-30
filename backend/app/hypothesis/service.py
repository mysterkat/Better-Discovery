from __future__ import annotations

import json
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any

import pandas as pd

from ..jobs.runners import CancelledError, check_cancelled, get_current_job
from ..market_data.catalog import MarketDataCatalog
from ..paths import DEFAULT_RESEARCH
from ..research.store import ExperimentStore
from .bar_engine import run_bar_replay, run_bar_replay_fast_metrics
from .challenge import evaluate_challenge_grid
from .grammar import generate_hypotheses, mutate_hypothesis
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

    @staticmethod
    def _grammar_timeframes(strategy: HypothesisSpec) -> tuple[str, ...]:
        if strategy.lineage != "strategy_grammar":
            return ()
        values = [
            str(block.get("timeframe", strategy.timeframe)).lower()
            for block in list(strategy.parameters.get("rule_blocks") or [])
            if isinstance(block, dict)
        ]
        return tuple(dict.fromkeys(value for value in values if value in {"m1", "m5", "m10", "m15"}))

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
        grammar_bars = {
            timeframe: self._bars(manifest, symbol, timeframe, warmup_from, request.date_to)
            for timeframe in self._grammar_timeframes(request.strategy)
            if timeframe != request.strategy.timeframe
        }
        signals = build_signal_frame(warm_bars, contexts, request.strategy, grammar_bars)
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
        min_required_trades: int | None = None,
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
                "min_trades": min_required_trades or request.min_closed_trades,
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

    @staticmethod
    def _quality_gate(row: dict[str, Any], request: HypothesisDiscoveryRequest, *, stage: str) -> bool:
        profit_factor = row.get("profit_factor")
        if row.get("net_profit", 0.0) <= 0:
            return False
        if profit_factor is None:
            return False
        min_pf = request.parent_min_profit_factor if stage == "parent" else request.final_min_profit_factor
        if float(profit_factor) < min_pf:
            return False
        if float(row.get("expected_payoff") or 0.0) <= 0:
            return False
        if float(row.get("max_drawdown_pct") or 0.0) > request.max_candidate_drawdown_pct:
            return False
        if stage == "final" and float(row.get("challenge_active_pass_rate") or 0.0) < request.final_min_active_pass_rate:
            return False
        return True

    @staticmethod
    def _parent_rank(row: dict[str, Any]) -> tuple[float, float, float, float]:
        return (
            float(row.get("profit_factor") or 0.0),
            float(row.get("challenge_active_pass_rate") or 0.0),
            float(row.get("net_profit") or 0.0),
            -float(row.get("max_drawdown_pct") or 0.0),
        )

    @staticmethod
    def _minimum_required_trades(request: HypothesisDiscoveryRequest, bars: pd.DataFrame) -> int:
        if request.min_trades_per_week > 0 and not bars.empty:
            times = pd.to_datetime(bars["time"], utc=True)
            trading_days = max(1, int(times.dt.normalize().nunique()))
            return max(1, int(round((trading_days / 5.0) * request.min_trades_per_week)))
        return request.min_closed_trades

    def _evaluate_candidate(
        self,
        request: HypothesisDiscoveryRequest,
        strategy: HypothesisSpec,
        bars: pd.DataFrame,
        base_frame: pd.DataFrame,
        grammar_frames: dict[str, pd.DataFrame] | None,
        min_required_trades: int,
    ) -> tuple[dict[str, Any], pd.DataFrame] | None:
        replay_request = self._replay_request(request, strategy, min_required_trades)
        strategy_grammar_frames = None
        if strategy.lineage == "strategy_grammar" and grammar_frames:
            strategy_grammar_frames = {
                timeframe: frame
                for timeframe, frame in grammar_frames.items()
                if any(
                    isinstance(block, dict)
                    and str(block.get("timeframe", strategy.timeframe)).lower() == timeframe
                    for block in list(strategy.parameters.get("rule_blocks") or [])
                )
            }
        signals = apply_signal_rules(base_frame, strategy, strategy_grammar_frames)
        signals = signals.loc[
            (signals.index >= pd.Timestamp(request.date_from))
            & (signals.index < pd.Timestamp(request.date_to))
        ]
        signal_count = int((signals["signal_direction"] != 0).sum())
        if signal_count < min_required_trades:
            return None

        fast_metrics = run_bar_replay_fast_metrics(bars, signals, replay_request)
        if fast_metrics["trades"] < min_required_trades:
            return None
        profit_factor = fast_metrics["profit_factor"]
        expected_payoff = fast_metrics["expected_payoff"]
        fast_min_pf = max(1.0, min(request.parent_min_profit_factor, request.final_min_profit_factor) - 0.10)
        if profit_factor is None or profit_factor < fast_min_pf or expected_payoff is None or expected_payoff <= 0:
            return None

        ledger, metrics = run_bar_replay(bars, signals, replay_request)
        if metrics["trades"] < min_required_trades:
            return None
        if metrics["profit_factor"] is None or metrics["profit_factor"] < fast_min_pf or metrics["expected_payoff"] is None or metrics["expected_payoff"] <= 0:
            return None
        challenge = evaluate_challenge_grid(ledger, request.challenge)
        return self._candidate_row(strategy, metrics, challenge), ledger

    def _evaluate_specs(
        self,
        request: HypothesisDiscoveryRequest,
        specs: list[HypothesisSpec],
        bars: pd.DataFrame,
        base_frame: pd.DataFrame,
        grammar_frames: dict[str, pd.DataFrame] | None,
        min_required_trades: int,
        *,
        label: str,
        completed_offset: int,
        total_budget: int,
        current_job: Any,
        started_at: float,
        workers: int,
        generation_index: int | None = None,
        generation_total: int | None = None,
        generation_phase: str | None = None,
        checkpoint: Any | None = None,
    ) -> tuple[list[tuple[dict[str, Any], pd.DataFrame]], int]:
        accepted: list[tuple[dict[str, Any], pd.DataFrame]] = []

        def update(completed: int) -> None:
            if current_job is None:
                return
            total_completed = completed_offset + completed
            current_job.mark_stage(label, min(total_completed, total_budget), total_budget)
            elapsed = max(0.001, time.time() - started_at)
            rate = max(1, total_completed) / elapsed
            remaining = max(0, total_budget - total_completed)
            current_job.eta_seconds = remaining / rate if rate > 0 else None
            current_job.meta["hypothesis_progress"] = {
                "completed_variants": total_completed,
                "total_variants": total_budget,
                "accepted_variants": len(accepted),
                "variants_per_hour": rate * 3600.0,
                "eta_seconds": current_job.eta_seconds,
                "stage": label,
                "generation_index": generation_index,
                "generation_total": generation_total,
                "generation_phase": generation_phase,
            }

        if not specs:
            return accepted, 0

        if workers <= 1:
            for index, strategy in enumerate(specs, start=1):
                check_cancelled()
                result = self._evaluate_candidate(request, strategy, bars, base_frame, grammar_frames, min_required_trades)
                if result is not None:
                    accepted.append(result)
                    if checkpoint is not None:
                        checkpoint(result, completed_offset + index, generation_index)
                update(index)
            return accepted, len(specs)

        executor = ThreadPoolExecutor(max_workers=workers)
        pending_specs = iter(specs)
        futures = {}
        cancel_futures = False
        completed = 0

        def submit_next() -> bool:
            try:
                strategy = next(pending_specs)
            except StopIteration:
                return False
            future = executor.submit(
                self._evaluate_candidate,
                request,
                strategy,
                bars,
                base_frame,
                grammar_frames,
                min_required_trades,
            )
            futures[future] = strategy
            return True

        try:
            for _ in range(min(workers, len(specs))):
                submit_next()
            while futures:
                check_cancelled()
                done, _pending = wait(futures, timeout=0.5, return_when=FIRST_COMPLETED)
                if not done:
                    continue
                for future in done:
                    futures.pop(future, None)
                    completed += 1
                    result = future.result()
                    if result is not None:
                        accepted.append(result)
                        if checkpoint is not None:
                            checkpoint(result, completed_offset + completed, generation_index)
                    update(completed)
                    submit_next()
        except Exception:
            cancel_futures = True
            for future in futures:
                future.cancel()
            raise
        finally:
            executor.shutdown(wait=not cancel_futures, cancel_futures=cancel_futures)
        return accepted, len(specs)

    def run_discovery(self, request: HypothesisDiscoveryRequest) -> dict[str, Any]:
        manifest = self.catalog.load(request.dataset_id)
        if manifest.state != "complete":
            raise ValueError(f"dataset {request.dataset_id} is not complete")
        experiment_id = self.store.create(
            "hypothesis_discovery",
            request.model_dump(mode="json"),
            dataset_role="development",
        )
        folder = self.output / "hypothesis_discovery" / experiment_id
        folder.mkdir(parents=True, exist_ok=False)
        summary_json = folder / "summary.json"
        summary_csv = folder / "summary.csv"
        request_path = folder / "request.json"
        live_json = folder / "live_candidates.json"
        live_csv = folder / "live_candidates.csv"
        checkpoint_json = folder / "checkpoint.json"
        request_path.write_text(
            json.dumps(request.model_dump(mode="json"), indent=2, default=str),
            encoding="utf-8",
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
            grammar_timeframes = tuple(
                dict.fromkeys(
                    timeframe
                    for strategy in specs
                    for timeframe in self._grammar_timeframes(strategy)
                )
            )
            grammar_frames: dict[str, pd.DataFrame] = {request.timeframe: base_frame}
            if grammar_timeframes:
                from .signals import align_signal_timeframe

                for grammar_timeframe in grammar_timeframes:
                    if grammar_timeframe == request.timeframe:
                        continue
                    warm_tf_bars = self._bars(
                        manifest,
                        request.symbol,
                        grammar_timeframe,
                        warmup_from,
                        request.date_to,
                    )
                    if warm_tf_bars.empty:
                        raise ValueError(f"missing grammar timeframe bars: {grammar_timeframe.upper()}")
                    signal_frame = build_base_frame(warm_tf_bars, contexts)
                    grammar_frames[grammar_timeframe] = align_signal_timeframe(
                        base_frame,
                        signal_frame,
                        grammar_timeframe,
                        request.timeframe,
                    )
            min_required_trades = self._minimum_required_trades(request, bars)
            rows: list[dict[str, Any]] = []
            top_ledgers: dict[str, pd.DataFrame] = {}
            current_job = get_current_job()
            variant_started_at = time.time()

            workers = min(request.parallel_workers, len(specs) or 1)
            evaluated_count = 0
            search_summary: dict[str, Any] = {
                "mode": request.search_mode,
                "generations": [],
                "parent_min_profit_factor": request.parent_min_profit_factor,
                "final_min_profit_factor": request.final_min_profit_factor,
                "final_min_active_pass_rate": request.final_min_active_pass_rate,
            }

            def compact_row(row: dict[str, Any]) -> dict[str, Any]:
                return {key: value for key, value in row.items() if key != "challenge"}

            def write_checkpoint(evaluated: int, generation_index: int | None = None) -> None:
                compact_rows = [compact_row(row) for row in rows]
                live_json.write_text(
                    json.dumps(
                        {
                            "experiment_id": experiment_id,
                            "status": "running",
                            "variants_generated": len(specs),
                            "variants_evaluated": evaluated,
                            "candidates_saved": len(rows),
                            "generation_index": generation_index,
                            "search_summary": search_summary,
                            "top_candidates": rows[: request.top_n],
                        },
                        indent=2,
                        default=str,
                    ),
                    encoding="utf-8",
                )
                pd.DataFrame(compact_rows).to_csv(live_csv, index=False)
                checkpoint_json.write_text(
                    json.dumps(
                        {
                            "experiment_id": experiment_id,
                            "job_status": "running",
                            "variants_evaluated": evaluated,
                            "generation_index": generation_index,
                            "candidate_count": len(rows),
                            "updated_at": time.time(),
                        },
                        indent=2,
                        default=str,
                    ),
                    encoding="utf-8",
                )

            def add_results(results: list[tuple[dict[str, Any], pd.DataFrame]]) -> None:
                for row, ledger in results:
                    if row["strategy_id"] in top_ledgers:
                        continue
                    rows.append(row)
                    top_ledgers[row["strategy_id"]] = ledger
                rows.sort(
                    key=lambda item: (
                        item["challenge_score"],
                        item["challenge_pass_count"],
                        -(item["median_days_to_target"] or 999.0),
                        item["profit_factor"] or 0.0,
                    ),
                    reverse=True,
                )

            def live_checkpoint_result(
                result: tuple[dict[str, Any], pd.DataFrame],
                evaluated: int,
                generation_index: int | None,
            ) -> None:
                row, _ledger = result
                if self._quality_gate(row, request, stage="final"):
                    add_results([result])
                    write_checkpoint(evaluated, generation_index)

            if request.search_mode == "guided" and specs:
                initial_count = min(
                    len(specs),
                    max(
                        request.guided_parents_kept,
                        int(round(request.max_variants * request.guided_initial_fraction)),
                    ),
                )
                generation_specs = specs[:initial_count]
                results, used = self._evaluate_specs(
                    request,
                    generation_specs,
                    bars,
                    base_frame,
                    grammar_frames,
                    min_required_trades,
                    label=f"Guided generation 0/{request.guided_generations}: seed scan ({workers} workers)",
                    completed_offset=evaluated_count,
                    total_budget=request.max_variants,
                    current_job=current_job,
                    started_at=variant_started_at,
                    workers=workers,
                    generation_index=0,
                    generation_total=request.guided_generations,
                    generation_phase="seed scan",
                    checkpoint=live_checkpoint_result,
                )
                evaluated_count += used
                add_results([result for result in results if self._quality_gate(result[0], request, stage="final")])
                parents = [
                    result for result in results
                    if self._quality_gate(result[0], request, stage="parent")
                ]
                parents.sort(key=lambda result: self._parent_rank(result[0]), reverse=True)
                parents = parents[:request.guided_parents_kept]
                search_summary["generations"].append({
                    "generation": 0,
                    "evaluated": used,
                    "accepted": len(results),
                    "parents": len(parents),
                    "finalists": len(rows),
                })
                write_checkpoint(evaluated_count, 0)

                seen_ids = {strategy.strategy_id for strategy in generation_specs}
                remaining_specs = specs[initial_count:]
                for generation in range(1, request.guided_generations + 1):
                    if evaluated_count >= request.max_variants or not parents:
                        break
                    budget_left = request.max_variants - evaluated_count
                    explore_count = min(
                        len(remaining_specs),
                        int(round(budget_left * request.guided_exploration_pct)),
                    )
                    child_budget = budget_left - explore_count
                    children: list[HypothesisSpec] = []
                    for parent_index, (parent_row, _ledger) in enumerate(parents):
                        if len(children) >= child_budget:
                            break
                        parent_strategy = next(
                            (strategy for strategy in specs if strategy.strategy_id == parent_row["strategy_id"]),
                            None,
                        )
                        if parent_strategy is None:
                            parent_strategy = HypothesisSpec(
                                strategy_id=parent_row["strategy_id"],
                                lineage=parent_row["lineage"],
                                hypothesis=parent_row["hypothesis"],
                                timeframe=request.timeframe,
                                parameters=parent_row["parameters"],
                            )
                        for child_index in range(request.guided_children_per_parent):
                            if len(children) >= child_budget:
                                break
                            child = mutate_hypothesis(
                                parent_strategy,
                                child_index=parent_index * request.guided_children_per_parent + child_index,
                                generation=generation,
                            )
                            if child.strategy_id in seen_ids:
                                continue
                            seen_ids.add(child.strategy_id)
                            children.append(child)
                    exploration = remaining_specs[:explore_count]
                    remaining_specs = remaining_specs[explore_count:]
                    generation_specs = [*children, *exploration]
                    if not generation_specs:
                        break
                    results, used = self._evaluate_specs(
                        request,
                        generation_specs,
                        bars,
                        base_frame,
                        grammar_frames,
                        min_required_trades,
                        label=f"Guided generation {generation}/{request.guided_generations}: mutation + exploration ({workers} workers)",
                        completed_offset=evaluated_count,
                        total_budget=request.max_variants,
                        current_job=current_job,
                        started_at=variant_started_at,
                        workers=workers,
                        generation_index=generation,
                        generation_total=request.guided_generations,
                        generation_phase="mutation + exploration",
                        checkpoint=live_checkpoint_result,
                    )
                    evaluated_count += used
                    add_results([result for result in results if self._quality_gate(result[0], request, stage="final")])
                    next_parents = [
                        result for result in [*parents, *results]
                        if self._quality_gate(result[0], request, stage="parent")
                    ]
                    next_parents.sort(key=lambda result: self._parent_rank(result[0]), reverse=True)
                    parents = next_parents[:request.guided_parents_kept]
                    search_summary["generations"].append({
                        "generation": generation,
                        "evaluated": used,
                        "accepted": len(results),
                        "children": len(children),
                        "exploration": len(exploration),
                        "parents": len(parents),
                        "finalists": len(rows),
                    })
                    write_checkpoint(evaluated_count, generation)
            else:
                results, used = self._evaluate_specs(
                    request,
                    specs,
                    bars,
                    base_frame,
                    grammar_frames,
                    min_required_trades,
                    label=f"Fixed family scan ({workers} workers)",
                    completed_offset=0,
                    total_budget=len(specs),
                    current_job=current_job,
                    started_at=variant_started_at,
                    workers=workers,
                    generation_index=0,
                    generation_total=0,
                    generation_phase="fixed family scan",
                    checkpoint=live_checkpoint_result,
                )
                evaluated_count = used
                add_results([result for result in results if self._quality_gate(result[0], request, stage="final")])
                search_summary["generations"].append({
                    "generation": 0,
                    "evaluated": used,
                    "accepted": len(results),
                    "finalists": len(rows),
                })
                write_checkpoint(evaluated_count, 0)

            rows.sort(
                key=lambda item: (
                    item["challenge_score"],
                    item["challenge_pass_count"],
                    -(item["median_days_to_target"] or 999.0),
                    item["profit_factor"] or 0.0,
                ),
                reverse=True,
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
                "variants_evaluated": evaluated_count,
                "variants_tested": len(rows),
                "parallel_workers": workers,
                "min_required_trades": min_required_trades,
                "search_summary": search_summary,
                "top_candidates": top_candidates,
            }
            summary_json.write_text(
                json.dumps(summary_payload, indent=2, default=str),
                encoding="utf-8",
            )
            checkpoint_json.write_text(
                json.dumps(
                    {
                        "experiment_id": experiment_id,
                        "job_status": "done",
                        "variants_evaluated": evaluated_count,
                        "candidate_count": len(rows),
                        "updated_at": time.time(),
                    },
                    indent=2,
                    default=str,
                ),
                encoding="utf-8",
            )
            result = HypothesisDiscoveryResult(
                experiment_id=experiment_id,
                dataset_id=request.dataset_id,
                symbol=request.symbol,
                timeframe=request.timeframe,
                variants_generated=len(specs),
                variants_tested=len(rows),
                variants_evaluated=evaluated_count,
                search_summary=search_summary,
                parallel_workers=workers,
                artifact_folder=str(folder),
                summary_csv=str(summary_csv),
                summary_json=str(summary_json),
                top_candidates=top_candidates,
            ).model_dump(mode="json")
            self.store.finish(experiment_id, result)
            return result
        except CancelledError:
            checkpoint_json.write_text(
                json.dumps(
                    {
                        "experiment_id": experiment_id,
                        "job_status": "cancelled",
                        "variants_evaluated": locals().get("evaluated_count", 0),
                        "candidate_count": len(locals().get("rows", [])),
                        "updated_at": time.time(),
                        "note": "Partial live candidates, if any, are in live_candidates.json and live_candidates.csv.",
                    },
                    indent=2,
                    default=str,
                ),
                encoding="utf-8",
            )
            raise
        except Exception as exc:
            self.store.fail(experiment_id, str(exc))
            raise
