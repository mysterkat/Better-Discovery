from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from app.local_replay.engine import run_replay, run_replay_stream
from app.local_replay.features import build_features
from app.local_replay.models import ReplayMetrics, ReplayRequest
from app.local_replay.service import LocalReplayService
from app.market_data.catalog import MarketDataCatalog
from app.market_data.models import DatasetManifest
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


def test_replay_request_requires_bounded_utc_range() -> None:
    with pytest.raises(ValueError, match="provided together"):
        ReplayRequest(
            dataset_id="test",
            set_path="dummy.set",
            date_from=datetime(2025, 1, 2, tzinfo=timezone.utc),
        )
    with pytest.raises(ValueError, match="date_to must be after date_from"):
        ReplayRequest(
            dataset_id="test",
            set_path="dummy.set",
            date_from=datetime(2025, 1, 2, tzinfo=timezone.utc),
            date_to=datetime(2025, 1, 1, tzinfo=timezone.utc),
        )


def test_local_replay_slices_range_and_partitions_exactly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = MarketDataCatalog(root=tmp_path / "market_data")
    service = LocalReplayService(catalog=catalog)
    set_path = tmp_path / "campaign.set"
    set_path.write_text(
        "DirectionMode=0\nSL_Pct=0.010000\nTP_Pct=0.001000\nLots=1.0\nSignalTF1=PERIOD_M5\n",
        encoding="utf-8",
    )

    manifest = DatasetManifest(
        dataset_id="slice-test",
        provider="dukascopy",
        venue="Dukascopy historical data feed",
        symbols=["XAUUSD"],
        timeframes=["m5", "m10"],
        requested_from="2025-01-01T00:00:00Z",
        requested_to="2025-01-03T00:00:00Z",
        created_at="2025-01-01T00:00:00Z",
    )
    ticks_1 = pd.DataFrame({
        "time": pd.to_datetime(["2025-01-01T00:00:00Z", "2025-01-01T01:00:00Z"], utc=True),
        "bid": [100.0, 100.1], "ask": [100.2, 100.3],
    })
    ticks_2 = pd.DataFrame({
        "time": pd.to_datetime(["2025-01-02T00:00:00Z", "2025-01-02T01:00:00Z"], utc=True),
        "bid": [101.0, 101.1], "ask": [101.2, 101.3],
    })
    bars_1 = pd.DataFrame({
        "time": pd.to_datetime(["2025-01-01T00:00:00Z", "2025-01-01T00:10:00Z"], utc=True),
        "open": [2000.0, 2001.0], "high": [2000.5, 2001.5], "low": [1999.5, 2000.5],
        "close": [2000.2, 2001.2], "tick_volume": [10, 11],
    })
    bars_2 = pd.DataFrame({
        "time": pd.to_datetime(["2025-01-02T00:00:00Z", "2025-01-02T00:10:00Z"], utc=True),
        "open": [2002.0, 2003.0], "high": [2002.5, 2003.5], "low": [2001.5, 2002.5],
        "close": [2002.2, 2003.2], "tick_volume": [12, 13],
    })
    signal_1 = pd.DataFrame({
        "time": pd.to_datetime(["2025-01-01T00:00:00Z", "2025-01-01T00:05:00Z"], utc=True),
        "open": [3000.0, 3001.0], "high": [3000.5, 3001.5], "low": [2999.5, 3000.5],
        "close": [3000.2, 3001.2], "tick_volume": [20, 21],
    })
    signal_2 = pd.DataFrame({
        "time": pd.to_datetime(["2025-01-02T00:00:00Z", "2025-01-02T00:05:00Z"], utc=True),
        "open": [3002.0, 3003.0], "high": [3002.5, 3003.5], "low": [3001.5, 3002.5],
        "close": [3002.2, 3003.2], "tick_volume": [22, 23],
    })
    catalog.write_parquet(manifest, ticks_1, kind="ticks", symbol="XAUUSD", relative_path=Path("ticks/part1.parquet"))
    catalog.write_parquet(manifest, ticks_2, kind="ticks", symbol="XAUUSD", relative_path=Path("ticks/part2.parquet"))
    catalog.write_parquet(manifest, bars_1, kind="bars", symbol="XAUUSD", timeframe="m10", relative_path=Path("bars/m10/part1.parquet"))
    catalog.write_parquet(manifest, bars_2, kind="bars", symbol="XAUUSD", timeframe="m10", relative_path=Path("bars/m10/part2.parquet"))
    catalog.write_parquet(manifest, signal_1, kind="bars", symbol="XAUUSD", timeframe="m5", relative_path=Path("bars/m5/part1.parquet"))
    catalog.write_parquet(manifest, signal_2, kind="bars", symbol="XAUUSD", timeframe="m5", relative_path=Path("bars/m5/part2.parquet"))
    catalog.complete(manifest, quality={"symbols": {"XAUUSD": {"point_size": 0.01}}})

    captured: dict[str, object] = {}

    def fake_run_replay_stream(tick_batches, bars, strategy, request, point_size, signal_bars=None, total_ticks=None):
        captured["tick_batches"] = [batch.copy() for batch in tick_batches]
        captured["bars"] = bars.copy()
        captured["signal_bars"] = {key: value.copy() for key, value in (signal_bars or {}).items()}
        captured["request"] = request
        captured["point_size"] = point_size
        captured["total_ticks"] = total_ticks
        return pd.DataFrame(), ReplayMetrics(
            trades=0, wins=0, win_rate_pct=None, net_profit=0.0, gross_profit=0.0,
            gross_loss=0.0, profit_factor=None, expected_payoff=None, max_drawdown=0.0,
            max_drawdown_pct=0.0,
        ), pd.DataFrame()

    monkeypatch.setitem(
        LocalReplayService.run.__globals__, "run_replay_stream", fake_run_replay_stream
    )

    request = ReplayRequest(
        dataset_id="slice-test",
        set_path=str(set_path),
        timeframe="m10",
        contract_size=1.0,
        commission_per_lot_round_turn=0.0,
        date_from=datetime(2025, 1, 2, 0, 0, tzinfo=timezone.utc),
        date_to=datetime(2025, 1, 2, 0, 5, tzinfo=timezone.utc),
    )
    service.run(request)

    tick_batches = captured["tick_batches"]
    bars = captured["bars"]
    signal_bars = captured["signal_bars"]
    assert len(tick_batches) == 1
    assert list(tick_batches[0]["time"]) == [pd.Timestamp("2025-01-02T00:00:00Z")]
    assert list(bars["time"]) == [pd.Timestamp("2025-01-02T00:00:00Z")]
    assert list(signal_bars["tf1"]["time"]) == [pd.Timestamp("2025-01-02T00:00:00Z")]


