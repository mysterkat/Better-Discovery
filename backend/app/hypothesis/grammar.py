from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from random import Random
from copy import deepcopy

from .models import HypothesisDiscoveryRequest, HypothesisSpec, Lineage


@dataclass(frozen=True)
class HypothesisProfile:
    lineage: Lineage
    thesis: str
    parameters: dict[str, object]


SESSION_WINDOWS: tuple[tuple[str, int, int], ...] = (
    ("all_day", 0, 24),
    ("asia_europe", 0, 12),
    ("europe_us", 6, 20),
    ("active_hours", 3, 22),
)

DIRECTION_MODES: tuple[str, ...] = ("both", "long_only", "short_only")
VOLATILITY_FILTERS: tuple[str, ...] = ("none", "h4_above_60d_median")


def _fingerprint(lineage: str, params: dict[str, int | float | str | bool]) -> str:
    raw = json.dumps({"lineage": lineage, "parameters": params}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _id(lineage: str, params: dict[str, int | float | str | bool]) -> str:
    return f"{lineage}_{_fingerprint(lineage, params)}"


def _grammar_id(params: dict[str, object]) -> str:
    raw = json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)
    return f"grammar_{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:12]}"


def _spec_from_profile(profile: HypothesisProfile, timeframe: str) -> HypothesisSpec:
    params = dict(profile.parameters)
    return HypothesisSpec(
        strategy_id=_id(profile.lineage, params),
        lineage=profile.lineage,
        hypothesis=profile.thesis,
        timeframe=timeframe,  # type: ignore[arg-type]
        parameters=params,
    )


def _with_common_filters(
    lineage: Lineage,
    thesis: str,
    base_profiles: Iterable[dict[str, int | float | str | bool]],
    *,
    directions: tuple[str, ...] = DIRECTION_MODES,
    volatility_filters: tuple[str, ...] = VOLATILITY_FILTERS,
    sessions: tuple[tuple[str, int, int], ...] = SESSION_WINDOWS,
) -> list[HypothesisProfile]:
    profiles: list[HypothesisProfile] = []
    for base in base_profiles:
        for direction in directions:
            for volatility_filter in volatility_filters:
                for session_name, session_start, session_end in sessions:
                    params = {
                        **base,
                        "direction_mode": direction,
                        "volatility_filter": volatility_filter,
                        "session_name": session_name,
                        "session_start_utc": session_start,
                        "session_end_utc": session_end,
                    }
                    profiles.append(HypothesisProfile(lineage, thesis, params))
    return profiles


def _time_series_breakout() -> list[HypothesisProfile]:
    thesis = (
        "Gold can follow through after a closed-bar channel break when higher "
        "timeframes point the same way, but the hold time must be capped."
    )
    bases = (
        {"channel_bars": 8, "atr_stop": 0.8, "atr_trail": 0.8, "max_hold_bars": 8},
        {"channel_bars": 12, "atr_stop": 0.9, "atr_trail": 1.2, "max_hold_bars": 12},
        {"channel_bars": 16, "atr_stop": 1.2, "atr_trail": 1.5, "max_hold_bars": 16},
        {"channel_bars": 32, "atr_stop": 1.5, "atr_trail": 2.0, "max_hold_bars": 32},
        {"channel_bars": 64, "atr_stop": 2.0, "atr_trail": 3.0, "max_hold_bars": 64},
    )
    return _with_common_filters("time_series_breakout", thesis, bases)


def _session_range_breakout() -> list[HypothesisProfile]:
    thesis = (
        "Gold sometimes expands after clearing a completed intraday range; the "
        "test must prove which range, if any, is worth trading."
    )
    range_profiles = (
        {"range_start_utc": 0, "range_end_utc": 2, "session_start_utc": 2, "session_end_utc": 12},
        {"range_start_utc": 0, "range_end_utc": 6, "session_start_utc": 6, "session_end_utc": 16},
        {"range_start_utc": 6, "range_end_utc": 8, "session_start_utc": 8, "session_end_utc": 18},
        {"range_start_utc": 11, "range_end_utc": 13, "session_start_utc": 13, "session_end_utc": 22},
        {"range_start_utc": 0, "range_end_utc": 1, "session_start_utc": 1, "session_end_utc": 24},
    )
    stop_profiles = (
        {"atr_stop": 0.9, "atr_trail": 1.0, "max_hold_bars": 8},
        {"atr_stop": 1.2, "atr_trail": 1.5, "max_hold_bars": 16},
        {"atr_stop": 1.8, "atr_trail": 2.5, "max_hold_bars": 32},
    )
    profiles: list[HypothesisProfile] = []
    for range_profile in range_profiles:
        for stop_profile in stop_profiles:
            for volatility_filter in VOLATILITY_FILTERS:
                params = {
                    **range_profile,
                    **stop_profile,
                    "direction_mode": "long_only",
                    "volatility_filter": volatility_filter,
                    "session_name": f"range_{range_profile['range_start_utc']}_{range_profile['range_end_utc']}",
                    "first_breakout_per_day": True,
                }
                profiles.append(HypothesisProfile("session_range_breakout", thesis, params))
    return profiles


