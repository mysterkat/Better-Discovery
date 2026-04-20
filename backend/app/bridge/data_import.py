"""Bridge to discovery_to_mc.py (CSV -> daily PnL array conversion)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .. import paths  # noqa: F401

import discovery_to_mc as _d2mc  # type: ignore[import-not-found]


def load_csv_as_daily_pnl(
    csv_path: str | Path,
    split_filter: str = "test",
) -> np.ndarray:
    """Thin wrapper around discovery_to_mc.load_pattern_csv."""
    return np.asarray(_d2mc.load_pattern_csv(str(csv_path), split_filter=split_filter), dtype=float)


def derive_pnl_pts(df: pd.DataFrame) -> list[float]:
    """Expose the points-derivation helper for UI previews."""
    return _d2mc.derive_pnl_pts(df).astype(float).tolist()


def preview_csv(csv_path: str | Path, limit: int = 20) -> dict[str, Any]:
    """Read a CSV header + head-rows for display in the Data Import tab."""
    p = Path(csv_path)
    if not p.exists():
        raise FileNotFoundError(str(p))
    df = pd.read_csv(p)
    head = df.head(limit)
    return {
        "path": str(p),
        "columns": list(df.columns),
        "n_rows": int(len(df)),
        "dtypes": {c: str(t) for c, t in df.dtypes.items()},
        "head": _records(head),
    }


def _records(df: pd.DataFrame) -> list[dict[str, Any]]:
    # Coerce NaN to None for JSON safety.
    return [
        {k: (None if pd.isna(v) else v) for k, v in row.items()}
        for row in df.to_dict(orient="records")
    ]
