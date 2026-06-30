from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, Field

from .paths import USER_DATA


ExternalDataKind = Literal["cot", "vix", "gvz", "gamma"]


class ExternalDataImportRequest(BaseModel):
    kind: ExternalDataKind
    source: str = Field(min_length=1)
    date_column: str = "date"
    value_column: str | None = None
    release_time_column: str | None = None
    symbol: str = "XAUUSD"


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _find_column(columns: list[str], choices: tuple[str, ...]) -> str | None:
    lowered = {column.lower().strip(): column for column in columns}
    for choice in choices:
        if choice in lowered:
            return lowered[choice]
    for column in columns:
        normalized = column.lower().strip().replace(" ", "_")
        if normalized in choices:
            return column
    return None


def _read_source(source: str) -> pd.DataFrame:
    if source.lower().startswith(("http://", "https://")):
        return pd.read_csv(source, compression="infer")
    path = Path(source).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"external data source not found: {source}")
    return pd.read_csv(path, compression="infer")


def _normalize(request: ExternalDataImportRequest, raw: pd.DataFrame) -> pd.DataFrame:
    if raw.empty:
        raise ValueError("external data CSV is empty")
    columns = [str(column) for column in raw.columns]
    date_col = request.date_column if request.date_column in raw.columns else _find_column(
        columns,
        ("date", "time", "timestamp", "report_date_as_yyyy-mm-dd", "report_date", "as_of_date"),
    )
    if not date_col:
        raise ValueError("could not find date column; set date_column explicitly")
    frame = pd.DataFrame()
    frame["date"] = pd.to_datetime(raw[date_col], utc=True, errors="coerce")
    frame = frame.loc[frame["date"].notna()].copy()
    frame["symbol"] = request.symbol.upper()
    frame["kind"] = request.kind

    if request.kind in {"vix", "gvz"}:
        value_col = request.value_column if request.value_column in raw.columns else _find_column(
            columns, ("close", "value", "last", "vix", "gvz")
        )
        if not value_col:
            raise ValueError(f"could not find {request.kind.upper()} value column; set value_column explicitly")
        frame["value"] = pd.to_numeric(raw.loc[frame.index, value_col], errors="coerce")
        frame["close"] = frame["value"]
    elif request.kind == "gamma":
        for target, choices in {
            "gamma_flip": ("gamma_flip", "flip", "zero_gamma", "zero_gamma_level"),
            "call_wall": ("call_wall", "callwall", "call_wall_strike"),
            "put_wall": ("put_wall", "putwall", "put_wall_strike"),
            "net_gamma": ("net_gamma", "gex", "gamma_exposure"),
        }.items():
            source_col = _find_column(columns, choices)
            if source_col:
                frame[target] = pd.to_numeric(raw.loc[frame.index, source_col], errors="coerce")
    elif request.kind == "cot":
        release_col = request.release_time_column if request.release_time_column in raw.columns else _find_column(
            columns, ("release_time", "released_at", "publication_time")
        )
        if release_col:
            frame["release_time"] = pd.to_datetime(raw.loc[frame.index, release_col], utc=True, errors="coerce")
        else:
            frame["release_time"] = frame["date"] + pd.Timedelta(days=3, hours=20, minutes=30)
        mappings = {
            "commercial_long": ("commercial_long", "producer_merchant_processor_user_longs", "comm_positions_long_all"),
            "commercial_short": ("commercial_short", "producer_merchant_processor_user_shorts", "comm_positions_short_all"),
            "spec_long": ("spec_long", "managed_money_longs", "m_money_positions_long_all", "noncomm_positions_long_all"),
            "spec_short": ("spec_short", "managed_money_shorts", "m_money_positions_short_all", "noncomm_positions_short_all"),
            "commercial_net": ("commercial_net", "comm_net"),
            "spec_net": ("spec_net", "managed_money_net", "noncomm_net"),
        }
        for target, choices in mappings.items():
            source_col = _find_column(columns, choices)
            if source_col:
                frame[target] = pd.to_numeric(raw.loc[frame.index, source_col], errors="coerce")
        if "commercial_net" not in frame and {"commercial_long", "commercial_short"}.issubset(frame.columns):
            frame["commercial_net"] = frame["commercial_long"] - frame["commercial_short"]
        if "spec_net" not in frame and {"spec_long", "spec_short"}.issubset(frame.columns):
            frame["spec_net"] = frame["spec_long"] - frame["spec_short"]

    return frame.sort_values("date").reset_index(drop=True)


class ExternalDataStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or USER_DATA / "external_data"
        self.root.mkdir(parents=True, exist_ok=True)

    def import_data(self, request: ExternalDataImportRequest) -> dict[str, Any]:
        raw = _read_source(request.source)
        frame = _normalize(request, raw)
        path = self.root / f"{request.kind}_{request.symbol.upper()}.parquet"
        frame.to_parquet(path, index=False, compression="zstd")
        metadata = {
            "kind": request.kind,
            "symbol": request.symbol.upper(),
            "source": request.source,
            "rows": int(len(frame)),
            "first_date": str(frame["date"].iloc[0]) if not frame.empty else None,
            "last_date": str(frame["date"].iloc[-1]) if not frame.empty else None,
            "path": str(path),
            "sha256": _hash(path),
            "imported_at": datetime.now(timezone.utc).isoformat(),
            "columns": list(frame.columns),
        }
        (self.root / f"{request.kind}_{request.symbol.upper()}.json").write_text(
            json.dumps(metadata, indent=2, default=str), encoding="utf-8"
        )
        return metadata

    def list_data(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for path in self.root.glob("*.json"):
            try:
                rows.append(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                continue
        return sorted(rows, key=lambda item: str(item.get("imported_at", "")), reverse=True)

    def context(self, symbol: str, as_of: Any) -> dict[str, Any]:
        as_of_ts = pd.Timestamp(as_of)
        if as_of_ts.tzinfo is None:
            as_of_ts = as_of_ts.tz_localize("UTC")
        symbol = symbol.upper()
        context: dict[str, Any] = {}
        for kind in ("cot", "vix", "gvz", "gamma"):
            path = self.root / f"{kind}_{symbol}.parquet"
            if not path.is_file():
                continue
            frame = pd.read_parquet(path)
            frame["date"] = pd.to_datetime(frame["date"], utc=True)
            usable = frame.loc[frame["date"] <= as_of_ts].copy()
            if kind == "cot" and "release_time" in usable:
                usable["release_time"] = pd.to_datetime(usable["release_time"], utc=True)
                usable = usable.loc[usable["release_time"] <= as_of_ts]
            if usable.empty:
                continue
            latest = usable.iloc[-1].to_dict()
            if kind in {"vix", "gvz"}:
                recent = usable.tail(20)
                value = float(latest.get("close") or latest.get("value") or 0.0)
                slope = float(recent["close"].iloc[-1] - recent["close"].iloc[0]) if len(recent) > 1 and "close" in recent else 0.0
                context[f"{kind}_value"] = value
                context[f"{kind}_regime"] = "stress" if value >= 25 else "expanding" if slope > 0 else "calm"
            elif kind == "cot":
                spec_net = latest.get("spec_net")
                context["cot_spec_net"] = None if pd.isna(spec_net) else float(spec_net)
                context["cot_commercial_net"] = None if pd.isna(latest.get("commercial_net")) else float(latest.get("commercial_net"))
                if len(usable) >= 52 and "spec_net" in usable:
                    rank = float((usable.tail(156)["spec_net"] <= float(spec_net)).mean()) if pd.notna(spec_net) else 0.5
                    context["cot_spec_net_rank"] = rank
                    context["cot_regime"] = "crowded_long" if rank >= 0.85 else "crowded_short" if rank <= 0.15 else "neutral"
            elif kind == "gamma":
                for key in ("gamma_flip", "call_wall", "put_wall", "net_gamma"):
                    if key in latest and pd.notna(latest[key]):
                        context[key] = float(latest[key])
                if "net_gamma" in context:
                    context["gamma_regime"] = "positive" if context["net_gamma"] > 0 else "negative"
        return context


EXTERNAL_DATA = ExternalDataStore()
