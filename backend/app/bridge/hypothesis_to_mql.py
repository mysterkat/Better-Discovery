"""Hypothesis strategy -> standalone MQL5 EA exporter."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from ..hypothesis.models import HypothesisSpec
from ..paths import USER_DATA


_OUTPUT_DIR = USER_DATA / "mql" / "hypothesis"
_MT5_EXPORT_SUBDIR = Path("MQL5") / "Experts" / "BetterDiscovery" / "Hypothesis"


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", value).strip("_")
    return cleaned[:80] or "HypothesisStrategy"


def _mql_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _mql_bool(value: bool) -> str:
    return "true" if value else "false"


def _as_float(params: dict[str, Any], key: str, default: float) -> float:
    try:
        return float(params.get(key, default))
    except (TypeError, ValueError):
        return default


def _as_int(params: dict[str, Any], key: str, default: int) -> int:
    try:
        return int(float(params.get(key, default)))
    except (TypeError, ValueError):
        return default


def _as_bool(params: dict[str, Any], key: str, default: bool) -> bool:
    raw = params.get(key, default)
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _timeframe_constant(timeframe: str) -> str:
    return {
        "m1": "PERIOD_M1",
        "m5": "PERIOD_M5",
        "m10": "PERIOD_M10",
        "m15": "PERIOD_M15",
    }.get(timeframe.lower(), "PERIOD_M15")


def _timeframe_set_value(value: object) -> str:
    return {"PERIOD_M1": "1", "PERIOD_M5": "5", "PERIOD_M10": "10", "PERIOD_M15": "15"}.get(str(value), str(value))


def _install_to_active_mt5(mq5_path: Path, set_path: Path, spec_path: Path) -> dict[str, Any]:
    """Copy exported files into the currently connected MT5 data folder.

    This avoids Windows file association opening a different, clean terminal.
    If MT5 is unavailable, export remains valid in userdata and the caller gets
    a warning instead of a hard failure.
    """
    try:
        from .mt5_setup import _resolve_mt5_paths  # noqa: WPS433

        paths = _resolve_mt5_paths()
    except Exception as exc:  # noqa: BLE001
        return {
            "installed": False,
            "preferred_mq5_path": str(mq5_path),
            "mt5_data_path": None,
            "warning": (
                "Could not resolve active MT5 data folder. Open your normal MT5 terminal, "
                f"log in, then export again. Detail: {type(exc).__name__}: {exc}"
            ),
        }

    data_path = Path(str(paths.get("data") or ""))
    if not data_path.is_dir():
        return {
            "installed": False,
            "preferred_mq5_path": str(mq5_path),
            "mt5_data_path": str(data_path),
            "warning": f"Resolved MT5 data folder does not exist: {data_path}",
        }

    target_dir = data_path / _MT5_EXPORT_SUBDIR
    target_dir.mkdir(parents=True, exist_ok=True)
    mt5_mq5 = target_dir / mq5_path.name
    mt5_set = target_dir / set_path.name
    mt5_spec = target_dir / spec_path.name
    shutil.copy2(mq5_path, mt5_mq5)
    shutil.copy2(set_path, mt5_set)
    shutil.copy2(spec_path, mt5_spec)
    warnings: list[str] = []
    trade_include = data_path / "MQL5" / "Include" / "Trade" / "Trade.mqh"
    if not trade_include.is_file():
        warnings.append(
            f"MT5 standard library include is missing in this terminal: {trade_include}"
        )
    return {
        "installed": True,
        "preferred_mq5_path": str(mt5_mq5),
        "mt5_mq5_path": str(mt5_mq5),
        "mt5_set_path": str(mt5_set),
        "mt5_spec_path": str(mt5_spec),
        "mt5_data_path": str(data_path),
        "mt5_experts_folder": str(target_dir),
        "warnings": warnings,
    }


def _default_max_hold(strategy: HypothesisSpec) -> int:
    if strategy.lineage == "trend_pullback":
        return 96
    if strategy.lineage == "volatility_expansion":
        return max(32, _as_int(strategy.parameters, "lookback", 16) * 2)
    return _as_int(strategy.parameters, "max_hold_bars", 16)


def _input_values(
    strategy: HypothesisSpec,
    *,
    magic_number: int,
    risk_fraction: float,
    daily_loss_pct: float,
    max_loss_pct: float,
    max_trades_per_day: int,
    max_spread_points: float,
) -> dict[str, str | int | float | bool]:
    params = strategy.parameters
    direction_mode = str(params.get("direction_mode", "both"))
    volatility_filter = str(params.get("volatility_filter", "none"))
    return {
        "InpStrategyId": strategy.strategy_id,
        "InpLineage": strategy.lineage,
        "InpHypothesis": strategy.hypothesis,
        "InpMagic": magic_number,
        "InpSignalTimeframe": _timeframe_constant(strategy.timeframe),
        "InpRiskFraction": risk_fraction,
        "InpFixedLots": 0.0,
        "InpDailyLossPct": daily_loss_pct,
        "InpMaxLossPct": max_loss_pct,
        "InpMaxTradesPerDay": max_trades_per_day,
        "InpMaxSpreadPoints": max_spread_points,
        "InpSlippagePoints": 20,
        "InpServerUtcOffsetHours": 0,
        "InpDirectionMode": direction_mode,
        "InpVolatilityFilter": volatility_filter,
        "InpSessionStartUtc": _as_int(params, "session_start_utc", 0),
        "InpSessionEndUtc": _as_int(params, "session_end_utc", 24),
        "InpMaxHoldBars": _default_max_hold(strategy),
        "InpAtrStop": _as_float(params, "atr_stop", 1.0),
        "InpAtrTrail": _as_float(params, "atr_trail", 0.0),
        "InpRewardRisk": _as_float(params, "reward_risk", 1.0),
        "InpChannelBars": _as_int(params, "channel_bars", 20),
        "InpLookback": _as_int(params, "lookback", _as_int(params, "sweep_lookback", 20)),
        "InpSqueezeQuantile": _as_float(params, "squeeze_quantile", 0.2),
        "InpVolumeRatio": _as_float(params, "volume_ratio", 1.0),
        "InpEmaLength": _as_int(params, "ema_length", 20),
        "InpPullbackAtr": _as_float(params, "pullback_atr", 0.5),
        "InpRsiTrigger": _as_float(params, "rsi_trigger", 50.0),
        "InpZLength": _as_int(params, "z_length", 32),
        "InpZEntry": _as_float(params, "z_entry", 1.75),
        "InpRsiExtreme": _as_float(params, "rsi_extreme", 30.0),
        "InpSweepLookback": _as_int(params, "sweep_lookback", 24),
        "InpPenetrationAtr": _as_float(params, "penetration_atr", 0.1),
        "InpReclaimBufferAtr": _as_float(params, "reclaim_buffer_atr", 0.0),
        "InpWickRejectMin": _as_float(params, "wick_reject_min", _as_float(params, "wick_min", 0.4)),
        "InpCloseLocationMin": _as_float(params, "close_location_min", 0.5),
        "InpBreakAtr": _as_float(params, "break_atr", _as_float(params, "break_buffer_atr", 0.0)),
        "InpCloseBackAtr": _as_float(params, "close_back_atr", 0.0),
        "InpBreakBufferAtr": _as_float(params, "break_buffer_atr", 0.0),
        "InpSpikeStyle": str(params.get("spike_style", "capitulation")),
        "InpSpikeRangeAtr": _as_float(params, "spike_range_atr", 1.8),
        "InpBodyMin": _as_float(params, "body_min", 0.5),
        "InpRangeStartUtc": _as_int(params, "range_start_utc", 0),
        "InpRangeEndUtc": _as_int(params, "range_end_utc", 6),
        "InpOpeningRangeMode": str(params.get("opening_range_mode", "continuation")),
        "InpRangeSweepAtr": _as_float(params, "sweep_atr", 0.05),
        "InpFirstSignalPerDay": _as_bool(params, "first_signal_per_day", _as_bool(params, "first_breakout_per_day", False)),
        "InpTrendOpenAtr": _as_float(params, "trend_open_atr", 0.25),
        "InpRollingSharpeMin": _as_float(params, "rolling_sharpe_min", -999.0),
        "InpWeekdays": str(params.get("weekdays", "0,1,2,3,4")),
        "InpRegimeMode": str(params.get("regime_mode", "any")),
        "InpSignalMode": str(params.get("signal_mode", "momentum")),
        "InpMomentumBars": _as_int(params, "momentum_bars", 8),
        "InpMomentumAtr": _as_float(params, "momentum_atr", 0.35),
        "InpContextFilter": str(params.get("context_filter", "none")),
        "InpGrammarStopMode": str(params.get("stop_mode", "atr")),
    }


def _input_block(values: dict[str, str | int | float | bool]) -> str:
    lines = [
        f'input string InpStrategyId = "{_mql_string(str(values["InpStrategyId"]))}";',
        f'input string InpLineage = "{_mql_string(str(values["InpLineage"]))}";',
        f'input string InpHypothesis = "{_mql_string(str(values["InpHypothesis"]))}";',
        f"input long InpMagic = {values['InpMagic']};",
        f"input ENUM_TIMEFRAMES InpSignalTimeframe = {values['InpSignalTimeframe']};",
    ]
    for key, value in values.items():
        if key in {"InpStrategyId", "InpLineage", "InpHypothesis", "InpMagic", "InpSignalTimeframe"}:
            continue
        if isinstance(value, bool):
            lines.append(f"input bool {key} = {_mql_bool(value)};")
        elif isinstance(value, int):
            lines.append(f"input int {key} = {value};")
        elif isinstance(value, float):
            lines.append(f"input double {key} = {value:.8g};")
        else:
            lines.append(f'input string {key} = "{_mql_string(str(value))}";')
    return "\n".join(lines)


def _set_text(values: dict[str, str | int | float | bool]) -> str:
    lines = []
    for key, value in values.items():
        if key == "InpSignalTimeframe":
            raw = _timeframe_set_value(value)
        elif isinstance(value, bool):
            raw = "true" if value else "false"
        else:
            raw = str(value)
        lines.append(f"{key}={raw}")
    return "\n".join(lines) + "\n"


def _mql_num(value: Any, default: float = 0.0) -> str:
    try:
        return f"{float(value):.10g}"
    except (TypeError, ValueError):
        return f"{default:.10g}"


def _mql_int(value: Any, default: int = 0) -> str:
    try:
        return str(int(float(value)))
    except (TypeError, ValueError):
        return str(default)


def _mql_text(value: Any, default: str = "") -> str:
    raw = str(value if value is not None else default)
    return _mql_string(raw)


def _grammar_block_function(index: int, block: dict[str, Any], default_timeframe: str = "m15") -> str:
    name = str(block.get("name", ""))
    lookback = _mql_int(block.get("lookback", 24), 24)
    buffer_atr = _mql_num(block.get("buffer_atr", 0.0), 0.0)
    penetration_atr = _mql_num(block.get("penetration_atr", 0.05), 0.05)
    wick_min = _mql_num(block.get("wick_min", block.get("wick_reject_min", 0.35)), 0.35)
    close_location_min = _mql_num(block.get("close_location_min", 0.45), 0.45)
    range_start = _mql_int(block.get("range_start_utc", 0), 0)
    range_end = _mql_int(block.get("range_end_utc", 6), 6)
    session_start = _mql_int(block.get("session_start_utc", block.get("range_end_utc", 0)), 0)
    session_end = _mql_int(block.get("session_end_utc", 24), 24)
    mode = _mql_text(block.get("mode", "new_or_retrace"), "new_or_retrace")
    weekdays = _mql_text(block.get("weekdays", "0,1,2,3,4"), "0,1,2,3,4")
    swing_left = _mql_int(block.get("swing_left", 2), 2)
    swing_right = _mql_int(block.get("swing_right", 2), 2)
    body_min = _mql_num(block.get("body_min", 0.50), 0.50)
    range_atr = _mql_num(block.get("range_atr", block.get("spike_range_atr", 1.2)), 1.2)
    rsi_extreme = _mql_num(block.get("rsi_extreme", 30.0), 30.0)
    ema_length = _mql_int(block.get("ema_length", 20), 20)
    pullback_atr = _mql_num(block.get("pullback_atr", 0.5), 0.5)
    rsi_trigger = _mql_num(block.get("rsi_trigger", 50.0), 50.0)
    max_distance_atr = _mql_num(block.get("max_distance_atr", 1.0), 1.0)
    ob_lookback = _mql_int(block.get("ob_lookback", 5), 5)
    displacement_atr = _mql_num(block.get("displacement_atr", 1.4), 1.4)
    retest_bars = _mql_int(block.get("retest_bars", 8), 8)
    tolerance_atr = _mql_num(block.get("tolerance_atr", 0.15), 0.15)
    min_rng_atr = _mql_num(block.get("min_rng_atr", 1.0), 1.0)
    max_rng_atr = _mql_num(block.get("max_rng_atr", 3.0), 3.0)
    trend_open_atr = _mql_num(block.get("trend_open_atr", 0.35), 0.35)
    quantile_factor = _mql_num(0.75 + float(block.get("quantile", 0.25) or 0.25), 1.0)
    timeframe = _timeframe_constant(str(block.get("timeframe", default_timeframe)))

    header = f"""bool GrammarBlock{index}(const int direction)
{{
   ENUM_TIMEFRAMES tf = {timeframe};
   MqlRates b, prev;
   if(!GBar(tf, 1, b) || !GBar(tf, 2, prev)) return false;
   double atr = GATR(tf, 1);
"""
    footer = "\n   return false;\n}\n"

    if name in {"liquidity_sweep_reclaim", "stop_run_reclaim"}:
        body = f"""
   double ph = HighestHigh(2, {lookback}), pl = LowestLow(2, {lookback});
   if(direction > 0)
      return b.low < pl - {penetration_atr} * atr && b.close > pl && LowerWickPct(b) >= {wick_min} && CloseLocation(b) >= {close_location_min};
   return b.high > ph + {penetration_atr} * atr && b.close < ph && UpperWickPct(b) >= {wick_min} && (1.0 - CloseLocation(b)) >= {close_location_min};
"""
    elif name in {"prior_day_liquidity", "prior_day_high_low"}:
        body = f"""
   double pdh = iHigh(_Symbol, PERIOD_D1, 1), pdl = iLow(_Symbol, PERIOD_D1, 1);
   if(direction > 0) return b.low < pdl - {buffer_atr} * atr && b.close > pdl + {buffer_atr} * atr;
   return b.high > pdh + {buffer_atr} * atr && b.close < pdh - {buffer_atr} * atr;
"""
    elif name in {"session_liquidity", "asian_range_liquidity", "london_sweep", "ny_sweep"}:
        if name == "asian_range_liquidity":
            range_start, range_end = "0", "6"
        elif name == "london_sweep":
            range_start, range_end = "6", "10"
        elif name == "ny_sweep":
            range_start, range_end = "12", "15"
        body = f"""
   double rh, rl;
   if(!RangeWindowLevels(1, {range_start}, {range_end}, rh, rl)) return false;
   int hour = UtcHour(b.time);
   bool in_window = hour >= {session_start} && hour < {session_end};
   if(!in_window) return false;
   if(direction > 0) return b.low < rl - {buffer_atr} * atr && b.close > rl + {buffer_atr} * atr;
   return b.high > rh + {buffer_atr} * atr && b.close < rh - {buffer_atr} * atr;
"""
    elif name == "equal_high_low_liquidity":
        body = f"""
   double ph = HighestHigh(2, {lookback}), pl = LowestLow(2, {lookback});
   double range = ph - pl;
   if(range > {tolerance_atr} * atr) return false;
   if(direction > 0) return b.low < pl - {buffer_atr} * atr && b.close > pl;
   return b.high > ph + {buffer_atr} * atr && b.close < ph;
"""
    elif name == "failed_breakout_reversal":
        body = f"""
   double ph = HighestHigh(2, {lookback}), pl = LowestLow(2, {lookback});
   if(direction > 0) return b.low < pl - {buffer_atr} * atr && b.close > pl && b.close > b.open;
   return b.high > ph + {buffer_atr} * atr && b.close < ph && b.close < b.open;
"""
    elif name in {"opening_range_break", "opening_range_reversal"}:
        if name == "opening_range_break":
            body = f"""
   double rh, rl;
   if(!RangeWindowLevels(1, {range_start}, {range_end}, rh, rl)) return false;
   int hour = UtcHour(b.time);
   if(hour < {session_start} || hour >= {session_end}) return false;
   if(direction > 0) return b.close > rh + {buffer_atr} * atr && prev.close <= rh + {buffer_atr} * atr;
   return b.close < rl - {buffer_atr} * atr && prev.close >= rl - {buffer_atr} * atr;
"""
        else:
            body = f"""
   double rh, rl;
   if(!RangeWindowLevels(1, {range_start}, {range_end}, rh, rl)) return false;
   int hour = UtcHour(b.time);
   if(hour < {session_start} || hour >= {session_end}) return false;
   if(direction > 0) return b.low < rl - {buffer_atr} * atr && b.close > rl;
   return b.high > rh + {buffer_atr} * atr && b.close < rh;
"""
    elif name == "inside_bar_expansion":
        body = f"""
   MqlRates mother;
   if(!Bar(3, mother) || !IsInsidePrevious()) return false;
   if(direction > 0) return b.close > mother.high + {buffer_atr} * atr;
   return b.close < mother.low - {buffer_atr} * atr;
"""
    elif name in {"fair_value_gap", "fvg"}:
        body = f"""
   double lower, upper;
   string mode = "{mode}";
   bool fresh = FreshFvg(direction, lower, upper);
   if(mode == "new") return fresh;
   if(!fresh && !FindLatestFvg(direction, 60, lower, upper)) return false;
   if(direction > 0) return b.low <= upper && b.close >= lower;
   return b.high >= lower && b.close <= upper;
"""
    elif name in {"inverse_fair_value_gap", "ifvg"}:
        body = f"""
   double lower, upper;
   int opposite = direction > 0 ? -1 : 1;
   if(!FindLatestFvg(opposite, 80, lower, upper)) return false;
   if(direction > 0) return b.close > upper + {buffer_atr} * atr;
   return b.close < lower - {buffer_atr} * atr;
"""
    elif name == "balanced_price_range":
        body = """
   double l1, u1, l2, u2;
   return FindLatestFvg(1, 40, l1, u1) && FindLatestFvg(-1, 40, l2, u2);
"""
    elif name == "displacement_candle":
        body = f"""
   bool strong = RangeAtr(b, 1) >= {range_atr} && BodyPct(b) >= {body_min};
   if(direction > 0) return strong && b.close > b.open;
   return strong && b.close < b.open;
"""
    elif name in {"fvg_fill", "fvg_mitigation", "fvg_mitigation_rejection"}:
        body = """
   double lower, upper;
   if(!FindLatestFvg(direction, 80, lower, upper)) return false;
   if(direction > 0) return b.low <= upper && b.close > lower && b.close > b.open;
   return b.high >= lower && b.close < upper && b.close < b.open;
"""
    elif name in {"order_block", "mitigation_block", "rejection_block"}:
        require_reject = "true" if name == "rejection_block" else "false"
        body = f"""
   return OrderBlockTouch(direction, {ob_lookback}, {displacement_atr}, {require_reject});
"""
    elif name == "breaker_block":
        body = f"""
   return BreakerBlockTouch(direction, {ob_lookback}, {displacement_atr}, {retest_bars});
"""
    elif name in {"market_structure_shift", "break_of_structure", "change_of_character", "internal_structure_break", "external_structure_break"}:
        if name == "internal_structure_break":
            swing_left, swing_right = "1", "1"
        elif name == "external_structure_break":
            swing_left, swing_right = "4", "3"
        choch_filter = ""
        if name == "change_of_character":
            choch_filter = "\n   if(direction > 0 && H1Trend() >= 0) return false;\n   if(direction < 0 && H1Trend() <= 0) return false;"
        context_filter = "\n   if(!ContextOk(direction)) return false;" if name == "break_of_structure" else ""
        body = f"""
   double level = 0.0;
   if(direction > 0)
   {{
      if(!LatestSwingHigh({swing_left}, {swing_right}, level)) return false;{choch_filter}{context_filter}
      return b.close > level + {buffer_atr} * atr && prev.close <= level + {buffer_atr} * atr;
   }}
   if(!LatestSwingLow({swing_left}, {swing_right}, level)) return false;{choch_filter}{context_filter}
   return b.close < level - {buffer_atr} * atr && prev.close >= level - {buffer_atr} * atr;
"""
    elif name == "higher_timeframe_bias":
        body = """
   return ContextOk(direction);
"""
    elif name == "premium_discount":
        body = """
   double hi = 0.0, lo = 0.0;
   if(!LatestSwingHigh(3, 2, hi) || !LatestSwingLow(3, 2, lo)) return false;
   double midpoint = (hi + lo) / 2.0;
   if(direction > 0) return b.close <= midpoint;
   return b.close >= midpoint;
"""
    elif name == "liquidity_pool_distance":
        body = f"""
   double ph = HighestHigh(2, {lookback}), pl = LowestLow(2, {lookback});
   if(direction > 0) return MathAbs(b.close - pl) <= {max_distance_atr} * atr;
   return MathAbs(ph - b.close) <= {max_distance_atr} * atr;
"""
    elif name == "volatility_regime":
        body = f"""
   string mode = "{mode}";
   if(mode == "compression") return BBWidth(1) <= AvgBBWidth(1, 100) * {quantile_factor};
   if(mode == "high_vol_kill") return RangeAtr(b, 1) <= {max_rng_atr};
   return RangeAtr(b, 1) >= {min_rng_atr};
"""
    elif name == "trend_day":
        body = f"""
   double day_open = iOpen(_Symbol, PERIOD_D1, 0);
   if(direction > 0) return ContextOk(direction) && b.close > day_open + {trend_open_atr} * atr;
   return ContextOk(direction) && b.close < day_open - {trend_open_atr} * atr;
"""
    elif name == "day_time_filter":
        body = f"""
   string needle = "," + IntegerToString(UtcWeekdayPython(b.time)) + ",";
   string haystack = ",{weekdays},";
   if(StringFind(haystack, needle) < 0) return false;
   int hour = UtcHour(b.time);
   return hour >= {session_start} && hour < {session_end};
"""
    elif name == "trend_pullback":
        body = f"""
   double ema = EMAByLength({ema_length}, 1);
   if(direction > 0) return b.low <= ema + {pullback_atr} * atr && b.close > ema && RSI(1) > {rsi_trigger};
   return b.high >= ema - {pullback_atr} * atr && b.close < ema && RSI(1) < 100.0 - {rsi_trigger};
"""
    elif name == "volatility_spike_reversal":
        body = f"""
   bool range_ok = RangeAtr(b, 1) >= {range_atr};
   if(direction > 0) return range_ok && RSI(1) <= {rsi_extreme} && (b.close > b.open || LowerWickPct(b) >= {wick_min});
   return range_ok && RSI(1) >= 100.0 - {rsi_extreme} && (b.close < b.open || UpperWickPct(b) >= {wick_min});
"""
    elif name in {"smt_divergence", "smt"}:
        body = """
   // SMT requires imported external proxy OHLC. The standalone EA keeps this strict.
   return false;
"""
    else:
        body = f"""
   // Unsupported generated block: {name}
   return false;
"""
    replacements = (
        ("RangeWindowLevels(", "GRangeWindowLevels(tf, "),
        ("HighestHigh(", "GHighestHigh(tf, "),
        ("LowestLow(", "GLowestLow(tf, "),
        ("LatestSwingHigh(", "GLatestSwingHigh(tf, "),
        ("LatestSwingLow(", "GLatestSwingLow(tf, "),
        ("FreshFvg(", "GFreshFvg(tf, "),
        ("FindLatestFvg(", "GFindLatestFvg(tf, "),
        ("OrderBlockTouch(", "GOrderBlockTouch(tf, "),
        ("BreakerBlockTouch(", "GBreakerBlockTouch(tf, "),
        ("IsInsidePrevious()", "GIsInsidePrevious(tf)"),
        ("EMAByLength(", "GEMAByLength(tf, "),
        ("RangeAtr(", "GRangeAtr(tf, "),
        ("AvgBBWidth(", "GAvgBBWidth(tf, "),
        ("BBWidth(", "GBBWidth(tf, "),
        ("RSI(", "GRSI(tf, "),
        ("Bar(", "GBar(tf, "),
    )
    for old, new in replacements:
        body = body.replace(old, new)
    return header + body + footer


def _grammar_helpers(strategy: HypothesisSpec) -> str:
    if strategy.lineage != "strategy_grammar":
        return ""
    blocks = list(strategy.parameters.get("rule_blocks") or [])
    block_functions = "\n".join(
        _grammar_block_function(index, block, strategy.timeframe)
        for index, block in enumerate(blocks)
        if isinstance(block, dict)
    )
    return f"""
bool GBar(const ENUM_TIMEFRAMES tf, const int shift, MqlRates &bar)
{{
   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   if(CopyRates(_Symbol, tf, shift, 1, rates) != 1)
      return false;
   bar = rates[0];
   return true;
}}

bool GRatesFrom(const ENUM_TIMEFRAMES tf, const int shift, const int count, MqlRates &rates[])
{{
   ArraySetAsSeries(rates, true);
   return CopyRates(_Symbol, tf, shift, count, rates) == count;
}}

double GBufferValue(const int handle, const int buffer, const int shift)
{{
   double values[];
   ArraySetAsSeries(values, true);
   if(CopyBuffer(handle, buffer, shift, 1, values) != 1)
      return 0.0;
   return values[0];
}}

double GATR(const ENUM_TIMEFRAMES tf, const int shift)
{{
   int handle = iATR(_Symbol, tf, 14);
   if(handle == INVALID_HANDLE) return _Point;
   double value = MathMax(GBufferValue(handle, 0, shift), _Point);
   IndicatorRelease(handle);
   return value;
}}

double GRSI(const ENUM_TIMEFRAMES tf, const int shift)
{{
   int handle = iRSI(_Symbol, tf, 14, PRICE_CLOSE);
   if(handle == INVALID_HANDLE) return 50.0;
   double value = GBufferValue(handle, 0, shift);
   IndicatorRelease(handle);
   return value;
}}

double GEMAByLength(const ENUM_TIMEFRAMES tf, const int length, const int shift)
{{
   int period = length <= 20 ? 20 : (length <= 50 ? 50 : 200);
   int handle = iMA(_Symbol, tf, period, 0, MODE_EMA, PRICE_CLOSE);
   if(handle == INVALID_HANDLE) return 0.0;
   double value = GBufferValue(handle, 0, shift);
   IndicatorRelease(handle);
   return value;
}}

double GRangeAtr(const ENUM_TIMEFRAMES tf, const MqlRates &bar, const int shift) {{ return (bar.high - bar.low) / GATR(tf, shift); }}

double GHighestHigh(const ENUM_TIMEFRAMES tf, const int start_shift, const int count)
{{
   MqlRates rates[];
   if(!GRatesFrom(tf, start_shift, MathMax(count, 1), rates)) return 0.0;
   double value = rates[0].high;
   for(int i = 1; i < ArraySize(rates); i++) value = MathMax(value, rates[i].high);
   return value;
}}

double GLowestLow(const ENUM_TIMEFRAMES tf, const int start_shift, const int count)
{{
   MqlRates rates[];
   if(!GRatesFrom(tf, start_shift, MathMax(count, 1), rates)) return 0.0;
   double value = rates[0].low;
   for(int i = 1; i < ArraySize(rates); i++) value = MathMin(value, rates[i].low);
   return value;
}}

double GMeanClose(const ENUM_TIMEFRAMES tf, const int shift, const int count)
{{
   MqlRates rates[];
   if(!GRatesFrom(tf, shift, MathMax(count, 1), rates)) return 0.0;
   double sum = 0.0;
   for(int i = 0; i < ArraySize(rates); i++) sum += rates[i].close;
   return sum / MathMax(ArraySize(rates), 1);
}}

double GStdClose(const ENUM_TIMEFRAMES tf, const int shift, const int count)
{{
   MqlRates rates[];
   if(!GRatesFrom(tf, shift, MathMax(count, 2), rates)) return 0.0;
   double mean = 0.0;
   for(int i = 0; i < ArraySize(rates); i++) mean += rates[i].close;
   mean /= MathMax(ArraySize(rates), 1);
   double var = 0.0;
   for(int i = 0; i < ArraySize(rates); i++) var += MathPow(rates[i].close - mean, 2.0);
   return MathSqrt(var / MathMax(ArraySize(rates), 1));
}}

double GBBWidth(const ENUM_TIMEFRAMES tf, const int shift)
{{
   double mean = GMeanClose(tf, shift, 20);
   if(mean <= 0.0) return 0.0;
   return 4.0 * GStdClose(tf, shift, 20) / mean;
}}

double GAvgBBWidth(const ENUM_TIMEFRAMES tf, const int shift, const int count)
{{
   double sum = 0.0;
   int n = MathMax(count, 1);
   for(int i = 0; i < n; i++) sum += GBBWidth(tf, shift + i);
   return sum / n;
}}

bool GRangeWindowLevels(const ENUM_TIMEFRAMES tf, const int shift, const int start_hour, const int end_hour, double &range_high, double &range_low)
{{
   MqlRates signal_bar;
   if(!GBar(tf, shift, signal_bar)) return false;
   int key = DayKey(signal_bar.time);
   MqlRates rates[];
   if(!GRatesFrom(tf, shift, 400, rates)) return false;
   bool found = false;
   range_high = -DBL_MAX;
   range_low = DBL_MAX;
   for(int i = 0; i < ArraySize(rates); i++)
   {{
      if(DayKey(rates[i].time) != key) continue;
      int hour = UtcHour(rates[i].time);
      if(hour >= start_hour && hour < end_hour)
      {{
         found = true;
         range_high = MathMax(range_high, rates[i].high);
         range_low = MathMin(range_low, rates[i].low);
      }}
   }}
   return found;
}}

bool GIsInsidePrevious(const ENUM_TIMEFRAMES tf)
{{
   MqlRates prev, mother;
   if(!GBar(tf, 2, prev) || !GBar(tf, 3, mother)) return false;
   return prev.high < mother.high && prev.low > mother.low;
}}

bool GLatestSwingHigh(const ENUM_TIMEFRAMES tf, const int left, const int right, double &level)
{{
   for(int shift = 1 + right; shift < 220; shift++)
   {{
      MqlRates candidate, other;
      if(!GBar(tf, shift, candidate)) return false;
      bool ok = true;
      for(int k = 1; k <= left; k++) {{ if(!GBar(tf, shift + k, other) || candidate.high < other.high) ok = false; }}
      for(int k = 1; k <= right; k++) {{ if(!GBar(tf, shift - k, other) || candidate.high < other.high) ok = false; }}
      if(ok) {{ level = candidate.high; return true; }}
   }}
   return false;
}}

bool GLatestSwingLow(const ENUM_TIMEFRAMES tf, const int left, const int right, double &level)
{{
   for(int shift = 1 + right; shift < 220; shift++)
   {{
      MqlRates candidate, other;
      if(!GBar(tf, shift, candidate)) return false;
      bool ok = true;
      for(int k = 1; k <= left; k++) {{ if(!GBar(tf, shift + k, other) || candidate.low > other.low) ok = false; }}
      for(int k = 1; k <= right; k++) {{ if(!GBar(tf, shift - k, other) || candidate.low > other.low) ok = false; }}
      if(ok) {{ level = candidate.low; return true; }}
   }}
   return false;
}}

bool GFreshFvg(const ENUM_TIMEFRAMES tf, const int direction, double &lower, double &upper)
{{
   MqlRates b, older;
   if(!GBar(tf, 1, b) || !GBar(tf, 3, older)) return false;
   if(direction > 0 && b.low > older.high) {{ lower = older.high; upper = b.low; return true; }}
   if(direction < 0 && b.high < older.low) {{ lower = b.high; upper = older.low; return true; }}
   return false;
}}

bool GFindLatestFvg(const ENUM_TIMEFRAMES tf, const int direction, const int lookback, double &lower, double &upper)
{{
   for(int shift = 1; shift <= lookback; shift++)
   {{
      MqlRates b, older;
      if(!GBar(tf, shift, b) || !GBar(tf, shift + 2, older)) return false;
      if(direction > 0 && b.low > older.high) {{ lower = older.high; upper = b.low; return true; }}
      if(direction < 0 && b.high < older.low) {{ lower = b.high; upper = older.low; return true; }}
   }}
   return false;
}}

bool GOrderBlockTouch(const ENUM_TIMEFRAMES tf, const int direction, const int lookback, const double displacement_atr, const bool require_reject)
{{
   MqlRates b;
   if(!GBar(tf, 1, b)) return false;
   for(int shift = 2; shift <= lookback + 2; shift++)
   {{
      MqlRates impulse, zone;
      if(!GBar(tf, shift - 1, impulse) || !GBar(tf, shift, zone)) continue;
      bool displacement = GRangeAtr(tf, impulse, shift - 1) >= displacement_atr && BodyPct(impulse) >= 0.55;
      if(direction > 0 && displacement && impulse.close > impulse.open && zone.close < zone.open)
      {{
         bool touched = b.low <= zone.high && b.close >= zone.low;
         return touched && (!require_reject || b.close > b.open);
      }}
      if(direction < 0 && displacement && impulse.close < impulse.open && zone.close > zone.open)
      {{
         bool touched = b.high >= zone.low && b.close <= zone.high;
         return touched && (!require_reject || b.close < b.open);
      }}
   }}
   return false;
}}

bool GBreakerBlockTouch(const ENUM_TIMEFRAMES tf, const int direction, const int lookback, const double displacement_atr, const int retest_bars)
{{
   MqlRates b;
   if(!GBar(tf, 1, b)) return false;
   for(int shift = 2; shift <= lookback + retest_bars + 2; shift++)
   {{
      MqlRates impulse, zone;
      if(!GBar(tf, shift - 1, impulse) || !GBar(tf, shift, zone)) continue;
      bool displacement = GRangeAtr(tf, impulse, shift - 1) >= displacement_atr && BodyPct(impulse) >= 0.55;
      if(direction > 0 && displacement && impulse.close < impulse.open && zone.close > zone.open)
      {{
         if(b.close > zone.high && b.low <= zone.high) return true;
      }}
      if(direction < 0 && displacement && impulse.close > impulse.open && zone.close < zone.open)
      {{
         if(b.close < zone.low && b.high >= zone.low) return true;
      }}
   }}
   return false;
}}

bool LatestSwingHigh(const int left, const int right, double &level)
{{
   for(int shift = 1 + right; shift < 220; shift++)
   {{
      MqlRates candidate, other;
      if(!Bar(shift, candidate)) return false;
      bool ok = true;
      for(int k = 1; k <= left; k++) {{ if(!Bar(shift + k, other) || candidate.high < other.high) ok = false; }}
      for(int k = 1; k <= right; k++) {{ if(!Bar(shift - k, other) || candidate.high < other.high) ok = false; }}
      if(ok) {{ level = candidate.high; return true; }}
   }}
   return false;
}}

bool LatestSwingLow(const int left, const int right, double &level)
{{
   for(int shift = 1 + right; shift < 220; shift++)
   {{
      MqlRates candidate, other;
      if(!Bar(shift, candidate)) return false;
      bool ok = true;
      for(int k = 1; k <= left; k++) {{ if(!Bar(shift + k, other) || candidate.low > other.low) ok = false; }}
      for(int k = 1; k <= right; k++) {{ if(!Bar(shift - k, other) || candidate.low > other.low) ok = false; }}
      if(ok) {{ level = candidate.low; return true; }}
   }}
   return false;
}}

bool FreshFvg(const int direction, double &lower, double &upper)
{{
   MqlRates b, older;
   if(!Bar(1, b) || !Bar(3, older)) return false;
   if(direction > 0 && b.low > older.high) {{ lower = older.high; upper = b.low; return true; }}
   if(direction < 0 && b.high < older.low) {{ lower = b.high; upper = older.low; return true; }}
   return false;
}}

bool FindLatestFvg(const int direction, const int lookback, double &lower, double &upper)
{{
   for(int shift = 1; shift <= lookback; shift++)
   {{
      MqlRates b, older;
      if(!Bar(shift, b) || !Bar(shift + 2, older)) return false;
      if(direction > 0 && b.low > older.high) {{ lower = older.high; upper = b.low; return true; }}
      if(direction < 0 && b.high < older.low) {{ lower = b.high; upper = older.low; return true; }}
   }}
   return false;
}}

bool OrderBlockTouch(const int direction, const int lookback, const double displacement_atr, const bool require_reject)
{{
   MqlRates b;
   if(!Bar(1, b)) return false;
   for(int shift = 2; shift <= lookback + 2; shift++)
   {{
      MqlRates impulse, zone;
      if(!Bar(shift - 1, impulse) || !Bar(shift, zone)) continue;
      bool displacement = RangeAtr(impulse, shift - 1) >= displacement_atr && BodyPct(impulse) >= 0.55;
      if(direction > 0 && displacement && impulse.close > impulse.open && zone.close < zone.open)
      {{
         bool touched = b.low <= zone.high && b.close >= zone.low;
         return touched && (!require_reject || b.close > b.open);
      }}
      if(direction < 0 && displacement && impulse.close < impulse.open && zone.close > zone.open)
      {{
         bool touched = b.high >= zone.low && b.close <= zone.high;
         return touched && (!require_reject || b.close < b.open);
      }}
   }}
   return false;
}}

bool BreakerBlockTouch(const int direction, const int lookback, const double displacement_atr, const int retest_bars)
{{
   MqlRates b;
   if(!Bar(1, b)) return false;
   for(int shift = 2; shift <= lookback + retest_bars + 2; shift++)
   {{
      MqlRates impulse, zone;
      if(!Bar(shift - 1, impulse) || !Bar(shift, zone)) continue;
      bool displacement = RangeAtr(impulse, shift - 1) >= displacement_atr && BodyPct(impulse) >= 0.55;
      if(direction > 0 && displacement && impulse.close < impulse.open && zone.close > zone.open)
      {{
         if(b.close > zone.high && b.low <= zone.high) return true;
      }}
      if(direction < 0 && displacement && impulse.close > impulse.open && zone.close < zone.open)
      {{
         if(b.close < zone.low && b.high >= zone.low) return true;
      }}
   }}
   return false;
}}

{block_functions}
"""


def _grammar_build_signal_branch(strategy: HypothesisSpec) -> str:
    if strategy.lineage != "strategy_grammar":
        return ""
    blocks = [block for block in list(strategy.parameters.get("rule_blocks") or []) if isinstance(block, dict)]
    if not blocks:
        return ""
    logic = str(strategy.parameters.get("block_logic", "all"))
    min_votes = _as_int(strategy.parameters, "min_block_votes", max(1, len(blocks)))
    long_terms = [f"GrammarBlock{index}(1)" for index in range(len(blocks))]
    short_terms = [f"GrammarBlock{index}(-1)" for index in range(len(blocks))]
    if logic == "any":
        long_expr = " || ".join(long_terms)
        short_expr = " || ".join(short_terms)
    elif logic == "vote":
        long_expr = f"({ ' + '.join(f'({term} ? 1 : 0)' for term in long_terms) }) >= {min_votes}"
        short_expr = f"({ ' + '.join(f'({term} ? 1 : 0)' for term in short_terms) }) >= {min_votes}"
    else:
        long_expr = " && ".join(long_terms)
        short_expr = " && ".join(short_terms)
    return f"""
   else if(InpLineage == "strategy_grammar")
   {{
      bool long_ok = {long_expr};
      bool short_ok = {short_expr};
      if(long_ok && !short_ok) direction = 1;
      if(short_ok && !long_ok) direction = -1;
      if(direction != 0 && InpGrammarStopMode == "structure")
      {{
         double level = 0.0;
         if(direction > 0 && LatestSwingLow(2, 2, level))
            stop_dist = MathMax(0.5 * atr, MathAbs(b.close - level));
         if(direction < 0 && LatestSwingHigh(2, 2, level))
            stop_dist = MathMax(0.5 * atr, MathAbs(level - b.close));
         target_dist = InpRewardRisk * stop_dist;
      }}
   }}
"""


def _ea_source(strategy: HypothesisSpec, values: dict[str, str | int | float | bool]) -> str:
    payload = json.dumps(strategy.model_dump(mode="json"), indent=2, default=str)
    return f"""//+------------------------------------------------------------------+
//| Better Discovery Hypothesis EA                                  |
//| Generated from deterministic XAUUSD hypothesis research.         |
//| Uses closed-bar signals and trades on the next tick/new bar.      |
//+------------------------------------------------------------------+
#property strict
#property version   "1.00"

#include <Trade/Trade.mqh>

{_input_block(values)}

CTrade trade;
int hAtr, hRsi, hMacd, hEma20, hEma50, hEma200, hH1Ema20, hH1Ema50, hH1Ema200, hH4Ema20, hH4Ema50, hH4Ema200;
datetime g_last_bar_time = 0;
double g_initial_equity = 0.0;
double g_day_start_equity = 0.0;
int g_day_key = -1;
int g_trades_today = 0;
int g_last_signal_day = -1;

/*
Embedded hypothesis spec:
{payload}
*/

int OnInit()
{{
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints((int)InpSlippagePoints);
   g_initial_equity = AccountInfoDouble(ACCOUNT_EQUITY);
   g_day_start_equity = g_initial_equity;
   hAtr = iATR(_Symbol, InpSignalTimeframe, 14);
   hRsi = iRSI(_Symbol, InpSignalTimeframe, 14, PRICE_CLOSE);
   hMacd = iMACD(_Symbol, InpSignalTimeframe, 12, 26, 9, PRICE_CLOSE);
   hEma20 = iMA(_Symbol, InpSignalTimeframe, 20, 0, MODE_EMA, PRICE_CLOSE);
   hEma50 = iMA(_Symbol, InpSignalTimeframe, 50, 0, MODE_EMA, PRICE_CLOSE);
   hEma200 = iMA(_Symbol, InpSignalTimeframe, 200, 0, MODE_EMA, PRICE_CLOSE);
   hH1Ema20 = iMA(_Symbol, PERIOD_H1, 20, 0, MODE_EMA, PRICE_CLOSE);
   hH1Ema50 = iMA(_Symbol, PERIOD_H1, 50, 0, MODE_EMA, PRICE_CLOSE);
   hH1Ema200 = iMA(_Symbol, PERIOD_H1, 200, 0, MODE_EMA, PRICE_CLOSE);
   hH4Ema20 = iMA(_Symbol, PERIOD_H4, 20, 0, MODE_EMA, PRICE_CLOSE);
   hH4Ema50 = iMA(_Symbol, PERIOD_H4, 50, 0, MODE_EMA, PRICE_CLOSE);
   hH4Ema200 = iMA(_Symbol, PERIOD_H4, 200, 0, MODE_EMA, PRICE_CLOSE);
   if(hAtr == INVALID_HANDLE || hRsi == INVALID_HANDLE || hEma20 == INVALID_HANDLE || hEma50 == INVALID_HANDLE || hEma200 == INVALID_HANDLE)
      return INIT_FAILED;
   return INIT_SUCCEEDED;
}}

void OnDeinit(const int reason)
{{
   IndicatorRelease(hAtr); IndicatorRelease(hRsi); IndicatorRelease(hMacd);
   IndicatorRelease(hEma20); IndicatorRelease(hEma50); IndicatorRelease(hEma200);
   IndicatorRelease(hH1Ema20); IndicatorRelease(hH1Ema50); IndicatorRelease(hH1Ema200);
   IndicatorRelease(hH4Ema20); IndicatorRelease(hH4Ema50); IndicatorRelease(hH4Ema200);
}}

double Buf(const int handle, const int buffer, const int shift)
{{
   double values[];
   ArraySetAsSeries(values, true);
   if(CopyBuffer(handle, buffer, shift, 1, values) != 1)
      return 0.0;
   return values[0];
}}

bool Bar(const int shift, MqlRates &bar)
{{
   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   if(CopyRates(_Symbol, InpSignalTimeframe, shift, 1, rates) != 1)
      return false;
   bar = rates[0];
   return true;
}}

bool RatesFrom(const int shift, const int count, MqlRates &rates[])
{{
   ArraySetAsSeries(rates, true);
   return CopyRates(_Symbol, InpSignalTimeframe, shift, count, rates) == count;
}}

double ATR(const int shift) {{ return MathMax(Buf(hAtr, 0, shift), _Point); }}
double RSI(const int shift) {{ return Buf(hRsi, 0, shift); }}
double MACDNorm(const int shift) {{ return Buf(hMacd, 0, shift) / ATR(shift); }}
double EMAByLength(const int length, const int shift)
{{
   if(length <= 20) return Buf(hEma20, 0, shift);
   if(length <= 50) return Buf(hEma50, 0, shift);
   return Buf(hEma200, 0, shift);
}}

int TrendHandles(const int e20, const int e50, const int e200, const int shift)
{{
   double v20 = Buf(e20, 0, shift), v50 = Buf(e50, 0, shift), v200 = Buf(e200, 0, shift);
   if(v20 > v50 && v50 > v200) return 1;
   if(v20 < v50 && v50 < v200) return -1;
   return 0;
}}

int H1Trend() {{ return TrendHandles(hH1Ema20, hH1Ema50, hH1Ema200, 1); }}
int H4Trend() {{ return TrendHandles(hH4Ema20, hH4Ema50, hH4Ema200, 1); }}
int SignalTrend(const int shift) {{ return TrendHandles(hEma20, hEma50, hEma200, shift); }}

double HighestHigh(const int start_shift, const int count)
{{
   MqlRates rates[];
   if(!RatesFrom(start_shift, MathMax(count, 1), rates)) return 0.0;
   double value = rates[0].high;
   for(int i = 1; i < ArraySize(rates); i++) value = MathMax(value, rates[i].high);
   return value;
}}

double LowestLow(const int start_shift, const int count)
{{
   MqlRates rates[];
   if(!RatesFrom(start_shift, MathMax(count, 1), rates)) return 0.0;
   double value = rates[0].low;
   for(int i = 1; i < ArraySize(rates); i++) value = MathMin(value, rates[i].low);
   return value;
}}

double MeanClose(const int shift, const int count)
{{
   MqlRates rates[];
   if(!RatesFrom(shift, MathMax(count, 1), rates)) return 0.0;
   double sum = 0.0;
   for(int i = 0; i < ArraySize(rates); i++) sum += rates[i].close;
   return sum / MathMax(ArraySize(rates), 1);
}}

double StdClose(const int shift, const int count)
{{
   MqlRates rates[];
   if(!RatesFrom(shift, MathMax(count, 2), rates)) return 0.0;
   double mean = 0.0;
   for(int i = 0; i < ArraySize(rates); i++) mean += rates[i].close;
   mean /= MathMax(ArraySize(rates), 1);
   double var = 0.0;
   for(int i = 0; i < ArraySize(rates); i++) var += MathPow(rates[i].close - mean, 2.0);
   return MathSqrt(var / MathMax(ArraySize(rates), 1));
}}

double BBWidth(const int shift)
{{
   double mean = MeanClose(shift, 20);
   if(mean <= 0.0) return 0.0;
   return 4.0 * StdClose(shift, 20) / mean;
}}

double AvgBBWidth(const int shift, const int count)
{{
   double sum = 0.0;
   int n = MathMax(count, 1);
   for(int i = 0; i < n; i++) sum += BBWidth(shift + i);
   return sum / n;
}}

double VolRatio(const int shift)
{{
   MqlRates rates[];
   if(!RatesFrom(shift, 20, rates)) return 1.0;
   double avg = 0.0;
   for(int i = 0; i < ArraySize(rates); i++) avg += (double)rates[i].tick_volume;
   avg /= MathMax(ArraySize(rates), 1);
   if(avg <= 0.0) return 1.0;
   return MathMin((double)rates[0].tick_volume / avg, 5.0);
}}

double RangeAtr(const MqlRates &bar, const int shift) {{ return (bar.high - bar.low) / ATR(shift); }}
double BodyPct(const MqlRates &bar)
{{
   double rng = MathMax(bar.high - bar.low, _Point);
   return MathAbs(bar.close - bar.open) / rng;
}}
double LowerWickPct(const MqlRates &bar)
{{
   double rng = MathMax(bar.high - bar.low, _Point);
   return (MathMin(bar.open, bar.close) - bar.low) / rng;
}}
double UpperWickPct(const MqlRates &bar)
{{
   double rng = MathMax(bar.high - bar.low, _Point);
   return (bar.high - MathMax(bar.open, bar.close)) / rng;
}}
double CloseLocation(const MqlRates &bar)
{{
   double rng = MathMax(bar.high - bar.low, _Point);
   return (bar.close - bar.low) / rng;
}}

datetime ToUtc(const datetime server_time) {{ return server_time - InpServerUtcOffsetHours * 3600; }}

int DayKey(const datetime server_time)
{{
   MqlDateTime dt;
   TimeToStruct(ToUtc(server_time), dt);
   return dt.year * 1000 + dt.day_of_year;
}}

int UtcHour(const datetime server_time)
{{
   MqlDateTime dt;
   TimeToStruct(ToUtc(server_time), dt);
   return dt.hour;
}}

int UtcWeekdayPython(const datetime server_time)
{{
   MqlDateTime dt;
   TimeToStruct(ToUtc(server_time), dt);
   return (dt.day_of_week + 6) % 7;
}}

bool InSession(const datetime server_time)
{{
   int hour = UtcHour(server_time);
   if(InpSessionStartUtc == InpSessionEndUtc) return true;
   if(InpSessionStartUtc < InpSessionEndUtc)
      return hour >= InpSessionStartUtc && hour < InpSessionEndUtc;
   return hour >= InpSessionStartUtc || hour < InpSessionEndUtc;
}}

bool WeekdayAllowed(const datetime server_time)
{{
   string needle = "," + IntegerToString(UtcWeekdayPython(server_time)) + ",";
   string haystack = "," + InpWeekdays + ",";
   return StringFind(haystack, needle) >= 0;
}}

bool DirectionAllowed(const int direction)
{{
   if(direction > 0 && InpDirectionMode == "short_only") return false;
   if(direction < 0 && InpDirectionMode == "long_only") return false;
   return true;
}}

bool ContextOk(const int direction)
{{
   int h1 = H1Trend(), h4 = H4Trend();
   if(InpContextFilter == "trend_aligned")
      return direction > 0 ? (h1 > 0 && h4 > 0) : (h1 < 0 && h4 < 0);
   if(InpContextFilter == "avoid_h1_h4_opposite")
      return direction > 0 ? (h1 >= 0 && h4 >= 0) : (h1 <= 0 && h4 <= 0);
   if(InpContextFilter == "avoid_h4_opposite")
      return direction > 0 ? h4 >= 0 : h4 <= 0;
   if(InpContextFilter == "h1_turn_with_h4")
      return direction > 0 ? (h1 > 0 && h4 >= 0) : (h1 < 0 && h4 <= 0);
   return true;
}}

bool VolatilityOk()
{{
   if(InpVolatilityFilter != "h4_above_60d_median") return true;
   int h4_atr = iATR(_Symbol, PERIOD_H4, 14);
   if(h4_atr == INVALID_HANDLE) return true;
   double current = Buf(h4_atr, 0, 1) / MathMax(iClose(_Symbol, PERIOD_H4, 1), _Point);
   double avg = 0.0;
   for(int i = 1; i <= 360; i++) avg += Buf(h4_atr, 0, i) / MathMax(iClose(_Symbol, PERIOD_H4, i), _Point);
   IndicatorRelease(h4_atr);
   return current > avg / 360.0;
}}

int Regime(const int shift)
{{
   int trend = SignalTrend(shift);
   MqlRates bar;
   if(!Bar(shift, bar)) return 4;
   double atr_pct = ATR(shift) / MathMax(bar.close, _Point);
   double atr_avg = 0.0;
   for(int i = shift; i < shift + 200; i++)
   {{
      MqlRates r;
      if(!Bar(i, r)) break;
      atr_avg += ATR(i) / MathMax(r.close, _Point);
   }}
   atr_avg /= 200.0;
   double bbw = BBWidth(shift), bbw_avg = AvgBBWidth(shift, 100);
   if(bbw < bbw_avg * 0.75 && atr_pct < atr_avg * 0.95) return 2;
   if(trend == 0 && bbw > bbw_avg * 1.25) return 3;
   if(trend > 0 && atr_pct > atr_avg * 1.1) return 0;
   if(trend < 0 && atr_pct > atr_avg * 1.1) return 1;
   return 4;
}}

bool RegimeOk(const int shift)
{{
   int regime = Regime(shift);
   if(InpRegimeMode == "any") return true;
   if(InpRegimeMode == "trend") return regime == 0 || regime == 1;
   if(InpRegimeMode == "compression") return regime == 2;
   if(InpRegimeMode == "range_or_transition") return regime == 3 || regime == 4;
   return true;
}}

double RollingSharpe(const int shift)
{{
   MqlRates rates[];
   if(!RatesFrom(shift, 21, rates)) return 0.0;
   double returns[20];
   double mean = 0.0;
   for(int i = 0; i < 20; i++)
   {{
      returns[i] = (rates[i].close - rates[i + 1].close) / MathMax(rates[i + 1].close, _Point);
      mean += returns[i];
   }}
   mean /= 20.0;
   double var = 0.0;
   for(int i = 0; i < 20; i++) var += MathPow(returns[i] - mean, 2.0);
   double std = MathSqrt(var / 20.0);
   if(std <= 0.0) return 0.0;
   return MathMax(-3.0, MathMin(3.0, mean / std));
}}

bool RangeWindowLevels(const int shift, const int start_hour, const int end_hour, double &range_high, double &range_low)
{{
   MqlRates signal_bar;
   if(!Bar(shift, signal_bar)) return false;
   int key = DayKey(signal_bar.time);
   MqlRates rates[];
   if(!RatesFrom(shift, 400, rates)) return false;
   bool found = false;
   range_high = -DBL_MAX;
   range_low = DBL_MAX;
   for(int i = 0; i < ArraySize(rates); i++)
   {{
      if(DayKey(rates[i].time) != key) continue;
      int hour = UtcHour(rates[i].time);
      if(hour >= start_hour && hour < end_hour)
      {{
         found = true;
         range_high = MathMax(range_high, rates[i].high);
         range_low = MathMin(range_low, rates[i].low);
      }}
   }}
   return found;
}}

bool IsInsidePrevious()
{{
   MqlRates prev, mother;
   if(!Bar(2, prev) || !Bar(3, mother)) return false;
   return prev.high < mother.high && prev.low > mother.low;
}}

{_grammar_helpers(strategy)}

int BuildSignal(double &stop_dist, double &target_dist, double &target_price, double &trail_atr, int &max_hold)
{{
   MqlRates b, prev;
   if(!Bar(1, b) || !Bar(2, prev)) return 0;
   double atr = ATR(1);
   stop_dist = InpAtrStop * atr;
   target_dist = InpRewardRisk * stop_dist;
   target_price = 0.0;
   trail_atr = InpAtrTrail;
   max_hold = MathMax(InpMaxHoldBars, 1);
   int direction = 0;
   int h1 = H1Trend(), h4 = H4Trend();

   if(InpLineage == "time_series_breakout")
   {{
      double ph = HighestHigh(2, InpChannelBars), pl = LowestLow(2, InpChannelBars);
      if(b.close > ph && h1 > 0 && h4 > 0) direction = 1;
      if(b.close < pl && h1 < 0 && h4 < 0) direction = -1;
   }}
   else if(InpLineage == "session_range_breakout")
   {{
      double rh, rl;
      if(RangeWindowLevels(1, InpRangeStartUtc, InpRangeEndUtc, rh, rl))
      {{
         if(b.close > rh && prev.close <= rh && h1 > 0 && h4 > 0) direction = 1;
      }}
   }}
   else if(InpLineage == "trend_pullback")
   {{
      double ema = EMAByLength(InpEmaLength, 1);
      bool long_cross = RSI(2) <= InpRsiTrigger && RSI(1) > InpRsiTrigger;
      double short_level = 100.0 - InpRsiTrigger;
      bool short_cross = RSI(2) >= short_level && RSI(1) < short_level;
      if(b.low <= ema + InpPullbackAtr * atr && b.close > ema && long_cross && h1 > 0 && h4 > 0) direction = 1;
      if(b.high >= ema - InpPullbackAtr * atr && b.close < ema && short_cross && h1 < 0 && h4 < 0) direction = -1;
   }}
   else if(InpLineage == "volatility_expansion")
   {{
      bool squeezed = BBWidth(2) <= AvgBBWidth(2, 100) * (0.75 + InpSqueezeQuantile);
      double ph = HighestHigh(2, InpLookback), pl = LowestLow(2, InpLookback);
      if(squeezed && VolRatio(1) >= InpVolumeRatio && b.close > ph && h1 >= 0) direction = 1;
      if(squeezed && VolRatio(1) >= InpVolumeRatio && b.close < pl && h1 <= 0) direction = -1;
      max_hold = MathMax(32, InpLookback * 2);
   }}
   else if(InpLineage == "regime_mean_reversion")
   {{
      double mean = MeanClose(1, InpZLength);
      double sigma = StdClose(1, InpZLength);
      if(sigma > 0.0)
      {{
         double z = (b.close - mean) / sigma;
         bool flat = MathAbs(Buf(hH1Ema50, 0, 1) - Buf(hH1Ema200, 0, 1)) / MathMax(ATR(1), _Point) < 0.75;
         if(z <= -InpZEntry && RSI(1) <= InpRsiExtreme && flat) {{ direction = 1; target_price = mean; }}
         if(z >= InpZEntry && RSI(1) >= 100.0 - InpRsiExtreme && flat) {{ direction = -1; target_price = mean; }}
      }}
   }}
   else if(InpLineage == "liquidity_sweep_reclaim")
   {{
      double ph = HighestHigh(2, InpSweepLookback), pl = LowestLow(2, InpSweepLookback);
      if(b.low < pl - InpPenetrationAtr * atr && b.close > pl + InpReclaimBufferAtr * atr && LowerWickPct(b) >= InpWickRejectMin && CloseLocation(b) >= InpCloseLocationMin && ContextOk(1)) direction = 1;
      if(b.high > ph + InpPenetrationAtr * atr && b.close < ph - InpReclaimBufferAtr * atr && UpperWickPct(b) >= InpWickRejectMin && (1.0 - CloseLocation(b)) >= InpCloseLocationMin && ContextOk(-1)) direction = -1;
   }}
   else if(InpLineage == "failed_breakout_reversal")
   {{
      double ph = HighestHigh(2, InpChannelBars), pl = LowestLow(2, InpChannelBars);
      if(b.low < pl - InpBreakAtr * atr && b.close > pl + InpCloseBackAtr * atr && b.close > b.open && ContextOk(1)) direction = 1;
      if(b.high > ph + InpBreakAtr * atr && b.close < ph - InpCloseBackAtr * atr && b.open > b.close && ContextOk(-1)) direction = -1;
   }}
   else if(InpLineage == "prior_day_level_continuation")
   {{
      double pdh = iHigh(_Symbol, PERIOD_D1, 1), pdl = iLow(_Symbol, PERIOD_D1, 1);
      if(b.close > pdh + InpBreakBufferAtr * atr && prev.close <= pdh + InpBreakBufferAtr * atr && ContextOk(1)) direction = 1;
      if(b.close < pdl - InpBreakBufferAtr * atr && prev.close >= pdl - InpBreakBufferAtr * atr && ContextOk(-1)) direction = -1;
   }}
   else if(InpLineage == "volatility_spike_reversal")
   {{
      bool range_ok = RangeAtr(b, 1) >= InpSpikeRangeAtr;
      if(InpSpikeStyle == "wick_reject")
      {{
         if(range_ok && LowerWickPct(b) >= InpWickRejectMin && CloseLocation(b) >= InpCloseLocationMin && RSI(1) <= InpRsiExtreme && ContextOk(1)) direction = 1;
         if(range_ok && UpperWickPct(b) >= InpWickRejectMin && CloseLocation(b) <= 1.0 - InpCloseLocationMin && RSI(1) >= 100.0 - InpRsiExtreme && ContextOk(-1)) direction = -1;
      }}
      else
      {{
         if(range_ok && BodyPct(b) >= InpBodyMin && b.close < b.open && RSI(1) <= InpRsiExtreme && ContextOk(1)) direction = 1;
         if(range_ok && BodyPct(b) >= InpBodyMin && b.close > b.open && RSI(1) >= 100.0 - InpRsiExtreme && ContextOk(-1)) direction = -1;
      }}
   }}
   else if(InpLineage == "opening_range_continuation_reversal")
   {{
      double rh, rl;
      if(RangeWindowLevels(1, InpRangeStartUtc, InpRangeEndUtc, rh, rl))
      {{
         if(InpOpeningRangeMode == "continuation")
         {{
            if(b.close > rh + InpBreakBufferAtr * atr && prev.close <= rh + InpBreakBufferAtr * atr && ContextOk(1)) direction = 1;
            if(b.close < rl - InpBreakBufferAtr * atr && prev.close >= rl - InpBreakBufferAtr * atr && ContextOk(-1)) direction = -1;
         }}
         else
         {{
            if(b.low < rl - InpRangeSweepAtr * atr && b.close > rl + InpBreakBufferAtr * atr && ContextOk(1)) direction = 1;
            if(b.high > rh + InpRangeSweepAtr * atr && b.close < rh - InpBreakBufferAtr * atr && ContextOk(-1)) direction = -1;
         }}
      }}
   }}
   else if(InpLineage == "trend_day_pullback")
   {{
      double ema = EMAByLength(InpEmaLength, 1);
      double day_open = iOpen(_Symbol, PERIOD_D1, 0);
      double short_level = 100.0 - InpRsiTrigger;
      if(h1 > 0 && h4 > 0 && b.close > day_open + InpTrendOpenAtr * atr && b.low <= ema + InpPullbackAtr * atr && b.close > ema && RSI(2) <= InpRsiTrigger && RSI(1) > InpRsiTrigger && RollingSharpe(1) >= InpRollingSharpeMin) direction = 1;
      if(h1 < 0 && h4 < 0 && b.close < day_open - InpTrendOpenAtr * atr && b.high >= ema - InpPullbackAtr * atr && b.close < ema && RSI(2) >= short_level && RSI(1) < short_level && RollingSharpe(1) <= -InpRollingSharpeMin) direction = -1;
   }}
   else if(InpLineage == "day_time_regime_filter")
   {{
      if(WeekdayAllowed(b.time) && RegimeOk(1))
      {{
         double threshold = InpMomentumAtr * atr;
         double prior_close = 0.0;
         MqlRates oldbar;
         if(Bar(1 + InpMomentumBars, oldbar)) prior_close = oldbar.close;
         if(InpSignalMode == "momentum")
         {{
            if(b.close > prior_close + threshold && ContextOk(1)) direction = 1;
            if(b.close < prior_close - threshold && ContextOk(-1)) direction = -1;
         }}
         else
         {{
            double mean = MeanClose(1, InpMomentumBars);
            if(b.close < mean - threshold && RSI(1) <= InpRsiExtreme && ContextOk(1)) direction = 1;
            if(b.close > mean + threshold && RSI(1) >= 100.0 - InpRsiExtreme && ContextOk(-1)) direction = -1;
         }}
      }}
   }}
   else if(InpLineage == "inside_bar_expansion")
   {{
      MqlRates mother;
      if(Bar(3, mother) && IsInsidePrevious() && VolRatio(1) >= InpVolumeRatio)
      {{
         if(b.close > mother.high + InpBreakBufferAtr * atr && ContextOk(1)) direction = 1;
         if(b.close < mother.low - InpBreakBufferAtr * atr && ContextOk(-1)) direction = -1;
      }}
   }}
{_grammar_build_signal_branch(strategy)}

   if(direction == 0) return 0;
   if(!DirectionAllowed(direction) || !InSession(b.time) || !VolatilityOk()) return 0;
   if(InpFirstSignalPerDay && DayKey(b.time) == g_last_signal_day) return 0;
   return direction;
}}

bool HasPosition()
{{
   if(!PositionSelect(_Symbol)) return false;
   return PositionGetInteger(POSITION_MAGIC) == InpMagic;
}}

double NormalizeLots(const double raw_lots)
{{
   double min_lot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double max_lot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(step <= 0.0) step = 0.01;
   double lots = MathFloor(raw_lots / step) * step;
   return MathMax(min_lot, MathMin(max_lot, lots));
}}

double LotsFromRisk(const double stop_dist)
{{
   if(InpFixedLots > 0.0) return NormalizeLots(InpFixedLots);
   double risk_money = AccountInfoDouble(ACCOUNT_EQUITY) * InpRiskFraction;
   double tick_size = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double tick_value = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   if(tick_size <= 0.0 || tick_value <= 0.0 || stop_dist <= 0.0) return NormalizeLots(0.01);
   return NormalizeLots(risk_money / ((stop_dist / tick_size) * tick_value));
}}

void RefreshDay()
{{
   int key = DayKey(TimeCurrent());
   if(key != g_day_key)
   {{
      g_day_key = key;
      g_day_start_equity = AccountInfoDouble(ACCOUNT_EQUITY);
      g_trades_today = 0;
   }}
}}

bool RiskGuardOk()
{{
   RefreshDay();
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   if(g_initial_equity > 0.0 && equity <= g_initial_equity * (1.0 - InpMaxLossPct / 100.0)) return false;
   if(g_day_start_equity > 0.0 && equity <= g_day_start_equity * (1.0 - InpDailyLossPct / 100.0)) return false;
   if(g_trades_today >= InpMaxTradesPerDay) return false;
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK), bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   if(InpMaxSpreadPoints > 0.0 && (ask - bid) / _Point > InpMaxSpreadPoints) return false;
   return true;
}}

void ManagePosition()
{{
   if(!HasPosition()) return;
   datetime entry_time = (datetime)PositionGetInteger(POSITION_TIME);
   int bars_held = (int)((TimeCurrent() - entry_time) / MathMax(PeriodSeconds(InpSignalTimeframe), 1));
   if(bars_held >= InpMaxHoldBars)
   {{
      trade.PositionClose(_Symbol);
      return;
   }}
   if(InpAtrTrail <= 0.0) return;
   long type = PositionGetInteger(POSITION_TYPE);
   double sl = PositionGetDouble(POSITION_SL);
   MqlRates b;
   if(!Bar(1, b)) return;
   double atr = ATR(1), next_sl = sl;
   if(type == POSITION_TYPE_BUY)
      next_sl = MathMax(sl, b.high - InpAtrTrail * atr);
   else
      next_sl = sl <= 0.0 ? b.low + InpAtrTrail * atr : MathMin(sl, b.low + InpAtrTrail * atr);
   trade.PositionModify(_Symbol, NormalizeDouble(next_sl, _Digits), PositionGetDouble(POSITION_TP));
}}

bool IsNewSignalBar()
{{
   datetime times[];
   ArraySetAsSeries(times, true);
   if(CopyTime(_Symbol, InpSignalTimeframe, 0, 1, times) != 1) return false;
   if(times[0] == g_last_bar_time) return false;
   g_last_bar_time = times[0];
   return true;
}}

void TryOpen()
{{
   if(HasPosition() || !RiskGuardOk()) return;
   double stop_dist, target_dist, target_price, trail_atr;
   int max_hold;
   int direction = BuildSignal(stop_dist, target_dist, target_price, trail_atr, max_hold);
   if(direction == 0 || stop_dist <= 0.0) return;
   double lots = LotsFromRisk(stop_dist);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK), bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   bool ok = false;
   if(direction > 0)
   {{
      double sl = NormalizeDouble(ask - stop_dist, _Digits);
      double tp = target_price > ask ? NormalizeDouble(target_price, _Digits) : NormalizeDouble(ask + target_dist, _Digits);
      ok = trade.Buy(lots, _Symbol, 0.0, sl, tp, InpStrategyId);
   }}
   else
   {{
      double sl = NormalizeDouble(bid + stop_dist, _Digits);
      double tp = target_price > 0.0 && target_price < bid ? NormalizeDouble(target_price, _Digits) : NormalizeDouble(bid - target_dist, _Digits);
      ok = trade.Sell(lots, _Symbol, 0.0, sl, tp, InpStrategyId);
   }}
   if(ok)
   {{
      g_trades_today++;
      MqlRates b;
      if(Bar(1, b)) g_last_signal_day = DayKey(b.time);
   }}
}}

