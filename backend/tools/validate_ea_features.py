"""Indicator-drift validation harness (v0.7.0).

Compares the 12 BETTER DISCOVERY features computed in Python
(`pattern_discovery_v6.py`) against the same features computed in MT5 by
the bundled BD_*.mq5 indicators. Surfaces per-column diffs so we can
catch any drift between the two implementations *before* it shows up as
a phantom MT5-vs-discovery trade-count gap.

USAGE
-----

# Step 1: dump the Python ground-truth CSV
python -m backend.tools.validate_ea_features python-dump \\
    --hist userdata/hist_data/xauusd_m5.csv \\
    --out  userdata/validation/python_features.csv

# Step 2: in MT5, attach backend/mt5/services/BD_FeatureDump.mq5 to the
#   same symbol+timeframe chart with algo trading enabled. It will write
#   to the MT5 Common\\Files\\bd_feature_dump.csv and tell you when done.

# Step 3: copy the MT5 CSV next to the Python one and diff them
python -m backend.tools.validate_ea_features diff \\
    --python userdata/validation/python_features.csv \\
    --mt5    userdata/validation/bd_feature_dump.csv \\
    --report userdata/validation/diff_report.txt

EXIT CODES
----------
0  All columns within tolerance (default 1e-3 abs or 1% rel — see --tolerance).
1  At least one column drifted past tolerance (details in stdout + report).
2  Bad input (file missing, schema mismatch, etc.).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


# ── Columns we validate. Order matters for report readability. ───────────────
# Some features (regime, prev_sess_bias, mtf_bull_score) depend on rolling
# quantiles or session boundaries that don't match perfectly in MT5; they're
# tagged "approx" — the harness reports their drift but doesn't fail on them.
APPROX_COLS = {"regime", "prev_sess_bias", "mtf_bull_score"}
VALIDATE_COLS = [
    "rsi14", "macd_norm", "atr_pct", "bb_width", "trend",
    "mtf_bull_score", "body_pct", "rng_atr", "vol_ratio",
    "vol_body_conf", "regime", "vol_price_div", "bb_expanding",
    "prev_sess_bias", "poc_dist", "bull", "uwk_pct", "lwk_pct",
    "stoch_k", "stoch_d", "pin_bar", "inside_bar", "outside_bar",
    "htf_div", "rolling_sharpe", "sd_zone", "vwap_dist",
]


# ── Python feature dump ──────────────────────────────────────────────────────

def _python_features(hist_csv: Path) -> pd.DataFrame:
    """Run pattern_discovery_v6's full multi-TF feature pipeline on the data.

    Uses ``load_raw_data()`` (not just ``_load_raw + _add_indicators``) so that
    multi-timeframe-derived columns — ``mtf_bull_score`` and ``htf_div`` — are
    actually populated. The single-TF path leaves both columns as zeros, which
    silently breaks any later diff against MT5.

    Imports are deferred so the module can be imported without sklearn/pandas
    in environments where we only want to do diff (no python-dump).
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "toolkit"))
    import pattern_discovery_v6 as pd6  # type: ignore[import-not-found]

    hist_path = Path(hist_csv).resolve()
    folder = hist_path.parent
    primary_name = hist_path.name      # e.g. "xauusd_m5.csv"
    sym = primary_name.split("_", 1)[0]

    # Auto-discover companion TFs in the same folder ({sym}_*.csv). Pattern
    # Discovery uses 5 slots; sort smallest TF → largest, primary is slot 1.
    tf_minutes = {
        "m1": 1, "m2": 2, "m3": 3, "m4": 4, "m5": 5, "m6": 6,
        "m10": 10, "m12": 12, "m15": 15, "m20": 20, "m30": 30,
        "h1": 60, "h2": 120, "h3": 180, "h4": 240, "h6": 360,
        "h8": 480, "h12": 720, "d1": 1440, "w1": 10080, "mn1": 43200,
    }
    candidates: list[tuple[int, str]] = []
    for f in folder.glob(f"{sym}_*.csv"):
        tf = f.stem.split("_", 1)[1].lower()
        candidates.append((tf_minutes.get(tf, 99_999_999), f.name))
    candidates.sort()

    if not candidates:
        raise RuntimeError(f"No {sym}_*.csv files found in {folder}")
    if primary_name not in {n for _, n in candidates}:
        candidates.insert(0, (tf_minutes.get(primary_name.split("_", 1)[1].split(".")[0], 0), primary_name))

    # Re-order so the user's selected primary is first
    candidates.sort(key=lambda x: (x[1] != primary_name, x[0]))
    slot_files = [n for _, n in candidates[:5]] + [""] * max(0, 5 - len(candidates))

    print(f"  Multi-TF feature build: primary={primary_name}; signal slots={[s for s in slot_files[1:] if s] or '(none)'}")

    # Wire pattern_discovery_v6 globals so load_raw_data() picks up our paths
    pd6.DATA_FOLDER = str(folder)
    pd6.TF1_FILE = slot_files[0]
    pd6.TF2_FILE = slot_files[1]
    pd6.TF3_FILE = slot_files[2]
    pd6.TF4_FILE = slot_files[3]
    pd6.TF5_FILE = slot_files[4]
    pd6.PRIMARY_TF = 1

    df = pd6.load_raw_data()
    df = pd6.add_extended_features(df)
    df = pd6.add_v5_features(df)
    # detect_regimes adds the `regime` column. Without this the diff
    # tool reports regime as "missing in one side".
    if hasattr(pd6, "detect_regimes"):
        df = pd6.detect_regimes(df)
    return df


