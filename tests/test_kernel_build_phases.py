"""S2 — the kernel scaffolds, the kernel verifies, the model only writes."""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from daedalus import native_tools
from daedalus import playground as pg
from daedalus.build_loop import BuildLoop, project_name_from_ask, session_slug
from daedalus.tool_router import native_specs, route

SNAKE = "Направи нова Playground игра snake: HTML canvas, стрелки, расте при ябълка. Една завършена среза."
SLERP = "Направи нова Playground 3D demo: камера с quaternion slerp към цел и look-at."
BAD_JS = "let gameLoopId = null;\ndocument.addEventListener('keydown', () => {});\nlet gameLoopId = 1;\n"
OK_JS = "let gameLoopId = null;\ndocument.addEventListener('keydown', () => {});\ngameLoopId = 1;\n"


@pytest.fixture
def playground(tmp_path, monkeypatch):
    from daedalus.paths import playground_projects_dir

    root = tmp_path / "projects"
    root.mkdir()
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(root))
    playground_projects_dir.cache_clear()
    created: list[dict] = []

    async def _create(name, *, kind="", source="built", description="", slug="", **_kw):
        slug = pg.slugify(slug or name)
        (root / slug).mkdir(parents=True, exist_ok=True)
        created.append({"name": name, "kind": kind, "slug": slug})
        return {"slug": slug, "kind": kind or "static"}

    async def _get(slug):
        return {"slug": slug, "kind": "static"}

    monkeypatch.setattr(pg, "create_project", _create)
    monkeypatch.setattr(pg, "get_project", _get)
    yield root, created
    playground_projects_dir.cache_clear()


def test_project_name_is_deterministic_and_named_after_the_game():
    assert project_name_from_ask(SNAKE, "playground.arcade", "static") == "snake-game"
    assert project_name_from_ask(SLERP, "playground.math3d", "static") == "slerp-demo"
    assert project_name_from_ask("Направи Godot 4 игра: tetris", "playground.godot", "godot") == "tetris-godot"
    anon = project_name_from_ask("направи нещо интерактивно", "playground.generic", "static")
    assert re.fullmatch(r"generic-[0-9a-f]{4}-game", anon)
    assert anon == project_name_from_ask("направи нещо интерактивно", "playground.generic", "static")
    assert session_slug("playground:snake-game") == "snake-game"
    assert session_slug("pg-auto-rev:racer:123") == "racer"
    assert session_slug("chat-1") == ""


def test_kernel_scaffold_creates_the_project_and_gates_playground_new(playground):
    root, created = playground
    rt = route(SNAKE)
    loop = BuildLoop(rt, SNAKE, fix_rounds=1)
    block = asyncio.run(loop.scaffold(""))
    assert created == [{"name": "snake-game", "kind": "static", "slug": "snake-game"}]
    assert loop.slug == "snake-game" and loop.scaffolded is True
    assert "PROJECT (kernel created)" in block and "Do not call playground_new" in block
    assert "write_file slug=snake-game path=index.html" in block
    assert loop.progress()[0].status == "done"  # create step is ✓ before the model talks
    names = {t["name"] for t in native_specs(rt, drop=loop.drop_tools())}
    assert "playground_new" not in names and "write_file" in names

    # same ask again → the folder exists → a hashed sibling, not a silent overwrite
    loop2 = BuildLoop(route(SNAKE), SNAKE, fix_rounds=1)
    asyncio.run(loop2.scaffold(""))
    assert re.fullmatch(r"snake-game-[0-9a-f]{4}", loop2.slug)


def test_kernel_scaffold_adopts_the_session_project(playground):
    root, created = playground
    (root / "racer").mkdir()
    loop = BuildLoop(route(SNAKE), SNAKE, fix_rounds=1)
    block = asyncio.run(loop.scaffold("playground:racer"))
    assert created == [] and loop.slug == "racer"
    assert "existing" in block and "slug=racer" in block


def test_scaffold_skips_routes_without_a_create_step_and_when_disabled(playground, monkeypatch):
    fix = route("поправи daedalus/agent.py harness patch")
    assert asyncio.run(BuildLoop(fix, "x", fix_rounds=1).scaffold("")) == ""
    monkeypatch.setenv("DAEDALUS_BUILD_SCAFFOLD", "0")
    loop = BuildLoop(route(SNAKE), SNAKE, fix_rounds=1)
    assert asyncio.run(loop.scaffold("")) == "" and loop.drop_tools() == set()


def test_scaffold_failure_leaves_playground_new_to_the_model(playground, monkeypatch):
    async def _boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(pg, "create_project", _boom)
    loop = BuildLoop(route(SNAKE), SNAKE, fix_rounds=1)
    assert asyncio.run(loop.scaffold("")) == ""
    assert loop.scaffolded is False and loop.drop_tools() == set()


