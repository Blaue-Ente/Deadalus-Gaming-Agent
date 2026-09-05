"""S4 — a file that does not parse is not 'written OK'; canvas smells are warnings."""
from __future__ import annotations

import asyncio

import pytest

from daedalus import write_gate
from daedalus.game_lint import check as lint_check, heuristic_web
from daedalus.job_contract import evaluate, unrecovered_failure_indexes
from daedalus.native_tools import execute_native_tool
from daedalus.write_gate import check_source, duplicate_top_level_declarations

SNAKE_BAD_JS = """const canvas = document.getElementById('c');
let gameLoopId = null;
function gameLoop() {}
document.addEventListener('keydown', () => {});
let gameLoopId = setInterval(gameLoop, 100);
"""
SNAKE_OK_JS = SNAKE_BAD_JS.replace("let gameLoopId = setInterval", "gameLoopId = setInterval")


def test_duplicate_let_is_a_syntax_failure_with_line():
    report = check_source("game.js", SNAKE_BAD_JS)
    assert report.ok is False
    assert report.backend in ("node --check", "heuristic")
    assert any("gameLoopId" in e and "line 5" in e for e in report.errors)
    assert check_source("game.js", SNAKE_OK_JS).ok is True


def test_heuristic_fallback_without_node(monkeypatch):
    monkeypatch.setattr(write_gate, "node_bin", lambda: "")
    report = check_source("game.js", SNAKE_BAD_JS)
    assert report.ok is False and report.backend == "heuristic" and "node not installed" in report.skipped
    assert duplicate_top_level_declarations(SNAKE_BAD_JS) == ["line 5: 'gameLoopId' already declared at line 2"]
    assert check_source("game.js", SNAKE_OK_JS).ok is True


def test_python_json_and_unknown_types():
    py = check_source("bot.py", "def f(:\n  pass\n")
    assert py.ok is False and "line 1" in py.errors[0]
    assert check_source("bot.py", "def f():\n    return 1\n").ok is True
    js = check_source("data.json", '{"a": 1,}')
    assert js.ok is False and js.backend == "json"
    css = check_source("style.css", "body { color: red")
    assert css.ok is True and "no checker" in css.skipped


def test_html_inline_script_reports_offset_line_and_missing_src(tmp_path):
    html = "<!doctype html>\n<html><body>\n<canvas id='c'></canvas>\n<script src=\"game.js\"></script>\n<script>\nlet a = 1;\nlet a = 2;\n</script>\n</body></html>\n"
    report = check_source("index.html", html, project_root=tmp_path)
    assert report.ok is False
    assert any("line 7" in e and "'a'" in e for e in report.errors)
    assert any("game.js" in w and "does not exist yet" in w for w in report.warnings)
    (tmp_path / "game.js").write_text("// ok", encoding="utf-8")
    fixed = check_source("index.html", html.replace("let a = 2;", "a = 2;"), project_root=tmp_path)
    assert fixed.ok is True and not any("game.js" in w for w in fixed.warnings)
    unclosed = check_source("index.html", "<html><script>\nlet x = 1;\n")
    assert unclosed.ok is False and "never closed" in unclosed.errors[0]


def test_web_heuristics_catch_snake_smells():
    js = """const tileCount = 20;
let snake = [{x: 20, y: 20}];
const ctx = canvas.getContext('2d');
function gameLoop() {}
setInterval(gameLoop, 100);
function restart() { setInterval(gameLoop, 100); }
"""
    codes = {i["code"] for i in heuristic_web(js)}
    assert codes == {"web-double-loop", "web-no-input", "web-off-grid-start"}
    good = js.replace("{x: 20, y: 20}", "{x: 10, y: 10}").replace("function restart() { setInterval(gameLoop, 100); }", "")
    good += "document.addEventListener('keydown', () => {});\n"
    assert heuristic_web(good) == []
    mixed = "function tick(){}\nsetInterval(tick, 16);\nrequestAnimationFrame(tick);\n"
    assert {i["code"] for i in heuristic_web(mixed)} == {"web-mixed-loop"}
    assert check_source("game.js", js).warnings  # ride along with the gate


