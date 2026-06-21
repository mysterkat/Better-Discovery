"""Immutable dataset catalog and compatibility exports."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from ..paths import DEFAULT_HIST_DATA, DEFAULT_MARKET_DATA
from .models import DatasetFile, DatasetManifest


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _range(frame: pd.DataFrame) -> tuple[str | None, str | None]:
    if frame.empty or "time" not in frame:
        return None, None
    return str(frame["time"].iloc[0]), str(frame["time"].iloc[-1])


class MarketDataCatalog:
    def __init__(self, root: Path = DEFAULT_MARKET_DATA) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def folder(self, dataset_id: str) -> Path:
        return self.root / dataset_id

    def save_manifest(self, manifest: DatasetManifest) -> Path:
        folder = self.folder(manifest.dataset_id)
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / "manifest.json"
        temp = target.with_suffix(".json.tmp")
        temp.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
        os.replace(temp, target)
        return target

    def load(self, dataset_id: str) -> DatasetManifest:
        path = self.folder(dataset_id) / "manifest.json"
        if not path.is_file():
            raise FileNotFoundError(f"market-data dataset not found: {dataset_id}")
        return DatasetManifest.model_validate_json(path.read_text(encoding="utf-8"))

    def list(self) -> list[DatasetManifest]:
        manifests: list[DatasetManifest] = []
        for path in self.root.glob("*/manifest.json"):
            try:
                manifests.append(DatasetManifest.model_validate_json(path.read_text(encoding="utf-8")))
            except Exception:
                continue
        return sorted(manifests, key=lambda item: item.created_at, reverse=True)

    def write_parquet(
        self, manifest: DatasetManifest, frame: pd.DataFrame, *, kind: str,
        symbol: str, relative_path: Path, timeframe: str | None = None,
    ) -> DatasetFile:
        path = self.folder(manifest.dataset_id) / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        frame.to_parquet(temp, index=False, compression="zstd")
        os.replace(temp, path)
        first, last = _range(frame)
        item = DatasetFile(
            kind=kind, symbol=symbol, timeframe=timeframe, path=str(path), rows=len(frame),
            sha256=_hash(path), first_time=first, last_time=last,
        )
        manifest.files = [entry for entry in manifest.files if Path(entry.path) != path]
        manifest.files.append(item)
        return item

    def write_discovery_csv_from_parquet(
        self, manifest: DatasetManifest, paths: list[Path], symbol: str, timeframe: str,
    ) -> DatasetFile:
        """Build the legacy CSV while retaining only one bar partition in memory."""
        path = self.folder(manifest.dataset_id) / "discovery" / f"{symbol.lower()}_{timeframe.lower()}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".csv.tmp")
        rows = 0
        first: str | None = None
        last: str | None = None
        wrote_header = False
        try:
            for source in sorted(paths):
                frame = pd.read_parquet(
                    source, columns=["time", "open", "high", "low", "close", "tick_volume"]
                )
                if frame.empty:
                    continue
                frame = frame.rename(columns={"tick_volume": "volume"})
                frame["time"] = pd.to_datetime(frame["time"], utc=True).dt.tz_localize(None)
                frame.to_csv(temp, mode="a", header=not wrote_header, index=False)
                wrote_header = True
                rows += len(frame)
                first = first or str(frame["time"].iloc[0])
                last = str(frame["time"].iloc[-1])
            if not wrote_header:
                pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"]).to_csv(
                    temp, index=False
                )
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)
        item = DatasetFile(
            kind="discovery_csv", symbol=symbol, timeframe=timeframe, path=str(path),
            rows=rows, sha256=_hash(path), first_time=first, last_time=last,
        )
        manifest.files = [entry for entry in manifest.files if Path(entry.path) != path]
        manifest.files.append(item)
        return item

    def write_discovery_csv(
        self, manifest: DatasetManifest, frame: pd.DataFrame, symbol: str, timeframe: str,
    ) -> DatasetFile:
        path = self.folder(manifest.dataset_id) / "discovery" / f"{symbol.lower()}_{timeframe.lower()}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        output = frame[["time", "open", "high", "low", "close", "tick_volume"]].copy()
        output = output.rename(columns={"tick_volume": "volume"})
        output["time"] = pd.to_datetime(output["time"], utc=True).dt.tz_localize(None)
        output.to_csv(path, index=False)
        first, last = _range(frame)
        item = DatasetFile(
            kind="discovery_csv", symbol=symbol, timeframe=timeframe, path=str(path),
            rows=len(frame), sha256=_hash(path), first_time=first, last_time=last,
        )
        manifest.files.append(item)
        return item

    def publish_discovery_csvs(self, manifest: DatasetManifest) -> list[str]:
        """Publish a validated CSV set to the legacy discovery folder."""
        DEFAULT_HIST_DATA.mkdir(parents=True, exist_ok=True)
        staged = [Path(item.path) for item in manifest.files if item.kind == "discovery_csv"]
        if not staged:
            return []
        pattern = re.compile(r"^[a-z0-9]+_[a-z]+\d*\.csv$", re.IGNORECASE)
        temporary: list[tuple[Path, Path]] = []
        for source in staged:
            target = DEFAULT_HIST_DATA / source.name
            temp = target.with_suffix(".csv.importing")
            shutil.copy2(source, temp)
            temporary.append((temp, target))
        for existing in DEFAULT_HIST_DATA.iterdir():
            if existing.is_file() and pattern.match(existing.name):
                existing.unlink()
        for temp, target in temporary:
            os.replace(temp, target)
        return [str(target) for _, target in temporary]

    def complete(self, manifest: DatasetManifest, quality: dict) -> DatasetManifest:
        manifest.state = "complete"
        manifest.completed_at = datetime.now(timezone.utc).isoformat()
        manifest.quality = quality
        self.save_manifest(manifest)
        (self.root / "current.json").write_text(
            json.dumps({"dataset_id": manifest.dataset_id}, indent=2), encoding="utf-8"
        )
        return manifest