def _trend_pullback() -> list[HypothesisProfile]:
    thesis = (
        "A strong gold trend may continue after a pullback to a moving average "
        "when short-term momentum turns back with the trend."
    )
    bases = (
        {"ema_length": 20, "pullback_atr": 0.35, "rsi_trigger": 48.0, "atr_stop": 0.8, "reward_risk": 1.0},
        {"ema_length": 20, "pullback_atr": 0.60, "rsi_trigger": 50.0, "atr_stop": 1.0, "reward_risk": 1.25},
        {"ema_length": 50, "pullback_atr": 0.75, "rsi_trigger": 50.0, "atr_stop": 1.2, "reward_risk": 1.5},
        {"ema_length": 50, "pullback_atr": 1.00, "rsi_trigger": 52.0, "atr_stop": 1.6, "reward_risk": 2.0},
        {"ema_length": 200, "pullback_atr": 1.25, "rsi_trigger": 52.0, "atr_stop": 2.0, "reward_risk": 2.0},
    )
    return _with_common_filters("trend_pullback", thesis, bases)


def _volatility_expansion() -> list[HypothesisProfile]:
    thesis = (
        "Gold can leave a quiet volatility state quickly; entries are only useful "
        "if the break has enough participation and a defined stop."
    )
    bases = (
        {"lookback": 8, "squeeze_quantile": 0.15, "volume_ratio": 1.05, "atr_stop": 0.8, "reward_risk": 1.0},
        {"lookback": 12, "squeeze_quantile": 0.20, "volume_ratio": 1.10, "atr_stop": 1.0, "reward_risk": 1.25},
        {"lookback": 16, "squeeze_quantile": 0.25, "volume_ratio": 1.15, "atr_stop": 1.2, "reward_risk": 1.5},
        {"lookback": 24, "squeeze_quantile": 0.30, "volume_ratio": 1.20, "atr_stop": 1.6, "reward_risk": 2.0},
    )
    return _with_common_filters("volatility_expansion", thesis, bases)


def _regime_mean_reversion() -> list[HypothesisProfile]:
    thesis = (
        "Gold can snap back inside flat higher-timeframe regimes after an extreme "
        "move; the strategy must stay disabled in directional regimes."
    )
    bases = (
        {"z_length": 24, "z_entry": 1.5, "rsi_extreme": 30.0, "atr_stop": 0.9, "max_hold_bars": 8},
        {"z_length": 32, "z_entry": 1.75, "rsi_extreme": 28.0, "atr_stop": 1.1, "max_hold_bars": 12},
        {"z_length": 48, "z_entry": 2.0, "rsi_extreme": 25.0, "atr_stop": 1.4, "max_hold_bars": 16},
        {"z_length": 64, "z_entry": 2.25, "rsi_extreme": 22.0, "atr_stop": 1.8, "max_hold_bars": 24},
    )
    return _with_common_filters("regime_mean_reversion", thesis, bases)


def _liquidity_sweep_reclaim() -> list[HypothesisProfile]:
    thesis = (
        "Gold often raids a nearby swing high or low before reclaiming the level; "
        "the entry only exists after the closed bar proves the reclaim."
    )
    bases = (
        {
            "sweep_lookback": 12, "penetration_atr": 0.05, "reclaim_buffer_atr": 0.00,
            "wick_reject_min": 0.35, "close_location_min": 0.45, "atr_stop": 0.8,
            "reward_risk": 1.0, "max_hold_bars": 6, "context_filter": "none",
            "first_signal_per_day": False,
        },
        {
            "sweep_lookback": 24, "penetration_atr": 0.10, "reclaim_buffer_atr": 0.02,
            "wick_reject_min": 0.45, "close_location_min": 0.50, "atr_stop": 1.0,
            "reward_risk": 1.25, "max_hold_bars": 8, "context_filter": "avoid_h4_opposite",
            "first_signal_per_day": False,
        },
        {
            "sweep_lookback": 48, "penetration_atr": 0.15, "reclaim_buffer_atr": 0.04,
            "wick_reject_min": 0.50, "close_location_min": 0.55, "atr_stop": 1.3,
            "reward_risk": 1.5, "max_hold_bars": 12, "context_filter": "avoid_h1_h4_opposite",
            "first_signal_per_day": True,
        },
    )
    sessions = (
        ("all_day", 0, 24),
        ("asia_europe", 0, 12),
        ("europe_us", 6, 20),
        ("active_hours", 3, 22),
    )
    return _with_common_filters("liquidity_sweep_reclaim", thesis, bases, sessions=sessions)


def _failed_breakout_reversal() -> list[HypothesisProfile]:
    thesis = (
        "A breakout that probes beyond a channel but closes back inside can trap "
        "late momentum traders and reverse over the next few bars."
    )
    bases = (
        {"channel_bars": 12, "break_atr": 0.05, "close_back_atr": 0.00, "atr_stop": 0.8, "reward_risk": 1.0, "max_hold_bars": 6, "context_filter": "none"},
        {"channel_bars": 24, "break_atr": 0.10, "close_back_atr": 0.02, "atr_stop": 1.0, "reward_risk": 1.25, "max_hold_bars": 8, "context_filter": "none"},
        {"channel_bars": 48, "break_atr": 0.15, "close_back_atr": 0.04, "atr_stop": 1.3, "reward_risk": 1.5, "max_hold_bars": 12, "context_filter": "avoid_h4_opposite"},
    )
    return _with_common_filters("failed_breakout_reversal", thesis, bases)


