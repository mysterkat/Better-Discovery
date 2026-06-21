"""Research workflow used identically by HTTP and MCP callers."""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any

from ..bridge import discovery as discovery_bridge
from ..local_replay import LOCAL_REPLAY
from ..local_replay.models import ReplayRequest
from ..local_replay.robustness import RobustnessRequest, run_robustness
from ..market_data import MARKET_DATA
from ..market_data.models import MarketDataImportRequest
from ..paths import DEFAULT_DISC_OUTPUT, DEFAULT_RESEARCH
from ..schemas.mc import MCCompareRequest
from .comparison import compare_sources
from .models import (
    BacktestSpec,
    GateResult,
    MT5Environment,
    PromotionPolicy,
    ReportMetrics,
    StrategySpec,
)
from .mt5 import MT5Worker, bootstrap_portable_mt5
from .report import parse_mt5_report
from .store import ExperimentStore


_DISCOVERY_LOCK = threading.Lock()


def evaluate(metrics: ReportMetrics, policy: PromotionPolicy) -> GateResult:
    checks = {
        f"trades >= {policy.min_trades}": (metrics.total_trades or 0) >= policy.min_trades,
        f"profit factor >= {policy.min_profit_factor}":
            (metrics.profit_factor or 0) >= policy.min_profit_factor,
        f"expected payoff > {policy.min_expected_payoff}":
            (metrics.expected_payoff or 0) > policy.min_expected_payoff,
        f"net profit > {policy.min_net_profit}":
            (metrics.net_profit or 0) > policy.min_net_profit,
        f"equity drawdown <= {policy.max_equity_drawdown_pct}%":
            metrics.maximal_equity_drawdown_pct is not None
            and metrics.maximal_equity_drawdown_pct <= policy.max_equity_drawdown_pct,
    }
    passed = [name for name, ok in checks.items() if ok]
    failed = [name for name, ok in checks.items() if not ok]
    return GateResult(decision="promote" if not failed else "reject", passed=passed, failed=failed)


