"""
discovery_to_mc.py
Adapter: reads a pattern-discovery trade CSV and returns a daily-P&L numpy
array ready for mc_funded_test.py callables.

XAUUSD P&L formula
-------------------
  1.0 standard lot = 100 troy oz
  $1 price move on 100 oz  → $100 USD P&L per lot
  pnl_usd = pnl_pts * lot * 100.0
  (pip_value_per_lot = 100.0 USD per 1.0-lot per 1-dollar price move)
"""

import pandas as pd
import numpy as np
from pathlib import Path


def derive_pnl_pts(df: pd.DataFrame) -> pd.Series:
    """Derive pnl_pts from available columns.

    Priority:
    1. pnl_pts  – direct column
    2. r_multiple + sl_pts  – r * sl for wins (r>0), -sl for losses (r<=0)
    3. tp_pts / sl_pts + outcome  – TP hit → tp_pts, SL hit → -sl_pts
    Falls back to a zero-series if none of the above are present.
    """
    cols = set(df.columns)

    if "pnl_pts" in cols:
        return df["pnl_pts"].astype(float)

    if "r_multiple" in cols and "sl_pts" in cols:
        r = df["r_multiple"].astype(float)
        sl = df["sl_pts"].astype(float).abs()
        # positive r → win: pnl = r * sl; negative r → loss: pnl = r * sl (already negative)
        return r * sl

    if "sl_pts" in cols and "tp_pts" in cols and "outcome" in cols:
        sl = df["sl_pts"].astype(float).abs()
        tp = df["tp_pts"].astype(float).abs()
        outcome = df["outcome"].astype(str).str.lower()
        pnl = pd.Series(0.0, index=df.index)
        pnl[outcome == "tp"] = tp[outcome == "tp"]
        pnl[outcome == "sl"] = -sl[outcome == "sl"]
        return pnl

    # fallback
    return pd.Series(0.0, index=df.index)


def trades_to_daily_pnl_usd(
    trades_df: pd.DataFrame,
    lot: float = 0.10,
    pip_value_per_lot: float = 100.0,
    split_filter: str | None = None,
) -> np.ndarray:
    """Convert a trade DataFrame to a daily-P&L USD numpy array.

    Args:
        trades_df: DataFrame with at least an exit/entry time column and
            pnl_pts (or r_multiple + sl_pts fallback).
        lot: position size in standard lots.
        pip_value_per_lot: USD P&L per 1.0-lot per 1-dollar price move.
            XAUUSD default = 100 (100 oz × $1 = $100).
        split_filter: if 'train' or 'test', keep only that split.

    Returns:
        np.ndarray of daily P&L in USD, sorted by date.
    """
    df = trades_df.copy()

    # Normalise column names to lowercase
    df.columns = [c.lower() for c in df.columns]

    # Split filter
    if split_filter is not None and "split" in df.columns:
        df = df[df["split"].str.lower() == split_filter.lower()]

    if df.empty:
        return np.array([], dtype=float)

    # Resolve date column: prefer exit_time, then entry_time, then time
    for col in ("exit_time", "entry_time", "time"):
        if col in df.columns:
            df["_date"] = pd.to_datetime(df[col], errors="coerce").dt.normalize()
            break
    else:
        raise ValueError("No recognisable time column found (exit_time / entry_time / time).")

    # Derive pnl_pts then convert to USD
    df["_pnl_pts"] = derive_pnl_pts(df)
    # pnl_usd = pnl_pts * lot * pip_value_per_lot
    df["_pnl_usd"] = df["_pnl_pts"] * lot * pip_value_per_lot

    daily = (
        df.dropna(subset=["_date"])
        .groupby("_date")["_pnl_usd"]
        .sum()
        .sort_index()
    )
    return daily.to_numpy(dtype=float)


def load_pattern_csv(csv_path: str | Path, split_filter: str = "test") -> np.ndarray:
    """Load an exported pattern CSV and return daily P&L USD (test split only by default).

    Args:
        csv_path: path to the CSV exported by pattern_discovery_v6.py.
        split_filter: 'train', 'test', or None for all rows.

    Returns:
        np.ndarray of daily P&L in USD, sorted by date.
    """
    df = pd.read_csv(Path(csv_path))
    return trades_to_daily_pnl_usd(df, split_filter=split_filter)


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    rng = np.random.default_rng(42)
    n = 20
    dates = pd.date_range("2024-01-01", periods=10, freq="B").repeat(2)[:n]

    synthetic = pd.DataFrame({
        "exit_time": dates,
        "pnl_pts":   rng.choice([-5.0, -3.0, 4.0, 8.0, 10.0], size=n),
        "split":     ["test"] * 16 + ["train"] * 4,
    })

    result = trades_to_daily_pnl_usd(synthetic, lot=0.10, split_filter="test")

    assert isinstance(result, np.ndarray), "Result is not a numpy array"
    assert np.issubdtype(result.dtype, np.floating), "Result dtype is not float"
    assert len(result) <= 10, f"Expected ≤10 days, got {len(result)}"

    print("daily_pnl_usd (test split, 0.10 lot):", result)
    print(f"Total P&L: ${result.sum():.2f}   Days: {len(result)}")
    print("Smoke test PASSED.")
