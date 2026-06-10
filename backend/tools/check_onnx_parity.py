"""End-to-end ONNX export parity check (dev tool, stage 1).

Trains a filter on synthetic data, exports it to ONNX, then runs the SAME
inputs through LightGBM (predict_proba) and onnxruntime and asserts the
P(win) outputs agree to float32 tolerance. This is the Python half of the
parity protocol; the MQL5 half (OnnxRun on the same vectors) is stage 2.

Usage:  python backend/tools/check_onnx_parity.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "toolkit"))

import onnx_filter as onf


def main() -> int:
    rng = np.random.default_rng(5)
    n = 1200
    rsi = rng.uniform(0, 100, n)
    win = rng.uniform(0, 1, n) < np.where(rsi > 55, 0.65, 0.38)
    df = pd.DataFrame({
        "split": "train",
        "rr": np.where(win, 1.2, -1.0),
        "feat_rsi14": rsi,
        "feat_trend": rng.integers(-1, 2, n).astype(float),
        "feat_atr_pct": rng.uniform(0.001, 0.01, n),
    })

    model, feats = onf.train_filter(df)
    with tempfile.TemporaryDirectory() as td:
        onnx_path = onf.export_onnx(model, feats, Path(td) / "m.onnx")
        if onnx_path is None:
            print("FAIL: ONNX export unavailable (converters not installed)")
            return 1

        import onnxruntime as ort
        sess = ort.InferenceSession(onnx_path, providers=["CPUExecutionProvider"])
        out_names = [o.name for o in sess.get_outputs()]

        X = df[feats].to_numpy(dtype=np.float32)[:50]
        p_ref = model.predict_proba(X)[:, 1]
        p_onx = np.array([
            sess.run(out_names, {"input": row.reshape(1, -1)})[-1].ravel()[-1]
            for row in X
        ])
        max_diff = float(np.abs(p_ref - p_onx).max())
        print(f"outputs: {out_names}; max |LightGBM - ONNX| over 50 rows = {max_diff:.2e}")
        if max_diff > 1e-5:
            print("FAIL: ONNX predictions diverge from LightGBM")
            return 1
    print("PASS: ONNX export reproduces LightGBM P(win) to float32 tolerance")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