def _prior_day_level_continuation() -> list[HypothesisProfile]:
    thesis = (
        "Gold can continue after accepting above the prior day high or below the "
        "prior day low, especially when the higher timeframe is not fighting it."
    )
    bases = (
        {"break_buffer_atr": 0.00, "atr_stop": 0.8, "reward_risk": 1.0, "max_hold_bars": 8, "context_filter": "avoid_h4_opposite", "first_signal_per_day": True},
        {"break_buffer_atr": 0.03, "atr_stop": 1.0, "reward_risk": 1.25, "max_hold_bars": 12, "context_filter": "avoid_h4_opposite", "first_signal_per_day": True},
        {"break_buffer_atr": 0.06, "atr_stop": 1.3, "reward_risk": 1.5, "max_hold_bars": 16, "context_filter": "trend_aligned", "first_signal_per_day": True},
    )
    return _with_common_filters("prior_day_level_continuation", thesis, bases)


def _volatility_spike_reversal() -> list[HypothesisProfile]:
    thesis = (
        "A large XAUUSD volatility bar can exhaust short-term positioning; the "
        "strategy tests whether the next bar mean-reverts after a closed spike."
    )
    bases = (
        {
            "spike_style": "capitulation", "spike_range_atr": 1.8, "body_min": 0.55,
            "rsi_extreme": 30.0, "atr_stop": 0.9, "reward_risk": 1.0,
            "max_hold_bars": 6, "context_filter": "none",
        },
        {
            "spike_style": "capitulation", "spike_range_atr": 2.4, "body_min": 0.65,
            "rsi_extreme": 28.0, "atr_stop": 1.1, "reward_risk": 1.25,
            "max_hold_bars": 8, "context_filter": "none",
        },
        {
            "spike_style": "wick_reject", "spike_range_atr": 1.6, "body_min": 0.25,
            "wick_min": 0.45, "close_location_min": 0.55, "rsi_extreme": 35.0,
            "atr_stop": 1.0, "reward_risk": 1.2, "max_hold_bars": 8,
            "context_filter": "avoid_h4_opposite",
        },
    )
    return _with_common_filters("volatility_spike_reversal", thesis, bases)


def _opening_range_continuation_reversal() -> list[HypothesisProfile]:
    thesis = (
        "Opening ranges define early liquidity. Gold may either continue after "
        "acceptance outside the range or reverse after a sweep and reclaim."
    )
    ranges = (
        {"range_start_utc": 0, "range_end_utc": 1, "session_start_utc": 1, "session_end_utc": 24},
        {"range_start_utc": 0, "range_end_utc": 6, "session_start_utc": 6, "session_end_utc": 18},
        {"range_start_utc": 6, "range_end_utc": 8, "session_start_utc": 8, "session_end_utc": 20},
        {"range_start_utc": 12, "range_end_utc": 14, "session_start_utc": 14, "session_end_utc": 23},
    )
    exits = (
        {"atr_stop": 0.9, "reward_risk": 1.0, "max_hold_bars": 8},
        {"atr_stop": 1.2, "reward_risk": 1.25, "max_hold_bars": 12},
    )
    profiles: list[HypothesisProfile] = []
    for range_profile in ranges:
        for exit_profile in exits:
            for mode_name in ("continuation", "reversal"):
                for direction in DIRECTION_MODES:
                    params = {
                        **range_profile,
                        **exit_profile,
                        "opening_range_mode": mode_name,
                        "break_buffer_atr": 0.02 if mode_name == "continuation" else 0.00,
                        "sweep_atr": 0.05,
                        "context_filter": "avoid_h4_opposite" if mode_name == "continuation" else "none",
                        "direction_mode": direction,
                        "volatility_filter": "none",
                        "session_name": f"or_{range_profile['range_start_utc']}_{range_profile['range_end_utc']}_{mode_name}",
                        "first_signal_per_day": True,
                    }
                    profiles.append(HypothesisProfile("opening_range_continuation_reversal", thesis, params))
    return profiles


def _trend_day_pullback() -> list[HypothesisProfile]:
    thesis = (
        "On a confirmed trend day, gold can resume after pulling back to an EMA "
        "while still holding the session's directional open."
    )
    bases = (
        {"ema_length": 20, "trend_open_atr": 0.20, "pullback_atr": 0.30, "rsi_trigger": 48.0, "rolling_sharpe_min": 0.05, "atr_stop": 0.8, "reward_risk": 1.0, "max_hold_bars": 8},
        {"ema_length": 20, "trend_open_atr": 0.35, "pullback_atr": 0.50, "rsi_trigger": 50.0, "rolling_sharpe_min": 0.10, "atr_stop": 1.0, "reward_risk": 1.25, "max_hold_bars": 12},
        {"ema_length": 50, "trend_open_atr": 0.50, "pullback_atr": 0.75, "rsi_trigger": 50.0, "rolling_sharpe_min": 0.15, "atr_stop": 1.3, "reward_risk": 1.5, "max_hold_bars": 16},
    )
    return _with_common_filters("trend_day_pullback", thesis, bases)


