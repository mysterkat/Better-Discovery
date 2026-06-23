from __future__ import annotations

import numpy as np
import pandas as pd

from ..local_replay.features import build_features
from .models import HypothesisSpec


def _merge_context(primary: pd.DataFrame, context: pd.DataFrame, prefix: str) -> pd.DataFrame:
    features = build_features(context)
    selected = features[["trend", "ema50", "ema200", "atr14", "atr_pct"]].shift(1).reset_index()
    selected = selected.rename(columns={
        "time": f"{prefix}_time",
        "trend": f"{prefix}_trend",
        "ema50": f"{prefix}_ema50",
        "ema200": f"{prefix}_ema200",
        "atr14": f"{prefix}_atr14",
        "atr_pct": f"{prefix}_atr_pct",
    })
    merged = pd.merge_asof(
        primary.reset_index().sort_values("time"),
        selected.sort_values(f"{prefix}_time"),
        left_on="time",
        right_on=f"{prefix}_time",
        direction="backward",
    )
    return merged.drop(columns=[f"{prefix}_time"]).set_index("time")


def build_base_frame(
    bars: pd.DataFrame,
    contexts: dict[str, pd.DataFrame],
    context_timeframes: tuple[str, ...] = ("h1", "h4"),
) -> pd.DataFrame:
    frame = build_features(bars)
    for prefix in context_timeframes:
        if prefix not in contexts:
            raise ValueError(f"missing required context bars: {prefix}")
        frame = _merge_context(frame, contexts[prefix], prefix)
    return frame


