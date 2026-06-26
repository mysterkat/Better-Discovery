from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from app.hypothesis.bar_engine import run_bar_replay, run_bar_replay_fast_metrics
from app.hypothesis.challenge import evaluate_challenge
from app.hypothesis.grammar import generate_hypotheses
from app.hypothesis.models import FTMOChallengeConfig, HypothesisBarRequest, HypothesisDiscoveryRequest, HypothesisSpec
from app.hypothesis.service import HypothesisResearchService
from app.hypothesis.signals import apply_signal_rules


def _spec(**overrides) -> HypothesisSpec:
    values = {
        "strategy_id": "tsb_test",
        "lineage": "time_series_breakout",
        "hypothesis": "A closed-bar channel breakout continues after the next tradable quote.",
        "parameters": {
            "channel_bars": 20,
            "atr_stop": 2.0,
            "atr_trail": 3.0,
            "max_hold_bars": 96,
        },
    }
    values.update(overrides)
    return HypothesisSpec(**values)


def _request(strategy: HypothesisSpec) -> HypothesisBarRequest:
    return HypothesisBarRequest(
        dataset_id="test",
        strategy=strategy,
        dataset_role="internal_oos",
        date_from=datetime(2025, 1, 1, tzinfo=timezone.utc),
        date_to=datetime(2025, 1, 2, tzinfo=timezone.utc),
        lot_size=1.0,
        contract_size=1.0,
        commission_per_lot_round_turn=0.0,
        slippage_price_units=0.05,
    )


def test_strategy_fingerprint_is_canonical() -> None:
    first = _spec()
    second = _spec(parameters={
        "max_hold_bars": 96,
        "atr_trail": 3.0,
        "atr_stop": 2.0,
        "channel_bars": 20,
    })
    assert first.fingerprint == second.fingerprint


def test_bar_replay_enters_next_bar_and_uses_stop_first_collision() -> None:
    times = pd.date_range("2025-01-01", periods=3, freq="15min", tz="UTC")
    bars = pd.DataFrame({
        "time": times,
        "bid_open": [99.9, 100.0, 100.0],
        "bid_high": [100.1, 100.2, 102.0],
        "bid_low": [99.8, 99.9, 98.0],
        "bid_close": [100.0, 100.1, 100.0],
        "ask_open": [100.1, 100.2, 100.2],
        "ask_high": [100.3, 100.4, 102.2],
        "ask_low": [100.0, 100.1, 98.2],
        "ask_close": [100.2, 100.3, 100.2],
    })
    signals = pd.DataFrame({
        "signal_direction": [1, 0, 0],
        "stop_distance": [1.0, 0.0, 0.0],
        "target_distance": [1.0, 0.0, 0.0],
        "signal_target_price": [0.0, 0.0, 0.0],
        "trail_atr": [0.0, 0.0, 0.0],
        "max_hold_bars": [10, 10, 10],
        "atr14": [1.0, 1.0, 1.0],
    }, index=times)

    ledger, metrics = run_bar_replay(bars, signals, _request(_spec()))

    assert len(ledger) == 1
    trade = ledger.iloc[0]
    assert trade["signal_time"] == times[0]
    assert trade["entry_time"] == times[1]
    assert trade["entry_price"] == 100.25
    assert trade["exit_reason"] == "stop_loss"
    assert trade["exit_price"] == 99.20
    assert metrics["net_profit"] < 0


