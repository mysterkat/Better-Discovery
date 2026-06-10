"""Optional ONNX trade-filter: train, validate honestly, export.

Stage 1 of the ONNX-in-EA plan (training side only — EA inference lands
separately once a filter has PROVEN uplift here):

  1. Discovery exports per-pattern trade CSVs with full EA feature snapshots
     (feat_* columns, EA_FEATURE_COLS order) for splits train / test / test_ea.
  2. This module fits a LightGBM classifier P(win | features at signal bar)
     on the TRAIN split, picks the probability threshold ON TRAIN ONLY, and
     reports baseline-vs-filtered metrics on the untouched EA-faithful OOS
     split (test_ea) — the same trade stream MT5 reproduces.
  3. If (and only if) the OOS report shows real uplift, export the model to
     ONNX for the EA. The export also writes a sidecar JSON with the exact
     feature order — the contract the EA's input vector must follow.

Strictly optional: discovery never imports this module. LightGBM is already
a discovery dependency; the ONNX converters (skl2onnx/onnxmltools) are NOT
bundled — without them training/validation still work and only the .onnx
export step is skipped with a note (the LightGBM native model is always
saved so it can be converted elsewhere).

Honesty rules baked in:
  - threshold chosen on train, never on validation
  - validation = test_ea split (EA-faithful), read once
  - a pattern with no feat_* columns (pre-2.3.x CSV) is rejected, not guessed
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

FEATURE_PREFIX = "feat_"
MIN_TRAIN_TRADES = 120     # below this a per-pattern filter is just noise
MIN_KEPT_FRAC = 0.30       # threshold may not discard more than 70% of trades
MIN_KEPT_TRADES = 40       # ...and must keep at least this many (train)
THRESHOLD_GRID = [round(0.40 + 0.02 * i, 2) for i in range(16)]  # 0.40..0.70


# ── data loading ──────────────────────────────────────────────────────────────

def feature_columns(df: pd.DataFrame) -> list[str]:
    """feat_* columns in EA order (as written by backtest_refined)."""
    return [c for c in df.columns if c.startswith(FEATURE_PREFIX)]


def load_pattern_frames(csv_path: str | Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (train_df, val_df) for one per-pattern trade CSV.

    val = the EA-faithful OOS split (test_ea), falling back to the
    cluster-gated test split only for CSVs that predate test_ea.
    """
    df = pd.read_csv(csv_path)
    feats = feature_columns(df)
    if not feats:
        raise ValueError(
            f"{csv_path}: no {FEATURE_PREFIX}* columns — re-run discovery with "
            "v2.3.1+ so trade CSVs include the EA feature snapshot.")
    if "split" not in df.columns or "rr" not in df.columns:
        raise ValueError(f"{csv_path}: missing required 'split'/'rr' columns")
    train = df[df["split"] == "train"].dropna(subset=feats)
    val = df[df["split"] == "test_ea"].dropna(subset=feats)
    if val.empty:
        val = df[df["split"] == "test"].dropna(subset=feats)
    return train.reset_index(drop=True), val.reset_index(drop=True)


# ── metrics ───────────────────────────────────────────────────────────────────

def trade_metrics(rr: np.ndarray) -> dict:
    """WR / PF / expectancy from net booked R values (WIN ⇔ rr > 0)."""
    rr = np.asarray(rr, dtype=np.float64)
    n = int(len(rr))
    if n == 0:
        return {"n": 0, "wr": 0.0, "pf": 0.0, "avg_r": 0.0, "net_r": 0.0}
    wins = rr > 0
    gw = float(rr[wins].sum())
    gl = float(-rr[~wins].sum())
    return {
        "n": n,
        "wr": round(float(wins.mean()) * 100, 1),
        "pf": round(gw / gl, 3) if gl > 0 else float("inf"),
        "avg_r": round(float(rr.mean()), 4),
        "net_r": round(float(rr.sum()), 2),
    }


# ── training ──────────────────────────────────────────────────────────────────