def cmd_python_dump(args: argparse.Namespace) -> int:
    hist = Path(args.hist).resolve()
    out  = Path(args.out).resolve()
    if not hist.is_file():
        print(f"ERROR: history CSV not found: {hist}", file=sys.stderr)
        return 2
    out.parent.mkdir(parents=True, exist_ok=True)

    df = _python_features(hist)
    keep = ["open", "high", "low", "close"] + [c for c in VALIDATE_COLS if c in df.columns]
    out_df = df[keep].copy()
    out_df.index.name = "time"
    # ISO-8601 so MT5's CSV (which writes broker time) lines up.
    out_df.to_csv(out, date_format="%Y-%m-%d %H:%M:%S", float_format="%.6f")
    print(f"Wrote {len(out_df):,} rows × {len(keep)} cols to {out}")
    return 0


# ── Diff ─────────────────────────────────────────────────────────────────────

def _align(py: pd.DataFrame, mt: pd.DataFrame) -> pd.DataFrame:
    """Inner-join on the timestamp index, suffix Python = _py, MT5 = _mt."""
    py = py.copy(); mt = mt.copy()
    py.index = pd.to_datetime(py.index)
    mt.index = pd.to_datetime(mt.index)
    merged = py.add_suffix("_py").join(mt.add_suffix("_mt"), how="inner")
    return merged


def _column_stats(s_py: pd.Series, s_mt: pd.Series, tol_abs: float, tol_rel: float) -> dict[str, Any]:
    diff = (s_mt - s_py).astype(float)
    abs_diff = diff.abs()
    denom = s_py.abs().clip(lower=1e-9)
    rel_diff = (abs_diff / denom).fillna(0.0)
    within = ((abs_diff <= tol_abs) | (rel_diff <= tol_rel))
    return {
        "n":          int(len(s_py)),
        "max_abs":    float(abs_diff.max()),
        "mean_abs":   float(abs_diff.mean()),
        "p99_abs":    float(np.percentile(abs_diff, 99)),
        "pct_match":  float(within.mean() * 100.0),
        "py_min":     float(s_py.min()),
        "py_max":     float(s_py.max()),
        "mt_min":     float(s_mt.min()),
        "mt_max":     float(s_mt.max()),
    }


def cmd_diff(args: argparse.Namespace) -> int:
    py_path = Path(args.python).resolve()
    mt_path = Path(args.mt5).resolve()
    if not py_path.is_file():
        print(f"ERROR: python CSV not found: {py_path}", file=sys.stderr); return 2
    if not mt_path.is_file():
        print(f"ERROR: MT5 CSV not found: {mt_path}", file=sys.stderr); return 2

    py = pd.read_csv(py_path, index_col="time", parse_dates=True)
    mt = pd.read_csv(mt_path, index_col="time", parse_dates=True)
    merged = _align(py, mt)
    if merged.empty:
        print("ERROR: no overlapping timestamps between the two CSVs.", file=sys.stderr)
        print(f"  python range: {py.index.min()} .. {py.index.max()}", file=sys.stderr)
        print(f"  mt5    range: {mt.index.min()} .. {mt.index.max()}", file=sys.stderr)
        return 2

    print(f"Aligned {len(merged):,} rows.\n")
    header = (
        f"{'feature':<20} {'n':>8} {'max_abs':>12} {'mean_abs':>12} "
        f"{'p99_abs':>12} {'%match':>8}  status"
    )
    print(header); print("-" * len(header))

    failed: list[str] = []
    report_lines: list[str] = [header, "-" * len(header)]
    for col in VALIDATE_COLS:
        py_col = f"{col}_py"; mt_col = f"{col}_mt"
        if py_col not in merged.columns or mt_col not in merged.columns:
            line = f"{col:<20} {'-':>8} {'(missing in one side)':>50}"
            print(line); report_lines.append(line); continue
        st = _column_stats(merged[py_col], merged[mt_col], args.tolerance, args.tolerance_rel)
        is_approx = col in APPROX_COLS
        ok = (st["pct_match"] >= args.min_pct_match) or is_approx
        tag = "approx" if is_approx else ("PASS" if ok else "FAIL")
        line = (
            f"{col:<20} {st['n']:>8d} {st['max_abs']:>12.6f} {st['mean_abs']:>12.6f} "
            f"{st['p99_abs']:>12.6f} {st['pct_match']:>7.2f}%  {tag}"
        )
        print(line); report_lines.append(line)
        if not ok and not is_approx:
            failed.append(col)

    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text("\n".join(report_lines) + "\n", encoding="utf-8")
        print(f"\nReport written to {args.report}")

    if failed:
        print(f"\nFAIL: {len(failed)} column(s) drifted past tolerance: {', '.join(failed)}")
        return 1
    print("\nOK: all non-approx columns within tolerance.")
    return 0


# ── CLI ──────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    p_dump = sub.add_parser("python-dump", help="Compute Python-side features and write CSV.")
    p_dump.add_argument("--hist", required=True, help="Path to a {sym}_{tf}.csv hist_data file.")
    p_dump.add_argument("--out",  required=True, help="Output CSV path.")
    p_dump.set_defaults(func=cmd_python_dump)

    p_diff = sub.add_parser("diff", help="Diff Python CSV vs MT5 CSV.")
    p_diff.add_argument("--python", required=True)
    p_diff.add_argument("--mt5",    required=True)
    p_diff.add_argument("--report", default=None, help="Optional plain-text report path.")
    p_diff.add_argument("--tolerance",     type=float, default=1e-3, help="Abs diff tolerance (default 1e-3).")
    p_diff.add_argument("--tolerance-rel", type=float, default=1e-2, help="Relative diff tolerance (default 1%%).")
    p_diff.add_argument("--min-pct-match", type=float, default=98.0,
                        help="Min %% of bars that must match within tolerance (default 98).")
    p_diff.set_defaults(func=cmd_diff)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
