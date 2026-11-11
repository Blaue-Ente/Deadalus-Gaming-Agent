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


def test_a_maze_with_css_hover_is_not_a_racer():
    from daedalus.arcade_craft import fun_signals

    maze = "button:hover { color: red } function overlap(a, b) {} // collapse walls; keep track of keys"
    assert fun_signals(maze)["signals"]["racer"] is False and fun_signals(maze)["blockers"] == []
    assert fun_signals("const LAPS = 3; let lapTime = 0; turbo boost")["signals"]["racer"] is True


def test_the_game_loop_late_in_a_long_script_is_seen(a0_tmp):  # noqa: F811
    from daedalus import playground as pg
    from daedalus import playtest

    slug = asyncio.run(pg.create_project("Long", kind="static"))["slug"]
    pg.write_file(slug, "index.html", '<canvas></canvas><script src="script.js"></script>')
    pg.write_file(slug, "script.js", "// setup\n" + "const pad = 1;\n" * 400 +
                  "addEventListener('keydown', e => {});\nfunction loop(){ requestAnimationFrame(loop) }\nlet score=0;\n")
    verdict = playtest._heuristic(playtest._project_snapshot(slug))
    assert not any("game loop" in b for b in verdict.get("blockers", []))


def test_a_critic_reply_cut_by_the_output_limit_still_has_its_scores():
    """Found live: a long Bulgarian critique hit 1800 tokens, the JSON never closed, and every game scored 0.0."""
    from daedalus.playground_quality import parse_quality_result

    cut = ('{\n  "scores": {"visual_polish": 2, "coherence": 3, "gameplay_loop": 3, "controls": 3},\n  "overall": 2,\n'
           '  "summary": "Лабиринтът е почти невидим.",\n  "issues": [\n    "HUD е обикновен текст", "Hit feedback: доба')
    out = parse_quality_result(cut)
    assert out["parsed"] is True and out["truncated"] is True and out["overall"] == 2.0
    assert out["scores"]["coherence"] == 3.0 and "невидим" in out["summary"]
    assert parse_quality_result("no json here")["parsed"] is False


def test_a_failed_gameplay_check_says_which_and_why():
    from daedalus.playground_gameplay_verify import failed_summary

    checks = [{"id": "canvas_or_visual", "ok": True, "detail": "canvas"},
              {"id": "post_start_state", "ok": False, "detail": "canvas=True game=False body=12"},
              {"id": "keyboard_input", "ok": False, "detail": "key_events=0 canvas=True game=False"}]
    text = failed_summary(checks)
    assert text.startswith("gameplay checks failed: ") and "post_start_state: canvas=True game=False" in text
    assert "keyboard_input" in text and "canvas_or_visual" not in text
