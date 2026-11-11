"""The playtest critic reads the files that are the game, whatever they are called.

Found live (2026-11-11): an Autopilot game kept its code in script.js; the critic read only fixed names (index.html,
main.js, game.js, ...), saw no input handler and no game loop, and Autopilot failed a working game."""
from __future__ import annotations

import asyncio

from a0_support import a0_tmp  # noqa: F401


def test_scripts_loaded_by_index_html_are_read(a0_tmp):  # noqa: F811
    from daedalus import playground as pg
    from daedalus import playtest

    slug = asyncio.run(pg.create_project("Lantern", kind="static"))["slug"]
    pg.write_file(slug, "index.html", '<canvas id="c"></canvas><script src="script.js"></script>')
    pg.write_file(slug, "script.js", "addEventListener('keydown', e => {});\nfunction loop(){ requestAnimationFrame(loop) }\n"
                                     "let score = 0; // game over at 0 lives\n")
    pg.write_file(slug, "src/enemies.js", "export const ghost = {};\n")
    snap = playtest._project_snapshot(slug)
    assert "=== script.js ===" in snap and "=== src/enemies.js ===" in snap
    verdict = playtest._heuristic(snap)
    assert not any("input handler" in b or "game loop" in b for b in verdict.get("blockers", []))
