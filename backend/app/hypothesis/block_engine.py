from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd


Side = str


def _series(frame: pd.DataFrame, name: str, default: float = 0.0) -> pd.Series:
    if name in frame:
        return frame[name]
    return pd.Series(default, index=frame.index, dtype=float)


def _bool(index: pd.Index, value: bool = True) -> pd.Series:
    return pd.Series(value, index=index, dtype=bool)


def _atr(frame: pd.DataFrame) -> pd.Series:
    return _series(frame, "atr14", 1.0).replace(0, np.nan).ffill().fillna(1.0)


def _hours(frame: pd.DataFrame) -> pd.Series:
    return pd.Series(frame.index.hour, index=frame.index)


def _dates(frame: pd.DataFrame) -> pd.Series:
    return pd.Series(frame.index.normalize(), index=frame.index)


def _rolling_prior_high(frame: pd.DataFrame, lookback: int) -> pd.Series:
    return frame["high"].shift(1).rolling(lookback, min_periods=lookback).max()


def _rolling_prior_low(frame: pd.DataFrame, lookback: int) -> pd.Series:
    return frame["low"].shift(1).rolling(lookback, min_periods=lookback).min()


def _previous_day_levels(frame: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    dates = _dates(frame)
    daily = frame.groupby(dates).agg({"high": "max", "low": "min"})
    return dates.map(daily["high"].shift(1)), dates.map(daily["low"].shift(1))


def _session_levels(frame: pd.DataFrame, start_hour: int, end_hour: int) -> tuple[pd.Series, pd.Series]:
    hours = _hours(frame)
    dates = frame.index.normalize()
    in_window = (hours >= start_hour) & (hours < end_hour)
    high = frame["high"].where(in_window).groupby(dates).transform("max")
    low = frame["low"].where(in_window).groupby(dates).transform("min")
    return high, low


def _closed_cross_above(value: pd.Series, level: pd.Series) -> pd.Series:
    return (value > level) & (value.shift(1) <= level)


def _closed_cross_below(value: pd.Series, level: pd.Series) -> pd.Series:
    return (value < level) & (value.shift(1) >= level)


def _confirmed_swing_levels(frame: pd.DataFrame, left: int, right: int) -> tuple[pd.Series, pd.Series]:
    """Return latest confirmed swing levels without using future data at signal time."""
    window = left + right + 1
    high = frame["high"]
    low = frame["low"]
    center_high = high.rolling(window, center=True, min_periods=window).max()
    center_low = low.rolling(window, center=True, min_periods=window).min()
    swing_high = high.where(high.eq(center_high)).shift(right)
    swing_low = low.where(low.eq(center_low)).shift(right)
    return swing_high.ffill(), swing_low.ffill()


def _equal_level_masks(frame: pd.DataFrame, lookback: int, tolerance_atr: float, side: Side) -> pd.Series:
    atr = _atr(frame)
    if side == "long":
        prior_low = _rolling_prior_low(frame, lookback)
        cluster_width = frame["low"].shift(1).rolling(lookback, min_periods=lookback).max() - prior_low
        return cluster_width <= atr * tolerance_atr
    prior_high = _rolling_prior_high(frame, lookback)
    cluster_width = prior_high - frame["high"].shift(1).rolling(lookback, min_periods=lookback).min()
    return cluster_width <= atr * tolerance_atr


def _latest_fvg(frame: pd.DataFrame, side: Side) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Return active FVG bounds and new-gap mask. Bullish gap is high[-2] < low[0]."""
    if side == "long":
        new_gap = frame["low"] > frame["high"].shift(2)
        lower = frame["high"].shift(2).where(new_gap).ffill()
        upper = frame["low"].where(new_gap).ffill()
    else:
        new_gap = frame["high"] < frame["low"].shift(2)
        lower = frame["high"].where(new_gap).ffill()
        upper = frame["low"].shift(2).where(new_gap).ffill()
    return lower, upper, new_gap.fillna(False)


def _displacement(frame: pd.DataFrame, side: Side, range_atr: float, body_min: float) -> pd.Series:
    bullish = frame["close"] > frame["open"]
    bearish = frame["close"] < frame["open"]
    strong = (_series(frame, "rng_atr") >= range_atr) & (_series(frame, "body_pct") >= body_min)
    return strong & (bullish if side == "long" else bearish)


def _order_block_zone(frame: pd.DataFrame, side: Side, lookback: int, range_atr: float) -> tuple[pd.Series, pd.Series]:
    displacement = _displacement(frame, side, range_atr, 0.55)
    opposite = frame["close"] < frame["open"] if side == "long" else frame["close"] > frame["open"]
    zone_high = pd.Series(np.nan, index=frame.index, dtype=float)
    zone_low = pd.Series(np.nan, index=frame.index, dtype=float)
    for offset in range(1, lookback + 1):
        candidate = opposite.shift(offset) & displacement
        zone_high = zone_high.where(~candidate, frame["high"].shift(offset))
        zone_low = zone_low.where(~candidate, frame["low"].shift(offset))
    return zone_low.ffill(), zone_high.ffill()


def _time_window(frame: pd.DataFrame, start: int, end: int) -> pd.Series:
    hours = _hours(frame)
    return (hours >= start) & (hours < end)


def _context_filter(frame: pd.DataFrame, side: Side, mode: str) -> pd.Series:
    h1_up = _series(frame, "h1_trend") > 0
    h1_down = _series(frame, "h1_trend") < 0
    h4_up = _series(frame, "h4_trend") > 0
    h4_down = _series(frame, "h4_trend") < 0
    if mode == "trend_aligned":
        return h1_up & h4_up if side == "long" else h1_down & h4_down
    if mode == "avoid_h1_h4_opposite":
        return ~h1_down & ~h4_down if side == "long" else ~h1_up & ~h4_up
    if mode == "avoid_h4_opposite":
        return ~h4_down if side == "long" else ~h4_up
    if mode == "h1_turn_with_h4":
        return h1_up & ~h4_down if side == "long" else h1_down & ~h4_up
    return _bool(frame.index)


def _smt_mask(frame: pd.DataFrame, side: Side, proxy: str, lookback: int) -> pd.Series:
    """Strict SMT: requires proxy OHLC columns and otherwise emits no signals."""
    prefix = f"smt_{proxy.lower()}"
    proxy_high = frame.get(f"{prefix}_high")
    proxy_low = frame.get(f"{prefix}_low")
    if proxy_high is None or proxy_low is None:
        return _bool(frame.index, False)
    xau_high = _rolling_prior_high(frame, lookback)
    xau_low = _rolling_prior_low(frame, lookback)
    other_high = proxy_high.shift(1).rolling(lookback, min_periods=lookback).max()
    other_low = proxy_low.shift(1).rolling(lookback, min_periods=lookback).min()
    if side == "long":
        return (frame["low"] < xau_low) & ~(proxy_low < other_low)
    return (frame["high"] > xau_high) & ~(proxy_high > other_high)


def evaluate_rule_block(frame: pd.DataFrame, block: dict[str, Any], side: Side) -> pd.Series:
    """Evaluate one named market-structure block on the closed signal bar.

    All definitions use only the current closed bar plus data that was already
    confirmed before that close. If required external data is missing, the block
    returns false rather than degrading into a different signal.
    """
    name = str(block.get("name", ""))
    lookback = int(block.get("lookback", 24))
    atr = _atr(frame)
    close, high, low, opened = frame["close"], frame["high"], frame["low"], frame.get("open", frame["close"])
    buffer = atr * float(block.get("buffer_atr", 0.0))

    if name in {"market_structure_shift", "break_of_structure", "change_of_character"}:
        swing_high, swing_low = _confirmed_swing_levels(
            frame,
            int(block.get("swing_left", 2)),
            int(block.get("swing_right", 2)),
        )
        crossed = _closed_cross_above(close, swing_high + buffer) if side == "long" else _closed_cross_below(close, swing_low - buffer)
        if name == "break_of_structure":
            return crossed & _context_filter(frame, side, str(block.get("context", "avoid_h4_opposite")))
        if name == "change_of_character":
            prior_trend = _series(frame, "h1_trend")
            return crossed & ((prior_trend.shift(1) < 0) if side == "long" else (prior_trend.shift(1) > 0))
        return crossed

    if name == "internal_structure_break":
        return evaluate_rule_block(frame, {**block, "name": "market_structure_shift", "swing_left": 1, "swing_right": 1}, side)

    if name == "external_structure_break":
        return evaluate_rule_block(frame, {**block, "name": "market_structure_shift", "swing_left": 4, "swing_right": 3}, side)

    if name == "higher_timeframe_bias":
        return _context_filter(frame, side, str(block.get("mode", "avoid_h4_opposite")))

    if name == "premium_discount":
        swing_high, swing_low = _confirmed_swing_levels(frame, 3, 2)
        midpoint = (swing_high + swing_low) / 2.0
        return close <= midpoint if side == "long" else close >= midpoint

    if name in {"prior_day_liquidity", "prior_day_high_low"}:
        pdh, pdl = _previous_day_levels(frame)
        if side == "long":
            return (low < pdl - buffer) & (close > pdl + buffer)
        return (high > pdh + buffer) & (close < pdh - buffer)

    if name in {"session_liquidity", "asian_range_liquidity", "london_sweep", "ny_sweep"}:
        if name == "asian_range_liquidity":
            start, end = 0, 6
        elif name == "london_sweep":
            start, end = 6, 10
        elif name == "ny_sweep":
            start, end = 12, 15
        else:
            start, end = int(block.get("range_start_utc", 0)), int(block.get("range_end_utc", 6))
        range_high, range_low = _session_levels(frame, start, end)
        trade_window = _time_window(frame, int(block.get("session_start_utc", end)), int(block.get("session_end_utc", 24)))
        if side == "long":
            return trade_window & (low < range_low - buffer) & (close > range_low + buffer)
        return trade_window & (high > range_high + buffer) & (close < range_high - buffer)

    if name == "equal_high_low_liquidity":
        cluster = _equal_level_masks(frame, lookback, float(block.get("tolerance_atr", 0.15)), side)
        prior_high, prior_low = _rolling_prior_high(frame, lookback), _rolling_prior_low(frame, lookback)
        if side == "long":
            return cluster & (low < prior_low - buffer) & (close > prior_low)
        return cluster & (high > prior_high + buffer) & (close < prior_high)

    if name in {"stop_run_reclaim", "liquidity_sweep_reclaim"}:
        prior_high, prior_low = _rolling_prior_high(frame, lookback), _rolling_prior_low(frame, lookback)
        penetration = atr * float(block.get("penetration_atr", 0.05))
        reclaim = atr * float(block.get("reclaim_buffer_atr", 0.0))
        close_location = (close - low) / _series(frame, "rng", 1.0).replace(0, np.nan)
        if side == "long":
            return (low < prior_low - penetration) & (close > prior_low + reclaim) & (_series(frame, "lwk_pct") >= float(block.get("wick_min", 0.35))) & (close_location >= float(block.get("close_location_min", 0.45)))
        upper_location = (high - close) / _series(frame, "rng", 1.0).replace(0, np.nan)
        return (high > prior_high + penetration) & (close < prior_high - reclaim) & (_series(frame, "uwk_pct") >= float(block.get("wick_min", 0.35))) & (upper_location >= float(block.get("close_location_min", 0.45)))

    if name == "liquidity_pool_distance":
        prior_high, prior_low = _rolling_prior_high(frame, lookback), _rolling_prior_low(frame, lookback)
        distance = (close - prior_low).abs() if side == "long" else (prior_high - close).abs()
        return distance <= atr * float(block.get("max_distance_atr", 0.8))

    if name in {"fair_value_gap", "fvg"}:
        lower, upper, new_gap = _latest_fvg(frame, side)
        mode = str(block.get("mode", "new_or_retrace"))
        if mode == "new":
            return new_gap
        return ((low <= upper) & (close >= lower)) if side == "long" else ((high >= lower) & (close <= upper))

    if name in {"inverse_fair_value_gap", "ifvg"}:
        bear_lower, bear_upper, _ = _latest_fvg(frame, "short")
        bull_lower, bull_upper, _ = _latest_fvg(frame, "long")
        if side == "long":
            return _closed_cross_above(close, bear_upper + buffer)
        return _closed_cross_below(close, bull_lower - buffer)

    if name == "balanced_price_range":
        _, _, bull = _latest_fvg(frame, "long")
        _, _, bear = _latest_fvg(frame, "short")
        bars = int(block.get("lookback", 12))
        return (bull.rolling(bars, min_periods=1).sum() > 0) & (bear.rolling(bars, min_periods=1).sum() > 0)

    if name == "displacement_candle":
        return _displacement(frame, side, float(block.get("range_atr", 1.4)), float(block.get("body_min", 0.55)))

    if name in {"fvg_fill", "fvg_mitigation", "fvg_mitigation_rejection"}:
        lower, upper, _ = _latest_fvg(frame, side)
        touched = (low <= upper) & (close > lower) if side == "long" else (high >= lower) & (close < upper)
        rejected = close > opened if side == "long" else close < opened
        return touched & rejected

    if name in {"order_block", "mitigation_block", "rejection_block"}:
        zone_low, zone_high = _order_block_zone(frame, side, int(block.get("ob_lookback", 5)), float(block.get("displacement_atr", 1.4)))
        touched = (low <= zone_high) & (close >= zone_low) if side == "long" else (high >= zone_low) & (close <= zone_high)
        rejected = close > opened if side == "long" else close < opened
        return touched & (rejected if name == "rejection_block" else True)

    if name == "breaker_block":
        opposite = "short" if side == "long" else "long"
        zone_low, zone_high = _order_block_zone(frame, opposite, int(block.get("ob_lookback", 5)), float(block.get("displacement_atr", 1.4)))
        broken = close > zone_high if side == "long" else close < zone_low
        retest = (low <= zone_high) if side == "long" else (high >= zone_low)
        return broken.shift(1).rolling(int(block.get("retest_bars", 8)), min_periods=1).max().fillna(False).astype(bool) & retest

    if name in {"smt_divergence", "smt"}:
        return _smt_mask(frame, side, str(block.get("proxy", "dxy")), int(block.get("lookback", 24)))

    if name in {"opening_range_break", "opening_range_reversal"}:
        rh, rl = _session_levels(frame, int(block.get("range_start_utc", 0)), int(block.get("range_end_utc", 1)))
        window = _time_window(frame, int(block.get("session_start_utc", 1)), int(block.get("session_end_utc", 24)))
        if name == "opening_range_break":
            return window & (_closed_cross_above(close, rh + buffer) if side == "long" else _closed_cross_below(close, rl - buffer))
        return window & ((low < rl - buffer) & (close > rl) if side == "long" else (high > rh + buffer) & (close < rh))

    if name == "volatility_regime":
        mode = str(block.get("mode", "expansion"))
        rng_atr = _series(frame, "rng_atr")
        if mode == "compression":
            return _series(frame, "bb_width") <= _series(frame, "bb_width").rolling(200, min_periods=50).quantile(float(block.get("quantile", 0.25)))
        if mode == "high_vol_kill":
            return rng_atr <= float(block.get("max_rng_atr", 3.0))
        return rng_atr >= float(block.get("min_rng_atr", 1.0))

    if name == "trend_day":
        day_open = opened.groupby(_dates(frame)).transform("first")
        trend_buffer = atr * float(block.get("trend_open_atr", 0.35))
        return (_context_filter(frame, side, "trend_aligned") & (close > day_open + trend_buffer)) if side == "long" else (_context_filter(frame, side, "trend_aligned") & (close < day_open - trend_buffer))

    if name == "day_time_filter":
        weekdays = {int(value) for value in str(block.get("weekdays", "0,1,2,3,4")).split(",") if value.strip()}
        weekday_ok = pd.Series(frame.index.weekday, index=frame.index).isin(weekdays or {0, 1, 2, 3, 4})
        return weekday_ok & _time_window(frame, int(block.get("session_start_utc", 0)), int(block.get("session_end_utc", 24)))

    if name == "trend_pullback":
        ema = _series(frame, f"ema{int(block.get('ema_length', 20))}", close)
        pullback = atr * float(block.get("pullback_atr", 0.5))
        if side == "long":
            return (low <= ema + pullback) & (close > ema) & (_series(frame, "rsi14") > float(block.get("rsi_trigger", 50)))
        return (high >= ema - pullback) & (close < ema) & (_series(frame, "rsi14") < 100.0 - float(block.get("rsi_trigger", 50)))

    if name == "inside_bar_expansion":
        inside = _series(frame, "inside_bar").shift(1) > 0
        mother_high, mother_low = high.shift(2), low.shift(2)
        return inside & ((close > mother_high + buffer) if side == "long" else (close < mother_low - buffer))

    if name == "failed_breakout_reversal":
        prior_high, prior_low = _rolling_prior_high(frame, lookback), _rolling_prior_low(frame, lookback)
        if side == "long":
            return (low < prior_low - buffer) & (close > prior_low) & (close > opened)
        return (high > prior_high + buffer) & (close < prior_high) & (close < opened)

    if name == "volatility_spike_reversal":
        extreme = float(block.get("rsi_extreme", 30.0))
        range_ok = _series(frame, "rng_atr") >= float(block.get("spike_range_atr", 1.8))
        if side == "long":
            return range_ok & (_series(frame, "rsi14") <= extreme) & ((close > opened) | (_series(frame, "lwk_pct") >= float(block.get("wick_min", 0.45))))
        return range_ok & (_series(frame, "rsi14") >= 100.0 - extreme) & ((close < opened) | (_series(frame, "uwk_pct") >= float(block.get("wick_min", 0.45))))

    raise ValueError(f"unsupported strategy grammar block: {name}")


def apply_strategy_grammar(frame: pd.DataFrame, params: dict[str, Any]) -> pd.DataFrame:
    direction = pd.Series(0, index=frame.index, dtype="int8")
    blocks = list(params.get("rule_blocks") or [])
    if not blocks:
        return pd.DataFrame({
            "signal_direction": direction,
            "stop_distance": pd.Series(0.0, index=frame.index),
            "target_distance": pd.Series(0.0, index=frame.index),
            "signal_target_price": pd.Series(0.0, index=frame.index),
            "trail_atr": pd.Series(0.0, index=frame.index),
            "max_hold_bars": pd.Series(0, index=frame.index, dtype="int32"),
        }, index=frame.index)

    mode = str(params.get("block_logic", "all"))
    min_votes = int(params.get("min_block_votes", max(1, math.ceil(len(blocks) * 0.7))))
    for side, sign in (("long", 1), ("short", -1)):
        masks = [evaluate_rule_block(frame, block, side) for block in blocks]
        if mode == "vote":
            side_mask = sum(mask.astype(int) for mask in masks) >= min_votes
        elif mode == "any":
            side_mask = masks[0].copy()
            for mask in masks[1:]:
                side_mask |= mask
        else:
            side_mask = masks[0].copy()
            for mask in masks[1:]:
                side_mask &= mask
        direction[side_mask.fillna(False)] = sign

    atr = _atr(frame)
    stop_mode = str(params.get("stop_mode", "atr"))
    stop_atr = float(params.get("atr_stop", 1.0))
    stop_distance = atr * stop_atr
    if stop_mode == "structure":
        swing_high, swing_low = _confirmed_swing_levels(frame, 2, 2)
        long_stop = (frame["close"] - swing_low).abs().clip(lower=atr * 0.5, upper=atr * stop_atr * 2.0)
        short_stop = (swing_high - frame["close"]).abs().clip(lower=atr * 0.5, upper=atr * stop_atr * 2.0)
        stop_distance = stop_distance.where(direction == 0, long_stop.where(direction > 0, short_stop))

    target_distance = stop_distance * float(params.get("reward_risk", 1.25))
    max_hold = pd.Series(int(params.get("max_hold_bars", 8)), index=frame.index, dtype="int32")
    trail = pd.Series(float(params.get("atr_trail", 0.0)), index=frame.index, dtype=float)
    return pd.DataFrame({
        "signal_direction": direction,
        "stop_distance": stop_distance,
        "target_distance": target_distance,
        "signal_target_price": pd.Series(0.0, index=frame.index),
        "trail_atr": trail,
        "max_hold_bars": max_hold,
    }, index=frame.index)

