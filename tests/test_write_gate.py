"""S3 navigation hints + S4 write-time quality gate."""
from __future__ import annotations

import asyncio

from daedalus import native_tools
from daedalus.game_lint import heuristic_javascript, run_action as lint_action
from daedalus.write_gate import arcade_blockers, check_write, enabled as gate_enabled


CLEAN_CANVAS = (
    "<!DOCTYPE html><html><body>"
    '<canvas id="g" width="320" height="240"></canvas>'
    "<script>function loop(t){requestAnimationFrame(loop);}"
    "requestAnimationFrame(loop);</script>"
    "</body></html>"
)

SNAKE_DUP = """<!DOCTYPE html><html><body>
<canvas id="g"></canvas>
<script>
let gameLoopId = null;
function loop() { requestAnimationFrame(loop); }
let gameLoopId = requestAnimationFrame(loop);
</script>
</body></html>
"""


def test_webgl_transpose_true_is_a_blocker():
    src = "gl.uniformMatrix4fv(LOC.uMVP, true, MVP);\n"
    issues = heuristic_javascript(src)
    assert any(i["code"] == "webgl-transpose" for i in issues)
    ok = heuristic_javascript("gl.uniformMatrix4fv(LOC.uMVP, false, MVP);\n")
    assert not any(i["code"] == "webgl-transpose" for i in ok)


def test_js_duplicate_let_same_scope():
    issues = heuristic_javascript(
        "let gameLoopId = null;\nfunction loop(){}\nlet gameLoopId = 1;\n"
    )
    codes = {i["code"] for i in issues}
    assert "js-duplicate-binding" in codes
    nested = heuristic_javascript("function a(){ let x = 1; }\nfunction b(){ let x = 2; }\n")
    assert not any(i["code"] == "js-duplicate-binding" for i in nested)


def test_game_lint_html_duplicate_let():
    r = lint_action("check", path="game.js", content="let a = 1;\nlet a = 2;\n")
    assert r["lang"] == "javascript"
    assert r["ok"] is False
    assert any(b["code"] == "js-duplicate-binding" for b in r.get("blockers") or [])
    html = lint_action("check", path="index.html", content=SNAKE_DUP)
    assert html["lang"] == "html"
    assert html["ok"] is False


def test_python_syntax_is_a_blocker():
    r = lint_action("check", path="mod.py", content="def f(\n")
    assert r["ok"] is False
    assert any(b["code"] == "python-syntax" for b in r.get("blockers") or [])


def test_write_gate_rejects_duplicate_let(monkeypatch):
    monkeypatch.delenv("DAEDALUS_WRITE_GATE", raising=False)
    assert gate_enabled() is True
    gate = check_write("index.html", SNAKE_DUP, kind="static", slug="snake-game")
    assert gate.ok is False
    codes = {b["code"] for b in gate.blockers}
    assert "js-duplicate-binding" in codes


def test_write_gate_clean_canvas_ok():
    gate = check_write("index.html", CLEAN_CANVAS, kind="static")
    assert gate.ok is True
    assert arcade_blockers("index.html", CLEAN_CANVAS, "static") == []


def test_write_gate_missing_canvas_and_loop():
    gate = check_write("index.html", "<p>hello</p>", kind="static")
    assert gate.ok is False
    codes = {b["code"] for b in gate.blockers}
    assert "arcade-missing-canvas" in codes
    assert "arcade-missing-loop" in codes


def test_write_gate_off_skips(monkeypatch):
    monkeypatch.setenv("DAEDALUS_WRITE_GATE", "0")
    gate = check_write("index.html", SNAKE_DUP, kind="static")
    assert gate.skipped is True
    assert gate.ok is True


def test_write_file_duplicate_let_returns_failed(monkeypatch):
    async def fake_get(slug):
        return {"slug": slug, "kind": "static"}

    monkeypatch.setattr("daedalus.playground.get_project", fake_get)
    monkeypatch.setattr("daedalus.playground.write_file", lambda *a, **k: None)
    monkeypatch.setattr("daedalus.project_status.record_built", lambda *a, **k: None)

    text = asyncio.run(native_tools.execute_native_tool(
        "write_file",
        {"slug": "snake-game", "path": "index.html", "content": SNAKE_DUP},
        memory=None, session_id="", read_file_fn=lambda *a, **k: {},
    ))
    assert "write_file FAILED" in text
    assert "js-duplicate-binding" in text
    assert "NEXT: call write_file with slug=snake-game" in text
    assert "playground/projects/snake-game/index.html" in text


