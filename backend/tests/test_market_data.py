from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from app.market_data.models import MarketDataImportRequest
from app.market_data.service import aggregate_ticks
from app.market_data.catalog import MarketDataCatalog
from app.market_data.models import DatasetManifest
from pathlib import Path
import pytest

from app.market_data.service import MarketDataService


def test_market_data_request_normalizes_symbols_and_timeframes() -> None:
    request = MarketDataImportRequest(
        provider="dukascopy",
        symbols=[" xauusd ", "XAUUSD"],
        timeframes=["M1", "m5"],
        date_from=datetime(2025, 1, 1, tzinfo=timezone.utc),
        date_to=datetime(2025, 1, 2, tzinfo=timezone.utc),
    )
    assert request.symbols == ["XAUUSD"]
    assert request.timeframes == ["m1", "m5"]


def test_tick_aggregation_retains_bid_ask_volume_and_spread() -> None:
    ticks = pd.DataFrame(
        {
            "time": pd.to_datetime(
                ["2025-01-01T00:00:01Z", "2025-01-01T00:00:40Z", "2025-01-01T00:01:02Z"],
                utc=True,
            ),
            "bid": [2000.0, 2001.0, 2002.0],
            "ask": [2000.2, 2001.4, 2002.1],
            "mid": [2000.1, 2001.2, 2002.05],
            "spread": [0.2, 0.4, 0.1],
            "bid_volume": [1.0, 2.0, 3.0],
            "ask_volume": [1.5, 2.5, 3.5],
        }
    )
    bars = aggregate_ticks(ticks, "m1")
    assert list(bars["tick_volume"]) == [2, 1]
    assert bars.iloc[0]["bid_open"] == 2000.0
    assert bars.iloc[0]["ask_close"] == 2001.4
    assert bars.iloc[0]["real_volume"] == 7.0
    assert round(float(bars.iloc[0]["spread_mean"]), 6) == 0.3


def test_discovery_csv_is_staged_inside_dataset(tmp_path: Path) -> None:
    catalog = MarketDataCatalog(tmp_path)
    manifest = DatasetManifest(
        dataset_id="sample", provider="dukascopy", venue="test", symbols=["XAUUSD"],
        timeframes=["m1"], requested_from="2025-01-01", requested_to="2025-01-02",
        created_at="2025-01-02T00:00:00Z",
    )
    frame = pd.DataFrame({
        "time": pd.to_datetime(["2025-01-01T00:00:00Z"], utc=True),
        "open": [1.0], "high": [2.0], "low": [0.5], "close": [1.5], "tick_volume": [3],
    })
    item = catalog.write_discovery_csv(manifest, frame, "XAUUSD", "m1")
    assert Path(item.path).is_relative_to(tmp_path / "sample")
    assert Path(item.path).is_file()


def _ticks(day: str, price: float) -> pd.DataFrame:
    times = pd.to_datetime([f"{day}T00:00:01Z", f"{day}T00:00:30Z"], utc=True)
    return pd.DataFrame({
        "time": times, "bid": [price, price + 0.1], "ask": [price + 0.2, price + 0.3],
        "bid_volume": [1.0, 1.0], "ask_volume": [1.0, 1.0],
        "mid": [price + 0.1, price + 0.2], "spread": [0.2, 0.2],
        "flags": [0, 0], "source": ["test", "test"],
    })


class _FakeProvider:
    venue = "test"
    calls: list[str] = []
    fail_on: str | None = None

    def __init__(self, _digits=None) -> None:
        pass

    def digits(self, _symbol: str) -> int:
        return 3

    def days(self, start: datetime, end: datetime):
        from datetime import timedelta
        current = start.date()
        while current < end.date():
            yield current
            current += timedelta(days=1)

    def fetch_day(self, _symbol, day, _start, _end) -> pd.DataFrame:
        value = day.isoformat()
        self.calls.append(value)
        if value == self.fail_on:
            raise RuntimeError("simulated interruption")
        return _ticks(value, 2000.0)


def _request(**updates) -> MarketDataImportRequest:
    values = {
        "provider": "dukascopy", "symbols": ["XAUUSD"], "timeframes": ["m1", "h1"],
        "date_from": datetime(2025, 1, 31, tzinfo=timezone.utc),
        "date_to": datetime(2025, 2, 2, tzinfo=timezone.utc),
        "include_ticks": True, "write_discovery_csv": False,
    }
    values.update(updates)
    return MarketDataImportRequest(**values)


def test_import_streams_daily_ticks_into_monthly_bar_partitions(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setitem(MarketDataService.import_data.__globals__, "DukascopyProvider", _FakeProvider)
    _FakeProvider.calls = []
    _FakeProvider.fail_on = None
    result = MarketDataService(MarketDataCatalog(tmp_path)).import_data(_request())
    bars = [item for item in result["files"] if item["kind"] == "bars"]
    assert len(bars) == 4
    assert {Path(item["path"]).stem for item in bars} == {"2025-01", "2025-02"}
    assert result["quality"]["symbols"]["XAUUSD"]["tick_rows"] == 4
    assert result["import_options"]["storage_layout"] == "daily_ticks_monthly_bars"


def test_failed_import_resumes_without_refetching_retained_days(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setitem(MarketDataService.import_data.__globals__, "DukascopyProvider", _FakeProvider)
    service = MarketDataService(MarketDataCatalog(tmp_path))
    _FakeProvider.calls = []
    _FakeProvider.fail_on = "2025-02-01"
    with pytest.raises(RuntimeError, match="simulated interruption"):
        service.import_data(_request())
    failed = service.catalog.list()[0]
    assert failed.state == "failed"

    _FakeProvider.calls = []
    _FakeProvider.fail_on = None
    result = service.import_data(_request(resume_dataset_id=failed.dataset_id))
    assert result["state"] == "complete"
    assert _FakeProvider.calls == ["2025-02-01"]
