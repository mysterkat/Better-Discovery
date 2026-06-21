"""Ledger-level permutation and chronological walk-forward robustness gates."""

from __future__ import annotations

import json
from math import ceil
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field


class RobustnessRequest(BaseModel):
    ledger_path: str
    permutations: int = Field(default=5_000, ge=500, le=100_000)
    seed: int = 42
    block_size: int = Field(default=5, ge=1, le=100)
    walk_forward_folds: int = Field(default=5, ge=3, le=20)
    significance_level: float = Field(default=0.05, gt=0, lt=0.5)
    min_positive_fold_fraction: float = Field(default=0.6, ge=0.5, le=1.0)


def _read(path: str) -> pd.DataFrame:
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"trade ledger not found: {source}")
    frame = pd.read_parquet(source) if source.suffix.lower() == ".parquet" else pd.read_csv(source)
    required = {"exit_time", "gross_pnl", "net_pnl"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"trade ledger missing columns: {sorted(missing)}")
    frame["exit_time"] = pd.to_datetime(frame["exit_time"], utc=True, errors="coerce")
    for column in ("gross_pnl", "net_pnl", "commission", "swap"):
        frame[column] = pd.to_numeric(frame.get(column, 0.0), errors="coerce").fillna(0.0)
    return frame.dropna(subset=["exit_time"]).sort_values("exit_time").reset_index(drop=True)


def _profit_factor(values: np.ndarray) -> float | None:
    gross_profit = float(values[values > 0].sum())
    gross_loss = float(abs(values[values < 0].sum()))
    return gross_profit / gross_loss if gross_loss else None


def _permutation(
    frame: pd.DataFrame, permutations: int, seed: int, block_size: int,
) -> dict[str, Any]:
    gross = frame["gross_pnl"].to_numpy(dtype=float)
    costs = (frame["commission"] + frame["swap"]).to_numpy(dtype=float)
    observed = float(frame["net_pnl"].sum())
    rng = np.random.default_rng(seed)
    block_index = np.arange(len(frame)) // block_size
    n_blocks = int(block_index[-1] + 1) if len(block_index) else 0
    null = np.empty(permutations, dtype=float)
    chunk = 2_000
    for start in range(0, permutations, chunk):
        count = min(chunk, permutations - start)
        signs = rng.choice(np.array([-1.0, 1.0]), size=(count, n_blocks))
        null[start:start + count] = (signs[:, block_index] * gross).sum(axis=1) - costs.sum()
    p_value = float((np.count_nonzero(null >= observed) + 1) / (permutations + 1))
    std = float(null.std(ddof=1))
    return {
        "trades": len(frame), "observed_net_profit": observed,
        "null_mean": float(null.mean()), "null_std": std,
        "null_p95": float(np.quantile(null, 0.95)), "p_value": p_value,
        "z_score": (observed - float(null.mean())) / std if std else None,
        "block_size": block_size, "permutations": permutations, "seed": seed,
    }


def run_robustness(request: RobustnessRequest) -> dict[str, Any]:
    frame = _read(request.ledger_path)
    if len(frame) < request.walk_forward_folds * 5:
        raise ValueError("not enough trades for the requested walk-forward folds")
    overall = _permutation(frame, request.permutations, request.seed, request.block_size)
    indexes = np.array_split(np.arange(len(frame)), request.walk_forward_folds)
    folds: list[dict[str, Any]] = []
    for fold_number, index in enumerate(indexes, start=1):
        fold = frame.iloc[index]
        pnl = fold["net_pnl"].to_numpy(dtype=float)
        permutation = _permutation(
            fold, request.permutations, request.seed + fold_number, request.block_size
        )
        folds.append({
            "fold": fold_number, "from": fold["exit_time"].iloc[0].isoformat(),
            "to": fold["exit_time"].iloc[-1].isoformat(), "trades": len(fold),
            "net_profit": float(pnl.sum()), "profit_factor": _profit_factor(pnl),
            "win_rate_pct": float(100 * np.mean(pnl > 0)),
            "permutation_p_value": permutation["p_value"],
        })
    positive_folds = sum(fold["net_profit"] > 0 for fold in folds)
    significant_folds = sum(
        fold["permutation_p_value"] <= request.significance_level for fold in folds
    )
    required_positive = ceil(request.walk_forward_folds * request.min_positive_fold_fraction)
    checks = {
        "overall_permutation_significant": overall["p_value"] <= request.significance_level,
        "positive_walk_forward_folds": positive_folds >= required_positive,
        "aggregate_net_profit_positive": overall["observed_net_profit"] > 0,
    }
    return {
        "method": "block_sign_permutation_of_gross_trade_outcomes_after_costs",
        "warning": "This is a ledger-level null test; it does not replace signal-timing permutation on raw market data.",
        "overall": overall, "walk_forward": {
            "folds": folds, "positive_folds": positive_folds,
            "significant_folds": significant_folds, "required_positive_folds": required_positive,
        },
        "gate": {
            "decision": "pass" if all(checks.values()) else "reject",
            "checks": checks,
        },
    }