def _day_time_regime_filter() -> list[HypothesisProfile]:
    thesis = (
        "Some XAUUSD behavior is conditional on weekday, hour, and volatility "
        "regime; this family tests simple momentum or reversal only inside that regime."
    )
    time_profiles = (
        {"weekdays": "0,1,2,3,4", "session_name": "weekday_all_day", "session_start_utc": 0, "session_end_utc": 24},
        {"weekdays": "1,2,3", "session_name": "midweek_active", "session_start_utc": 3, "session_end_utc": 22},
        {"weekdays": "0,4", "session_name": "monday_friday", "session_start_utc": 0, "session_end_utc": 24},
        {"weekdays": "0,1,2,3,4", "session_name": "us_late", "session_start_utc": 12, "session_end_utc": 23},
    )
    signal_profiles = (
        {"signal_mode": "momentum", "regime_mode": "trend", "momentum_bars": 4, "momentum_atr": 0.25, "rsi_extreme": 30.0, "atr_stop": 0.8, "reward_risk": 1.0, "max_hold_bars": 6, "context_filter": "avoid_h4_opposite"},
        {"signal_mode": "momentum", "regime_mode": "any", "momentum_bars": 8, "momentum_atr": 0.35, "rsi_extreme": 30.0, "atr_stop": 1.0, "reward_risk": 1.25, "max_hold_bars": 8, "context_filter": "h1_turn_with_h4"},
        {"signal_mode": "reversal", "regime_mode": "range_or_transition", "momentum_bars": 12, "momentum_atr": 0.60, "rsi_extreme": 30.0, "atr_stop": 1.0, "reward_risk": 1.2, "max_hold_bars": 8, "context_filter": "none"},
        {"signal_mode": "reversal", "regime_mode": "compression", "momentum_bars": 16, "momentum_atr": 0.75, "rsi_extreme": 28.0, "atr_stop": 1.2, "reward_risk": 1.4, "max_hold_bars": 12, "context_filter": "none"},
    )
    profiles: list[HypothesisProfile] = []
    for time_profile in time_profiles:
        for signal_profile in signal_profiles:
            for direction in DIRECTION_MODES:
                params = {
                    **time_profile,
                    **signal_profile,
                    "direction_mode": direction,
                    "volatility_filter": "none",
                }
                profiles.append(HypothesisProfile("day_time_regime_filter", thesis, params))
    return profiles


def _inside_bar_expansion() -> list[HypothesisProfile]:
    thesis = (
        "Inside bars mark short-lived compression; gold can expand when the next "
        "closed bar clears the mother bar with enough participation."
    )
    bases = (
        {"break_buffer_atr": 0.00, "volume_ratio": 1.00, "atr_stop": 0.8, "reward_risk": 1.0, "max_hold_bars": 6, "context_filter": "avoid_h4_opposite"},
        {"break_buffer_atr": 0.03, "volume_ratio": 1.10, "atr_stop": 1.0, "reward_risk": 1.25, "max_hold_bars": 8, "context_filter": "avoid_h4_opposite"},
        {"break_buffer_atr": 0.06, "volume_ratio": 1.20, "atr_stop": 1.3, "reward_risk": 1.5, "max_hold_bars": 12, "context_filter": "trend_aligned"},
    )
    return _with_common_filters("inside_bar_expansion", thesis, bases)


GRAMMAR_ENTRY_BLOCKS: tuple[dict[str, object], ...] = (
    {"group": "liquidity", "name": "liquidity_sweep_reclaim", "lookback": 12, "penetration_atr": 0.05, "wick_min": 0.35},
    {"group": "liquidity", "name": "liquidity_sweep_reclaim", "lookback": 24, "penetration_atr": 0.10, "wick_min": 0.45},
    {"group": "liquidity", "name": "prior_day_liquidity", "buffer_atr": 0.02},
    {"group": "liquidity", "name": "session_liquidity", "range_start_utc": 0, "range_end_utc": 6, "session_start_utc": 6, "session_end_utc": 20},
    {"group": "sessions", "name": "asian_range_liquidity", "buffer_atr": 0.03, "session_start_utc": 6, "session_end_utc": 18},
    {"group": "sessions", "name": "london_sweep", "buffer_atr": 0.03, "session_end_utc": 18},
    {"group": "sessions", "name": "ny_sweep", "buffer_atr": 0.03, "session_end_utc": 22},
    {"group": "liquidity", "name": "equal_high_low_liquidity", "lookback": 36, "tolerance_atr": 0.15, "buffer_atr": 0.02},
    {"group": "structure", "name": "failed_breakout_reversal", "lookback": 24, "buffer_atr": 0.05},
    {"group": "sessions", "name": "opening_range_break", "range_start_utc": 0, "range_end_utc": 1, "session_start_utc": 1, "session_end_utc": 20, "buffer_atr": 0.02},
    {"group": "sessions", "name": "opening_range_reversal", "range_start_utc": 0, "range_end_utc": 1, "session_start_utc": 1, "session_end_utc": 20, "buffer_atr": 0.03},
    {"group": "volatility", "name": "inside_bar_expansion", "buffer_atr": 0.02},
    {"group": "imbalance", "name": "fair_value_gap", "mode": "new_or_retrace"},
    {"group": "imbalance", "name": "inverse_fair_value_gap", "buffer_atr": 0.01},
    {"group": "orderflow", "name": "order_block", "ob_lookback": 5, "displacement_atr": 1.4},
    {"group": "orderflow", "name": "breaker_block", "ob_lookback": 5, "retest_bars": 8},
    {"group": "orderflow", "name": "mitigation_block", "ob_lookback": 8, "displacement_atr": 1.2},
    {"group": "orderflow", "name": "rejection_block", "ob_lookback": 8, "displacement_atr": 1.2},
    {"group": "structure", "name": "trend_pullback", "ema_length": 20, "pullback_atr": 0.45, "rsi_trigger": 50.0},
    {"group": "volatility", "name": "volatility_spike_reversal", "spike_range_atr": 1.8, "rsi_extreme": 32.0},
)

