"""Bridge for the Set->MQL exporter.

No such module exists in MONTE CARLO/src yet. This bridge raises a clear
NotImplementedError so the UI surfaces the gap instead of silently failing.
The gap is documented in NOTES_FOR_USER.md.
"""

from __future__ import annotations


class SetToMqlNotAvailable(NotImplementedError):
    """Raised when export() is called. Replace when an exporter lands upstream."""


def export(pattern_id: str, template: str) -> str:
    raise SetToMqlNotAvailable(
        "Set->MQL exporter is not yet available in MONTE CARLO/src. "
        "See NOTES_FOR_USER.md for the requested upstream entry point."
    )
