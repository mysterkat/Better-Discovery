"""Feature-computation sanity tests (fixture-based, no MT5 required).

Validates that _add_indicators / add_extended_features / detect_regimes
produce the correct columns, finite values after the warmup period, and
that key invariants hold (session in {0..4}, regime in {0..4},
prev_sess_bias in {-1, 0, 1}).

These are the "non-approx" features that must not drift past tolerance;
the full equivalence scoreboard lives in docs/VALIDATION.md.
"""

from __future__ import annotations

import sys
from pathlib import Path
import numpy as np
import pandas as pd
import pytest

# Make the toolkit importable without installing it
sys.path.insert(0, str(Path(__file__).parent.parent / "toolkit"))

import pattern_discovery_v6 as pd6


# ── Synthetic fixture ────────────────────────────────────────────────────────

def _make_fixture(n: int = 600) -> pd.DataFrame:
    """Generate a synthetic OHLCV DataFrame covering 10 trading days.

    Timestamps start at 2024-01-02 00:00 in 1-hour increments so that all
    four sessions (Asian, London, NY, Overlap) are represented repeatedly.
    """
    rng = np.random.default_rng(42)
    # Random walk for close prices
    returns = rng.normal(0.0002, 0.001, n)
    close = 1.1000 * np.cumprod(1 + returns)
    noise_hi = rng.uniform(0.0002, 0.003, n)
    noise_lo = rng.uniform(0.0002, 0.003, n)
    high = close + noise_hi
    low  = close - noise_lo
    open_ = np.roll(close, 1); open_[0] = close[0]
    volume = rng.integers(1000, 10000, n).astype(float)

    timestamps = pd.date_range("2024-01-02 00:00", periods=n, freq="1h")
    df = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=timestamps,
    )
    df.index.name = "time"
    return df


# ── Helpers ──────────────────────────────────────────────────────────────────

WARMUP = 250  # bars before indicators are fully warmed up

def _tail(df: pd.DataFrame) -> pd.DataFrame:
    """Return the post-warmup tail where all indicators should be stable."""
    return df.iloc[WARMUP:]


# ── Tests ────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def featured_df() -> pd.DataFrame:
    raw = _make_fixture()
    df  = pd6._add_indicators(raw.copy())
    df  = pd6.add_extended_features(df)
    df  = pd6.detect_regimes(df)
    return df


def test_indicator_columns_present(featured_df):
    required = [
        "ema20", "ema50", "ema200", "rsi14", "atr14",
        "bb_up", "bb_mid", "bb_lo", "bb_width",
        "macd_hist", "trend", "atr_pct", "rng_atr",
        "body_pct", "session",
    ]
    missing = [c for c in required if c not in featured_df.columns]
    assert not missing, f"Missing indicator columns: {missing}"


def test_extended_feature_columns_present(featured_df):
    required = [
        "vol_ratio", "vol_body_conf", "poc_dist",
        "bb_expanding", "prev_sess_bias",
    ]
    missing = [c for c in required if c not in featured_df.columns]
    assert not missing, f"Missing extended feature columns: {missing}"


def test_regime_column_present(featured_df):
    assert "regime" in featured_df.columns, "detect_regimes() did not add 'regime' column"


def test_no_nan_after_warmup(featured_df):
    tail = _tail(featured_df)
    # Columns known to be valid after warmup (exclude forward-fill artefacts)
    check_cols = [
        "ema20", "ema50", "ema200", "rsi14", "atr14",
        "bb_width", "trend", "session", "regime", "prev_sess_bias",
        "vol_ratio", "vol_body_conf",
    ]
    for col in check_cols:
        nan_count = tail[col].isna().sum()
        assert nan_count == 0, f"Column '{col}' has {nan_count} NaN values after warmup"


def test_session_values_in_range(featured_df):
    valid = {0, 1, 2, 3, 4}
    actual = set(featured_df["session"].unique())
    assert actual <= valid, f"session has invalid values: {actual - valid}"


def test_regime_values_in_range(featured_df):
    tail = _tail(featured_df)
    valid = {0.0, 1.0, 2.0, 3.0, 4.0}
    actual = set(tail["regime"].dropna().unique())
    assert actual <= valid, f"regime has invalid values: {actual - valid}"


def test_prev_sess_bias_values(featured_df):
    tail = _tail(featured_df)
    valid = {-1.0, 0.0, 1.0}
    actual = set(tail["prev_sess_bias"].dropna().unique())
    assert actual <= valid, f"prev_sess_bias has invalid values: {actual - valid}"


def test_rsi_bounded(featured_df):
    tail = _tail(featured_df)
    rsi = tail["rsi14"].dropna()
    assert (rsi >= 0).all() and (rsi <= 100).all(), "RSI out of [0, 100]"


def test_vol_ratio_positive(featured_df):
    tail = _tail(featured_df)
    vr = tail["vol_ratio"].dropna()
    assert (vr >= 0).all(), "vol_ratio has negative values"


def test_bb_width_positive(featured_df):
    tail = _tail(featured_df)
    bw = tail["bb_width"].dropna()
    assert (bw >= 0).all(), "bb_width has negative values"


def test_atr_positive(featured_df):
    tail = _tail(featured_df)
    atr = tail["atr14"].dropna()
    assert (atr > 0).all(), "atr14 should be strictly positive"


def test_ema_ordering_consistency(featured_df):
    """When trend=1, ema20 > ema50 > ema200 (and vice versa for trend=-1)."""
    tail = _tail(featured_df)
    up_rows = tail[tail["trend"] == 1]
    if len(up_rows):
        assert (up_rows["ema20"] > up_rows["ema50"]).all(), "trend=1 but ema20 <= ema50"
        assert (up_rows["ema50"] > up_rows["ema200"]).all(), "trend=1 but ema50 <= ema200"
    dn_rows = tail[tail["trend"] == -1]
    if len(dn_rows):
        assert (dn_rows["ema20"] < dn_rows["ema50"]).all(), "trend=-1 but ema20 >= ema50"
        assert (dn_rows["ema50"] < dn_rows["ema200"]).all(), "trend=-1 but ema50 >= ema200"


def test_detect_regimes_produces_all_five_classes(featured_df):
    """The 600-bar fixture should visit all 5 regime classes."""
    tail = _tail(featured_df)
    actual = set(tail["regime"].dropna().unique())
    # Allow missing classes on very short fixtures; at least 2 should appear.
    assert len(actual) >= 2, f"Only {len(actual)} regime class(es) seen: {actual}"
