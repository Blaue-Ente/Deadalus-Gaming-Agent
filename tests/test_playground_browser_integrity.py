"""Browser verify: integrity gates vs runtime errors."""
from __future__ import annotations

from daedalus.playground_browser import integrity_fatal_and_warnings, paint_summary


def test_integrity_scaffold_is_warning_only() -> None:
    fatal, warn = integrity_fatal_and_warnings(["scaffold_left_in_place"])
    assert fatal == []
    assert warn and "Phaser seed" in warn[0]


def test_integrity_orphan_is_fatal() -> None:
    fatal, warn = integrity_fatal_and_warnings(["scaffold_left_in_place", "orphan_game_js_not_loaded"])
    assert "orphan_game_js_not_loaded" in fatal
    assert warn  # scaffold still warns


def test_paint_summary_integrity_warning_not_uncaught() -> None:
    s = paint_summary(
        reachable=True,
        dark_frame=False,
        blank_screen=False,
        has_fatal=False,
        has_canvas=True,
        page_errors=[],
        console_errors=[],
        failed_requests=[],
        dom_text_len=0,
        integrity_warnings=["kernel Phaser seed still present"],
    )
    assert "integrity" in s.lower() or "Phaser seed" in s
    assert "uncaught" not in s.lower()