GRAMMAR_CONFIRMATION_BLOCKS: tuple[dict[str, object], ...] = (
    {"group": "structure", "name": "market_structure_shift", "swing_left": 2, "swing_right": 2, "buffer_atr": 0.01},
    {"group": "structure", "name": "break_of_structure", "swing_left": 3, "swing_right": 2, "buffer_atr": 0.02, "context": "avoid_h4_opposite"},
    {"group": "structure", "name": "change_of_character", "swing_left": 2, "swing_right": 2, "buffer_atr": 0.01},
    {"group": "structure", "name": "internal_structure_break", "buffer_atr": 0.01},
    {"group": "structure", "name": "external_structure_break", "buffer_atr": 0.02},
    {"group": "volatility", "name": "displacement_candle", "range_atr": 1.2, "body_min": 0.50},
    {"group": "imbalance", "name": "fair_value_gap", "mode": "new"},
    {"group": "imbalance", "name": "fvg_mitigation_rejection"},
    {"group": "imbalance", "name": "inverse_fair_value_gap", "buffer_atr": 0.01},
    {"group": "imbalance", "name": "balanced_price_range", "lookback": 12},
    {"group": "structure", "name": "higher_timeframe_bias", "mode": "avoid_h4_opposite"},
    {"group": "structure", "name": "premium_discount"},
    {"group": "liquidity", "name": "liquidity_pool_distance", "lookback": 48, "max_distance_atr": 1.0},
)

GRAMMAR_FILTER_BLOCKS: tuple[dict[str, object], ...] = (
    {"group": "sessions", "name": "day_time_filter", "weekdays": "0,1,2,3,4", "session_start_utc": 0, "session_end_utc": 24},
    {"group": "sessions", "name": "day_time_filter", "weekdays": "1,2,3", "session_start_utc": 3, "session_end_utc": 22},
    {"group": "volatility", "name": "volatility_regime", "mode": "expansion", "min_rng_atr": 0.8},
    {"group": "volatility", "name": "volatility_regime", "mode": "compression", "quantile": 0.30},
    {"group": "volatility", "name": "volatility_regime", "mode": "high_vol_kill", "max_rng_atr": 3.2},
    {"group": "structure", "name": "trend_day", "trend_open_atr": 0.30},
    {"group": "structure", "name": "higher_timeframe_bias", "mode": "trend_aligned"},
)

GRAMMAR_SMT_BLOCKS: tuple[dict[str, object], ...] = (
    {"group": "smt", "name": "smt_divergence", "proxy": "dxy", "lookback": 24},
    {"group": "smt", "name": "smt_divergence", "proxy": "silver", "lookback": 24},
    {"group": "smt", "name": "smt_divergence", "proxy": "us10y", "lookback": 24},
)


def _strip_group(block: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in block.items() if key != "group"}


def _block_groups_from_params(params: dict[str, object]) -> tuple[str, ...]:
    raw = str(params.get("grammar_block_groups", ""))
    values = tuple(item.strip() for item in raw.split(",") if item.strip())
    return values or ("liquidity", "structure", "imbalance", "orderflow", "sessions", "volatility")


def _timeframes_from_params(params: dict[str, object], fallback: str) -> tuple[str, ...]:
    raw = str(params.get("grammar_timeframes", fallback))
    values = tuple(dict.fromkeys(item.strip().lower() for item in raw.split(",") if item.strip()))
    return tuple(value for value in values if value in {"m1", "m5", "m10", "m15"}) or (fallback,)


def _mutate_numeric(
    rng: Random,
    params: dict[str, object],
    key: str,
    choices: tuple[int | float, ...],
) -> None:
    current = params.get(key)
    if current not in choices:
        params[key] = rng.choice(choices)
        return
    idx = choices.index(current)  # type: ignore[arg-type]
    lo = max(0, idx - 1)
    hi = min(len(choices) - 1, idx + 1)
    params[key] = choices[rng.randint(lo, hi)]


def _mutate_block_value(rng: Random, block: dict[str, object]) -> None:
    name = str(block.get("name", ""))
    numeric_choices: dict[str, tuple[int | float, ...]] = {
        "lookback": (8, 12, 16, 24, 36, 48, 64),
        "sweep_lookback": (8, 12, 24, 36, 48),
        "channel_bars": (8, 12, 16, 24, 32, 48, 64),
        "buffer_atr": (0.0, 0.01, 0.02, 0.03, 0.05, 0.08),
        "penetration_atr": (0.0, 0.03, 0.05, 0.08, 0.10, 0.15),
        "wick_min": (0.25, 0.35, 0.45, 0.55, 0.65),
        "tolerance_atr": (0.05, 0.10, 0.15, 0.20, 0.30),
        "range_atr": (0.8, 1.0, 1.2, 1.5, 1.8, 2.2),
        "body_min": (0.25, 0.35, 0.50, 0.60, 0.70),
        "ema_length": (20, 50, 200),
        "pullback_atr": (0.25, 0.35, 0.45, 0.60, 0.75, 1.00),
        "rsi_trigger": (45.0, 48.0, 50.0, 52.0, 55.0),
        "ob_lookback": (3, 5, 8, 13),
        "displacement_atr": (0.9, 1.1, 1.2, 1.4, 1.8),
        "retest_bars": (4, 6, 8, 12, 16),
        "max_distance_atr": (0.5, 0.75, 1.0, 1.5, 2.0),
        "trend_open_atr": (0.15, 0.25, 0.30, 0.45, 0.60),
        "min_rng_atr": (0.5, 0.7, 0.8, 1.0, 1.2),
        "max_rng_atr": (2.2, 2.8, 3.2, 4.0),
    }
    mutable = [key for key in block if key in numeric_choices]
    if mutable:
        key = rng.choice(mutable)
        _mutate_numeric(rng, block, key, numeric_choices[key])
    if "timeframe" in block:
        timeframe_pool = str(block.get("_timeframe_pool", "")).split(",")
        timeframe_pool = [tf for tf in timeframe_pool if tf in {"m1", "m5", "m10", "m15"}]
        if timeframe_pool and rng.random() < 0.40:
            block["timeframe"] = rng.choice(timeframe_pool)
    if name == "day_time_filter" and rng.random() < 0.35:
        start = rng.choice((0, 3, 6, 8, 12))
        end = rng.choice((18, 20, 22, 24))
        block["session_start_utc"] = min(start, end - 1)
        block["session_end_utc"] = end


