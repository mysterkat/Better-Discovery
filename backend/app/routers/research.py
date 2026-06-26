"""HTTP access to the deterministic research orchestrator."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..hypothesis.models import HypothesisDiscoveryRequest
from ..hypothesis.service import HypothesisResearchService
from ..research.models import BacktestSpec, MT5Environment, PromotionPolicy
from ..research.service import RESEARCH
from ..local_replay.models import ReplayRequest
from ..local_replay.robustness import RobustnessRequest
from ..jobs.manager import JOBS
from ..jobs.runners import run_in_thread
from ..schemas.common import JobRef

router = APIRouter(prefix="/research")
HYPOTHESIS = HypothesisResearchService()


@router.post("/local-replay", response_model=JobRef)
def local_replay(req: ReplayRequest) -> JobRef:
    job = JOBS.create(
        kind="local_replay",
        meta={"dataset_id": req.dataset_id, "symbol": req.symbol, "timeframe": req.timeframe},
    )
    run_in_thread(job, lambda: RESEARCH.run_local_replay(req))
    return JobRef(job_id=job.job_id, status=job.status)


@router.post("/local-robustness", response_model=JobRef)
def local_robustness(req: RobustnessRequest) -> JobRef:
    job = JOBS.create(kind="local_robustness", meta={"ledger": req.ledger_path})
    run_in_thread(job, lambda: RESEARCH.run_local_robustness(req))
    return JobRef(job_id=job.job_id, status=job.status)


@router.post("/hypothesis-discovery", response_model=JobRef)
def hypothesis_discovery(req: HypothesisDiscoveryRequest) -> JobRef:
    job = JOBS.create(
        kind="hypothesis_discovery",
        meta={
            "dataset_id": req.dataset_id,
            "symbol": req.symbol,
            "timeframe": req.timeframe,
            "max_variants": req.max_variants,
            "target_profit_pct": req.challenge.target_profit_pct,
            "max_attempt_days": req.challenge.max_attempt_days,
        },
    )
    run_in_thread(job, lambda: HYPOTHESIS.run_discovery(req))
    return JobRef(job_id=job.job_id, status=job.status)


class PathRequest(BaseModel):
    path: str


class PortableSetupRequest(BaseModel):
    destination: str | None = None


class DiscoveryRequest(BaseModel):
    overrides: dict[str, Any] = Field(default_factory=dict)


class VariantRequest(BaseModel):
    set_path: str
    parameter_overrides: dict[str, str | int | float | bool]
    hypothesis: str


class ParseReportRequest(BaseModel):
    report_path: str
    policy: PromotionPolicy = Field(default_factory=PromotionPolicy)


class PipelineRequest(BaseModel):
    set_path: str
    backtest: BacktestSpec
    environment: MT5Environment | None = None
    policy: PromotionPolicy = Field(default_factory=PromotionPolicy)


def _run(call):
    try:
        return call()
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    except (RuntimeError, TimeoutError) as exc:
        raise HTTPException(502, str(exc)) from exc


@router.get("/status")
def status() -> dict:
    return _run(RESEARCH.status)


@router.post("/mt5/setup-portable")
def setup_portable_mt5(req: PortableSetupRequest) -> dict:
    return _run(lambda: RESEARCH.setup_portable_mt5(req.destination))


@router.post("/discovery")
def run_discovery(req: DiscoveryRequest) -> dict:
    return _run(lambda: RESEARCH.run_discovery(req.overrides))


@router.get("/candidates")
def candidates(root: str | None = None) -> list[dict]:
    return _run(lambda: RESEARCH.list_candidates(root))


@router.post("/strategies/import")
def import_strategy(req: PathRequest) -> dict:
    return _run(lambda: RESEARCH.import_strategy(req.path))


@router.post("/strategies/variant")
def create_variant(req: VariantRequest) -> dict:
    return _run(
        lambda: RESEARCH.create_variant(
            req.set_path, req.parameter_overrides, req.hypothesis
        )
    )


@router.post("/reports/parse")
def parse_report(req: ParseReportRequest) -> dict:
    return _run(lambda: RESEARCH.parse_report(req.report_path, req.policy))


@router.post("/pipeline")
def run_pipeline(req: PipelineRequest) -> dict:
    return _run(
        lambda: RESEARCH.run_pipeline(
            req.set_path, req.backtest, req.environment, req.policy
        )
    )


@router.get("/experiments")
def list_experiments(limit: int = 50) -> list[dict]:
    return RESEARCH.store.list(limit)


@router.get("/experiments/{experiment_id}")
def get_experiment(experiment_id: str) -> dict:
    result = RESEARCH.store.get(experiment_id)
    if result is None:
        raise HTTPException(404, f"unknown experiment: {experiment_id}")
    return result
