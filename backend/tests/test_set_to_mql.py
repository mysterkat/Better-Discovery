"""Set→MQL converter: template input sync must never drop EA parameters."""

from __future__ import annotations

from pathlib import Path

from backend.app.bridge import set_to_mql as mql


def _template_input_names() -> set[str]:
    lines = Path(mql.default_template_path()).read_text(encoding="utf-8").splitlines()
    return {s.name for s in mql._parse_template_inputs(lines)}


def test_template_parses_commission_inputs() -> None:
    names = _template_input_names()
    assert "Commission_R" in names
    assert "Swap_R_PerBar" in names


def test_export_includes_every_template_input() -> None:
    set_text = "\n".join(
        [
            "; Pattern 01 Cluster 01 [LONG] [DIRECTIONAL]",
            "MagicNumber=10001",
            "Commission_R=0.050000",
            "Swap_R_PerBar=0.001000",
            "DirectionMode=0",
            "SL_Pct=0.005220",
            "TP_Pct=0.003630",
            "Lots=0.10",
            "CooldownBars=3",
            "MaxHoldBars=32",
        ]
    )
    out = mql.export(set_text, output_name="_test_template_sync")
    text = Path(out).read_text(encoding="utf-8")
    exported = mql._collect_input_names(text)
    required = _template_input_names()
    missing = required - exported
    assert not missing, f"missing inputs in export: {sorted(missing)}"
