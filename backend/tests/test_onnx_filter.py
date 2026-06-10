"""ONNX trade-filter trainer: honest validation on synthetic planted signal."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "toolkit"))

lgb = pytest.importorskip("lightgbm")

import onnx_filter as onf
import pattern_discovery_v6 as pd6


def _synthetic_csv(tmp_path: Path, n_train: int = 900, n_val: int = 450,
                   seed: int = 11) -> Path:
    """Trades where feat_rsi14 > 55 wins 65% of the time, else 38% —
    a real, learnable filter signal with noise."""
    rng = np.random.default_rng(seed)

    def _rows(n, split):
        rsi = rng.uniform(0, 100, n)
        trend = rng.integers(-1, 2, n).astype(float)
        noise = rng.uniform(0, 1, n)
        p_win = np.where(rsi > 55, 0.65, 0.38)
        win = rng.uniform(0, 1, n) < p_win
        rr = np.where(win, 1.2, -1.0) + noise * 0.01
        return pd.DataFrame({
            "split": split, "result": np.where(win, "WIN", "LOSS"), "rr": rr,
            "feat_rsi14": rsi, "feat_trend": trend,
            "feat_atr_pct": rng.uniform(0.001, 0.01, n),
        })

    df = pd.concat([_rows(n_train, "train"), _rows(n_val, "test_ea")],
                   ignore_index=True)
    p = tmp_path / "cluster_1_LONG_seed11.csv"
    df.to_csv(p, index=False)
    return p


def test_filter_finds_planted_signal(tmp_path):
    csv = _synthetic_csv(tmp_path)
    rep = onf.run_for_csv(csv, out_dir=tmp_path)
    b, f = rep["val_ea_oos"]["baseline"], rep["val_ea_oos"]["filtered"]
    assert f["n"] >= 30
    assert f["wr"] > b["wr"], "filter should raise OOS win rate on planted signal"
    assert f["pf"] > b["pf"], "filter should raise OOS profit factor"
    assert rep["verdict"] == "UPLIFT"
    # contract artifacts always written
    assert Path(rep["native_model"]).exists()
    assert Path(rep["sidecar"]).exists()


def test_threshold_chosen_on_train_only(tmp_path):
    csv = _synthetic_csv(tmp_path)
    train_df, val_df = onf.load_pattern_frames(csv)
    model, feats = onf.train_filter(train_df)
    t = onf.choose_threshold(model, train_df, feats)
    assert t in onf.THRESHOLD_GRID
    ev = onf.evaluate(model, train_df, feats, t)
    n_total = ev["baseline"]["n"]
    assert ev["filtered"]["n"] >= max(onf.MIN_KEPT_TRADES,
                                      onf.MIN_KEPT_FRAC * n_total)


def test_rejects_csv_without_feature_snapshot(tmp_path):
    p = tmp_path / "cluster_old_seed1.csv"
    pd.DataFrame({"split": ["train"], "rr": [1.0]}).to_csv(p, index=False)
    with pytest.raises(ValueError, match="feat_"):
        onf.load_pattern_frames(p)


def test_too_few_trades_rejected(tmp_path):
    csv = _synthetic_csv(tmp_path, n_train=50, n_val=30)
    train_df, _ = onf.load_pattern_frames(csv)
    with pytest.raises(ValueError, match="not enough"):
        onf.train_filter(train_df)


def test_feature_snapshot_columns_in_backtest_export():
    """backtest_refined attaches feat_* columns in EA_FEATURE_COLS order."""
    assert len(pd6.EA_FEATURE_COLS) == 27
    # converter's column table must agree (same EA contract)
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from backend.app.bridge import set_to_mql
    assert pd6.EA_FEATURE_COLS == set_to_mql._COLS


def test_onnx_export_graceful_without_converters(tmp_path, monkeypatch):
    """When skl2onnx/onnxmltools are missing the export returns None."""
    import builtins
    real_import = builtins.__import__

    def _block(name, *a, **k):
        if name.startswith(("skl2onnx", "onnxmltools")):
            raise ImportError(f"blocked for test: {name}")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _block)
    csv = _synthetic_csv(tmp_path)
    train_df, _ = onf.load_pattern_frames(csv)
    model, feats = onf.train_filter(train_df)
    assert onf.export_onnx(model, feats, tmp_path / "m.onnx") is None
    assert not (tmp_path / "m.onnx").exists()