def test_fast_metrics_match_canonical_bar_replay() -> None:
    times = pd.date_range("2025-01-01", periods=8, freq="15min", tz="UTC")
    bars = pd.DataFrame({
        "time": times,
        "bid_open": [99.9, 100.0, 100.8, 100.4, 100.0, 100.2, 100.1, 100.5],
        "bid_high": [100.1, 101.0, 101.2, 100.8, 100.4, 100.7, 100.9, 101.0],
        "bid_low": [99.8, 99.9, 100.2, 99.8, 99.7, 99.9, 99.8, 100.2],
        "bid_close": [100.0, 100.8, 100.5, 100.0, 100.2, 100.1, 100.6, 100.8],
        "ask_open": [100.1, 100.2, 101.0, 100.6, 100.2, 100.4, 100.3, 100.7],
        "ask_high": [100.3, 101.2, 101.4, 101.0, 100.6, 100.9, 101.1, 101.2],
        "ask_low": [100.0, 100.1, 100.4, 100.0, 99.9, 100.1, 100.0, 100.4],
        "ask_close": [100.2, 101.0, 100.7, 100.2, 100.4, 100.3, 100.8, 101.0],
    })
    signals = pd.DataFrame({
        "signal_direction": [1, 0, 0, 1, 0, 0, 0, 0],
        "stop_distance": [0.8, 0.0, 0.0, 0.5, 0.0, 0.0, 0.0, 0.0],
        "target_distance": [0.0] * 8,
        "signal_target_price": [0.0] * 8,
        "trail_atr": [0.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        "max_hold_bars": [2, 2, 2, 2, 2, 2, 2, 2],
        "atr14": [0.4] * 8,
    }, index=times)
    request = _request(_spec())

    _, canonical = run_bar_replay(bars, signals, request)
    fast = run_bar_replay_fast_metrics(bars, signals, request)

    assert fast["trades"] == canonical["trades"]
    for key in ("net_profit", "profit_factor", "expected_payoff", "max_drawdown_pct"):
        assert fast[key] == pytest.approx(canonical[key])


def test_request_normalizes_naive_dates_to_utc() -> None:
    request = HypothesisBarRequest(
        dataset_id="test",
        strategy=_spec(),
        dataset_role="fit",
        date_from=datetime(2025, 1, 1),
        date_to=datetime(2025, 1, 2),
    )
    assert request.date_from.tzinfo == timezone.utc
    assert request.date_to.tzinfo == timezone.utc


def test_long_only_session_filter_is_applied_after_breakout_signal() -> None:
    times = pd.date_range("2025-01-01T12:00:00Z", periods=3, freq="1h")
    base = pd.DataFrame({
        "close": [100.0, 102.0, 98.0],
        "high": [101.0, 103.0, 99.0],
        "low": [99.0, 101.0, 97.0],
        "atr14": [1.0, 1.0, 1.0],
        "h1_trend": [1, 1, -1],
        "h4_trend": [1, 1, -1],
        "h4_atr_pct": [0.01, 0.01, 0.01],
    }, index=times)
    strategy = _spec(parameters={
        "channel_bars": 1,
        "atr_stop": 2.0,
        "atr_trail": 3.0,
        "max_hold_bars": 10,
        "direction_mode": "long_only",
        "session_start_utc": 13,
        "session_end_utc": 18,
        "volatility_filter": "none",
    })
    signals = apply_signal_rules(base, strategy)
    assert signals.loc[times[1], "signal_direction"] == 1
    assert signals.loc[times[2], "signal_direction"] == 0


def test_breakout_quality_filter_uses_closed_signal_bar() -> None:
    times = pd.date_range("2025-01-01T13:00:00Z", periods=3, freq="1h")
    base = pd.DataFrame({
        "open": [99.5, 101.8, 97.5],
        "close": [100.0, 102.0, 98.0],
        "high": [101.0, 103.0, 99.0],
        "low": [99.0, 101.0, 97.0],
        "atr14": [1.0, 1.0, 1.0],
        "body_pct": [0.25, 0.10, 0.50],
        "rng": [2.0, 2.0, 2.0],
        "rng_atr": [2.0, 2.0, 2.0],
        "rsi14": [50.0, 60.0, 40.0],
        "macd_norm": [0.0, 0.2, -0.2],
        "prev_sess_bias": [0.0, 1.0, -1.0],
        "h1_trend": [1, 1, -1],
        "h4_trend": [1, 1, -1],
        "h4_atr_pct": [0.01, 0.01, 0.01],
    }, index=times)
    strategy = _spec(parameters={
        "channel_bars": 1,
        "atr_stop": 2.0,
        "atr_trail": 3.0,
        "max_hold_bars": 10,
        "direction_mode": "long_only",
        "session_start_utc": 13,
        "session_end_utc": 18,
        "volatility_filter": "none",
        "breakout_body_min": 0.45,
    })

    signals = apply_signal_rules(base, strategy)

    assert signals.loc[times[1], "signal_direction"] == 0


def test_session_range_breakout_waits_for_completed_range_and_fires_once() -> None:
    times = pd.date_range("2025-01-01T12:00:00Z", periods=7, freq="1h")
    base = pd.DataFrame({
        "open": [99.0, 100.0, 101.0, 100.0, 102.0, 100.0, 103.0],
        "close": [99.5, 100.5, 100.0, 102.0, 101.0, 103.0, 102.0],
        "high": [100.0, 101.0, 101.5, 102.5, 102.5, 103.5, 103.5],
        "low": [98.5, 99.5, 99.5, 99.5, 100.5, 99.5, 101.5],
        "atr14": [1.0] * 7,
        "body_pct": [0.5] * 7,
        "rng": [1.5] * 7,
        "rng_atr": [1.5] * 7,
        "rsi14": [60.0] * 7,
        "macd_norm": [0.2] * 7,
        "prev_sess_bias": [1.0] * 7,
        "h1_trend": [1] * 7,
        "h4_trend": [1] * 7,
        "h4_atr_pct": [0.01] * 7,
    }, index=times)
    strategy = HypothesisSpec(
        strategy_id="session_range_test",
        lineage="session_range_breakout",
        hypothesis="A completed session range break carries conditional continuation information.",
        parameters={
            "range_start_utc": 12,
            "range_end_utc": 14,
            "session_start_utc": 14,
            "session_end_utc": 19,
            "atr_stop": 3.0,
            "atr_trail": 5.0,
            "max_hold_bars": 96,
            "direction_mode": "long_only",
            "volatility_filter": "none",
            "first_breakout_per_day": True,
        },
    )

    signals = apply_signal_rules(base, strategy)

    assert signals.loc[times[:2], "signal_direction"].sum() == 0
    assert signals.loc[times[3], "signal_direction"] == 1
    assert signals["signal_direction"].sum() == 1


def test_campaign_policy_override_controls_gate() -> None:
    metrics = {
        "trades": 16,
        "profit_factor": 1.16,
        "expected_payoff": 1.0,
        "max_drawdown_pct": 5.0,
        "positive_month_fraction": 0.5,
        "positive_quarter_fraction": 0.5,
        "positive_years": 1,
    }
    gate = HypothesisResearchService._gate(
        metrics,
        "validation",
        {"min_trades": 15, "min_profit_factor": 1.15},
    )
    assert gate["decision"] == "promote"


def test_hypothesis_grammar_is_deterministic_and_mixed_by_family() -> None:
    request = HypothesisDiscoveryRequest(
        dataset_id="test",
        date_from=datetime(2025, 1, 1, tzinfo=timezone.utc),
        date_to=datetime(2025, 2, 1, tzinfo=timezone.utc),
        families=("time_series_breakout", "trend_pullback"),
        max_variants=10,
    )

    first = generate_hypotheses(request)
    second = generate_hypotheses(request)

    assert [item.fingerprint for item in first] == [item.fingerprint for item in second]
    assert {item.lineage for item in first} == {"time_series_breakout", "trend_pullback"}


def test_ftmo_challenge_replay_passes_fast_target_hit() -> None:
    ledger = pd.DataFrame({
        "entry_time": pd.date_range("2025-01-01T00:00:00Z", periods=5, freq="4h"),
        "exit_time": pd.date_range("2025-01-01T01:00:00Z", periods=5, freq="4h"),
        "net_pnl": [100.0] * 5,
        "initial_risk": [100.0] * 5,
    })
    config = FTMOChallengeConfig(
        max_attempt_days=3,
        start_frequency="1D",
        risk_fractions=(0.02,),
        internal_daily_stop_pcts=(4.0,),
        max_trades_per_day_options=(10,),
    )

    result = evaluate_challenge(
        ledger,
        risk_fraction=0.02,
        internal_daily_stop_pct=4.0,
        max_trades_per_day=10,
        config=config,
    )

    assert result["summary"]["pass_count"] == 1
    assert result["summary"]["best_days_to_target"] is not None
    assert result["summary"]["best_days_to_target"] < 1.0


def test_ftmo_challenge_replay_fails_daily_loss() -> None:
    ledger = pd.DataFrame({
        "entry_time": [pd.Timestamp("2025-01-01T00:00:00Z")],
        "exit_time": [pd.Timestamp("2025-01-01T00:15:00Z")],
        "net_pnl": [-300.0],
        "initial_risk": [100.0],
    })
    config = FTMOChallengeConfig(
        max_attempt_days=3,
        start_frequency="1D",
        risk_fractions=(0.02,),
        internal_daily_stop_pcts=(4.0,),
        max_trades_per_day_options=(10,),
    )

    result = evaluate_challenge(
        ledger,
        risk_fraction=0.02,
        internal_daily_stop_pct=4.0,
        max_trades_per_day=10,
        config=config,
    )

    assert result["summary"]["prop_fail_count"] == 1
    assert result["attempts"][0]["fail_reason"] == "daily_loss"
