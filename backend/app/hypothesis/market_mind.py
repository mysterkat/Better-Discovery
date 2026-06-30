from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class MarketMindPlan:
    regime_id: str
    summary: dict[str, Any]
    recipes: list[dict[str, Any]]
    block_group_weights: dict[str, float]
    exploration_pct: float

    def model_dump(self) -> dict[str, Any]:
        return {
            "regime_id": self.regime_id,
            "summary": self.summary,
            "recipes": self.recipes,
            "block_group_weights": self.block_group_weights,
            "exploration_pct": self.exploration_pct,
        }


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


def _normalize(weights: dict[str, float]) -> dict[str, float]:
    cleaned = {key: max(0.0, float(value)) for key, value in weights.items()}
    total = sum(cleaned.values())
    if total <= 0:
        return {key: 1.0 / len(cleaned) for key in cleaned} if cleaned else {}
    return {key: value / total for key, value in cleaned.items()}


def _daily_frame(bars: pd.DataFrame) -> pd.DataFrame:
    if bars.empty:
        return pd.DataFrame()
    frame = bars.copy()
    frame["time"] = pd.to_datetime(frame["time"], utc=True)
    frame = frame.sort_values("time").set_index("time")
    price_cols = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
    }
    daily = frame.resample("1D").agg(price_cols).dropna()
    if daily.empty:
        return daily
    daily["return"] = daily["close"].pct_change()
    daily["range_pct"] = (daily["high"] - daily["low"]) / daily["close"].replace(0, np.nan)
    daily["body_pct"] = (daily["close"] - daily["open"]).abs() / (daily["high"] - daily["low"]).replace(0, np.nan)
    daily["upper_wick_pct"] = (daily["high"] - daily[["open", "close"]].max(axis=1)) / (daily["high"] - daily["low"]).replace(0, np.nan)
    daily["lower_wick_pct"] = (daily[["open", "close"]].min(axis=1) - daily["low"]) / (daily["high"] - daily["low"]).replace(0, np.nan)
    daily["ema50"] = daily["close"].ewm(span=50, adjust=False).mean()
    daily["ema200"] = daily["close"].ewm(span=200, adjust=False).mean()
    return daily.fillna(0)