def test_write_file_clean_canvas_ok(monkeypatch):
    async def fake_get(slug):
        return {"slug": slug, "kind": "static"}

    monkeypatch.setattr("daedalus.playground.get_project", fake_get)
    monkeypatch.setattr("daedalus.playground.write_file", lambda *a, **k: None)
    monkeypatch.setattr("daedalus.project_status.record_built", lambda *a, **k: None)

    text = asyncio.run(native_tools.execute_native_tool(
        "write_file",
        {"slug": "snake-game", "path": "index.html", "content": CLEAN_CANVAS},
        memory=None, session_id="", read_file_fn=lambda *a, **k: {},
    ))
    assert "write_file OK" in text
    assert "FAILED" not in text


def test_read_file_failed_names_playground():
    text = asyncio.run(native_tools.execute_native_tool(
        "read_file",
        {"path": "index.html", "slug": "dungeon-keys"},
        memory=None,
        session_id="playground:live-eval",
        read_file_fn=lambda *a, **k: {"ok": False, "error": "not found"},
    ))
    assert "read_file FAILED" in text
    assert "playground/projects/dungeon-keys/" in text
    assert "Do not find /workspace" in text
    assert "NEXT:" in text


def test_read_file_ok_names_playground():
    text = asyncio.run(native_tools.execute_native_tool(
        "read_file",
        {"path": "index.html", "slug": "slerp-lookat"},
        memory=None,
        session_id="playground:live-eval",
        read_file_fn=lambda *a, **k: {
            "ok": True, "path": "playground/projects/slerp-lookat/index.html",
            "content": "<canvas></canvas>",
        },
    ))
    assert "read_file FAILED" not in text
    assert "playground/projects/slerp-lookat/" in text
    assert "NEXT: call write_file" in text


def test_list_dir_harness_refused_names_playground():
    text = asyncio.run(native_tools.execute_native_tool(
        "list_dir",
        {"path": "/workspace"},
        memory=None,
        session_id="playground:live-eval",
        read_file_fn=lambda *a, **k: {},
    ))
    assert "harness listing refused" in text
    assert "playground/projects" in text
    assert "NEXT:" in text


def test_math3d_ok_names_next_write():
    text = asyncio.run(native_tools.execute_native_tool(
        "math3d",
        {
            "action": "quat_slerp",
            "q0": [0, 0, 0, 1],
            "q1": [0, 1, 0, 0],
            "t": 0.5,
            "slug": "slerp-lookat",
        },
        memory=None, session_id="playground:live-eval",
        read_file_fn=lambda *a, **k: {},
    ))
    assert "math3d" in text
    assert "FAILED" not in text
    assert "NEXT: call write_file with slug=slerp-lookat" in text
    assert "playground/projects/slerp-lookat/" in text


def test_math3d_mismatch_is_failed():
    text = asyncio.run(native_tools.execute_native_tool(
        "math3d",
        {
            "action": "quat_slerp",
            "q0": [0, 0, 0, 1],
            "q1": [0, 0, 0, 1],
            "t": 0.5,
            "claimed": [1, 0, 0, 0],
            "slug": "slerp-lookat",
        },
        memory=None, session_id="", read_file_fn=lambda *a, **k: {},
    ))
    assert "FAILED" in text
    assert "NEXT: call math3d again" in text


def test_spatial_and_game_lint_name_slug():
    spat = asyncio.run(native_tools.execute_native_tool(
        "spatial",
        {"action": "advise", "n": 80, "slug": "crowd-demo"},
        memory=None, session_id="", read_file_fn=lambda *a, **k: {},
    ))
    assert "playground/projects/crowd-demo/" in spat
    assert "write_file" in spat
    lint = asyncio.run(native_tools.execute_native_tool(
        "game_lint",
        {"action": "check", "path": "game.js", "slug": "snake-game", "content": "let a=1;\nlet a=2;\n"},
        memory=None, session_id="", read_file_fn=lambda *a, **k: {},
    ))
    assert "FAILED" in lint
    assert "NEXT: call write_file with slug=snake-game" in lint
