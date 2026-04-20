"""Bridge to pattern_discovery_v6.py.

pattern_discovery_v6.main() is parameterless: it reads module-level constants
(RANDOM_SEED, OUTPUT_FOLDER, TRAIN_RATIO, ...) and writes CSV results into
OUTPUT_FOLDER. To expose parameters to the UI without editing the source file,
this bridge monkey-patches the imported module's attributes before calling
main(). Only known constant names are allowed.
"""

from __future__ import annotations

from typing import Any

from .. import paths  # noqa: F401


# Whitelist of module-level constants safe to override from the UI. Anything
# outside this set raises KeyError so we can't silently clobber internals.
OVERRIDABLE_CONSTANTS: set[str] = {
    "RANDOM_SEED",
    "TRAIN_RATIO",
    "CORES_RESERVED",
    "DATA_FOLDER",
    "OUTPUT_FOLDER",
    "MULTI_SEED_COUNT",
    "WINDOW_SIZE",
    "SHAPE_MATCH_THRESHOLD",
    "MEANINGFUL_SUSTAIN_BARS",
    "SL_PCT_QUANTILE",
    "TP_PCT_QUANTILE",
    "GENE_DIVERSITY_THRESHOLD",
    "ENSEMBLE_OVERLAP_THRESHOLD",
    "DISCRIM_MIN_ACCURACY",
    "SCORE_WILSON_CONFIDENCE",
    "MIN_TEST_TRADES_PER_DAY",
    "INDICATOR_WARMUP_BARS",
    "MT5_SERVER_UTC_OFFSET",
}


def _get_module():
    # Lazy import: pattern_discovery_v6 is ~3k lines and pulls heavy deps.
    # Only import when the user actually starts a discovery run.
    import importlib

    return importlib.import_module("pattern_discovery_v6")


def list_defaults() -> dict[str, Any]:
    """Return the current values of overridable constants, for UI defaults."""
    mod = _get_module()
    out: dict[str, Any] = {}
    for name in OVERRIDABLE_CONSTANTS:
        if hasattr(mod, name):
            val = getattr(mod, name)
            # Stringify paths and other non-JSON-native types lazily at the router layer.
            out[name] = val
    return out


def run_discovery(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """Monkey-patch globals and call pattern_discovery_v6.main().

    Returns a minimal summary dict; the full artifacts land in OUTPUT_FOLDER
    as CSVs and are picked up by a separate read step.
    """
    mod = _get_module()
    overrides = overrides or {}

    unknown = set(overrides) - OVERRIDABLE_CONSTANTS
    if unknown:
        raise KeyError(f"not overridable: {sorted(unknown)}")

    # Snapshot originals so we can restore them after the run.
    original: dict[str, Any] = {}
    for name, val in overrides.items():
        if not hasattr(mod, name):
            raise KeyError(f"pattern_discovery_v6 has no attribute '{name}'")
        original[name] = getattr(mod, name)
        setattr(mod, name, val)

    try:
        mod.main()
    finally:
        for name, val in original.items():
            setattr(mod, name, val)

    return {
        "ok": True,
        "output_folder": str(getattr(mod, "OUTPUT_FOLDER", "")),
        "overrides_applied": list(overrides.keys()),
    }
