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


NEW_HYPOTHESIS_FAMILIES = (
    "strategy_grammar",
    "liquidity_sweep_reclaim",
    "failed_breakout_reversal",
    "prior_day_level_continuation",
    "volatility_spike_reversal",
    "opening_range_continuation_reversal",
    "trend_day_pullback",
    "day_time_regime_filter",
    "inside_bar_expansion",
)


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


def _base_frame(times: pd.DatetimeIndex, **overrides) -> pd.DataFrame:
    n = len(times)
    data = {
        "open": [100.0] * n,
        "high": [101.0] * n,
        "low": [99.0] * n,
        "close": [100.0] * n,
        "atr14": [1.0] * n,
        "rsi14": [50.0] * n,
        "macd_norm": [0.0] * n,
        "prev_sess_bias": [0.0] * n,
        "h1_trend": [1] * n,
        "h4_trend": [1] * n,
        "h1_ema50": [100.0] * n,
        "h1_ema200": [100.0] * n,
        "h1_atr14": [1.0] * n,
        "h4_atr_pct": [0.01] * n,
        "rolling_sharpe": [0.2] * n,
        "inside_bar": [0.0] * n,
        "vol_ratio": [1.2] * n,
        "regime": [4] * n,
    }
    data.update(overrides)
    frame = pd.DataFrame(data, index=times)
    rng = (frame["high"] - frame["low"]).replace(0, 1.0)
    body = (frame["close"] - frame["open"]).abs()
    derived = {
        "rng": rng,
        "rng_atr": rng / frame["atr14"].replace(0, 1.0),
        "body_pct": body / rng,
        "lwk_pct": (frame[["open", "close"]].min(axis=1) - frame["low"]) / rng,
        "uwk_pct": (frame["high"] - frame[["open", "close"]].max(axis=1)) / rng,
        "ema20": frame["close"],
        "ema50": frame["close"],
        "ema200": frame["close"],
        "bb_width": [0.01] * n,
    }
    for key, value in derived.items():
        if key not in overrides:
            frame[key] = value
    return frame


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


