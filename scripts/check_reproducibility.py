#!/usr/bin/env python3
"""Diff two MT5 Strategy Tester trade-list CSVs to verify EA determinism.

Usage:
    python scripts/check_reproducibility.py run_1.csv run_2.csv
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd


_NUMERIC_COLS = ("Price", "S/L", "T/P", "Profit", "Balance")
_TOL = 1e-6  # floating-point comparison tolerance


def load(path: str) -> pd.DataFrame:
    p = Path(path)
    if not p.is_file():
        sys.exit(f"File not found: {path}")
    # MT5 CSV may have a BOM and variable delimiters — try tab then comma.
    for sep in ("\t", ",", ";"):
        try:
            df = pd.read_csv(p, sep=sep, encoding="utf-8-sig", skipinitialspace=True)
            if len(df.columns) > 3:
                break
        except Exception:
            continue
    df.columns = df.columns.str.strip()
    # Drop summary/header rows that MT5 sometimes appends
    if "Time" in df.columns:
        df = df[df["Time"].notna() & df["Time"].astype(str).str.match(r"\d{4}")]
    return df.reset_index(drop=True)


def compare(path1: str, path2: str) -> bool:
    print(f"Comparing {path1}  vs  {path2}")
    df1 = load(path1)
    df2 = load(path2)

    n1, n2 = len(df1), len(df2)
    print(f"  Run 1 trades: {n1}")
    print(f"  Run 2 trades: {n2}")

    if n1 != n2:
        print(f"  Trade count mismatch: {n1} vs {n2}")
        print("FAIL — trade counts differ.")
        return False
    print("  Trade count match: OK")

    # Check columns match
    if set(df1.columns) != set(df2.columns):
        extra1 = set(df1.columns) - set(df2.columns)
        extra2 = set(df2.columns) - set(df1.columns)
        print(f"  Column mismatch — only in run1: {extra1}  only in run2: {extra2}")
        print("FAIL — column sets differ.")
        return False

    diffs: list[str] = []
    for col in df1.columns:
        if col in _NUMERIC_COLS:
            try:
                v1 = pd.to_numeric(df1[col], errors="coerce")
                v2 = pd.to_numeric(df2[col], errors="coerce")
                mask = (v1 - v2).abs() > _TOL
                if mask.any():
                    diffs.append(f"{col}: {mask.sum()} rows differ (max Δ={( v1-v2).abs().max():.8f})")
            except Exception as e:
                diffs.append(f"{col}: comparison error — {e}")
        else:
            mask = df1[col].astype(str).str.strip() != df2[col].astype(str).str.strip()
            if mask.any():
                diffs.append(f"{col}: {mask.sum()} rows differ")

    if diffs:
        print("  Column diffs:")
        for d in diffs:
            print(f"    • {d}")
        print("FAIL — trades are not identical.")
        return False

    print("  Column diffs: none")
    print("  All trades identical: OK")
    print("PASS — EA backtest is deterministic.")
    return True


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("Usage: python check_reproducibility.py <run1.csv> <run2.csv>")
    ok = compare(sys.argv[1], sys.argv[2])
    sys.exit(0 if ok else 1)
