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


def _context_masks(
    frame: pd.DataFrame,
    mode: str,
) -> tuple[pd.Series, pd.Series]:
    h1_up = frame["h1_trend"] > 0
    h1_down = frame["h1_trend"] < 0
    h4_up = frame["h4_trend"] > 0
    h4_down = frame["h4_trend"] < 0
    all_ok = pd.Series(True, index=frame.index)
    if mode == "trend_aligned":
        return h1_up & h4_up, h1_down & h4_down
    if mode == "avoid_h1_h4_opposite":
        return ~h1_down & ~h4_down, ~h1_up & ~h4_up
    if mode == "avoid_h4_opposite":
        return ~h4_down, ~h4_up
    if mode == "h1_turn_with_h4":
        return h1_up & ~h4_down, h1_down & ~h4_up
    if mode == "none":
        return all_ok, all_ok
    raise ValueError(f"unsupported context_filter: {mode}")


def _previous_day_levels(frame: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    dates = pd.Series(frame.index.normalize(), index=frame.index)
    daily = frame.groupby(dates).agg({"high": "max", "low": "min"})
    return dates.map(daily["high"].shift(1)), dates.map(daily["low"].shift(1))


def _day_open(frame: pd.DataFrame, opened: pd.Series) -> pd.Series:
    dates = pd.Series(frame.index.normalize(), index=frame.index)
    return opened.groupby(dates).transform("first")


def _one_signal_per_day(mask: pd.Series, frame: pd.DataFrame) -> pd.Series:
    dates = pd.Series(frame.index.normalize(), index=frame.index)
    return mask & (mask.groupby(dates).cumsum() == 1)


def _parse_weekdays(raw: str) -> set[int]:
    values: set[int] = set()
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        value = int(item)
        if value < 0 or value > 6:
            raise ValueError("weekdays must use 0=Monday through 6=Sunday")
        values.add(value)
    return values or {0, 1, 2, 3, 4}


def _regime_mask(frame: pd.DataFrame, mode: str) -> pd.Series:
    regime = frame.get("regime", pd.Series(4, index=frame.index))
    if mode == "any":
        return pd.Series(True, index=frame.index)
    if mode == "trend":
        return regime.isin([0, 1])
    if mode == "compression":
        return regime == 2
    if mode == "range_or_transition":
        return regime.isin([3, 4])
    raise ValueError(f"unsupported regime_mode: {mode}")


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
    opened = frame.get("open", close)
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

    elif strategy.lineage == "liquidity_sweep_reclaim":
        lookback = int(params["sweep_lookback"])
        prior_high = high.shift(1).rolling(lookback, min_periods=lookback).max()
        prior_low = low.shift(1).rolling(lookback, min_periods=lookback).min()
        penetration = atr * float(params["penetration_atr"])
        reclaim = atr * float(params["reclaim_buffer_atr"])
        long_context, short_context = _context_masks(
            frame, str(params.get("context_filter", "avoid_h4_opposite"))
        )
        close_location = (close - low) / frame["rng"].replace(0, np.nan)
        upper_location = (high - close) / frame["rng"].replace(0, np.nan)
        wick_min = float(params["wick_reject_min"])
        raw_long = (
            (low < prior_low - penetration)
            & (close > prior_low + reclaim)
            & (frame["lwk_pct"] >= wick_min)
            & (close_location >= float(params.get("close_location_min", 0.45)))
            & long_context
        )
        raw_short = (
            (high > prior_high + penetration)
            & (close < prior_high - reclaim)
            & (frame["uwk_pct"] >= wick_min)
            & (upper_location >= float(params.get("close_location_min", 0.45)))
            & short_context
        )
        if bool(params.get("first_signal_per_day", False)):
            raw_long = _one_signal_per_day(raw_long, frame)
            raw_short = _one_signal_per_day(raw_short, frame)
        direction[raw_long] = 1
        direction[raw_short] = -1
        stop_distance[:] = atr * float(params["atr_stop"])
        target_distance[:] = stop_distance * float(params["reward_risk"])
        max_hold[:] = int(params["max_hold_bars"])

    elif strategy.lineage == "failed_breakout_reversal":
        lookback = int(params["channel_bars"])
        prior_high = high.shift(1).rolling(lookback, min_periods=lookback).max()
        prior_low = low.shift(1).rolling(lookback, min_periods=lookback).min()
        break_buffer = atr * float(params["break_atr"])
        close_back = atr * float(params["close_back_atr"])
        long_context, short_context = _context_masks(
            frame, str(params.get("context_filter", "none"))
        )
        raw_short = (
            (high > prior_high + break_buffer)
            & (close < prior_high - close_back)
            & (opened > close)
            & short_context
        )
        raw_long = (
            (low < prior_low - break_buffer)
            & (close > prior_low + close_back)
            & (close > opened)
            & long_context
        )
        direction[raw_long] = 1
        direction[raw_short] = -1
        stop_distance[:] = atr * float(params["atr_stop"])
        target_distance[:] = stop_distance * float(params["reward_risk"])
        max_hold[:] = int(params["max_hold_bars"])

    elif strategy.lineage == "prior_day_level_continuation":
        previous_high, previous_low = _previous_day_levels(frame)
        buffer = atr * float(params["break_buffer_atr"])
        long_level = previous_high + buffer
        short_level = previous_low - buffer
        long_context, short_context = _context_masks(
            frame, str(params.get("context_filter", "avoid_h4_opposite"))
        )
        raw_long = (close > long_level) & (close.shift(1) <= long_level) & long_context
        raw_short = (close < short_level) & (close.shift(1) >= short_level) & short_context
        if bool(params.get("first_signal_per_day", True)):
            raw_long = _one_signal_per_day(raw_long, frame)
            raw_short = _one_signal_per_day(raw_short, frame)
        direction[raw_long] = 1
        direction[raw_short] = -1
        stop_distance[:] = atr * float(params["atr_stop"])
        target_distance[:] = stop_distance * float(params["reward_risk"])
        max_hold[:] = int(params["max_hold_bars"])

    elif strategy.lineage == "volatility_spike_reversal":
        close_location = (close - low) / frame["rng"].replace(0, np.nan)
        style = str(params.get("spike_style", "capitulation"))
        range_ok = frame["rng_atr"] >= float(params["spike_range_atr"])
        body_ok = frame["body_pct"] >= float(params["body_min"])
        extreme = float(params["rsi_extreme"])
        long_context, short_context = _context_masks(
            frame, str(params.get("context_filter", "none"))
        )
        if style == "wick_reject":
            raw_long = (
                range_ok
                & (frame["lwk_pct"] >= float(params["wick_min"]))
                & (close_location >= float(params["close_location_min"]))
                & (frame["rsi14"] <= extreme)
                & long_context
            )
            raw_short = (
                range_ok
                & (frame["uwk_pct"] >= float(params["wick_min"]))
                & (close_location <= 1.0 - float(params["close_location_min"]))
                & (frame["rsi14"] >= 100.0 - extreme)
                & short_context
            )
        elif style == "capitulation":
            raw_long = range_ok & body_ok & (close < opened) & (frame["rsi14"] <= extreme) & long_context
            raw_short = range_ok & body_ok & (close > opened) & (frame["rsi14"] >= 100.0 - extreme) & short_context
        else:
            raise ValueError(f"unsupported spike_style: {style}")
        direction[raw_long] = 1
        direction[raw_short] = -1
        stop_distance[:] = atr * float(params["atr_stop"])
        target_distance[:] = stop_distance * float(params["reward_risk"])
        max_hold[:] = int(params["max_hold_bars"])

    elif strategy.lineage == "opening_range_continuation_reversal":
        range_start = int(params["range_start_utc"])
        range_end = int(params["range_end_utc"])
        if not (0 <= range_start < range_end <= 24):
            raise ValueError("range_start_utc/range_end_utc must define a non-wrapping UTC window")
        hours = pd.Series(frame.index.hour, index=frame.index)
        dates = frame.index.normalize()
        source_mask = (hours >= range_start) & (hours < range_end)
        range_high = high.where(source_mask).groupby(dates).transform("max")
        range_low = low.where(source_mask).groupby(dates).transform("min")
        buffer = atr * float(params["break_buffer_atr"])
        mode_name = str(params["opening_range_mode"])
        long_context, short_context = _context_masks(
            frame, str(params.get("context_filter", "avoid_h4_opposite"))
        )
        entry_window = (
            (hours >= int(params["session_start_utc"]))
            & (hours < int(params["session_end_utc"]))
        )
        if mode_name == "continuation":
            raw_long = (
                (close > range_high + buffer)
                & (close.shift(1) <= range_high + buffer)
                & entry_window
                & long_context
            )
            raw_short = (
                (close < range_low - buffer)
                & (close.shift(1) >= range_low - buffer)
                & entry_window
                & short_context
            )
        elif mode_name == "reversal":
            sweep = atr * float(params["sweep_atr"])
            raw_long = (
                (low < range_low - sweep)
                & (close > range_low + buffer)
                & entry_window
                & long_context
            )
            raw_short = (
                (high > range_high + sweep)
                & (close < range_high - buffer)
                & entry_window
                & short_context
            )
        else:
            raise ValueError(f"unsupported opening_range_mode: {mode_name}")
        if bool(params.get("first_signal_per_day", True)):
            any_signal = raw_long | raw_short
            first = any_signal & (any_signal.groupby(dates).cumsum() == 1)
            raw_long &= first
            raw_short &= first
        direction[raw_long] = 1
        direction[raw_short] = -1
        stop_distance[:] = atr * float(params["atr_stop"])
        target_distance[:] = stop_distance * float(params["reward_risk"])
        max_hold[:] = int(params["max_hold_bars"])

    elif strategy.lineage == "trend_day_pullback":
        ema = frame[f"ema{int(params['ema_length'])}"]
        day_open = _day_open(frame, opened)
        trend_open_buffer = atr * float(params["trend_open_atr"])
        pullback = atr * float(params["pullback_atr"])
        rsi_trigger = float(params["rsi_trigger"])
        sharpe_min = float(params.get("rolling_sharpe_min", -999.0))
        long_day = h1_up & h4_up & (close > day_open + trend_open_buffer)
        short_day = h1_down & h4_down & (close < day_open - trend_open_buffer)
        long_cross = (frame["rsi14"].shift(1) <= rsi_trigger) & (frame["rsi14"] > rsi_trigger)
        short_level = 100.0 - rsi_trigger
        short_cross = (frame["rsi14"].shift(1) >= short_level) & (frame["rsi14"] < short_level)
        raw_long = (
            long_day
            & (low <= ema + pullback)
            & (close > ema)
            & long_cross
            & (frame["rolling_sharpe"] >= sharpe_min)
        )
        raw_short = (
            short_day
            & (high >= ema - pullback)
            & (close < ema)
            & short_cross
            & (frame["rolling_sharpe"] <= -sharpe_min)
        )
        direction[raw_long] = 1
        direction[raw_short] = -1
        stop_distance[:] = atr * float(params["atr_stop"])
        target_distance[:] = stop_distance * float(params["reward_risk"])
        max_hold[:] = int(params["max_hold_bars"])

    elif strategy.lineage == "day_time_regime_filter":
        weekdays = _parse_weekdays(str(params["weekdays"]))
        weekday_ok = pd.Series(frame.index.weekday, index=frame.index).isin(weekdays)
        regime_ok = _regime_mask(frame, str(params.get("regime_mode", "any")))
        lookback = int(params["momentum_bars"])
        threshold = atr * float(params["momentum_atr"])
        mode_name = str(params["signal_mode"])
        long_context, short_context = _context_masks(
            frame, str(params.get("context_filter", "avoid_h4_opposite"))
        )
        if mode_name == "momentum":
            raw_long = (close > close.shift(lookback) + threshold) & long_context
            raw_short = (close < close.shift(lookback) - threshold) & short_context
        elif mode_name == "reversal":
            mean = close.rolling(lookback, min_periods=lookback).mean()
            raw_long = (close < mean - threshold) & (frame["rsi14"] <= float(params["rsi_extreme"])) & long_context
            raw_short = (close > mean + threshold) & (frame["rsi14"] >= 100.0 - float(params["rsi_extreme"])) & short_context
        else:
            raise ValueError(f"unsupported signal_mode: {mode_name}")
        direction[raw_long & weekday_ok & regime_ok] = 1
        direction[raw_short & weekday_ok & regime_ok] = -1
        stop_distance[:] = atr * float(params["atr_stop"])
        target_distance[:] = stop_distance * float(params["reward_risk"])
        max_hold[:] = int(params["max_hold_bars"])

    elif strategy.lineage == "inside_bar_expansion":
        buffer = atr * float(params["break_buffer_atr"])
        inside_previous = frame["inside_bar"].shift(1) > 0
        mother_high = high.shift(2)
        mother_low = low.shift(2)
        volume_ok = frame["vol_ratio"] >= float(params["volume_ratio"])
        long_context, short_context = _context_masks(
            frame, str(params.get("context_filter", "avoid_h4_opposite"))
        )
        raw_long = inside_previous & volume_ok & (close > mother_high + buffer) & long_context
        raw_short = inside_previous & volume_ok & (close < mother_low - buffer) & short_context
        direction[raw_long] = 1
        direction[raw_short] = -1
        stop_distance[:] = atr * float(params["atr_stop"])
        target_distance[:] = stop_distance * float(params["reward_risk"])
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