def mutate_hypothesis(
    parent: HypothesisSpec,
    *,
    child_index: int,
    generation: int,
    seed: int = 910300,
) -> HypothesisSpec:
    """Create a nearby child from a profitable parent.

    Mutations are deliberately small: exits, session, direction, timeframe,
    and one rule block at a time. This keeps the search focused around edge
    instead of reverting to blind random sampling.
    """
    raw_seed = f"{parent.fingerprint}:{generation}:{child_index}:{seed}"
    rng = Random(int(hashlib.sha1(raw_seed.encode("utf-8")).hexdigest()[:12], 16))
    params: dict[str, object] = deepcopy(parent.parameters)

    if parent.lineage == "strategy_grammar":
        timeframe_pool = _timeframes_from_params(params, parent.timeframe)
        blocks = [dict(block) for block in list(params.get("rule_blocks") or []) if isinstance(block, dict)]
        enabled_groups = set(_block_groups_from_params(params))
        entry_blocks = _allowed_blocks(GRAMMAR_ENTRY_BLOCKS, enabled_groups)
        confirmation_blocks = _allowed_blocks(GRAMMAR_CONFIRMATION_BLOCKS, enabled_groups)
        filter_blocks = [dict(block) for block in GRAMMAR_FILTER_BLOCKS if str(block.get("group")) in enabled_groups]
        smt_blocks = _allowed_blocks(GRAMMAR_SMT_BLOCKS, enabled_groups) if "smt" in enabled_groups else []
        block_pool = entry_blocks + confirmation_blocks + filter_blocks + smt_blocks

        if blocks and rng.random() < 0.55:
            block = dict(rng.choice(blocks))
            block["_timeframe_pool"] = ",".join(timeframe_pool)
            _mutate_block_value(rng, block)
            block.pop("_timeframe_pool", None)
            replace_at = rng.randrange(len(blocks))
            blocks[replace_at] = block
        elif block_pool:
            fresh = _strip_group(dict(rng.choice(block_pool)))
            fresh["timeframe"] = rng.choice(timeframe_pool)
            if len(blocks) < 5 and rng.random() < 0.55:
                blocks.append(fresh)
            elif blocks:
                blocks[rng.randrange(len(blocks))] = fresh

        if len(blocks) > 2 and rng.random() < 0.20:
            del blocks[rng.randrange(len(blocks))]
        params["rule_blocks"] = blocks
        params["block_logic"] = "all" if len(blocks) <= 4 else "vote"
        params["min_block_votes"] = max(2, len(blocks) - 1)

    for key, choices in {
        "atr_stop": (0.6, 0.7, 0.9, 1.1, 1.4, 1.8, 2.2),
        "reward_risk": (0.8, 1.0, 1.25, 1.5, 2.0, 2.5),
        "atr_trail": (0.0, 0.4, 0.6, 1.0, 1.5, 2.0),
        "max_hold_bars": (3, 4, 6, 8, 12, 16, 24, 32),
        "session_start_utc": (0, 3, 6, 8, 12),
        "session_end_utc": (18, 20, 22, 24),
    }.items():
        if key in params and rng.random() < 0.45:
            _mutate_numeric(rng, params, key, choices)

    if int(params.get("session_start_utc", 0)) >= int(params.get("session_end_utc", 24)):
        params["session_start_utc"] = 0
        params["session_end_utc"] = 24
    if rng.random() < 0.20:
        params["direction_mode"] = rng.choice(DIRECTION_MODES)
    if rng.random() < 0.20:
        params["first_signal_per_day"] = not bool(params.get("first_signal_per_day", False))
    params["guided_parent"] = parent.strategy_id
    params["guided_generation"] = generation

    return HypothesisSpec(
        strategy_id=_id(parent.lineage, params),  # type: ignore[arg-type]
        lineage=parent.lineage,
        hypothesis=parent.hypothesis,
        timeframe=parent.timeframe,
        parameters=params,
    )


def _allowed_blocks(
    blocks: tuple[dict[str, object], ...],
    enabled_groups: set[str],
) -> list[dict[str, object]]:
    selected = [dict(block) for block in blocks if str(block.get("group")) in enabled_groups]
    return selected or [dict(block) for block in blocks]