def test_fast_metrics_match_canonical_bar_replay_for_shorts() -> None:
    times = pd.date_range("2025-01-01", periods=8, freq="15min", tz="UTC")
    bars = pd.DataFrame({
        "time": times,
        "bid_open": [100.0, 99.8, 99.2, 99.5, 99.7, 99.4, 99.1, 98.8],
        "bid_high": [100.2, 100.0, 99.6, 99.8, 100.0, 99.6, 99.3, 99.0],
        "bid_low": [99.7, 99.0, 98.8, 99.0, 99.2, 98.9, 98.7, 98.4],
        "bid_close": [99.9, 99.2, 99.4, 99.7, 99.4, 99.1, 98.8, 98.6],
        "ask_open": [100.2, 100.0, 99.4, 99.7, 99.9, 99.6, 99.3, 99.0],
        "ask_high": [100.4, 100.2, 99.8, 100.0, 100.2, 99.8, 99.5, 99.2],
        "ask_low": [99.9, 99.2, 99.0, 99.2, 99.4, 99.1, 98.9, 98.6],
        "ask_close": [100.1, 99.4, 99.6, 99.9, 99.6, 99.3, 99.0, 98.8],
    })
    signals = pd.DataFrame({
        "signal_direction": [-1, 0, 0, -1, 0, 0, 0, 0],
        "stop_distance": [0.6, 0.0, 0.0, 0.5, 0.0, 0.0, 0.0, 0.0],
        "target_distance": [0.7, 0.0, 0.0, 0.6, 0.0, 0.0, 0.0, 0.0],
        "signal_target_price": [0.0] * 8,
        "trail_atr": [0.0] * 8,
        "max_hold_bars": [3] * 8,
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


def test_hypothesis_discovery_parallel_workers_are_validated() -> None:
    request = HypothesisDiscoveryRequest(
        dataset_id="test",
        date_from=datetime(2025, 1, 1, tzinfo=timezone.utc),
        date_to=datetime(2025, 2, 1, tzinfo=timezone.utc),
        parallel_workers=4,
    )
    assert request.parallel_workers == 4

    with pytest.raises(ValueError):
        HypothesisDiscoveryRequest(
            dataset_id="test",
            date_from=datetime(2025, 1, 1, tzinfo=timezone.utc),
            date_to=datetime(2025, 2, 1, tzinfo=timezone.utc),
            parallel_workers=0,
        )

    with pytest.raises(ValueError):
        HypothesisDiscoveryRequest(
            dataset_id="test",
            date_from=datetime(2025, 1, 1, tzinfo=timezone.utc),
            date_to=datetime(2025, 2, 1, tzinfo=timezone.utc),
            parallel_workers=33,
        )


@pytest.mark.parametrize("timeframe", ("m1", "m5", "m10", "m15"))
def test_hypothesis_discovery_accepts_supported_execution_timeframes(timeframe: str) -> None:
    request = HypothesisDiscoveryRequest(
        dataset_id="test",
        timeframe=timeframe,
        date_from=datetime(2025, 1, 1, tzinfo=timezone.utc),
        date_to=datetime(2025, 2, 1, tzinfo=timezone.utc),
    )

    assert request.timeframe == timeframe


def test_min_trades_per_week_scales_with_trading_days() -> None:
    request = HypothesisDiscoveryRequest(
        dataset_id="test",
        date_from=datetime(2025, 1, 1, tzinfo=timezone.utc),
        date_to=datetime(2025, 2, 1, tzinfo=timezone.utc),
        min_closed_trades=999,
        min_trades_per_week=2.5,
    )
    bars = pd.DataFrame({
        "time": pd.to_datetime([
            "2025-01-06T00:00:00Z",
            "2025-01-07T00:00:00Z",
            "2025-01-08T00:00:00Z",
            "2025-01-09T00:00:00Z",
            "2025-01-10T00:00:00Z",
            "2025-01-13T00:00:00Z",
            "2025-01-14T00:00:00Z",
            "2025-01-15T00:00:00Z",
            "2025-01-16T00:00:00Z",
            "2025-01-17T00:00:00Z",
        ], utc=True),
    })

    assert HypothesisResearchService._minimum_required_trades(request, bars) == 5


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


def test_hypothesis_grammar_includes_market_structure_families() -> None:
    request = HypothesisDiscoveryRequest(
        dataset_id="test",
        date_from=datetime(2025, 1, 1, tzinfo=timezone.utc),
        date_to=datetime(2025, 2, 1, tzinfo=timezone.utc),
        families=NEW_HYPOTHESIS_FAMILIES,
        max_variants=len(NEW_HYPOTHESIS_FAMILIES) * 2,
    )

    specs = generate_hypotheses(request)

    assert set(item.lineage for item in specs) == set(NEW_HYPOTHESIS_FAMILIES)


@pytest.mark.parametrize("family", NEW_HYPOTHESIS_FAMILIES)
def test_new_hypothesis_families_apply_without_signal_errors(family: str) -> None:
    request = HypothesisDiscoveryRequest(
        dataset_id="test",
        date_from=datetime(2025, 1, 1, tzinfo=timezone.utc),
        date_to=datetime(2025, 1, 15, tzinfo=timezone.utc),
        families=(family,),
        max_variants=1,
    )
    strategy = generate_hypotheses(request)[0]
    times = pd.date_range("2025-01-01T00:00:00Z", periods=240, freq="1h")
    close = [100.0 + index * 0.05 for index in range(len(times))]
    base = _base_frame(
        times,
        open=[value - 0.1 for value in close],
        high=[value + 0.4 for value in close],
        low=[value - 0.4 for value in close],
        close=close,
    )

    signals = apply_signal_rules(base, strategy)

    assert {"signal_direction", "stop_distance", "target_distance", "max_hold_bars"}.issubset(signals.columns)


def test_liquidity_sweep_reclaim_detects_closed_bar_reclaim() -> None:
    times = pd.date_range("2025-01-01T00:00:00Z", periods=4, freq="1h")
    base = _base_frame(
        times,
        open=[100.5, 100.2, 99.7, 100.0],
        high=[101.0, 100.8, 100.0, 100.5],
        low=[100.0, 99.5, 98.8, 99.8],
        close=[100.6, 100.0, 99.8, 100.2],
    )
    strategy = HypothesisSpec(
        strategy_id="sweep_reclaim_test",
        lineage="liquidity_sweep_reclaim",
        hypothesis="A swept swing low that closes back above the level can reverse.",
        parameters={
            "sweep_lookback": 2,
            "penetration_atr": 0.05,
            "reclaim_buffer_atr": 0.0,
            "wick_reject_min": 0.5,
            "close_location_min": 0.45,
            "atr_stop": 1.0,
            "reward_risk": 1.0,
            "max_hold_bars": 4,
            "context_filter": "none",
            "direction_mode": "both",
            "session_start_utc": 0,
            "session_end_utc": 24,
            "volatility_filter": "none",
        },
    )

    signals = apply_signal_rules(base, strategy)

    assert signals.loc[times[2], "signal_direction"] == 1


def test_strategy_grammar_generates_rule_trees() -> None:
    request = HypothesisDiscoveryRequest(
        dataset_id="test",
        date_from=datetime(2025, 1, 1, tzinfo=timezone.utc),
        date_to=datetime(2025, 2, 1, tzinfo=timezone.utc),
        families=("strategy_grammar",),
        max_variants=25,
    )

    specs = generate_hypotheses(request)

    assert len(specs) == 25
    assert {item.lineage for item in specs} == {"strategy_grammar"}
    assert all(item.parameters.get("rule_blocks") for item in specs)


def test_strategy_grammar_respects_block_group_controls() -> None:
    request = HypothesisDiscoveryRequest(
        dataset_id="test",
        date_from=datetime(2025, 1, 1, tzinfo=timezone.utc),
        date_to=datetime(2025, 2, 1, tzinfo=timezone.utc),
        families=("strategy_grammar",),
        max_variants=20,
        grammar_block_groups=("imbalance",),
        grammar_complexity="simple",
        grammar_randomness="low",
    )

    specs = generate_hypotheses(request)

    assert len(specs) == 20
    assert all("imbalance" in str(item.parameters.get("grammar_block_groups", "")) for item in specs)
    block_names = {
        block["name"]
        for item in specs
        for block in item.parameters["rule_blocks"]  # type: ignore[index]
    }
    assert block_names <= {
        "fair_value_gap",
        "inverse_fair_value_gap",
        "fvg_mitigation_rejection",
        "balanced_price_range",
    }


def test_strategy_grammar_fvg_retrace_block_detects_closed_bar_entry() -> None:
    times = pd.date_range("2025-01-01T00:00:00Z", periods=5, freq="1h")
    base = _base_frame(
        times,
        open=[100.0, 100.4, 101.4, 101.2, 101.5],
        high=[100.2, 100.6, 102.0, 101.8, 102.2],
        low=[99.8, 100.1, 101.0, 100.5, 101.2],
        close=[100.1, 100.5, 101.8, 101.4, 102.0],
    )
    strategy = HypothesisSpec(
        strategy_id="grammar_fvg_test",
        lineage="strategy_grammar",
        hypothesis="A bullish fair value gap retrace can be used as a grammar entry block.",
        parameters={
            "rule_blocks": [{"name": "fair_value_gap", "mode": "new_or_retrace"}],
            "block_logic": "all",
            "direction_mode": "long_only",
            "session_start_utc": 0,
            "session_end_utc": 24,
            "volatility_filter": "none",
            "atr_stop": 1.0,
            "reward_risk": 1.0,
            "max_hold_bars": 4,
        },
    )

    signals = apply_signal_rules(base, strategy)

    assert signals.loc[times[2], "signal_direction"] == 1
    assert signals.loc[times[3], "signal_direction"] == 1


def test_strategy_grammar_smt_requires_external_proxy_data() -> None:
    times = pd.date_range("2025-01-01T00:00:00Z", periods=6, freq="1h")
    base = _base_frame(times)
    strategy = HypothesisSpec(
        strategy_id="grammar_smt_test",
        lineage="strategy_grammar",
        hypothesis="SMT blocks must not pass when the external proxy data is unavailable.",
        parameters={
            "rule_blocks": [{"name": "smt_divergence", "proxy": "dxy", "lookback": 3}],
            "block_logic": "all",
            "direction_mode": "both",
            "session_start_utc": 0,
            "session_end_utc": 24,
            "volatility_filter": "none",
            "atr_stop": 1.0,
            "reward_risk": 1.0,
            "max_hold_bars": 4,
        },
    )

    signals = apply_signal_rules(base, strategy)

    assert int((signals["signal_direction"] != 0).sum()) == 0


def test_failed_breakout_reversal_detects_back_inside_close() -> None:
    times = pd.date_range("2025-01-01T00:00:00Z", periods=4, freq="1h")
    base = _base_frame(
        times,
        open=[100.0, 100.5, 101.5, 100.0],
        high=[101.0, 101.0, 102.0, 100.5],
        low=[99.5, 100.0, 100.5, 99.5],
        close=[100.5, 100.7, 100.8, 100.0],
    )
    strategy = HypothesisSpec(
        strategy_id="failed_breakout_test",
        lineage="failed_breakout_reversal",
        hypothesis="A channel break that closes back inside can reverse next bar.",
        parameters={
            "channel_bars": 2,
            "break_atr": 0.05,
            "close_back_atr": 0.0,
            "atr_stop": 1.0,
            "reward_risk": 1.0,
            "max_hold_bars": 4,
            "context_filter": "none",
            "direction_mode": "both",
            "session_start_utc": 0,
            "session_end_utc": 24,
            "volatility_filter": "none",
        },
    )

    signals = apply_signal_rules(base, strategy)

    assert signals.loc[times[2], "signal_direction"] == -1


def test_prior_day_level_continuation_uses_previous_day_levels() -> None:
    times = pd.DatetimeIndex([
        pd.Timestamp("2025-01-01T22:00:00Z"),
        pd.Timestamp("2025-01-01T23:00:00Z"),
        pd.Timestamp("2025-01-02T00:00:00Z"),
        pd.Timestamp("2025-01-02T01:00:00Z"),
    ])
    base = _base_frame(
        times,
        open=[100.0, 100.2, 100.4, 101.0],
        high=[100.5, 101.0, 100.8, 101.8],
        low=[99.5, 99.8, 100.0, 100.8],
        close=[100.2, 100.4, 100.6, 101.5],
    )
    strategy = HypothesisSpec(
        strategy_id="prior_day_test",
        lineage="prior_day_level_continuation",
        hypothesis="A close through the prior day high can continue next bar.",
        parameters={
            "break_buffer_atr": 0.0,
            "atr_stop": 1.0,
            "reward_risk": 1.0,
            "max_hold_bars": 4,
            "context_filter": "none",
            "first_signal_per_day": True,
            "direction_mode": "both",
            "session_start_utc": 0,
            "session_end_utc": 24,
            "volatility_filter": "none",
        },
    )

    signals = apply_signal_rules(base, strategy)

    assert signals.loc[times[3], "signal_direction"] == 1


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
