"""HTTP access to the deterministic research orchestrator."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..hypothesis.models import HypothesisBarRequest, HypothesisDiscoveryRequest, HypothesisSpec
from ..hypothesis.service import HypothesisResearchService
from ..paths import DEFAULT_LIBRARY
from ..research.service import RESEARCH
from ..local_replay.robustness import RobustnessRequest
from ..jobs.manager import JOBS
from ..jobs.runners import run_in_thread
from ..schemas.common import JobRef

router = APIRouter(prefix="/research")
HYPOTHESIS = HypothesisResearchService()


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


class PortableSetupRequest(BaseModel):
    destination: str | None = None


class SavedStrategyReplayRequest(BaseModel):
    dataset_id: str
    pattern_id: str
    date_from: datetime
    date_to: datetime
    dataset_role: Literal["validation", "walk_forward", "lockbox"] = "validation"
    initial_balance: float = Field(default=10_000.0, gt=0)
    lot_size: float = Field(default=0.1, gt=0)
    contract_size: float = Field(default=100.0, gt=0)
    commission_per_lot_round_turn: float = Field(default=7.0, ge=0)
    slippage_price_units: float = Field(default=0.10, ge=0)


def _run(call):
    try:
        return call()
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    except (RuntimeError, TimeoutError) as exc:
        raise HTTPException(502, str(exc)) from exc


def _load_saved_hypothesis(pattern_id: str) -> tuple[HypothesisSpec, dict[str, Any]]:
    folder = (DEFAULT_LIBRARY / pattern_id).resolve()
    try:
        folder.relative_to(DEFAULT_LIBRARY.resolve())
    except ValueError as exc:
        raise HTTPException(400, "invalid library pattern id") from exc
    metadata_path = folder / "metadata.json"
    if not metadata_path.is_file():
        raise HTTPException(404, f"saved strategy not found: {pattern_id}")
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(500, f"saved strategy metadata unreadable: {pattern_id}") from exc
    raw_strategy = metadata.get("hypothesis_strategy")
    if not isinstance(raw_strategy, dict):
        raise HTTPException(422, "saved strategy does not contain hypothesis strategy JSON")
    try:
        return HypothesisSpec.model_validate(raw_strategy), metadata
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/status")
def status() -> dict:
    return _run(RESEARCH.status)


@router.post("/saved-strategy-replay", response_model=JobRef)
def saved_strategy_replay(req: SavedStrategyReplayRequest) -> JobRef:
    strategy, metadata = _load_saved_hypothesis(req.pattern_id)
    request = HypothesisBarRequest(
        dataset_id=req.dataset_id,
        strategy=strategy,
        date_from=req.date_from,
        date_to=req.date_to,
        dataset_role=req.dataset_role,
        initial_balance=req.initial_balance,
        lot_size=req.lot_size,
        contract_size=req.contract_size,
        commission_per_lot_round_turn=req.commission_per_lot_round_turn,
        slippage_price_units=req.slippage_price_units,
    )
    job = JOBS.create(
        kind="saved_strategy_replay",
        meta={
            "dataset_id": req.dataset_id,
            "pattern_id": req.pattern_id,
            "strategy_id": strategy.strategy_id,
            "timeframe": strategy.timeframe,
        },
    )

    def run() -> dict[str, Any]:
        result = HYPOTHESIS.run(request)
        result["pattern_id"] = req.pattern_id
        result["strategy_id"] = strategy.strategy_id
        result["library_name"] = metadata.get("name") or req.pattern_id
        return result

    run_in_thread(job, run)
    return JobRef(job_id=job.job_id, status=job.status)


@router.post("/mt5/setup-portable")
def setup_portable_mt5(req: PortableSetupRequest) -> dict:
    return _run(lambda: RESEARCH.setup_portable_mt5(req.destination))


@router.get("/experiments")
def list_experiments(limit: int = 50) -> list[dict]:
    return RESEARCH.store.list(limit)


@router.get("/experiments/{experiment_id}")
def get_experiment(experiment_id: str) -> dict:
    result = RESEARCH.store.get(experiment_id)
    if result is None:
        raise HTTPException(404, f"unknown experiment: {experiment_id}")
    return result