def _strategy_grammar(
    max_variants: int,
    seed: int = 310200,
    *,
    block_groups: tuple[str, ...] | None = None,
    complexity: str = "medium",
    randomness: str = "balanced",
    grammar_timeframes: tuple[str, ...] = ("m5",),
) -> list[HypothesisProfile]:
    thesis = (
        "Autonomous strategy grammar: combine liquidity, ICT/SMT structure, "
        "imbalance, session, volatility, and exit blocks into explainable closed-bar rules."
    )
    seed_offset = {"low": 7, "balanced": 97, "high": 997}.get(randomness, 97)
    rng = Random(seed + seed_offset)
    enabled_groups = set(block_groups or ("liquidity", "structure", "imbalance", "orderflow", "sessions", "volatility"))
    timeframe_pool = tuple(dict.fromkeys(tf.lower() for tf in grammar_timeframes if tf.lower() in {"m1", "m5", "m10", "m15"})) or ("m5",)
    entry_blocks = _allowed_blocks(GRAMMAR_ENTRY_BLOCKS, enabled_groups)
    confirmation_blocks = _allowed_blocks(GRAMMAR_CONFIRMATION_BLOCKS, enabled_groups)
    filter_blocks = [dict(block) for block in GRAMMAR_FILTER_BLOCKS if str(block.get("group")) in enabled_groups]
    smt_blocks = _allowed_blocks(GRAMMAR_SMT_BLOCKS, enabled_groups) if "smt" in enabled_groups else []
    extra_confirmation_chance = {"simple": 0.15, "medium": 0.55, "complex": 0.85}.get(complexity, 0.55)
    filter_chance = {"simple": 0.35, "medium": 0.75, "complex": 0.90}.get(complexity, 0.75)
    smt_chance = {"simple": 0.04, "medium": 0.12, "complex": 0.25}.get(complexity, 0.12)
    profiles: list[HypothesisProfile] = []
    seen: set[str] = set()
    attempts = 0
    while len(profiles) < max_variants and attempts < max_variants * 12:
        attempts += 1
        entry = _strip_group(dict(rng.choice(entry_blocks)))
        entry["timeframe"] = rng.choice(timeframe_pool)
        confirmations = [_strip_group(dict(rng.choice(confirmation_blocks)))]
        confirmations[0]["timeframe"] = rng.choice(timeframe_pool)
        if rng.random() < extra_confirmation_chance:
            extra = _strip_group(dict(rng.choice(confirmation_blocks)))
            if extra["name"] != confirmations[0]["name"]:
                extra["timeframe"] = rng.choice(timeframe_pool)
                confirmations.append(extra)
        filters: list[dict[str, object]] = []
        if filter_blocks and rng.random() < filter_chance:
            selected_filter = _strip_group(dict(rng.choice(filter_blocks)))
            selected_filter["timeframe"] = rng.choice(timeframe_pool)
            filters.append(selected_filter)
        if smt_blocks and rng.random() < smt_chance:
            selected_smt = _strip_group(dict(rng.choice(smt_blocks)))
            selected_smt["timeframe"] = rng.choice(timeframe_pool)
            filters.append(selected_smt)

        blocks = [entry, *confirmations, *filters]
        params: dict[str, object] = {
            "recipe": "strategy_grammar",
            "grammar_complexity": complexity,
            "grammar_randomness": randomness,
            "grammar_block_groups": ",".join(sorted(enabled_groups)),
            "grammar_timeframes": ",".join(timeframe_pool),
            "rule_blocks": blocks,
            "block_logic": "all" if len(blocks) <= 4 else "vote",
            "min_block_votes": max(2, len(blocks) - 1),
            "direction_mode": rng.choice(DIRECTION_MODES),
            "session_start_utc": int(rng.choice((0, 3, 6, 8, 12))),
            "session_end_utc": int(rng.choice((18, 20, 22, 24))),
            "volatility_filter": "none",
            "stop_mode": rng.choice(("atr", "structure")),
            "atr_stop": rng.choice((0.7, 0.9, 1.1, 1.4, 1.8)),
            "reward_risk": rng.choice((0.8, 1.0, 1.25, 1.5, 2.0)),
            "atr_trail": rng.choice((0.0, 0.0, 0.6, 1.0, 1.5)),
            "max_hold_bars": rng.choice((4, 6, 8, 12, 16, 24)),
            "first_signal_per_day": rng.random() < 0.25,
        }
        if int(params["session_start_utc"]) >= int(params["session_end_utc"]):
            params["session_start_utc"] = 0
            params["session_end_utc"] = 24
        fp = _grammar_id(params)
        if fp in seen:
            continue
        seen.add(fp)
        profiles.append(HypothesisProfile("strategy_grammar", thesis, params))
    return profiles


BUILDERS = {
    "strategy_grammar": lambda: _strategy_grammar(1_000),
    "time_series_breakout": _time_series_breakout,
    "session_range_breakout": _session_range_breakout,
    "trend_pullback": _trend_pullback,
    "volatility_expansion": _volatility_expansion,
    "regime_mean_reversion": _regime_mean_reversion,
    "liquidity_sweep_reclaim": _liquidity_sweep_reclaim,
    "failed_breakout_reversal": _failed_breakout_reversal,
    "prior_day_level_continuation": _prior_day_level_continuation,
    "volatility_spike_reversal": _volatility_spike_reversal,
    "opening_range_continuation_reversal": _opening_range_continuation_reversal,
    "trend_day_pullback": _trend_day_pullback,
    "day_time_regime_filter": _day_time_regime_filter,
    "inside_bar_expansion": _inside_bar_expansion,
}


