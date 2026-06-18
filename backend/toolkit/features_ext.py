"""
features_ext.py — standalone HIGH-INFORMATION feature functions
================================================================
Pure pandas/numpy feature engineering for a price DataFrame.

Expected input
--------------
A DataFrame with columns ``open, high, low, close`` (``volume`` optional) and
a *DatetimeIndex*.  Every function:

  * takes ``df`` as its first argument,
  * returns a **new** DataFrame with extra columns appended (NO in-place
    mutation, NO dropped rows, NO reindexing),
  * is deterministic and dependency-free (only pandas + numpy).

Three families are provided:

  (a) MARKET STRUCTURE  — rolling swing highs/lows, break-of-structure (BOS),
      distance-to-recent-swing, fair-value-gap (FVG) detection,
      wick-rejection / liquidity-sweep flags.
  (b) TIME              — hour-of-day, day-of-week, session one-hot
      (Asian/London/NY/overlap), minutes-since-session-open.
  (c) RANK-NORMALIZATION — rolling z-score / rolling percentile-rank helper so
      that "RSI high" means high *relative to recent history*, not absolute.

Dispatcher
----------
``add_all_ext_features(df, enable=None)`` runs the lot (or a chosen subset).

Cross-asset stub
----------------
``add_cross_asset_features(df, ext_data={})`` is a documented no-op placeholder
for wiring DXY / yields / silver once that data exists.

Design notes
------------
  * Session boundaries mirror ``pattern_discovery_v6.py`` exactly:
    0=Asian, 1=London, 2=NY, 3=Overlap, 4=Off, with the SAME server-time
    offset (``SERVER_UTC_OFFSET``) so columns line up with the discovery engine.
  * All "future-looking" structure features are computed causally (only past /
    current bars) so they are safe for live inference and walk-forward tests.
    The single exception — FVG confirmation — is explicitly documented inline.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# Broker-server-time alignment. MT5's Hour() returns broker server time; the
# discovery engine shifts the Python index by this many hours before bucketing
# sessions. Keep this in sync with pattern_discovery_v6.MT5_SERVER_UTC_OFFSET.
SERVER_UTC_OFFSET: int = 2

# Session id -> human label (matches pattern_discovery_v6 encoding).
SESSION_LABELS = {0: "asian", 1: "london", 2: "ny", 3: "overlap", 4: "off"}

_EPS = 1e-9


# =============================================================================
#  internal helpers
# =============================================================================
def _need(df: pd.DataFrame, cols) -> None:
    """Raise a clear error if required columns are missing."""
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise KeyError(f"features_ext: DataFrame missing required column(s): {missing}")


def _server_hour(index: pd.DatetimeIndex) -> np.ndarray:
    """Hour-of-day shifted into broker server time, as int array 0..23."""
    return ((index.hour + SERVER_UTC_OFFSET) % 24).to_numpy()


def _session_id(index: pd.DatetimeIndex) -> np.ndarray:
    """Integer session id per bar (0=Asian..4=Off), identical to discovery.

    No-overlap layout in server time:
      ASIAN 02-06:59, LONDON 07-11:59, NY 12-16:59, OVERLAP 17-20:59, else OFF.
    """
    h = _server_hour(index)
    sess = np.full(len(index), 4, dtype=np.int8)  # OFF default
    sess[(h >= 2) & (h < 7)] = 0   # ASIAN
    sess[(h >= 7) & (h < 12)] = 1  # LONDON
    sess[(h >= 12) & (h < 17)] = 2  # NY
    sess[(h >= 17) & (h < 21)] = 3  # OVERLAP
    return sess


# =============================================================================
#  (a) MARKET STRUCTURE
# =============================================================================
def add_market_structure(
    df: pd.DataFrame,
    swing_lookback: int = 5,
    swing_window: int = 50,
    wick_ratio: float = 2.0,
    sweep_window: int = 20,
) -> pd.DataFrame:
    """Append price-action / market-structure features.

    Parameters
    ----------
    swing_lookback : int
        Fractal half-width. A *confirmed* swing high at bar ``i`` is a high that
        is the strict max of the window ``[i-k, i+k]`` (k=swing_lookback). To
        stay causal, confirmation is shifted forward by ``k`` bars (a swing is
        only *known* k bars later), so no future information leaks.
    swing_window : int
        Rolling window for "most recent confirmed swing" reference levels and
        for distance-to-swing normalisation.
    wick_ratio : float
        A bar is a rejection bar when one wick is >= ``wick_ratio`` * body.
    sweep_window : int
        Lookback for the prior extreme used in liquidity-sweep detection.

    New columns
    -----------
    swing_high_flag, swing_low_flag : 1.0 on the bar that IS a confirmed
        fractal high / low (placed at the bar itself, but only set once
        confirmable — see causality note above).
    swing_high_level, swing_low_level : last confirmed swing price, forward
        filled (the "liquidity" levels structure trades off).
    dist_to_swing_high, dist_to_swing_low : (level - close) and (close - level)
        in ATR-free fractional terms (… / close), signed so positive = level is
        on the expected side.
    bos_up, bos_down : 1.0 when close breaks the most recent confirmed swing
        high (up) / low (down) — a break of structure.
    fvg_up, fvg_down : 1.0 on the middle bar of a 3-bar fair-value gap
        (bullish: low[i+1] > high[i-1]; bearish: high[i+1] < low[i-1]).
    fvg_size : signed gap size as a fraction of close (+ bullish, - bearish).
    wick_rejection : +1 long-lower-wick (bullish rejection), -1 long-upper-wick
        (bearish rejection), 0 otherwise.
    liquidity_sweep : +1 when the bar's low pierces the prior ``sweep_window``
        low but the close reclaims back above it (stop-run below support);
        -1 for the mirror case above resistance; 0 otherwise.
    """
    _need(df, ["open", "high", "low", "close"])
    out = df.copy()
    k = max(1, int(swing_lookback))

    o = out["open"].to_numpy(float)
    h = out["high"].to_numpy(float)
    low = out["low"].to_numpy(float)
    c = out["close"].to_numpy(float)
    n = len(out)

    # --- confirmed fractal swings (centered max/min), then shift to be causal -
    hi_s = out["high"]
    lo_s = out["low"]
    win = 2 * k + 1
    # centered rolling extreme: a bar is a swing high if it equals the max of
    # its symmetric window. Use center=True for detection, then shift forward k
    # so the flag only appears once the right side of the window is known.
    cen_max = hi_s.rolling(win, center=True, min_periods=win).max()
    cen_min = lo_s.rolling(win, center=True, min_periods=win).min()
    raw_sh = (hi_s >= cen_max) & cen_max.notna()
    raw_sl = (lo_s <= cen_min) & cen_min.notna()
    # causal confirmation: shift the detection forward by k bars.
    sh_flag = raw_sh.shift(k, fill_value=False)
    sl_flag = raw_sl.shift(k, fill_value=False)
    out["swing_high_flag"] = sh_flag.astype(float)
    out["swing_low_flag"] = sl_flag.astype(float)

    # last confirmed swing price (forward-filled liquidity level)
    sh_level = hi_s.shift(k).where(sh_flag).ffill()
    sl_level = lo_s.shift(k).where(sl_flag).ffill()
    out["swing_high_level"] = sh_level
    out["swing_low_level"] = sl_level

    # signed distance to the nearest relevant swing, normalised by close.
    # positive dist_to_swing_high => price still below the swing high (room up).
    out["dist_to_swing_high"] = (sh_level - out["close"]) / (out["close"] + _EPS)
    out["dist_to_swing_low"] = (out["close"] - sl_level) / (out["close"] + _EPS)

    # --- break of structure: close crosses the most recent confirmed swing ----
    bos_up = (out["close"] > sh_level) & (out["close"].shift(1) <= sh_level.shift(1))
    bos_down = (out["close"] < sl_level) & (out["close"].shift(1) >= sl_level.shift(1))
    out["bos_up"] = bos_up.fillna(False).astype(float)
    out["bos_down"] = bos_down.fillna(False).astype(float)

    # --- fair value gap (3-bar imbalance) -------------------------------------
    # The flag is placed on the MIDDLE bar i. It references bar i+1, so this
    # column is known only one bar later — fine for analysis/labelling, but a
    # live system must treat it as a lag-1 confirmation signal.
    fvg_up = np.zeros(n)
    fvg_down = np.zeros(n)
    fvg_size = np.zeros(n)
    if n >= 3:
        prev_high = h[:-2]   # bar i-1
        next_low = low[2:]   # bar i+1
        prev_low = low[:-2]
        next_high = h[2:]
        mid = slice(1, n - 1)
        bull = next_low > prev_high
        bear = next_high < prev_low
        fvg_up[mid] = bull.astype(float)
        fvg_down[mid] = bear.astype(float)
        gap = np.where(bull, next_low - prev_high,
                       np.where(bear, next_high - prev_low, 0.0))
        fvg_size[mid] = gap / (c[mid] + _EPS)
    out["fvg_up"] = fvg_up
    out["fvg_down"] = fvg_down
    out["fvg_size"] = fvg_size

    # --- wick rejection -------------------------------------------------------
    body = np.abs(c - o)
    upper_wick = h - np.maximum(o, c)
    lower_wick = np.minimum(o, c) - low
    denom = body + _EPS
    bull_rej = lower_wick >= wick_ratio * denom
    bear_rej = upper_wick >= wick_ratio * denom
    out["wick_rejection"] = np.where(bull_rej & ~bear_rej, 1.0,
                                     np.where(bear_rej & ~bull_rej, -1.0, 0.0))

    # --- liquidity sweep / stop-run -------------------------------------------
    # prior extreme strictly BEFORE the current bar (shift 1 => causal).
    prior_low = out["low"].rolling(sweep_window, min_periods=1).min().shift(1)
    prior_high = out["high"].rolling(sweep_window, min_periods=1).max().shift(1)
    swept_low = (out["low"] < prior_low) & (out["close"] > prior_low)
    swept_high = (out["high"] > prior_high) & (out["close"] < prior_high)
    out["liquidity_sweep"] = np.where(swept_low.fillna(False), 1.0,
                                      np.where(swept_high.fillna(False), -1.0, 0.0))
    return out


# =============================================================================
#  (b) TIME
# =============================================================================
def add_time_features(df: pd.DataFrame) -> pd.DataFrame:
    """Append calendar / session timing features (broker-server aligned).

    Requires a DatetimeIndex.

    New columns
    -----------
    hour_of_day      : 0..23 in BROKER SERVER time (offset by SERVER_UTC_OFFSET).
    day_of_week      : 0=Mon .. 6=Sun.
    session          : integer session id 0..4 (matches discovery engine).
    sess_asian, sess_london, sess_ny, sess_overlap : one-hot session flags
        (the 'off' session is the implicit all-zero baseline).
    minutes_since_session_open : minutes elapsed since this contiguous session
        block began (0 on the first bar of the block). For 'off' bars this is
        the time since the off-block started.

    Cyclical encodings (smooth, no midnight discontinuity):
    hour_sin, hour_cos, dow_sin, dow_cos.
    """
    if not isinstance(df.index, pd.DatetimeIndex):
        raise TypeError("add_time_features requires a DatetimeIndex.")
    out = df.copy()
    idx = out.index

    hod = _server_hour(idx)
    out["hour_of_day"] = hod.astype(np.int16)
    out["day_of_week"] = idx.dayofweek.to_numpy().astype(np.int16)

    sess = _session_id(idx)
    out["session"] = sess
    out["sess_asian"] = (sess == 0).astype(float)
    out["sess_london"] = (sess == 1).astype(float)
    out["sess_ny"] = (sess == 2).astype(float)
    out["sess_overlap"] = (sess == 3).astype(float)

    # minutes since the current session BLOCK opened. A new block starts wherever
    # the session id changes from the previous bar. Within a block we measure the
    # wall-clock delta from the block's first timestamp.
    block_id = np.concatenate([[0], np.cumsum(np.diff(sess) != 0)])
    block_id = pd.Series(block_id, index=idx)
    block_start = idx.to_series().groupby(block_id).transform("first")
    mins = (idx.to_series() - block_start).dt.total_seconds() / 60.0
    out["minutes_since_session_open"] = mins.to_numpy()

    # cyclical encodings
    out["hour_sin"] = np.sin(2 * np.pi * hod / 24.0)
    out["hour_cos"] = np.cos(2 * np.pi * hod / 24.0)
    dow = out["day_of_week"].to_numpy()
    out["dow_sin"] = np.sin(2 * np.pi * dow / 7.0)
    out["dow_cos"] = np.cos(2 * np.pi * dow / 7.0)
    return out


# =============================================================================
#  (c) RANK-NORMALIZATION
# =============================================================================
def rolling_normalize(
    df: pd.DataFrame,
    columns,
    window: int = 100,
    method: str = "zscore",
    min_periods: int | None = None,
    suffix: str | None = None,
) -> pd.DataFrame:
    """Rolling-normalise columns so values are relative to recent history.

    This is the key to "RSI high means high *vs recent*, not absolute > 70".

    Parameters
    ----------
    columns : str | iterable of str
        Column(s) to normalise. Missing columns are skipped silently so the
        helper can be pointed at an optional feature set.
    window : int
        Rolling lookback length.
    method : {"zscore", "rank"}
        * "zscore" — (x - rolling_mean) / rolling_std. Unbounded, ~N(0,1).
        * "rank"   — rolling percentile rank of the last value in the window,
                     scaled to [-1, 1] (0 = median). Robust to outliers.
    min_periods : int | None
        Minimum observations in window. Defaults to ``max(2, window // 2)``.
    suffix : str | None
        Output column suffix. Defaults to ``"_z"`` (zscore) or ``"_rank"``.

    Notes
    -----
    All windows are trailing (causal): the value at bar ``i`` uses only bars
    ``[i-window+1, i]``. Safe for walk-forward / live use.
    """
    if isinstance(columns, str):
        columns = [columns]
    if method not in ("zscore", "rank"):
        raise ValueError(f"rolling_normalize: unknown method {method!r}")
    if min_periods is None:
        min_periods = max(2, window // 2)
    if suffix is None:
        suffix = "_z" if method == "zscore" else "_rank"

    out = df.copy()
    for col in columns:
        if col not in out.columns:
            continue
        s = out[col]
        if method == "zscore":
            mean = s.rolling(window, min_periods=min_periods).mean()
            std = s.rolling(window, min_periods=min_periods).std(ddof=0)
            out[f"{col}{suffix}"] = ((s - mean) / (std + _EPS))
        else:  # rank -> percentile of the last point within its window, in [-1,1]
            def _pct_last(arr: np.ndarray) -> float:
                last = arr[-1]
                # fraction of window strictly below + half of ties => smooth rank
                below = np.sum(arr < last)
                ties = np.sum(arr == last)
                pct = (below + 0.5 * ties) / len(arr)
                return 2.0 * pct - 1.0
            out[f"{col}{suffix}"] = s.rolling(
                window, min_periods=min_periods
            ).apply(_pct_last, raw=True)
    return out


# =============================================================================
#  CROSS-ASSET FEATURES
# =============================================================================
def _clean_ext_name(name: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "_" for ch in str(name)).strip("_")


def add_cross_asset_features(df: pd.DataFrame, ext_data: dict | None = None) -> pd.DataFrame:
    """Append DXY / yields / silver / index context features.

    Intended to enrich an instrument (e.g. XAUUSD) with intermarket context:
      * DXY level / momentum (gold is broadly inverse to the dollar),
      * US 10y / 2y yields and their slope (real-rate proxy for gold),
      * silver (XAGUSD) for the gold/silver ratio and precious-metals beta.

    Parameters
    ----------
    ext_data : dict | None
        Mapping ``{name: DataFrame}`` of external series, each indexed by the
        SAME (or a reindex-compatible) DatetimeIndex as ``df``. Example::

            ext_data = {
                "dxy":   dxy_df,    # columns: close, ...
                "us10y": yields_df,
                "silver": xag_df,
            }

        Each series is reindexed onto ``df.index`` and forward-filled BEFORE
        deriving features, so a primary bar only sees external values known at
        or before that timestamp.

    Returns
    -------
    pd.DataFrame
        A copy of ``df`` with five columns per external series:
        ``ret1``, ``ret4``, ``mom_z``, ``rel_ret1``, and ``ratio_z``.
    """
    out = df.copy()
    if not ext_data:
        return out  # no-op: nothing wired yet

    base_close = out["close"].astype(float)
    base_ret1 = base_close.pct_change().replace([np.inf, -np.inf], np.nan)
    for raw_name, ext in sorted(ext_data.items(), key=lambda kv: str(kv[0]).lower()):
        if ext is None or "close" not in ext.columns:
            continue
        name = _clean_ext_name(raw_name)
        aligned = ext.sort_index().reindex(out.index).ffill()
        close = aligned["close"].astype(float)
        ret1 = close.pct_change().replace([np.inf, -np.inf], np.nan)
        ret4 = close.pct_change(4).replace([np.inf, -np.inf], np.nan)
        mom = close.pct_change(12).replace([np.inf, -np.inf], np.nan)
        mom_mean = mom.rolling(100, min_periods=25).mean()
        mom_std = mom.rolling(100, min_periods=25).std(ddof=0)
        ratio = base_close / (close + _EPS)
        ratio_mean = ratio.rolling(200, min_periods=50).mean()
        ratio_std = ratio.rolling(200, min_periods=50).std(ddof=0)

        out[f"xa_{name}_ret1"] = ret1
        out[f"xa_{name}_ret4"] = ret4
        out[f"xa_{name}_mom_z"] = (mom - mom_mean) / (mom_std + _EPS)
        out[f"xa_{name}_rel_ret1"] = base_ret1 - ret1
        out[f"xa_{name}_ratio_z"] = (ratio - ratio_mean) / (ratio_std + _EPS)
    return out


# =============================================================================
#  DISPATCHER
# =============================================================================
# Registry of feature blocks. Keys are the enable-set tokens.
_FEATURE_BLOCKS = {
    "structure": add_market_structure,
    "time": add_time_features,
    "cross_asset": add_cross_asset_features,
}
# "normalize" is handled specially (needs target columns) — see below.
_DEFAULT_NORM_COLS = ("rsi14", "atr_pct", "bb_width", "macd_norm", "vol_ratio")


def add_all_ext_features(
    df: pd.DataFrame,
    enable: set | None = None,
    *,
    norm_cols=_DEFAULT_NORM_COLS,
    norm_window: int = 100,
    norm_method: str = "zscore",
    ext_data: dict | None = None,
) -> pd.DataFrame:
    """Run every extended-feature block (or a chosen subset) over ``df``.

    Parameters
    ----------
    enable : set | None
        Subset of block tokens to run. ``None`` => run all of:
        ``{"structure", "time", "normalize", "cross_asset"}``.
        Unknown tokens raise ``ValueError`` to catch typos early.
    norm_cols : iterable of str
        Columns the ``"normalize"`` block rank-normalises. Missing columns are
        skipped, so it's safe to list indicators that may not be present.
    norm_window, norm_method :
        Forwarded to :func:`rolling_normalize`.
    ext_data : dict | None
        Forwarded to :func:`add_cross_asset_features`.

    Returns
    -------
    pd.DataFrame
        A new frame with all requested feature columns appended.
    """
    all_tokens = set(_FEATURE_BLOCKS) | {"normalize"}
    if enable is None:
        enable = set(all_tokens)
    else:
        enable = set(enable)
        unknown = enable - all_tokens
        if unknown:
            raise ValueError(
                f"add_all_ext_features: unknown enable token(s) {unknown}; "
                f"valid tokens are {sorted(all_tokens)}"
            )

    out = df.copy()
    # deterministic order; structure/time before normalize so freshly-created
    # columns could in principle be normalised too.
    if "time" in enable:
        out = add_time_features(out)
    if "structure" in enable:
        out = add_market_structure(out)
    if "normalize" in enable:
        out = rolling_normalize(
            out, columns=norm_cols, window=norm_window, method=norm_method
        )
    if "cross_asset" in enable:
        out = add_cross_asset_features(out, ext_data=ext_data)
    return out


__all__ = [
    "add_market_structure",
    "add_time_features",
    "rolling_normalize",
    "add_cross_asset_features",
    "add_all_ext_features",
    "SESSION_LABELS",
    "SERVER_UTC_OFFSET",
]
