"""Bridge to mc_funded_test.py.

Exposes the four phase runners and all 15 advanced-metric functions. Results
are passed through _jsonify so pandas/numpy objects become JSON-safe.
"""

from __future__ import annotations

from typing import Any, Callable

import numpy as np
import pandas as pd

from .. import paths  # ensure MONTE CARLO/src is on sys.path  # noqa: F401

import mc_funded_test as _mc  # type: ignore[import-not-found]


PHASE_RUNNERS: dict[str, Callable[..., dict]] = {
    "phase1": _mc.run_mc_phase1,
    "phase2": _mc.run_mc_phase2,
    "funded": _mc.run_mc_funded,
    "longterm": _mc.run_mc_longterm,
}

ADVANCED_METRICS: dict[str, Callable[..., Any]] = {
    "failure_mode_breakdown": _mc.failure_mode_breakdown,
    "time_to_pass_distribution": _mc.time_to_pass_distribution,
    "lot_size_sweep": _mc.lot_size_sweep,
    "recovery_probability": _mc.recovery_probability,
    "worst_streak_check": _mc.worst_streak_check,
    "conditional_phase2_pass_rate": _mc.conditional_phase2_pass_rate,
    "conservative_mode_simulator": _mc.conservative_mode_simulator,
    "phase2_time_to_pass": _mc.phase2_time_to_pass,
    "time_to_first_payout": _mc.time_to_first_payout,
    "payout_cadence_optimizer": _mc.payout_cadence_optimizer,
    "funded_lifetime": _mc.funded_lifetime,
    "kelly_fraction": _mc.kelly_fraction,
    "risk_of_ruin_horizons": _mc.risk_of_ruin_horizons,
    "multi_strategy_portfolio": _mc.multi_strategy_portfolio,
    "fat_tail_stress": _mc.fat_tail_stress,
}


def run_phase(phase: str, daily_pnl: np.ndarray, params: dict[str, Any]) -> dict[str, Any]:
    if phase not in PHASE_RUNNERS:
        raise ValueError(f"unknown phase '{phase}'; expected one of {list(PHASE_RUNNERS)}")
    fn = PHASE_RUNNERS[phase]
    return _jsonify(fn(daily_pnl, **params))


def run_advanced(metric: str, params: dict[str, Any]) -> Any:
    if metric not in ADVANCED_METRICS:
        raise ValueError(f"unknown metric '{metric}'; expected one of {list(ADVANCED_METRICS)}")
    fn = ADVANCED_METRICS[metric]
    # Pull the PnL arg out of params so advanced callers can pass it by name.
    # Some advanced functions take daily_pnl_list or daily_pnl_per_lot_unit.
    return _jsonify(fn(**params))


def _jsonify(obj: Any) -> Any:
    """Recursively convert numpy/pandas objects to JSON-safe types."""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, pd.DataFrame):
        # Only include a head by default; runners can opt into full df via params.
        return {
            "columns": list(obj.columns),
            "records": obj.to_dict(orient="records"),
        }
    if isinstance(obj, pd.Series):
        return obj.tolist()
    if isinstance(obj, dict):
        return {str(k): _jsonify(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_jsonify(x) for x in obj]
    # Fallback: str() so the HTTP layer never explodes on exotic types.
    return str(obj)
