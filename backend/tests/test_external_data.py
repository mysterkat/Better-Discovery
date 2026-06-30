from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from app.external_data import ExternalDataImportRequest, ExternalDataStore


def test_external_vix_import_normalizes_and_summarizes_context(tmp_path) -> None:
    source = tmp_path / "vix.csv"
    pd.DataFrame({
        "date": ["2026-01-01", "2026-01-02", "2026-01-05"],
        "close": [16.0, 18.0, 28.0],
    }).to_csv(source, index=False)
    store = ExternalDataStore(tmp_path / "store")

    metadata = store.import_data(ExternalDataImportRequest(kind="vix", source=str(source)))
    context = store.context("XAUUSD", datetime(2026, 1, 6, tzinfo=timezone.utc))

    assert metadata["rows"] == 3
    assert context["vix_value"] == 28.0
    assert context["vix_regime"] == "stress"


def test_external_cot_import_respects_release_time(tmp_path) -> None:
    source = tmp_path / "cot.csv"
    pd.DataFrame({
        "date": ["2026-01-06", "2026-01-13"],
        "release_time": ["2026-01-09T20:30:00Z", "2026-01-16T20:30:00Z"],
        "spec_net": [1000, 5000],
        "commercial_net": [-1000, -5000],
    }).to_csv(source, index=False)
    store = ExternalDataStore(tmp_path / "store")

    store.import_data(ExternalDataImportRequest(kind="cot", source=str(source)))
    early = store.context("XAUUSD", datetime(2026, 1, 15, tzinfo=timezone.utc))
    late = store.context("XAUUSD", datetime(2026, 1, 17, tzinfo=timezone.utc))

    assert early["cot_spec_net"] == 1000.0
    assert late["cot_spec_net"] == 5000.0