def test_game_lint_check_handles_web():
    r = lint_check("game.js", content=SNAKE_BAD_JS)
    assert r["lang"] == "web" and r["ok"] is False
    assert r["blockers"][0]["code"] == "web-syntax"
    ok = lint_check("x.js", content=SNAKE_OK_JS)
    assert ok["ok"] is True and ok["backends"]["web"]["ran"] is True


@pytest.fixture
def playground(tmp_path, monkeypatch):
    from daedalus import playground as pg
    from daedalus.paths import playground_projects_dir

    root = tmp_path / "projects"
    root.mkdir()
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(root))
    playground_projects_dir.cache_clear()

    async def _get(slug):
        return {"slug": slug, "kind": "static"}

    monkeypatch.setattr(pg, "get_project", _get)
    yield root
    playground_projects_dir.cache_clear()


def _write(slug, path, content):
    return asyncio.run(execute_native_tool(
        "write_file", {"slug": slug, "path": path, "content": content},
        memory=None, session_id="playground:x", read_file_fn=lambda *a: {},
    ))


def test_write_file_fails_on_syntax_but_keeps_the_file(playground):
    out = _write("snake-game", "game.js", SNAKE_BAD_JS)
    assert out.startswith("── write_file FAILED syntax snake-game/game.js")
    assert "gameLoopId" in out and "write_file again" in out and "slug=snake-game" in out
    assert (playground / "snake-game" / "game.js").read_text(encoding="utf-8") == SNAKE_BAD_JS
    ok = _write("snake-game", "game.js", SNAKE_OK_JS)
    assert ok.startswith("── write_file OK snake-game/game.js") and "gate:" in ok
    html = _write("snake-game", "index.html", "<canvas id='c'></canvas><script src='game.js'></script>")
    assert html.startswith("── write_file OK") and "does not exist yet" not in html
    off = _write("snake-game", "style.css", "body{}")
    assert off.startswith("── write_file OK") and "gate:" not in off.split("\n")[0]


def test_write_gate_can_be_disabled(playground, monkeypatch):
    monkeypatch.setenv("DAEDALUS_WRITE_GATE", "0")
    out = _write("snake-game", "game.js", SNAKE_BAD_JS)
    assert out.startswith("── write_file OK")


def test_failed_write_is_repaired_only_by_the_same_file():
    calls = [
        {"tool": "write_file", "ok": False, "input": {"slug": "s", "path": "game.js"}},
        {"tool": "write_file", "ok": True, "input": {"slug": "s", "path": "index.html"}},
    ]
    assert unrecovered_failure_indexes(calls) == [0]
    assert evaluate("направи snake игра", "Готово.", tool_calls=calls, kind="static").complete is False
    calls.append({"tool": "write_file", "ok": True, "input": {"slug": "s", "path": "game.js"}})
    assert unrecovered_failure_indexes(calls) == []
    assert evaluate("направи snake игра", "Готово.", tool_calls=calls, kind="static").complete is True


def test_build_loop_keeps_bad_write_open():
    from daedalus.build_loop import BuildLoop
    from daedalus.tool_router import route

    msg = "Направи нова Playground игра snake: HTML canvas. Една завършена среза."
    loop = BuildLoop(route(msg), msg, fix_rounds=2)
    loop.observe("playground_new", {"name": "snake-game"}, "── playground_new OK slug=snake-game ──", ok=True)
    loop.observe("write_file", {"slug": "snake-game", "path": "index.html"}, "── write_file OK ──", ok=True)
    loop.observe("write_file", {"slug": "snake-game", "path": "game.js"}, "── write_file FAILED syntax snake-game/game.js ──\nsyntax: line 5: 'gameLoopId' already declared", ok=False)
    nudge = loop.on_quiet("Готово.")
    assert nudge is not None
    assert "game.js" in nudge and "gameLoopId" in nudge
    assert "NEXT: repeat write_file with corrected arguments (slug=snake-game)" in nudge
    loop.observe("write_file", {"slug": "snake-game", "path": "game.js"}, "── write_file OK ──", ok=True)
    assert loop.on_quiet("Готово.") is None and loop.stop_reason == "complete"
