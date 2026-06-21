from __future__ import annotations

from pathlib import Path

import pandas as pd

from app.local_replay.engine import run_replay, run_replay_stream
from app.local_replay.features import build_features
from app.local_replay.models import ReplayRequest
from app.research.models import StrategySpec


def test_bid_ask_replay_enters_at_ask_and_exits_at_bid(tmp_path: Path) -> None:
    set_path = tmp_path / "simple.set"
    set_path.write_text(
        "DirectionMode=0\nSL_Pct=0.010000\nTP_Pct=0.001000\nLots=1.0\n"
        "CooldownBars=0\nMaxSpreadPoints=100\nbull_lo=1\nbull_hi=1\n",
        encoding="utf-8",
    )
    bars = pd.DataFrame({
        "time": pd.to_datetime(["2025-01-01T00:00:00Z", "2025-01-01T00:01:00Z"], utc=True),
        "open": [100.0, 100.0], "high": [100.5, 100.5], "low": [99.9, 99.9],
        "close": [100.4, 100.4], "tick_volume": [10, 10],
    })
    ticks = pd.DataFrame({
        "time": pd.to_datetime(["2025-01-01T00:01:01Z", "2025-01-01T00:01:30Z"], utc=True),
        "bid": [100.0, 100.3], "ask": [100.1, 100.4],
    })
    request = ReplayRequest(
        dataset_id="test", set_path=str(set_path), timeframe="m1",
        contract_size=1.0, commission_per_lot_round_turn=0.0,
    )
    ledger, metrics, _ = run_replay(ticks, bars, StrategySpec.from_set(set_path), request, 0.01)
    assert len(ledger) == 1
    assert ledger.iloc[0]["entry_price"] == 100.1
    assert ledger.iloc[0]["exit_price"] >= 100.3
    assert ledger.iloc[0]["exit_reason"] == "take_profit"
    assert metrics.net_profit > 0

    streamed, streamed_metrics, _ = run_replay_stream(
        (ticks.iloc[:1], ticks.iloc[1:]), bars, StrategySpec.from_set(set_path), request, 0.01,
        total_ticks=len(ticks),
    )
    pd.testing.assert_frame_equal(streamed, ledger)
    assert streamed_metrics == metrics


def test_local_feature_formulas_match_discovery() -> None:
    import numpy as np
    import pattern_discovery_v6 as discovery

    count = 320
    time = pd.date_range("2025-01-01", periods=count, freq="10min", tz="UTC")
    close = 2000 + np.sin(np.arange(count) / 12) * 8 + np.arange(count) * 0.03
    bars = pd.DataFrame({
        "time": time, "open": close - 0.2, "high": close + 1.1,
        "low": close - 1.0, "close": close, "tick_volume": 100 + np.arange(count) % 17,
    })
    local = build_features(bars, 0)
    raw = bars.rename(columns={"tick_volume": "volume"}).set_index("time")
    old_offset = discovery.MT5_SERVER_UTC_OFFSET
    old_research = discovery.USE_RESEARCH_FEATURES
    old_extra = discovery.USE_EXT_FEATURES
    try:
        discovery.MT5_SERVER_UTC_OFFSET = 0
        discovery.USE_RESEARCH_FEATURES = False
        discovery.USE_EXT_FEATURES = set()
        expected = discovery._add_indicators(raw.copy())
        expected["mtf_bull_score"] = (expected["trend"] == 1).astype(int)
        expected["mtf_bear_score"] = (expected["trend"] == -1).astype(int)
        expected = discovery.add_extended_features(expected)
        expected = discovery.detect_regimes(expected)
        expected = discovery.add_v5_features(expected)
    finally:
        discovery.MT5_SERVER_UTC_OFFSET = old_offset
        discovery.USE_RESEARCH_FEATURES = old_research
        discovery.USE_EXT_FEATURES = old_extra
    for column in (
        "rsi14", "macd_norm", "atr_pct", "bb_width", "trend", "body_pct", "rng_atr",
        "vol_ratio", "vol_body_conf", "regime", "poc_dist", "stoch_k", "rolling_sharpe",
        "sd_zone", "vwap_dist",
    ):
        np.testing.assert_allclose(local[column], expected[column], rtol=1e-10, atol=1e-10)
