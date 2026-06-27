from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass

from .models import HypothesisDiscoveryRequest, HypothesisSpec, Lineage


@dataclass(frozen=True)
class HypothesisProfile:
    lineage: Lineage
    thesis: str
    parameters: dict[str, int | float | str | bool]


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


BUILDERS = {
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


def generate_hypotheses(request: HypothesisDiscoveryRequest) -> list[HypothesisSpec]:
    """Generate a deterministic, explainable hypothesis set.

    This is deliberately not a random optimizer. Each variant comes from a coded
    market-behavior profile, then the FTMO challenge replay decides whether it
    deserves deeper MT5 testing.
    """
    requested = request.families or tuple(BUILDERS.keys())  # type: ignore[assignment]
    family_profiles = {lineage: BUILDERS[lineage]() for lineage in requested}
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
            specs.append(
                HypothesisSpec(
                    strategy_id=_id(profile.lineage, params),
                    lineage=profile.lineage,
                    hypothesis=profile.thesis,
                    timeframe=request.timeframe,
                    parameters=params,
                )
            )
            if len(specs) >= request.max_variants:
                break
        if not progressed:
            break

    return specs
