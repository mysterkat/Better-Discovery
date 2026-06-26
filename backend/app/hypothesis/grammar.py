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


BUILDERS = {
    "time_series_breakout": _time_series_breakout,
    "session_range_breakout": _session_range_breakout,
    "trend_pullback": _trend_pullback,
    "volatility_expansion": _volatility_expansion,
    "regime_mean_reversion": _regime_mean_reversion,
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