def apply_signal_rules(base_frame: pd.DataFrame, strategy: HypothesisSpec) -> pd.DataFrame:
    frame = base_frame.copy()
    params = strategy.parameters
    direction = pd.Series(0, index=frame.index, dtype="int8")
    stop_distance = pd.Series(np.nan, index=frame.index, dtype=float)
    target_distance = pd.Series(np.nan, index=frame.index, dtype=float)
    target_price = pd.Series(np.nan, index=frame.index, dtype=float)
    trail_atr = pd.Series(0.0, index=frame.index, dtype=float)
    max_hold = pd.Series(0, index=frame.index, dtype="int32")
    close, high, low, atr = frame["close"], frame["high"], frame["low"], frame["atr14"]
    h1_up = frame["h1_trend"] > 0
    h1_down = frame["h1_trend"] < 0
    h4_up = frame["h4_trend"] > 0
    h4_down = frame["h4_trend"] < 0

    if strategy.lineage == "time_series_breakout":
        lookback = int(params["channel_bars"])
        prior_high = high.shift(1).rolling(lookback, min_periods=lookback).max()
        prior_low = low.shift(1).rolling(lookback, min_periods=lookback).min()
        direction[(close > prior_high) & h1_up & h4_up] = 1
        direction[(close < prior_low) & h1_down & h4_down] = -1
        stop_distance[:] = atr * float(params["atr_stop"])
        trail_atr[:] = float(params["atr_trail"])
        max_hold[:] = int(params["max_hold_bars"])

    elif strategy.lineage == "session_range_breakout":
        range_start = int(params["range_start_utc"])
        range_end = int(params["range_end_utc"])
        if not (0 <= range_start < range_end <= 24):
            raise ValueError("range_start_utc/range_end_utc must define a non-wrapping UTC window")
        hours = pd.Series(frame.index.hour, index=frame.index)
        source_mask = (hours >= range_start) & (hours < range_end)
        dates = frame.index.normalize()
        completed_range_high = high.where(source_mask).groupby(dates).transform("max")
        entry_start = int(params["session_start_utc"])
        entry_end = int(params["session_end_utc"])
        entry_window = (hours >= entry_start) & (hours < entry_end)
        crossed = (close > completed_range_high) & (close.shift(1) <= completed_range_high)
        raw_long = crossed & entry_window & h1_up & h4_up
        if bool(params.get("first_breakout_per_day", True)):
            raw_long &= raw_long.groupby(dates).cumsum() == 1
        direction[raw_long] = 1
        stop_distance[:] = atr * float(params["atr_stop"])
        trail_atr[:] = float(params["atr_trail"])
        max_hold[:] = int(params["max_hold_bars"])

    elif strategy.lineage == "trend_pullback":
        ema = frame[f"ema{int(params['ema_length'])}"]
        threshold = atr * float(params["pullback_atr"])
        rsi_trigger = float(params["rsi_trigger"])
        long_cross = (frame["rsi14"].shift(1) <= rsi_trigger) & (frame["rsi14"] > rsi_trigger)
        short_level = 100.0 - rsi_trigger
        short_cross = (frame["rsi14"].shift(1) >= short_level) & (frame["rsi14"] < short_level)
        direction[(low <= ema + threshold) & (close > ema) & long_cross & h1_up & h4_up] = 1
        direction[(high >= ema - threshold) & (close < ema) & short_cross & h1_down & h4_down] = -1
        stop_distance[:] = atr * float(params["atr_stop"])
        target_distance[:] = stop_distance * float(params["reward_risk"])
        max_hold[:] = 96

    elif strategy.lineage == "volatility_expansion":
        lookback = int(params["lookback"])
        quantile = float(params["squeeze_quantile"])
        width_threshold = frame["bb_width"].rolling(200, min_periods=100).quantile(quantile)
        squeezed = frame["bb_width"].shift(1) <= width_threshold.shift(1)
        prior_high = high.shift(1).rolling(lookback, min_periods=lookback).max()
        prior_low = low.shift(1).rolling(lookback, min_periods=lookback).min()
        volume_ok = frame["vol_ratio"] >= float(params["volume_ratio"])
        direction[squeezed & volume_ok & (close > prior_high) & ~h1_down] = 1
        direction[squeezed & volume_ok & (close < prior_low) & ~h1_up] = -1
        stop_distance[:] = atr * float(params["atr_stop"])
        target_distance[:] = stop_distance * float(params["reward_risk"])
        max_hold[:] = max(32, lookback * 2)

    elif strategy.lineage == "regime_mean_reversion":
        length = int(params["z_length"])
        mean = close.rolling(length, min_periods=length).mean()
        sigma = close.rolling(length, min_periods=length).std(ddof=0).replace(0, np.nan)
        zscore = (close - mean) / sigma
        h1_gap = (frame["h1_ema50"] - frame["h1_ema200"]).abs()
        flat_context = h1_gap / frame["h1_atr14"].replace(0, np.nan) < 0.75
        extreme = float(params["rsi_extreme"])
        direction[(zscore <= -float(params["z_entry"])) & (frame["rsi14"] <= extreme) & flat_context] = 1
        direction[(zscore >= float(params["z_entry"])) & (frame["rsi14"] >= 100 - extreme) & flat_context] = -1
        stop_distance[:] = atr * float(params["atr_stop"])
        target_price[:] = mean
        max_hold[:] = int(params["max_hold_bars"])

    else:  # pragma: no cover - guarded by the model literal
        raise ValueError(f"unsupported lineage: {strategy.lineage}")

    mode = str(params.get("direction_mode", "both"))
    if mode == "long_only":
        direction[direction < 0] = 0
    elif mode == "short_only":
        direction[direction > 0] = 0
    elif mode != "both":
        raise ValueError(f"unsupported direction_mode: {mode}")

    start_hour = int(params.get("session_start_utc", 0))
    end_hour = int(params.get("session_end_utc", 24))
    if not (0 <= start_hour <= 23 and 1 <= end_hour <= 24 and start_hour < end_hour):
        raise ValueError("session_start_utc/session_end_utc must define a non-wrapping UTC window")
    hours = pd.Series(frame.index.hour, index=frame.index)
    direction[~((hours >= start_hour) & (hours < end_hour))] = 0

    volatility_filter = str(params.get("volatility_filter", "none"))
    if volatility_filter == "h4_above_60d_median":
        median = frame["h4_atr_pct"].rolling(60 * 96, min_periods=20 * 96).median()
        direction[~(frame["h4_atr_pct"] > median)] = 0
    elif volatility_filter != "none":
        raise ValueError(f"unsupported volatility_filter: {volatility_filter}")

    # Optional closed-bar entry-quality filters. These only remove signals and do
    # not alter the registered exit model, which keeps entry and exit evidence separate.
    long_signal = direction > 0
    if "breakout_body_min" in params:
        direction[long_signal & (frame["body_pct"] < float(params["breakout_body_min"]))] = 0
    long_signal = direction > 0
    if "breakout_close_location_min" in params:
        close_location = (close - low) / frame["rng"].replace(0, np.nan)
        direction[
            long_signal & (close_location < float(params["breakout_close_location_min"]))
        ] = 0
    long_signal = direction > 0
    if "breakout_range_atr_min" in params:
        direction[long_signal & (frame["rng_atr"] < float(params["breakout_range_atr_min"]))] = 0
    long_signal = direction > 0
    if "rsi_min_long" in params:
        direction[long_signal & (frame["rsi14"] < float(params["rsi_min_long"]))] = 0
    long_signal = direction > 0
    if "macd_norm_min_long" in params:
        direction[long_signal & (frame["macd_norm"] < float(params["macd_norm_min_long"]))] = 0
    long_signal = direction > 0
    if "previous_session_bias_min" in params:
        direction[
            long_signal & (frame["prev_sess_bias"] < float(params["previous_session_bias_min"]))
        ] = 0

    frame["signal_direction"] = direction
    frame["stop_distance"] = stop_distance
    frame["target_distance"] = target_distance
    frame["signal_target_price"] = target_price
    frame["trail_atr"] = trail_atr
    frame["max_hold_bars"] = max_hold
    return frame.replace([np.inf, -np.inf], np.nan).fillna(0)


def build_signal_frame(
    bars: pd.DataFrame,
    contexts: dict[str, pd.DataFrame],
    strategy: HypothesisSpec,
) -> pd.DataFrame:
    base = build_base_frame(bars, contexts, strategy.context_timeframes)
    return apply_signal_rules(base, strategy)