def train_filter(train_df: pd.DataFrame, seed: int = 42):
    """Fit P(win | signal-bar features) on the TRAIN split. Returns
    (model, feats) or raises ValueError when the sample is too small."""
    import lightgbm as lgb   # discovery dependency; lazy so import never breaks

    feats = feature_columns(train_df)
    if len(train_df) < MIN_TRAIN_TRADES:
        raise ValueError(
            f"only {len(train_df)} train trades (<{MIN_TRAIN_TRADES}) — "
            "not enough to fit a per-pattern filter honestly")
    X = train_df[feats].to_numpy(dtype=np.float32)
    y = (train_df["rr"].to_numpy(dtype=np.float64) > 0).astype(np.int32)
    if y.min() == y.max():
        raise ValueError("degenerate labels: train split is all wins or all losses")
    model = lgb.LGBMClassifier(
        n_estimators=200, num_leaves=15, learning_rate=0.05,
        min_child_samples=30, subsample=0.8, subsample_freq=1,
        colsample_bytree=0.8, random_state=seed, n_jobs=-1, verbose=-1,
    )
    model.fit(X, y)
    return model, feats


def choose_threshold(model, train_df: pd.DataFrame, feats: list[str]) -> float:
    """Pick the P(win) cut that maximises TRAIN profit factor subject to
    keeping ≥ MIN_KEPT_FRAC of trades and ≥ MIN_KEPT_TRADES. TRAIN ONLY —
    tuning this on the validation split would re-introduce selection bias."""
    X = train_df[feats].to_numpy(dtype=np.float32)
    rr = train_df["rr"].to_numpy(dtype=np.float64)
    p = model.predict_proba(X)[:, 1]
    best_t, best_pf = THRESHOLD_GRID[0], -1.0
    for t in THRESHOLD_GRID:
        keep = p >= t
        if keep.sum() < max(MIN_KEPT_TRADES, MIN_KEPT_FRAC * len(rr)):
            continue
        pf = trade_metrics(rr[keep])["pf"]
        if pf != float("inf") and pf > best_pf:
            best_t, best_pf = t, pf
    return best_t


def evaluate(model, df: pd.DataFrame, feats: list[str], threshold: float) -> dict:
    """Baseline vs filtered metrics on one split."""
    X = df[feats].to_numpy(dtype=np.float32)
    rr = df["rr"].to_numpy(dtype=np.float64)
    p = model.predict_proba(X)[:, 1] if len(df) else np.zeros(0)
    keep = p >= threshold
    return {
        "baseline": trade_metrics(rr),
        "filtered": trade_metrics(rr[keep]),
        "kept_frac": round(float(keep.mean()), 3) if len(rr) else 0.0,
        "threshold": threshold,
    }


# ── export ────────────────────────────────────────────────────────────────────

def export_onnx(model, feats: list[str], out_path: str | Path) -> str | None:
    """Export to ONNX (float32 [1,N] input, zipmap off). Returns the path, or
    None when the converter packages aren't installed (non-fatal: the LightGBM
    native model is saved regardless by run_for_csv)."""
    out_path = Path(out_path)
    try:
        from skl2onnx import convert_sklearn, update_registered_converter
        from skl2onnx.common.data_types import FloatTensorType
        from skl2onnx.common.shape_calculator import (
            calculate_linear_classifier_output_shapes)
        from onnxmltools.convert.lightgbm.operator_converters.LightGbm import (
            convert_lightgbm)
        import lightgbm as lgb
    except ImportError as e:
        print(f"[onnx_filter] ONNX export skipped (pip install skl2onnx "
              f"onnxmltools to enable): {e}")
        return None
    update_registered_converter(
        lgb.LGBMClassifier, "LightGbmLGBMClassifier",
        calculate_linear_classifier_output_shapes, convert_lightgbm,
        options={"nocl": [True, False], "zipmap": [True, False]})
    onx = convert_sklearn(
        model, initial_types=[("input", FloatTensorType([1, len(feats)]))],
        options={id(model): {"zipmap": False}})
    out_path.write_bytes(onx.SerializeToString())
    return str(out_path)