def _spec_from_grammar_profile(
    profile: HypothesisProfile,
    timeframe: str,
    *,
    market_mind: dict[str, object] | None = None,
) -> HypothesisSpec:
    params = dict(profile.parameters)
    if market_mind:
        params.update(market_mind)
    return HypothesisSpec(
        strategy_id=_id(profile.lineage, params),  # type: ignore[arg-type]
        lineage=profile.lineage,
        hypothesis=profile.thesis,
        timeframe=timeframe,  # type: ignore[arg-type]
        parameters=params,
    )


def _generate_market_mind_hypotheses(
    request: HypothesisDiscoveryRequest,
    market_mind_plan: dict[str, object],
) -> list[HypothesisSpec]:
    recipes = [
        recipe for recipe in list(market_mind_plan.get("recipes") or [])
        if isinstance(recipe, dict)
    ]
    bias_pct = float(market_mind_plan.get("bias_pct", request.market_mind_bias_pct))
    biased_budget = int(round(request.max_variants * max(0.0, min(1.0, bias_pct))))
    explore_budget = max(0, request.max_variants - biased_budget)
    timeframe_pool = request.grammar_timeframes or (request.timeframe,)
    specs: list[HypothesisSpec] = []
    seen: set[str] = set()

    recipe_total = sum(float(recipe.get("weight", 0.0) or 0.0) for recipe in recipes) or 1.0
    for index, recipe in enumerate(recipes):
        share = float(recipe.get("weight", 0.0) or 0.0) / recipe_total
        count = max(1, int(round(biased_budget * share))) if biased_budget > 0 else 0
        groups = tuple(
            str(group)
            for group in list(recipe.get("groups") or [])
            if str(group) in {"liquidity", "structure", "imbalance", "orderflow", "sessions", "volatility", "smt"}
        )
        profiles = _strategy_grammar(
            count,
            seed=710400 + index * 997,
            block_groups=groups or request.grammar_block_groups,
            complexity=request.grammar_complexity,
            randomness=request.grammar_randomness,
            grammar_timeframes=timeframe_pool,
        )
        for profile in profiles:
            spec = _spec_from_grammar_profile(
                profile,
                request.timeframe,
                market_mind={
                    "recipe": "market_mind",
                    "market_mind_regime": str(market_mind_plan.get("regime_id", "unknown")),
                    "market_mind_focus": str(recipe.get("name", "unknown")),
                    "market_mind_reason": str(recipe.get("why", "")),
                    "market_mind_groups": ",".join(groups),
                    "market_mind_bias_pct": bias_pct,
                },
            )
            if spec.strategy_id in seen:
                continue
            seen.add(spec.strategy_id)
            specs.append(spec)
            if len(specs) >= request.max_variants:
                return specs

    if explore_budget > 0 and len(specs) < request.max_variants:
        explore_profiles = _strategy_grammar(
            max(explore_budget, request.max_variants - len(specs)),
            seed=810400,
            block_groups=request.grammar_block_groups,
            complexity=request.grammar_complexity,
            randomness="high" if request.grammar_randomness != "low" else "balanced",
            grammar_timeframes=timeframe_pool,
        )
        for profile in explore_profiles:
            spec = _spec_from_grammar_profile(
                profile,
                request.timeframe,
                market_mind={
                    "recipe": "market_mind",
                    "market_mind_regime": str(market_mind_plan.get("regime_id", "unknown")),
                    "market_mind_focus": "exploration",
                    "market_mind_reason": "reserved random exploration budget",
                    "market_mind_groups": ",".join(request.grammar_block_groups or ()),
                    "market_mind_bias_pct": bias_pct,
                },
            )
            if spec.strategy_id in seen:
                continue
            seen.add(spec.strategy_id)
            specs.append(spec)
            if len(specs) >= request.max_variants:
                break
    return specs[: request.max_variants]


def generate_hypotheses(
    request: HypothesisDiscoveryRequest,
    market_mind_plan: dict[str, object] | None = None,
) -> list[HypothesisSpec]:
    """Generate a deterministic, explainable hypothesis set.

    This is deliberately not a random optimizer. Each variant comes from a coded
    market-behavior profile, then the FTMO challenge replay decides whether it
    deserves deeper MT5 testing.
    """
    if request.search_mode == "market_mind" and market_mind_plan:
        return _generate_market_mind_hypotheses(request, market_mind_plan)

    requested = request.families or tuple(BUILDERS.keys())  # type: ignore[assignment]
    family_profiles = {
        lineage: (
            _strategy_grammar(
                request.max_variants,
                seed=310200 + len(requested),
                block_groups=request.grammar_block_groups,
                complexity=request.grammar_complexity,
                randomness=request.grammar_randomness,
                grammar_timeframes=request.grammar_timeframes or (request.timeframe,),
            )
            if lineage == "strategy_grammar"
            else BUILDERS[lineage]()
        )
        for lineage in requested
    }
    indices = {lineage: 0 for lineage in requested}
    specs: list[HypothesisSpec] = []

    while len(specs) < request.max_variants:
        progressed = False
        for lineage in requested:
            profiles = family_profiles[lineage]
            index = indices[lineage]
            if index >= len(profiles):
                continue
            profile = profiles[index]
            indices[lineage] += 1
            progressed = True
            params = dict(profile.parameters)
            specs.append(_spec_from_profile(profile, request.timeframe))
            if len(specs) >= request.max_variants:
                break
        if not progressed:
            break

    return specs
