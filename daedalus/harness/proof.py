"""Proof levels (spec 3.7): what a result is allowed to claim. One list, shown in Settings and stored on Runs."""
from __future__ import annotations

from typing import Any

# level -> (i18n key for the label shown in Studio, what produces it today)
LEVELS: tuple[tuple[int, str, str], ...] = (
    (0, "studio.proofL0", "files written"),
    (1, "studio.proofL1", "engine started and answered (headless or rendered)"),
    (2, "studio.proofL2", "headless browser render"),
    (3, "studio.proofL3", "native window frame captured and not blank"),
    (4, "studio.proofL4", "L3 + real keyboard/mouse input + the picture changed more than it moves on its own"),
    (5, "studio.proofL5", "L4 + the mentor's model read a hidden label from a frame (vision verified)"),
)


def describe(*, engine_found: bool, browser_found: bool, native_ready: bool,
             vision_verified: bool | None) -> list[dict[str, Any]]:
    """Each level and whether this machine can reach it right now. L1 needs the engine, L2 a headless browser,
    L3/L4 the whole native stack, and L5 additionally a stored, positive vision probe."""
    out: list[dict[str, Any]] = []
    for level, key, produced_by in LEVELS:
        if level == 0:
            reachable = True
        elif level == 1:
            reachable = bool(engine_found)
        elif level == 2:
            reachable = bool(browser_found)
        elif level in (3, 4):
            reachable = bool(native_ready)
        else:
            reachable = bool(native_ready) and bool(vision_verified)
        out.append({"level": level, "key": key, "produced_by": produced_by, "reachable": reachable})
    return out
