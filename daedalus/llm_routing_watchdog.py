"""Last LLM routing failure — surfaced in Connectors recommendations (not silent)."""
from __future__ import annotations

import time
from typing import Any

_last: dict[str, Any] | None = None


def note_failure(
    *,
    error: str,
    requested_model: str | None = None,
    source: str = "",
) -> None:
    global _last
    _last = {
        "error": (error or "")[:800],
        "requested_model": (requested_model or "")[:240],
        "source": (source or "")[:120],
        "ts": time.time(),
    }


def note_success(*, source: str = "") -> None:
    """Clear sticky failure after a successful routed call."""
    global _last
    if _last is None:
        return
    if source and _last.get("source") and source != _last.get("source"):
        return
    _last = None


def last_failure() -> dict[str, Any] | None:
    return dict(_last) if _last else None


def clear() -> None:
    """Tests and rare diagnostics — sticky routing errors reset."""
    global _last
    _last = None