void OnTick()
{{
   ManagePosition();
   if(IsNewSignalBar()) TryOpen();
}}
"""


def export(
    strategy: HypothesisSpec,
    *,
    output_name: str | None = None,
    risk_fraction: float = 0.01,
    daily_loss_pct: float = 4.0,
    max_loss_pct: float = 8.0,
    max_trades_per_day: int = 4,
    max_spread_points: float = 80.0,
) -> dict[str, Any]:
    """Write a standalone EA and matching .set file for a hypothesis strategy."""
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    magic_number = int(strategy.fingerprint[:8], 16) % 2_000_000_000
    name = _safe_name(output_name or strategy.strategy_id)
    values = _input_values(
        strategy,
        magic_number=magic_number,
        risk_fraction=risk_fraction,
        daily_loss_pct=daily_loss_pct,
        max_loss_pct=max_loss_pct,
        max_trades_per_day=max_trades_per_day,
        max_spread_points=max_spread_points,
    )
    mq5_path = _OUTPUT_DIR / f"{name}.mq5"
    set_path = _OUTPUT_DIR / f"{name}.set"
    spec_path = _OUTPUT_DIR / f"{name}.hypothesis.json"
    mq5_path.write_text(_ea_source(strategy, values), encoding="utf-8", newline="\n")
    set_path.write_text(_set_text(values), encoding="utf-8", newline="\n")
    spec_path.write_text(strategy.model_dump_json(indent=2), encoding="utf-8")
    mt5_install = _install_to_active_mt5(mq5_path, set_path, spec_path)
    warnings = [
        "Generated EA uses MT5 server bars and closed-bar logic; results can differ from Python bar replay because broker time, spread, and indicator implementations differ.",
        "Use this for MT5 validation/backtesting before any live or funded use.",
    ]
    if mt5_install.get("warning"):
        warnings.append(str(mt5_install["warning"]))
    warnings.extend(str(item) for item in mt5_install.get("warnings", []))
    return {
        "mq5_path": str(mq5_path.resolve()),
        "set_path": str(set_path.resolve()),
        "spec_path": str(spec_path.resolve()),
        "preferred_mq5_path": str(mt5_install.get("preferred_mq5_path") or mq5_path.resolve()),
        "mt5_installed": bool(mt5_install.get("installed", False)),
        "mt5_mq5_path": mt5_install.get("mt5_mq5_path"),
        "mt5_set_path": mt5_install.get("mt5_set_path"),
        "mt5_spec_path": mt5_install.get("mt5_spec_path"),
        "mt5_data_path": mt5_install.get("mt5_data_path"),
        "mt5_experts_folder": mt5_install.get("mt5_experts_folder"),
        "strategy_id": strategy.strategy_id,
        "lineage": strategy.lineage,
        "magic_number": magic_number,
        "warnings": warnings,
    }
