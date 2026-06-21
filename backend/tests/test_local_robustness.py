from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from app.local_replay.robustness import RobustnessRequest, run_robustness


def _ledger(path: Path, pnl: np.ndarray) -> Path:
    frame = pd.DataFrame({
        "exit_time": pd.date_range("2025-01-01", periods=len(pnl), freq="D", tz="UTC"),
        "gross_pnl": pnl, "commission": 0.0, "swap": 0.0, "net_pnl": pnl,
    })
    frame.to_csv(path, index=False)
    return path


def test_robust_positive_ledger_passes_permutation_and_walk_forward(tmp_path: Path) -> None:
    rng = np.random.default_rng(7)
    pnl = rng.normal(3.0, 1.0, 100)
    result = run_robustness(RobustnessRequest(
        ledger_path=str(_ledger(tmp_path / "trades.csv", pnl)), permutations=1000,
        seed=11, block_size=5, walk_forward_folds=5,
    ))
    assert result["gate"]["decision"] == "pass"
    assert result["overall"]["p_value"] <= 0.05
    assert result["walk_forward"]["positive_folds"] == 5


def test_flat_ledger_fails_robustness_gate(tmp_path: Path) -> None:
    pnl = np.tile(np.array([-1.0, 1.0]), 50)
    result = run_robustness(RobustnessRequest(
        ledger_path=str(_ledger(tmp_path / "flat.csv", pnl)), permutations=1000,
        seed=11, block_size=2, walk_forward_folds=5,
    ))
    assert result["gate"]["decision"] == "reject"
