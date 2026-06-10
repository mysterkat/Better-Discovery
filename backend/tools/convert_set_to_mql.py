#!/usr/bin/env python3
"""Convert a Pattern Discovery .set file to a ready-to-compile .mq5 EA.

Usage (from project root):
    python backend/tools/convert_set_to_mql.py path/to/pattern.set
    python backend/tools/convert_set_to_mql.py path/to/pattern.set -o pattern_01_C01_LONG

Output defaults to userdata/mql/<stem>.mq5 (or the name from pattern metadata).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running as a script without installing the package.
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.app.bridge.set_to_mql import (  # noqa: E402
    _suggest_filename,
    export_from_set_path,
    export_report,
    parse_set_file,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert .set to .mq5 for MetaTrader 5")
    parser.add_argument("set_file", type=Path, help="Path to the .set file")
    parser.add_argument(
        "-o",
        "--output-name",
        default=None,
        help="Output filename stem (no extension), e.g. pattern_01_C01_LONG",
    )
    parser.add_argument(
        "--beside",
        action="store_true",
        help="Also write <pattern>_C01_LONG.mq5 next to the .set file",
    )
    args = parser.parse_args()

    set_path = args.set_file.resolve()
    if not set_path.is_file():
        print(f"ERROR: .set file not found: {set_path}", file=sys.stderr)
        return 1

    also: list[Path] = []
    if args.beside:
        parsed = parse_set_file(set_path.read_text(encoding="utf-8", errors="replace"))
        stem = args.output_name or _suggest_filename(
            parsed.get("meta", {}), parsed.get("params", {})
        )
        also.append(set_path.parent / f"{stem}.mq5")

    out = export_from_set_path(
        set_path,
        output_name=args.output_name,
        also_write_paths=also or None,
    )
    report = export_report(out)
    if report["missing_inputs"]:
        print(f"ERROR: missing inputs: {report['missing_inputs']}", file=sys.stderr)
        return 2
    print(out)
    if also:
        for p in also:
            print(f"also: {p.resolve()}")
    print(
        f"inputs: {report['inputs_present']}/{report['inputs_required']} "
        f"(Commission_R={report['has_commission_r']}, "
        f"Swap_R_PerBar={report['has_swap_r_per_bar']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