def test_local_replay_rejects_out_of_coverage_range(tmp_path: Path) -> None:
    catalog = MarketDataCatalog(root=tmp_path / "market_data")
    service = LocalReplayService(catalog=catalog)
    set_path = tmp_path / "campaign.set"
    set_path.write_text("DirectionMode=0\nSL_Pct=0.010000\nTP_Pct=0.001000\nLots=1.0\n", encoding="utf-8")

    manifest = DatasetManifest(
        dataset_id="coverage-test",
        provider="dukascopy",
        venue="Dukascopy historical data feed",
        symbols=["XAUUSD"],
        timeframes=["m10"],
        requested_from="2025-01-01T00:00:00Z",
        requested_to="2025-01-03T00:00:00Z",
        created_at="2025-01-01T00:00:00Z",
    )
    bars = pd.DataFrame({
        "time": pd.to_datetime(["2025-01-02T00:00:00Z", "2025-01-02T00:10:00Z"], utc=True),
        "open": [2002.0, 2003.0], "high": [2002.5, 2003.5], "low": [2001.5, 2002.5],
        "close": [2002.2, 2003.2], "tick_volume": [12, 13],
    })
    ticks = pd.DataFrame({
        "time": pd.to_datetime(["2025-01-02T00:00:00Z", "2025-01-02T01:00:00Z"], utc=True),
        "bid": [101.0, 101.1], "ask": [101.2, 101.3],
    })
    catalog.write_parquet(manifest, ticks, kind="ticks", symbol="XAUUSD", relative_path=Path("ticks/part.parquet"))
    catalog.write_parquet(manifest, bars, kind="bars", symbol="XAUUSD", timeframe="m10", relative_path=Path("bars/m10/part.parquet"))
    catalog.complete(manifest, quality={"symbols": {"XAUUSD": {"point_size": 0.01}}})

    request = ReplayRequest(
        dataset_id="coverage-test",
        set_path=str(set_path),
        timeframe="m10",
        contract_size=1.0,
        commission_per_lot_round_turn=0.0,
        date_from=datetime(2025, 1, 2, 0, 0, tzinfo=timezone.utc),
        date_to=datetime(2025, 1, 4, 0, 0, tzinfo=timezone.utc),
    )
    with pytest.raises(ValueError, match="outside dataset coverage"):
        service.run(request)