# ── orchestration ─────────────────────────────────────────────────────────────

def run_for_csv(csv_path: str | Path, out_dir: str | Path | None = None,
                seed: int = 42) -> dict:
    """Train + honestly validate a filter for one pattern CSV; write the model
    (native + ONNX when possible), the feature-order sidecar, and return the
    summary dict."""
    csv_path = Path(csv_path)
    out_dir = Path(out_dir) if out_dir else csv_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = csv_path.stem

    train_df, val_df = load_pattern_frames(csv_path)
    model, feats = train_filter(train_df, seed=seed)
    threshold = choose_threshold(model, train_df, feats)
    report = {
        "pattern": stem,
        "features": feats,
        "train": evaluate(model, train_df, feats, threshold),
        "val_ea_oos": evaluate(model, val_df, feats, threshold),
        "verdict": None,
    }
    b, f = report["val_ea_oos"]["baseline"], report["val_ea_oos"]["filtered"]
    # Uplift verdict on the untouched EA-faithful OOS split: the filter earns
    # the EA integration only if it improves PF without collapsing the sample.
    improved = (f["n"] >= 30 and b["pf"] not in (0.0, float("inf"))
                and f["pf"] > b["pf"] * 1.05 and f["wr"] >= b["wr"])
    report["verdict"] = "UPLIFT" if improved else "NO-UPLIFT"

    native_path = out_dir / f"filter_{stem}.lgbm.txt"
    model.booster_.save_model(str(native_path))
    report["native_model"] = str(native_path)
    sidecar = out_dir / f"filter_{stem}.features.json"
    sidecar.write_text(json.dumps(
        {"feature_order": feats, "threshold": threshold,
         "input_shape": [1, len(feats)], "label": "P(win) = output[1]"},
        indent=1), encoding="utf-8")
    report["sidecar"] = str(sidecar)
    onnx_path = export_onnx(model, feats, out_dir / f"filter_{stem}.onnx")
    report["onnx_model"] = onnx_path
    return report


def _fmt_split(name: str, ev: dict) -> str:
    b, f = ev["baseline"], ev["filtered"]
    return (f"  {name}: baseline n={b['n']} WR={b['wr']}% PF={b['pf']} "
            f"-> filtered n={f['n']} WR={f['wr']}% PF={f['pf']} "
            f"(kept {ev['kept_frac']:.0%} @ p>={ev['threshold']})")


def _main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python onnx_filter.py <discovery_seed_dir | pattern_csv>")
        return 2
    target = Path(argv[1])
    csvs = ([target] if target.is_file()
            else sorted(target.glob("cluster_*_seed*.csv")))
    if not csvs:
        print(f"no pattern CSVs found in {target}")
        return 1
    rc = 0
    lines = ["# ONNX trade-filter report", ""]
    for c in csvs:
        try:
            rep = run_for_csv(c)
        except Exception as e:
            print(f"[onnx_filter] {c.name}: skipped — {e}")
            lines += [f"## {c.stem}", f"skipped — {e}", ""]
            rc = 1
            continue
        print(f"[onnx_filter] {c.name}: {rep['verdict']}")
        print(_fmt_split("train  ", rep["train"]))
        print(_fmt_split("EA-OOS ", rep["val_ea_oos"]))
        lines += [f"## {c.stem} — {rep['verdict']}",
                  _fmt_split("train", rep["train"]),
                  _fmt_split("EA-OOS", rep["val_ea_oos"]),
                  f"  model: {rep['onnx_model'] or rep['native_model']}", ""]
    out_md = (csvs[0].parent / "onnx_filter_report.md")
    out_md.write_text("\n".join(lines), encoding="utf-8")
    print(f"[onnx_filter] report -> {out_md}")
    return rc


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