def test_kernel_verify_blocks_bad_file_then_passes_after_fix(playground):
    root, _ = playground
    loop = BuildLoop(route(SNAKE), SNAKE, fix_rounds=3)
    asyncio.run(loop.scaffold(""))
    proj = root / "snake-game"
    (proj / "index.html").write_text("<canvas id='c'></canvas><script src='game.js'></script>", encoding="utf-8")
    (proj / "game.js").write_text(BAD_JS, encoding="utf-8")
    # the model wrote both (the gate is bypassed here on purpose — verify must catch it on its own)
    loop.observe("write_file", {"slug": "snake-game", "path": "index.html"}, "── write_file OK ──", ok=True)
    loop.observe("write_file", {"slug": "snake-game", "path": "game.js"}, "── write_file OK ──", ok=True)

    nudge = asyncio.run(loop.on_quiet("Готово."))
    assert nudge is not None
    assert "VERIFY (kernel ran it" in nudge
    assert "game_lint check snake-game/game.js FAILED" in nudge and "gameLoopId" in nudge
    assert "NEXT: fix game.js — write_file slug=snake-game path=game.js" in nudge
    assert any(o.tool == "playtest" and o.args.get("kernel") for o in loop.observations)
    assert loop.log_row()["verified"] is True

    # no new write → verify is not re-run, the same blocker stands
    n_obs = len(loop.observations)
    asyncio.run(loop.on_quiet("still broken"))
    assert len(loop.observations) == n_obs

    (proj / "game.js").write_text(OK_JS, encoding="utf-8")
    loop.observe("write_file", {"slug": "snake-game", "path": "game.js"}, "── write_file OK ──", ok=True)
    assert asyncio.run(loop.on_quiet("Готово.")) is None
    assert loop.stop_reason == "complete"
    assert "Kernel verify" in loop.summary_block()


def test_verify_can_be_disabled(playground, monkeypatch):
    monkeypatch.setenv("DAEDALUS_BUILD_VERIFY", "0")
    root, _ = playground
    loop = BuildLoop(route(SNAKE), SNAKE, fix_rounds=3)
    asyncio.run(loop.scaffold(""))
    (root / "snake-game" / "game.js").write_text(BAD_JS, encoding="utf-8")
    loop.observe("write_file", {"slug": "snake-game", "path": "game.js"}, "── write_file OK ──", ok=True)
    assert asyncio.run(loop.on_quiet("Готово.")) is None
    assert not any(o.tool == "game_lint" for o in loop.observations)


def test_list_dir_during_build_is_redirected_to_the_project():
    loop = BuildLoop(route(SNAKE), SNAKE, fix_rounds=1)
    loop.observe("playground_new", {"name": "snake-game"}, "── playground_new OK slug=snake-game ──", ok=True)
    args, note = loop.prepare("list_dir", {"path": "/workspace"})
    assert args == {"path": ".", "slug": "snake-game"} and "not the harness" in note
    args, note = loop.prepare("list_dir", {"path": "daedalus/knowledge"})
    assert args["path"] == "." and args["slug"] == "snake-game"
    args, note = loop.prepare("list_dir", {"path": "assets"})
    assert args == {"path": "assets", "slug": "snake-game"}
    args, note = loop.prepare("list_dir", {"path": "/workspace", "slug": "other"})
    assert args == {"path": "/workspace", "slug": "other"} and note == ""


def test_native_pass_verifies_before_accepting(playground, monkeypatch):
    root, _ = playground
    executed: list[str] = []

    async def _exec(name, args, **_kw):
        executed.append(name)
        if name == "write_file":
            (root / args["slug"] / args["path"]).write_text(args["content"], encoding="utf-8")
            return f"── write_file OK {args['slug']}/{args['path']} ──"
        return f"── {name} OK ──"

    monkeypatch.setattr(native_tools, "execute_native_tool", _exec)
    rounds = [
        [{"type": "tool_use", "id": "1", "name": "write_file", "input": {"path": "game.js", "content": BAD_JS}}],
        [{"type": "text", "text": "Готово."}],
        [{"type": "tool_use", "id": "2", "name": "write_file", "input": {"path": "game.js", "content": OK_JS}}],
        [{"type": "text", "text": "Готово."}],
    ]
    sent: list[list[dict]] = []

    async def _model(system, messages, tools):
        sent.append(list(messages))
        return {"stop_reason": "x", "content": rounds.pop(0)}

    loop = BuildLoop(route(SNAKE), SNAKE, fix_rounds=2)
    asyncio.run(loop.scaffold(""))
    text, log = asyncio.run(native_tools.run_native_discovery_pass(
        "sys", SNAKE, _model, memory=None, session_id="", read_file_fn=lambda *a: {}, max_rounds=3, loop=loop,
    ))
    assert executed == ["write_file", "write_file"]          # the model never touched playground_new or lint
    assert log[0]["input"]["slug"] == "snake-game"           # kernel filled the slug
    nudges = [m["content"] for m in sent[-1] if m["role"] == "user" and isinstance(m["content"], str) and "PLAN PROGRESS" in m["content"]]
    assert len(nudges) == 1 and "game_lint check snake-game/game.js FAILED" in nudges[0]
    assert loop.stop_reason == "complete" and loop.nudges == 1
    row = loop.log_row()
    assert row["scaffolded"] is True and row["verified"] is True and row["complete"] is True


def test_agent_scaffolds_before_building():
    src = Path("daedalus/agent.py").read_text(encoding="utf-8")
    assert "await _loop.scaffold(session_id" in src
    assert "native_specs(_route, drop=_loop.drop_tools())" in src