class ResearchService:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.store = ExperimentStore(db_path)

    def status(self, environment: MT5Environment | None = None) -> dict[str, Any]:
        return {
            "database": str(self.store.path),
            "research_root": str(DEFAULT_RESEARCH),
            "mt5": MT5Worker(environment).status(),
            "guardrails": {
                "live_order_placement": False,
                "automatic_live_deployment": False,
                "report_artifacts_are_immutable": True,
            },
        }

    def setup_portable_mt5(self, destination: str | None = None) -> dict[str, Any]:
        return bootstrap_portable_mt5(destination=destination)

    def import_market_data(self, request: MarketDataImportRequest) -> dict[str, Any]:
        experiment_id = self.store.create("market_data_import", request.model_dump(mode="json"))
        try:
            result = MARKET_DATA.import_data(request)
            self.store.finish(experiment_id, result)
            return {"experiment_id": experiment_id, **result}
        except Exception as exc:
            self.store.fail(experiment_id, str(exc))
            raise

    def run_local_replay(self, request: ReplayRequest) -> dict[str, Any]:
        strategy = StrategySpec.from_set(request.set_path)
        if request.dataset_role == "lockbox" and self.store.has_completed_lockbox(
            strategy.fingerprint, kind="local_replay"
        ):
            raise ValueError("lockbox already consumed for this strategy fingerprint")
        experiment_id = self.store.create(
            "local_replay", request.model_dump(mode="json"),
            strategy_fingerprint=strategy.fingerprint, dataset_role=request.dataset_role,
        )
        try:
            result = LOCAL_REPLAY.run(request)
            self.store.finish(experiment_id, result)
            return {"experiment_id": experiment_id, **result}
        except Exception as exc:
            self.store.fail(experiment_id, str(exc))
            raise

    def compare_monte_carlo(self, request: MCCompareRequest) -> dict[str, Any]:
        experiment_id = self.store.create("mc_source_comparison", request.model_dump(mode="json"))
        try:
            result = compare_sources(request)
            self.store.finish(experiment_id, result)
            return {"experiment_id": experiment_id, **result}
        except Exception as exc:
            self.store.fail(experiment_id, str(exc))
            raise

    def run_local_robustness(self, request: RobustnessRequest) -> dict[str, Any]:
        experiment_id = self.store.create("local_robustness", request.model_dump(mode="json"))
        try:
            result = run_robustness(request)
            artifact = DEFAULT_RESEARCH / "local_replay" / f"robustness_{experiment_id}.json"
            artifact.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
            payload = {"artifact": str(artifact), **result}
            self.store.finish(experiment_id, payload)
            return {"experiment_id": experiment_id, **payload}
        except Exception as exc:
            self.store.fail(experiment_id, str(exc))
            raise

    def run_discovery(self, overrides: dict[str, Any]) -> dict[str, Any]:
        experiment_id = self.store.create("discovery", {"overrides": overrides})
        try:
            with _DISCOVERY_LOCK:
                result = discovery_bridge.run_discovery(overrides)
            candidates = []
            for pattern in result.get("patterns", []):
                set_path = pattern.get("set_file")
                if not set_path:
                    continue
                strategy = StrategySpec.from_set(set_path)
                candidates.append(
                    {
                        "name": strategy.name,
                        "set_path": set_path,
                        "fingerprint": strategy.fingerprint,
                        "meta": strategy.discovery_meta,
                        "discovery_metrics": pattern,
                    }
                )
            payload = {"discovery": result, "candidates": candidates}
            self.store.finish(experiment_id, payload)
            return {"experiment_id": experiment_id, **payload}
        except Exception as exc:
            self.store.fail(experiment_id, str(exc))
            raise

    @staticmethod
    def list_candidates(root: str | None = None) -> list[dict[str, Any]]:
        base = Path(root or DEFAULT_DISC_OUTPUT).resolve()
        if not base.is_dir():
            return []
        candidates = []
        for path in sorted(base.rglob("*.set"), key=lambda p: p.stat().st_mtime, reverse=True):
            strategy = StrategySpec.from_set(path)
            candidates.append(
                {
                    "name": strategy.name,
                    "set_path": str(path),
                    "fingerprint": strategy.fingerprint,
                    "meta": strategy.discovery_meta,
                    "modified": path.stat().st_mtime,
                }
            )
        return candidates

    def import_strategy(self, set_path: str) -> dict[str, Any]:
        strategy = StrategySpec.from_set(set_path)
        folder = DEFAULT_RESEARCH / "artifacts" / strategy.fingerprint
        folder.mkdir(parents=True, exist_ok=True)
        spec_path = folder / "strategy.json"
        if not spec_path.exists():
            spec_path.write_text(strategy.model_dump_json(indent=2), encoding="utf-8")
        return {"strategy": strategy.model_dump(), "fingerprint": strategy.fingerprint,
                "spec_path": str(spec_path)}

    def create_variant(
        self,
        set_path: str,
        parameter_overrides: dict[str, str | int | float | bool],
        hypothesis: str,
    ) -> dict[str, Any]:
        if len(hypothesis.strip()) < 20:
            raise ValueError("variant hypothesis must be at least 20 characters")
        parent = StrategySpec.from_set(set_path)
        if self.store.has_completed_lockbox(parent.fingerprint):
            raise ValueError(
                "this exact strategy has seen the lockbox and is frozen; start a new "
                "research lineage without reusing that lockbox result"
            )
        unknown = sorted(set(parameter_overrides) - set(parent.parameters))
        if unknown:
            raise ValueError(f"variant may only change existing parameters: {unknown}")
        source = Path(set_path).read_text(encoding="utf-8", errors="replace")
        for key, value in parameter_overrides.items():
            rendered = str(value).lower() if isinstance(value, bool) else str(value)
            source, count = re.subn(
                rf"(?m)^{re.escape(key)}\s*=.*$", f"{key}={rendered}", source, count=1
            )
            if count != 1:
                raise ValueError(f"could not update parameter {key}")
        variant_seed = json.dumps(parameter_overrides, sort_keys=True, default=str)
        suffix = __import__("hashlib").sha256(variant_seed.encode()).hexdigest()[:10]
        folder = DEFAULT_RESEARCH / "artifacts" / "variants" / parent.fingerprint
        folder.mkdir(parents=True, exist_ok=True)
        variant_path = folder / f"{parent.name}_v_{suffix}.set"
        variant_path.write_text(source, encoding="utf-8")
        variant = StrategySpec.from_set(variant_path)
        lineage = {
            "parent_fingerprint": parent.fingerprint,
            "variant_fingerprint": variant.fingerprint,
            "hypothesis": hypothesis.strip(),
            "changes": parameter_overrides,
        }
        variant_path.with_suffix(".lineage.json").write_text(
            json.dumps(lineage, indent=2, default=str), encoding="utf-8"
        )
        return {"set_path": str(variant_path), **lineage}

    def parse_report(
        self,
        report_path: str,
        policy: PromotionPolicy | None = None,
    ) -> dict[str, Any]:
        metrics = parse_mt5_report(report_path)
        gate = evaluate(metrics, policy or PromotionPolicy())
        return {"metrics": metrics.model_dump(), "gate": gate.model_dump()}

    def run_pipeline(
        self,
        set_path: str,
        backtest: BacktestSpec,
        environment: MT5Environment | None = None,
        policy: PromotionPolicy | None = None,
    ) -> dict[str, Any]:
        strategy = StrategySpec.from_set(set_path)
        if (
            backtest.dataset_role == "lockbox"
            and self.store.has_completed_lockbox(strategy.fingerprint, kind="mt5_backtest")
        ):
            raise ValueError("lockbox already consumed for this strategy fingerprint")
        request = {
            "strategy": strategy.model_dump(),
            "backtest": backtest.model_dump(mode="json"),
            "environment": environment.model_dump() if environment else None,
            "policy": (policy or PromotionPolicy()).model_dump(),
        }
        experiment_id = self.store.create(
            "mt5_backtest",
            request,
            strategy_fingerprint=strategy.fingerprint,
            dataset_role=backtest.dataset_role,
        )
        try:
            if (
                strategy.discovery_meta.get("box_superset_warning")
                and not strategy.discovery_meta.get("ea_faithful_oos")
            ):
                raise RuntimeError(
                    "legacy discovery candidate has cluster/EA-box inflation and no "
                    "EA-faithful OOS metrics; rerun discovery with the current fidelity gates"
                )
            worker = MT5Worker(environment)
            generated = worker.generate_ea(strategy)
            compile_result = worker.compile(generated["installed_source"])
            if not compile_result["success"]:
                raise RuntimeError(f"EA compilation failed: {compile_result['log_tail']}")
            run = worker.backtest(strategy, backtest)
            assessment = self.parse_report(run["report_path"], policy)
            result = {
                "strategy_fingerprint": strategy.fingerprint,
                "generated": generated,
                "compile": compile_result,
                "backtest": run,
                **assessment,
            }
            self.store.finish(experiment_id, result)
            return {"experiment_id": experiment_id, **result}
        except Exception as exc:
            self.store.fail(experiment_id, str(exc))
            raise


RESEARCH = ResearchService()
