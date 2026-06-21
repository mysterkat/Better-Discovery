"""Deterministic EA feature formulas mirrored from pattern_discovery_v6."""

from __future__ import annotations

import numpy as np
import pandas as pd


def _ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()


def _rsi(series: pd.Series, length: int = 14) -> pd.Series:
    delta = series.diff()
    gains = delta.clip(lower=0).ewm(com=length - 1, adjust=False).mean()
    losses = (-delta.clip(upper=0)).ewm(com=length - 1, adjust=False).mean()
    return 100 - 100 / (1 + gains / losses.replace(0, np.nan))


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> pd.Series:
    true_range = pd.concat(
        [high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1
    ).max(axis=1)
    return true_range.ewm(com=length - 1, adjust=False).mean()


def build_features(
    bars: pd.DataFrame,
    session_utc_offset: int = 0,
    signal_bars: dict[str, pd.DataFrame] | None = None,
) -> pd.DataFrame:
    required = {"time", "open", "high", "low", "close"}
    missing = required - set(bars.columns)
    if missing:
        raise ValueError(f"bar data missing required columns: {sorted(missing)}")
    frame = bars.copy()
    frame["time"] = pd.to_datetime(frame["time"], utc=True)
    frame = frame.set_index("time").sort_index()
    if "volume" not in frame and "tick_volume" in frame:
        frame["volume"] = frame["tick_volume"]
    close, high, low, opened = frame["close"], frame["high"], frame["low"], frame["open"]
    frame["ema20"], frame["ema50"], frame["ema200"] = _ema(close, 20), _ema(close, 50), _ema(close, 200)
    frame["rsi14"] = _rsi(close)
    frame["atr14"] = _atr(high, low, close)
    middle = close.rolling(20).mean()
    sigma = close.rolling(20).std(ddof=0)
    frame["bb_up"], frame["bb_mid"], frame["bb_lo"] = middle + 2 * sigma, middle, middle - 2 * sigma
    frame["bb_width"] = (frame["bb_up"] - frame["bb_lo"]) / middle
    macd = _ema(close, 12) - _ema(close, 26)
    frame["macd_hist"] = macd - _ema(macd, 9)
    up = (frame["ema20"] > frame["ema50"]) & (frame["ema50"] > frame["ema200"])
    down = (frame["ema20"] < frame["ema50"]) & (frame["ema50"] < frame["ema200"])
    frame["trend"] = np.where(up, 1, np.where(down, -1, 0))
    frame["rng"], frame["body"] = high - low, (close - opened).abs()
    frame["uwk"] = high - frame[["open", "close"]].max(axis=1)
    frame["lwk"] = frame[["open", "close"]].min(axis=1) - low
    frame["bull"] = (close >= opened).astype(int)
    atr = frame["atr14"].replace(0, np.nan)
    rng = frame["rng"].replace(0, np.nan)
    frame["atr_pct"], frame["rng_atr"] = frame["atr14"] / close, frame["rng"] / atr
    frame["body_pct"], frame["uwk_pct"], frame["lwk_pct"] = frame["body"] / rng, frame["uwk"] / rng, frame["lwk"] / rng
    frame["macd_norm"] = frame["macd_hist"] / atr
    hours = (frame.index.hour + session_utc_offset) % 24
    session = np.full(len(frame), 4, dtype=np.int8)
    session[(hours >= 2) & (hours < 7)] = 0
    session[(hours >= 7) & (hours < 12)] = 1
    session[(hours >= 12) & (hours < 17)] = 2
    session[(hours >= 17) & (hours < 21)] = 3
    frame["session"] = session
    frame["mtf_bull_score"] = (frame["trend"] == 1).astype(int)
    frame["mtf_bear_score"] = (frame["trend"] == -1).astype(int)
    signal_rsi_columns: list[str] = []
    for prefix, signal_bars_frame in (signal_bars or {}).items():
        signal = build_features(signal_bars_frame, session_utc_offset)
        columns = ["trend", "rsi14", "ema20", "ema50", "atr14"]
        shifted = signal[columns].shift(1).reset_index().rename(columns={"time": "signal_time"})
        merged = pd.merge_asof(
            frame.reset_index().sort_values("time"), shifted.sort_values("signal_time"),
            left_on="time", right_on="signal_time", direction="backward", suffixes=("", "_signal"),
        ).set_index("time")
        for column in columns:
            source = f"{column}_signal" if f"{column}_signal" in merged else column
            frame[f"{prefix}_{column}"] = merged[source].to_numpy()
        frame["mtf_bull_score"] += (frame[f"{prefix}_trend"] == 1).astype(int)
        frame["mtf_bear_score"] += (frame[f"{prefix}_trend"] == -1).astype(int)
        signal_rsi_columns.append(f"{prefix}_rsi14")

    volume = frame.get("volume", pd.Series(1.0, index=frame.index)).replace(0, np.nan).fillna(1)
    volume_ma = volume.rolling(20, min_periods=1).mean()
    frame["vol_ratio"] = (volume / volume_ma).clip(0, 5)
    frame["vol_body_conf"] = (frame["vol_ratio"] * frame["body_pct"]).clip(0, 5)
    price_direction = np.sign(close - opened)
    high_volume = frame["vol_ratio"] > 1.2
    frame["vol_price_div"] = np.where((price_direction < 0) & high_volume, 1, np.where((price_direction > 0) & ~high_volume, -1, 0))
    frame["bb_expanding"] = (frame["bb_width"] > frame["bb_width"].shift(3)).astype(float)
    poc_distance = np.zeros(len(frame))
    closes, highs, lows, volumes = close.to_numpy(), high.to_numpy(), low.to_numpy(), volume.to_numpy()
    last_poc = 0.0
    for index in range(100, len(frame)):
        if index % 5 == 0:
            slice_high, slice_low = highs[index - 100:index], lows[index - 100:index]
            price_range = slice_high.max() - slice_low.min()
            if price_range > 0:
                histogram, edges = np.histogram(
                    closes[index - 100:index], bins=20,
                    range=(slice_low.min(), slice_high.max()), weights=volumes[index - 100:index],
                )
                last_poc = edges[np.argmax(histogram)] + (edges[1] - edges[0]) / 2
        if last_poc > 0:
            poc_distance[index] = (closes[index] - last_poc) / (closes[index] + 1e-9)
    frame["poc_dist"] = poc_distance
    previous_bias = np.zeros(len(frame))
    changes = np.r_[0, np.where(np.diff(session) != 0)[0] + 1, len(frame)]
    close_values = close.to_numpy()
    for index in range(1, len(changes) - 1):
        previous_start, previous_end = changes[index - 1], changes[index]
        bias = np.sign(close_values[previous_end - 1] - close_values[previous_start])
        previous_bias[changes[index]:changes[index + 1]] = bias
    frame["prev_sess_bias"] = previous_bias

    low14, high14 = low.rolling(14, min_periods=1).min(), high.rolling(14, min_periods=1).max()
    frame["stoch_k"] = (100 * (close - low14) / (high14 - low14 + 1e-9)).clip(0, 100)
    frame["stoch_d"] = frame["stoch_k"].rolling(3, min_periods=1).mean()
    upper_wick = high - pd.concat([opened, close], axis=1).max(axis=1)
    lower_wick = pd.concat([opened, close], axis=1).min(axis=1) - low
    frame["pin_bar"] = (pd.concat([upper_wick, lower_wick], axis=1).max(axis=1) / rng).clip(0, 1).fillna(0)
    frame["inside_bar"] = ((high < high.shift(1)) & (low > low.shift(1))).astype(float)
    frame["outside_bar"] = ((high > high.shift(1)) & (low < low.shift(1))).astype(float)
    ltf_slope = frame["rsi14"] - frame["rsi14"].shift(5)
    htf_rsi = frame[signal_rsi_columns[-1]] if signal_rsi_columns else pd.Series(50, index=frame.index)
    htf_slope = (htf_rsi - htf_rsi.shift(3)).fillna(0)
    htf_div = np.zeros(len(frame))
    htf_div[(ltf_slope.to_numpy() > 2) & (htf_slope.to_numpy() < -1)] = 1
    htf_div[(ltf_slope.to_numpy() < -2) & (htf_slope.to_numpy() > 1)] = -1
    frame["htf_div"] = htf_div
    returns = close.pct_change()
    rolling_std = returns.rolling(20, min_periods=5).std().replace(0, np.nan)
    frame["rolling_sharpe"] = (returns.rolling(20, min_periods=5).mean() / rolling_std).clip(-3, 3).fillna(0)
    swing_high, swing_low = high.rolling(25, min_periods=5).max(), low.rolling(25, min_periods=5).min()
    supply_demand = np.zeros(len(frame))
    supply_demand[((close - swing_low) / (atr + 1e-9)).to_numpy() < 1] = 1
    supply_demand[((swing_high - close) / (atr + 1e-9)).to_numpy() < 1] = -1
    frame["sd_zone"] = supply_demand
    typical = (high + low + close) / 3
    vwap = (typical * volume).rolling(96, min_periods=1).sum() / volume.rolling(96, min_periods=1).sum()
    frame["vwap_dist"] = ((close - vwap) / vwap * 100).clip(-5, 5).fillna(0)

    atr_median = frame["atr_pct"].rolling(200, min_periods=50).median()
    high_vol, low_vol = frame["atr_pct"] > atr_median * 1.1, frame["atr_pct"] < atr_median * 0.9
    squeeze = frame["bb_width"] < frame["bb_width"].rolling(100, min_periods=20).quantile(0.25)
    wide = frame["bb_width"] > frame["bb_width"].rolling(100, min_periods=20).quantile(0.75)
    regime = np.full(len(frame), 4, dtype=np.int8)
    regime[(squeeze & low_vol).to_numpy()] = 2
    regime[((~up) & (~down) & wide).to_numpy()] = 3
    regime[(down & high_vol).to_numpy()] = 1
    regime[(up & high_vol).to_numpy()] = 0
    frame["regime"] = regime
    return frame.fillna(0)
