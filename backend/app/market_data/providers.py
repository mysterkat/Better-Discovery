"""Market-data providers. Provider output always uses UTC timestamps."""

from __future__ import annotations

import lzma
import ssl
import struct
import time as time_module
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta, timezone
from typing import Iterator

import httpx
import pandas as pd

try:
    import truststore
except ImportError:  # setup script installs it; fallback keeps non-provider tools usable
    truststore = None


DUKASCOPY_DIGITS: dict[str, int] = {
    "XAUUSD": 3, "XAGUSD": 3,
    "BTCUSD": 1, "ETHUSD": 2,
    "USDJPY": 3, "EURJPY": 3, "GBPJPY": 3, "AUDJPY": 3,
}


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class DukascopyProvider:
    name = "dukascopy"
    venue = "Dukascopy historical data feed"
    base_url = "https://datafeed.dukascopy.com/datafeed"
    record = struct.Struct(">3I2f")
    hourly_workers = 6
    hourly_attempts = 4

    def __init__(self, price_digits: dict[str, int] | None = None) -> None:
        self.price_digits = {**DUKASCOPY_DIGITS, **(price_digits or {})}
        tls = (
            truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            if truststore is not None else ssl.create_default_context()
        )
        self.client = httpx.Client(timeout=15.0, follow_redirects=True, verify=tls)

    def close(self) -> None:
        self.client.close()

    def digits(self, symbol: str) -> int:
        return self.price_digits.get(symbol.upper(), 5)

    def _hour_url(self, symbol: str, hour: datetime) -> str:
        return (
            f"{self.base_url}/{symbol.upper()}/{hour.year:04d}/"
            f"{hour.month - 1:02d}/{hour.day:02d}/{hour.hour:02d}h_ticks.bi5"
        )

    def _get_hour(self, url: str) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(self.hourly_attempts):
            try:
                response = self.client.get(url)
                if response.status_code < 500:
                    return response
                if response.status_code == 503 and attempt == self.hourly_attempts - 1:
                    # Dukascopy also uses 503 for unavailable/empty hour files.
                    # Preserve it after the full retry budget so the caller can
                    # record a gap instead of aborting a multi-year import.
                    return response
                last_error = httpx.HTTPStatusError(
                    f"transient provider response {response.status_code}",
                    request=response.request, response=response,
                )
            except httpx.TransportError as exc:
                last_error = exc
            if attempt < self.hourly_attempts - 1:
                time_module.sleep(0.5 * (2 ** attempt))
        assert last_error is not None
        if isinstance(last_error, httpx.TransportError):
            return httpx.Response(
                503,
                request=httpx.Request("GET", url),
                headers={"X-BD-Transport-Gap": type(last_error).__name__},
            )
        raise last_error

    def fetch_day(self, symbol: str, day: date, start: datetime, end: datetime) -> pd.DataFrame:
        rows: list[tuple[datetime, float, float, float, float]] = []
        missing_hours: list[dict[str, str]] = []
        scale = float(10 ** self.digits(symbol))
        day_start = datetime.combine(day, time.min, tzinfo=timezone.utc)
        # Dukascopy's empty closed-market hours may take ~10 seconds to return
        # a transient-looking 503. Avoid known closures, then fetch the day's
        # remaining independent hour files with a conservative bounded pool.
        if day.weekday() == 5:  # Saturday
            hours: list[datetime] = []
        else:
            first_hour = 20 if day.weekday() == 6 else 0  # Sunday reopen varies with DST
            hours = [
                day_start + timedelta(hours=hour_no)
                for hour_no in range(first_hour, 24)
                if day_start + timedelta(hours=hour_no + 1) > start
                and day_start + timedelta(hours=hour_no) < end
            ]
        with ThreadPoolExecutor(max_workers=min(self.hourly_workers, len(hours) or 1)) as pool:
            responses = list(pool.map(lambda hour: self._get_hour(self._hour_url(symbol, hour)), hours))
        for hour, response in zip(hours, responses, strict=True):
            if response.status_code in (403, 404, 503):
                expected_open = (
                    hour.weekday() < 4
                    or (hour.weekday() == 4 and hour.hour < 21)
                    or (hour.weekday() == 6 and hour.hour >= 22)
                )
                if response.status_code == 503 and expected_open:
                    missing_hours.append({
                        "hour": hour.isoformat(),
                        "reason": response.headers.get("X-BD-Transport-Gap", "HTTP 503"),
                    })
                continue
            response.raise_for_status()
            if not response.content:
                continue
            try:
                payload = lzma.decompress(response.content)
            except lzma.LZMAError as exc:
                raise RuntimeError(f"invalid Dukascopy BI5 payload for {symbol} {hour.isoformat()}") from exc
            usable = len(payload) - (len(payload) % self.record.size)
            for offset in range(0, usable, self.record.size):
                millis, ask_i, bid_i, ask_volume, bid_volume = self.record.unpack_from(payload, offset)
                timestamp = hour + timedelta(milliseconds=millis)
                if start <= timestamp < end:
                    rows.append((timestamp, bid_i / scale, ask_i / scale, bid_volume, ask_volume))
        frame = pd.DataFrame(rows, columns=["time", "bid", "ask", "bid_volume", "ask_volume"])
        frame.attrs["missing_hours"] = missing_hours
        if frame.empty:
            return frame
        frame["time"] = pd.to_datetime(frame["time"], utc=True)
        frame["mid"] = (frame["bid"] + frame["ask"]) / 2.0
        frame["spread"] = frame["ask"] - frame["bid"]
        frame["flags"] = 0
        frame["source"] = self.name
        result = frame.sort_values("time").drop_duplicates("time", keep="last").reset_index(drop=True)
        result.attrs["missing_hours"] = missing_hours
        return result

    def days(self, start: datetime, end: datetime) -> Iterator[date]:
        current = _utc(start).date()
        last = (_utc(end) - timedelta(microseconds=1)).date()
        while current <= last:
            yield current
            current += timedelta(days=1)


def normalize_range(start: datetime, end: datetime) -> tuple[datetime, datetime]:
    return _utc(start), _utc(end)
