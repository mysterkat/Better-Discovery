"""
labels_ext.py — ALPHA labeling, not trend labeling
==================================================

Standalone, dependency-light (pure pandas / numpy) labeling helpers whose
single purpose is to separate *edge* (alpha) from *drift* (the underlying
uptrend/bull-bias).  In a persistently rising instrument (XAUUSD, equity
indices) a naive "did price go up over the next N bars?" label is dominated
by the trend: a coin-flip entry looks ~55-60% accurate simply because the
market drifts up.  Any discovery engine trained on such labels learns the
trend, not a repeatable signal, and the edge evaporates out-of-sample.

This module provides three labelers that neutralise that artifact:

  (a) triple_barrier            — López de Prado triple-barrier method with
                                  ATR-volatility-scaled TP/SL barriers; labels
                                  by *which barrier is touched first*, so the
                                  label reflects path-dependent risk/reward,
                                  not a fixed-horizon point return.
  (b) beta_neutral_forward_return — forward return with the drift removed
                                  (subtract a market proxy, or self-detrend by
                                  subtracting the same series' rolling forward
                                  drift).  Kills "long-bias == uptrend".
  (c) forward_return            — plain N-bar forward return helper (the raw,
                                  trend-contaminated baseline, exposed for
                                  comparison / building blocks).

Conventions (match the rest of backend/toolkit):
  * Input `df` carries lowercase OHLC columns: open / high / low / close,
    indexed by bar order (RangeIndex or DatetimeIndex — both fine).
  * ATR is computed locally (Wilder-style SMA of True Range) so this file has
    no dependency on the discovery engine's feature pipeline.
  * Returns are simple (arithmetic) returns unless noted; "R" = realized
    profit/loss expressed in units of the initial stop distance (risk).

References:
  Marcos López de Prado, "Advances in Financial Machine Learning" (2018),
  ch. 3 — Labeling: the triple-barrier method and meta-labeling.

Pure pandas/numpy. No side effects. No file IO.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# =============================================================================
#  Internal helpers
# =============================================================================
def _atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    """Average True Range (Wilder-style rolling mean of True Range).

    True Range = max(high-low, |high-prev_close|, |low-prev_close|).
    Used to scale the triple barrier so that a "1 ATR" stop means the same
    *probability of being hit by noise* regardless of the current vol regime.
    """
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.rolling(n, min_periods=1).mean()


def _ohlc(df: pd.DataFrame):
    """Extract lowercase OHLC numpy arrays; tolerate already-lowercased frames.

    Returns (open, high, low, close) as float ndarrays.
    Raises KeyError with a clear message if a required column is missing.
    """
    cols = {c.lower(): c for c in df.columns}
    need = ["open", "high", "low", "close"]
    missing = [k for k in need if k not in cols]
    if missing:
        raise KeyError(
            f"labels_ext: df is missing OHLC column(s) {missing}; "
            f"have {list(df.columns)}"
        )
    return (
        df[cols["open"]].to_numpy(dtype=float),
        df[cols["high"]].to_numpy(dtype=float),
        df[cols["low"]].to_numpy(dtype=float),
        df[cols["close"]].to_numpy(dtype=float),
    )


# =============================================================================
#  (c) forward_return — raw fixed-horizon return (baseline / building block)
# =============================================================================
def forward_return(
    df: pd.DataFrame,
    fwd_bars: int,
    price_col: str = "close",
    log: bool = False,
) -> pd.Series:
    """N-bar forward (look-ahead) return of `price_col`.

    For each bar i: r_i = price[i + fwd_bars] / price[i] - 1   (simple), or
                     r_i = ln(price[i + fwd_bars] / price[i])   (log=True).

    This is the *trend-contaminated* baseline — in a rising market its mean is
    positive purely from drift. Exposed as a helper and as the raw input to
    `beta_neutral_forward_return`. The last `fwd_bars` rows are NaN (no future
    data). NOT shifted into the past: index i holds the return realised *after*
    bar i, so it is a forward label, never a feature — do not feed it as a
    predictor (look-ahead leakage).

    Parameters
    ----------
    df        : DataFrame with `price_col`.
    fwd_bars  : horizon in bars (>= 1).
    price_col : column to use (default "close").
    log       : log-returns instead of simple.

    Returns
    -------
    pd.Series aligned to df.index, name "fwd_ret_{fwd_bars}".
    """
    if fwd_bars < 1:
        raise ValueError("fwd_bars must be >= 1")
    col = price_col if price_col in df.columns else price_col.lower()
    if col not in df.columns:
        raise KeyError(f"labels_ext: price_col '{price_col}' not in df")
    p = df[col].astype(float)
    fwd = p.shift(-fwd_bars)
    if log:
        out = np.log(fwd / p)
    else:
        out = fwd / p - 1.0
    out.name = f"fwd_ret_{fwd_bars}"
    return out


# =============================================================================
#  (b) beta_neutral_forward_return — forward return MINUS drift
# =============================================================================
def beta_neutral_forward_return(
    df: pd.DataFrame,
    fwd_bars: int,
    market_series: pd.Series | None = None,
    price_col: str = "close",
    drift_window: int = 500,
    beta: float = 1.0,
    log: bool = False,
) -> pd.Series:
    """Forward return with drift removed — the anti-trend-artifact label.

    The raw forward return ``r`` is positive on average in a bull market even
    for random entries. This subtracts an expected-drift term so the residual
    measures *excess* return (alpha) attributable to the entry condition, not
    to the market going up:

        excess_i = r_i  -  beta * drift_i

    Two modes for ``drift_i``:

      * market_series GIVEN  (beta-neutral / market-relative):
            drift_i = forward return of the market proxy over the same horizon,
            scaled by `beta`. excess = asset_fwd_ret - beta * market_fwd_ret.
            Use this when you have an index/benchmark to neutralise against
            (e.g. subtract XAU spot drift from a XAU-correlated strategy).

      * market_series is None (self-detrend):
            drift_i = the series' OWN trailing average per-bar drift, projected
            forward over `fwd_bars` and measured CAUSALLY (a rolling mean of
            past 1-bar returns over `drift_window`, shifted by 1 so bar i uses
            only information available at/before i). This removes the local
            trend baseline without any external data, killing the
            "long-bias is just the uptrend" effect.

    Causality note: the drift estimate uses only PAST bars (trailing window,
    shifted +1), while the realised return uses FUTURE bars. The label is
    therefore (past-conditioned drift) subtracted from (true forward move) —
    no future leakage enters the drift term.

    Parameters
    ----------
    df            : DataFrame with `price_col`.
    fwd_bars      : forward horizon in bars (>= 1).
    market_series : optional benchmark price series (any index aligned to df,
                    or same length). If None, self-detrend.
    price_col     : asset price column (default "close").
    drift_window  : look-back window (bars) for the self-detrend drift estimate.
    beta          : sensitivity to the market drift (ignored in self-detrend
                    where it is implicitly 1).
    log           : use log returns throughout.

    Returns
    -------
    pd.Series aligned to df.index, name "excess_ret_{fwd_bars}".
    Last `fwd_bars` rows are NaN; leading rows may be NaN until the drift
    window fills.
    """
    if fwd_bars < 1:
        raise ValueError("fwd_bars must be >= 1")

    r = forward_return(df, fwd_bars, price_col=price_col, log=log)

    if market_series is not None:
        # ---- market-relative / beta-neutral mode ----------------------------
        mkt = pd.Series(np.asarray(market_series, dtype=float))
        if len(mkt) != len(df):
            raise ValueError(
                f"market_series length {len(mkt)} != df length {len(df)}"
            )
        mkt.index = df.index
        mkt_fwd = mkt.shift(-fwd_bars)
        if log:
            drift = np.log(mkt_fwd / mkt)
        else:
            drift = mkt_fwd / mkt - 1.0
        excess = r - beta * drift
    else:
        # ---- self-detrend mode ----------------------------------------------
        col = price_col if price_col in df.columns else price_col.lower()
        p = df[col].astype(float)
        if log:
            one_bar = np.log(p / p.shift(1))
        else:
            one_bar = p / p.shift(1) - 1.0
        # Causal trailing mean per-bar drift, then project over the horizon.
        # shift(1) so bar i's drift uses only bars <= i-1 (no peeking).
        mean_bar = one_bar.rolling(drift_window, min_periods=max(2, drift_window // 5)).mean().shift(1)
        drift = mean_bar * fwd_bars
        excess = r - drift

    excess.name = f"excess_ret_{fwd_bars}"
    return excess


# =============================================================================
#  (a) triple_barrier — López de Prado, ATR-vol-scaled barriers
# =============================================================================
def triple_barrier(
    df: pd.DataFrame,
    tp_atr_mult: float = 2.0,
    sl_atr_mult: float = 1.0,
    max_hold: int = 20,
    atr_n: int = 14,
    side: int = 1,
    entry: str = "close",
) -> pd.DataFrame:
    """López de Prado triple-barrier labels with ATR-volatility-scaled barriers.

    For every bar i we open a hypothetical position at `entry` price and walk
    forward up to `max_hold` bars, watching three barriers:

        * upper (take-profit) : entry + side * tp_atr_mult * ATR_i
        * lower (stop-loss)   : entry - side * sl_atr_mult * ATR_i
        * vertical (timeout)  : reached at i + max_hold bars

    The label is set by WHICHEVER HORIZONTAL BARRIER IS TOUCHED FIRST:

        +1  take-profit hit first   (signal "worked")
        -1  stop-loss hit first     (signal "failed")
         0  neither — timed out at the vertical barrier

    Scaling the barriers by ATR (not fixed pips) makes the labeling
    vol-stationary: a TP/SL of "k * ATR" has roughly constant hit-probability
    under noise across calm and violent regimes, so labels are comparable
    through time. This is the core trick that lets a model learn *edge* rather
    than *volatility regime*. The realized-R output further normalises P&L by
    the initial stop distance, so outcomes are directly comparable bar-to-bar.

    Path resolution within a bar
    -----------------------------
    Intrabar we cannot know whether the high or the low printed first. We adopt
    the CONSERVATIVE convention: if BOTH barriers are breached inside the same
    bar, assume the STOP was hit first (label -1). This avoids optimistic bias
    — the standard, defensible choice for strategy research.

    Look-ahead safety
    ------------------
    Barriers are sized from ATR_i (info known AT bar i) and the walk uses only
    bars > i. The outputs are forward LABELS (targets), never features. The
    last bars (those whose vertical barrier exceeds the data end) are still
    labeled using whatever future is available, with `bars_to_hit` capped and a
    `truncated` flag set so callers can drop right-censored rows if desired.

    Parameters
    ----------
    df          : DataFrame with lowercase OHLC (open/high/low/close).
    tp_atr_mult : take-profit distance in ATR units (> 0).
    sl_atr_mult : stop-loss distance in ATR units (> 0).
    max_hold    : vertical-barrier horizon in bars (>= 1).
    atr_n       : ATR look-back length (default 14).
    side        : +1 for long entries, -1 for short. The label semantics
                  (+1 == TP hit, -1 == SL hit) are expressed relative to `side`,
                  so a short whose price falls still gets label +1.
    entry       : price column the position is opened at (default "close";
                  use "open" to model next-bar-open fills).

    Returns
    -------
    pd.DataFrame aligned to df.index with columns:
        label        : int8  {+1 TP-first, -1 SL-first, 0 timeout}
        bars_to_hit  : int   bars from entry to the resolving barrier
                             (== max_hold on timeout)
        ret_R        : float realized return in R-multiples (P&L / initial
                             stop distance); +tp_atr_mult/sl_atr_mult on a clean
                             TP, -1.0 on a clean SL, fractional on timeout.
        ret          : float realized simple price return of the trade.
        exit_price   : float price at the resolving barrier (barrier level on a
                             clean hit; close at timeout).
        truncated    : bool  True if the walk hit the data end before resolving
                             (right-censored — consider dropping for training).

    Notes
    -----
    Implementation is an explicit forward scan (O(n * max_hold)). For typical
    discovery horizons (max_hold <= a few hundred bars) this is fast enough and
    keeps the barrier logic transparent and auditable, which matters more than
    micro-optimisation for a labeling primitive.
    """
    if tp_atr_mult <= 0 or sl_atr_mult <= 0:
        raise ValueError("tp_atr_mult and sl_atr_mult must be > 0")
    if max_hold < 1:
        raise ValueError("max_hold must be >= 1")
    if side not in (1, -1):
        raise ValueError("side must be +1 (long) or -1 (short)")

    o, h, l, c = _ohlc(df)
    n = len(df)

    high_s = pd.Series(h)
    low_s = pd.Series(l)
    close_s = pd.Series(c)
    atr = _atr(high_s, low_s, close_s, atr_n).to_numpy(dtype=float)

    ecol = entry if entry in df.columns else entry.lower()
    if ecol not in df.columns:
        raise KeyError(f"labels_ext: entry column '{entry}' not in df")
    entry_px = df[ecol].to_numpy(dtype=float)

    label = np.zeros(n, dtype=np.int8)
    bars_to_hit = np.full(n, max_hold, dtype=np.int64)
    ret_R = np.full(n, np.nan, dtype=float)
    ret = np.full(n, np.nan, dtype=float)
    exit_price = np.full(n, np.nan, dtype=float)
    truncated = np.zeros(n, dtype=bool)

    for i in range(n):
        a = atr[i]
        e = entry_px[i]
        if not np.isfinite(a) or a <= 0 or not np.isfinite(e):
            # Cannot size barriers (warm-up / bad data) — leave as timeout NaN.
            truncated[i] = True
            continue

        tp_dist = tp_atr_mult * a
        sl_dist = sl_atr_mult * a
        if side == 1:
            tp_level = e + tp_dist
            sl_level = e - sl_dist
        else:
            tp_level = e - tp_dist
            sl_level = e + sl_dist

        end = min(i + max_hold, n - 1)
        resolved = False
        for j in range(i + 1, end + 1):
            hj = h[j]
            lj = l[j]
            if side == 1:
                hit_tp = hj >= tp_level
                hit_sl = lj <= sl_level
            else:
                hit_tp = lj <= tp_level
                hit_sl = hj >= sl_level

            if hit_tp or hit_sl:
                # Conservative: simultaneous breach -> stop assumed first.
                if hit_sl:
                    label[i] = -1
                    exit_price[i] = sl_level
                    ret_R[i] = -1.0
                else:
                    label[i] = 1
                    exit_price[i] = tp_level
                    ret_R[i] = tp_atr_mult / sl_atr_mult
                bars_to_hit[i] = j - i
                ret[i] = side * (exit_price[i] / e - 1.0)
                resolved = True
                break

        if not resolved:
            # Timeout (vertical barrier) or right-censored end of data.
            bars_to_hit[i] = end - i
            exit_price[i] = c[end]
            px_ret = side * (c[end] - e)
            ret[i] = side * (c[end] / e - 1.0)
            ret_R[i] = px_ret / sl_dist  # fractional R at timeout
            label[i] = 0
            if (i + max_hold) > (n - 1):
                truncated[i] = True

    return pd.DataFrame(
        {
            "label": label,
            "bars_to_hit": bars_to_hit,
            "ret_R": ret_R,
            "ret": ret,
            "exit_price": exit_price,
            "truncated": truncated,
        },
        index=df.index,
    )


__all__ = [
    "triple_barrier",
    "beta_neutral_forward_return",
    "forward_return",
]
