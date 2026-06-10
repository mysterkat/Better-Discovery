# ONNX trade filter (stage 1: train + validate)

Goal: a small ML model inside the EA that vetoes low-quality signals —
`box rule fires AND P(win) ≥ threshold`. MT5 runs ONNX models natively, so
the filter works in the Strategy Tester and live with zero runtime
dependencies. This stage ships the training/validation side only; the EA
integration lands once a filter has **proven uplift** on EA-faithful OOS data.

## Pipeline

1. Discovery (v2.3.1+) writes a full EA feature snapshot (`feat_*` columns,
   27 features in EA `COLUMN INDEX TABLE` order) into every per-pattern trade
   CSV, for all splits (`train`, `test`, `test_ea`).
2. `backend/toolkit/onnx_filter.py` trains LightGBM `P(win | features)` on the
   `train` split, picks the probability threshold **on train only**, and
   reports baseline-vs-filtered WR/PF on the untouched `test_ea` split — the
   exact trade stream MT5 reproduces.

```powershell
python backend\toolkit\onnx_filter.py "userdata\discovery\<seed folder>"
# or one pattern:
python backend\toolkit\onnx_filter.py "...\cluster_1_LONG_seed3473452712.csv"
```

Per pattern it writes:

| artifact | purpose |
|---|---|
| `filter_<pattern>.lgbm.txt` | LightGBM native model (always) |
| `filter_<pattern>.features.json` | feature order + threshold — the EA input contract |
| `filter_<pattern>.onnx` | ONNX model (only if `skl2onnx` + `onnxmltools` installed) |
| `onnx_filter_report.md` | baseline vs filtered metrics, UPLIFT / NO-UPLIFT verdict |

`pip install skl2onnx onnxmltools` enables the `.onnx` export; without them
everything else still runs and the native model can be converted elsewhere.

## Honesty rules (enforced in code)

- Threshold tuned on train, never on validation — re-tuning on OOS would
  recreate the selection bias removed from the discovery gate.
- Validation = `test_ea` (EA-faithful box-only OOS), read once.
- `< 120` train trades → refuse to fit (per-pattern filters need data).
- The threshold may not discard more than 70% of trades.
- Verdict is UPLIFT only if OOS PF improves ≥ 5% with WR not lower and ≥ 30
  trades kept. NO-UPLIFT means: do not put this filter in the EA.

## Stage 2 (not yet implemented)

EA integration: `UseOnnxFilter` input + `OnnxCreateFromBuffer` on an embedded
model resource, plus a Python↔MQL5 parity script (same input vector through
`onnxruntime` and `OnnxRun` must match to float32 tolerance) and a per-trade
Strategy Tester diff. Only worth building for patterns whose stage-1 report
says UPLIFT.