def analyze_market_mind(
    bars: pd.DataFrame,
    contexts: dict[str, pd.DataFrame] | None = None,
    external_context: dict[str, Any] | None = None,
    *,
    bias_pct: float = 0.70,
) -> MarketMindPlan:
    """Build a long-horizon generation plan from local market data.

    The plan intentionally stays descriptive. It biases which grammar blocks are
    sampled; entries still have to earn promotion through replay and challenge
    scoring.
    """
    daily = _daily_frame(bars)
    if daily.empty or len(daily) < 60:
        summary = {
            "history_days": int(len(daily)),
            "warning": "not enough bars for long-term market-mind analysis",
        }
        recipes = [
            {
                "name": "balanced_exploration",
                "weight": 1.0,
                "groups": ["liquidity", "structure", "imbalance", "orderflow", "sessions", "volatility", "smt"],
                "why": "insufficient long-term structure, keep generation broad",
            }
        ]
        return MarketMindPlan("insufficient_history", summary, recipes, _normalize({g: 1.0 for g in recipes[0]["groups"]}), 1.0 - bias_pct)

    recent = daily.tail(min(260, len(daily)))
    multi_year = daily.tail(min(1260, len(daily)))
    trend_return = _safe_float(recent["close"].iloc[-1] / recent["close"].iloc[0] - 1.0)
    ema_gap = _safe_float((recent["ema50"].iloc[-1] - recent["ema200"].iloc[-1]) / recent["close"].iloc[-1])
    trend_days = float(((recent["ema50"] > recent["ema200"]).mean()))
    vol_now = _safe_float(recent["range_pct"].tail(20).median())
    vol_long = _safe_float(multi_year["range_pct"].median())
    vol_percentile = float((multi_year["range_pct"] <= vol_now).mean()) if vol_now > 0 else 0.5
    compression = float((recent["range_pct"].tail(20).median() < recent["range_pct"].rolling(120, min_periods=20).median().tail(20).median()) if len(recent) >= 120 else 0.0)

    prev_high = daily["high"].shift(1)
    prev_low = daily["low"].shift(1)
    broke_high = daily["high"] > prev_high
    broke_low = daily["low"] < prev_low
    high_follow = (daily["close"] > prev_high) & broke_high
    low_follow = (daily["close"] < prev_low) & broke_low
    breakout_follow = _safe_float((high_follow | low_follow).tail(260).mean(), 0.0)
    failed_break = _safe_float(((broke_high & (daily["close"] < prev_high)) | (broke_low & (daily["close"] > prev_low))).tail(260).mean(), 0.0)
    wick_reject = _safe_float(((daily["upper_wick_pct"] > 0.45) | (daily["lower_wick_pct"] > 0.45)).tail(260).mean(), 0.0)
    large_range = daily["range_pct"] > daily["range_pct"].rolling(120, min_periods=30).quantile(0.80)
    next_reversal = np.sign(daily["return"]).shift(-1) == -np.sign(daily["return"])
    spike_reversal = _safe_float((large_range & next_reversal).tail(260).mean(), 0.0)

    hour_summary: dict[str, float] = {}
    if not bars.empty:
        intraday = bars.copy()
        intraday["time"] = pd.to_datetime(intraday["time"], utc=True)
        intraday = intraday.set_index("time").sort_index()
        rng = (intraday["high"] - intraday["low"]) / intraday["close"].replace(0, np.nan)
        hour_summary = {
            f"h{int(hour):02d}": _safe_float(value)
            for hour, value in rng.groupby(intraday.index.hour).median().sort_values(ascending=False).head(5).items()
        }

    bullish_trend = trend_return > 0.08 and ema_gap > 0
    bearish_trend = trend_return < -0.08 and ema_gap < 0
    high_vol = vol_percentile >= 0.65
    low_vol = vol_percentile <= 0.35
    reversal_edge = failed_break + wick_reject + spike_reversal
    continuation_edge = breakout_follow + abs(trend_return) + max(0.0, abs(ema_gap) * 8.0)

    weights = {
        "liquidity": 1.0 + failed_break * 4.0 + wick_reject * 2.0,
        "structure": 1.0 + continuation_edge * 2.0,
        "imbalance": 1.0 + (1.5 if high_vol else 0.2) + abs(ema_gap) * 10.0,
        "orderflow": 1.0 + failed_break * 1.5 + continuation_edge,
        "sessions": 1.0 + (0.8 if hour_summary else 0.0),
        "volatility": 1.0 + vol_percentile * 2.0 + spike_reversal * 3.0 + (1.0 if low_vol else 0.0),
        "smt": 0.6,
    }
    if external_context:
        if external_context.get("cot_regime") in {"crowded_long", "crowded_short"}:
            weights["liquidity"] += 1.0
            weights["volatility"] += 0.5
        if external_context.get("vix_regime") in {"expanding", "stress"}:
            weights["volatility"] += 1.0
            weights["structure"] += 0.5

    recipes: list[dict[str, Any]] = []
    if reversal_edge >= 0.75 or failed_break > breakout_follow:
        recipes.append({
            "name": "trap_reversal",
            "weight": 0.30,
            "groups": ["liquidity", "structure", "volatility", "sessions", "imbalance"],
            "why": "failed breaks, wick rejection, or spike reversals dominate recent daily behavior",
        })
    if continuation_edge >= 0.35 or bullish_trend or bearish_trend:
        recipes.append({
            "name": "trend_continuation",
            "weight": 0.28,
            "groups": ["structure", "imbalance", "orderflow", "volatility", "sessions"],
            "why": "multi-month trend and breakout acceptance favor continuation blocks",
        })
    if high_vol or spike_reversal > 0.12:
        recipes.append({
            "name": "volatility_response",
            "weight": 0.20,
            "groups": ["volatility", "liquidity", "structure", "imbalance"],
            "why": "current volatility is elevated or large-range days are reversing",
        })
    if low_vol or compression:
        recipes.append({
            "name": "compression_expansion",
            "weight": 0.18,
            "groups": ["volatility", "sessions", "structure", "imbalance"],
            "why": "recent range compression suggests expansion candidates deserve budget",
        })
    recipes.append({
        "name": "session_liquidity",
        "weight": 0.14,
        "groups": ["sessions", "liquidity", "structure", "volatility"],
        "why": "intraday XAUUSD behavior should still test session-specific liquidity timing",
    })
    recipes = sorted(recipes, key=lambda item: float(item["weight"]), reverse=True)[:5]
    recipe_total = sum(float(item["weight"]) for item in recipes) or 1.0
    for item in recipes:
        item["weight"] = float(item["weight"]) / recipe_total

    direction = "uptrend" if bullish_trend else "downtrend" if bearish_trend else "mixed"
    vol_state = "high_vol" if high_vol else "low_vol" if low_vol else "normal_vol"
    behavior = "reversal_prone" if reversal_edge > continuation_edge else "continuation_prone"
    regime_id = f"{direction}_{vol_state}_{behavior}"
    summary = {
        "history_days": int(len(daily)),
        "analysis_start": str(daily.index.min().date()),
        "analysis_end": str(daily.index.max().date()),
        "trend_return_1y": trend_return,
        "ema50_ema200_gap": ema_gap,
        "ema50_above_ema200_fraction": trend_days,
        "volatility_percentile": vol_percentile,
        "breakout_followthrough_rate": breakout_follow,
        "failed_breakout_rate": failed_break,
        "wick_rejection_rate": wick_reject,
        "spike_reversal_rate": spike_reversal,
        "active_hours_utc": hour_summary,
        "external_context": external_context or {},
    }
    return MarketMindPlan(regime_id, summary, recipes, _normalize(weights), 1.0 - bias_pct)
