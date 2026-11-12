"""Gap 4a (owner, 2026-11-12, plan approved): every game gets sound and a look, free and procedural."""
from __future__ import annotations

import asyncio
import types

import pytest
from a0_support import a0_tmp  # noqa: F401

from daedalus import game_juice, mentor_lead


@pytest.fixture
def game(a0_tmp):  # noqa: F811
    from daedalus import playground as pg

    slug = asyncio.run(pg.create_project("Hop", kind="static"))["slug"]
    pg.write_file(slug, "index.html", "<canvas></canvas><script type=module src=game.js></script>")
    pg.write_file(slug, "game.js", "addEventListener('keydown',()=>{}); requestAnimationFrame(()=>{});")
    return slug


def _root(slug):
    from daedalus import playground as pg
    return pg.project_dir(slug)


def _loop(slug, playbook="playground.arcade", **kw):
    loop = types.SimpleNamespace(slug=slug, kind="static", lead_accepted=True, lead_fix_used=0,
                                 user_message="make a hopping game",
                                 route=types.SimpleNamespace(playbook=types.SimpleNamespace(id=playbook)))
    for k, v in kw.items():
        setattr(loop, k, v)
    return loop


def test_sfx_is_placed_once_and_never_over_the_games_copy(game):
    root = _root(game)
    assert game_juice.ensure_sfx(root, "static") and (root / "sfx.js").read_text() == game_juice.SFX_JS
    (root / "sfx.js").write_text("// the game's own version")
    assert not game_juice.ensure_sfx(root, "static") and (root / "sfx.js").read_text() == "// the game's own version"
    assert game_juice.sfx_path("vite-react") == "src/sfx.js" and game_juice.sfx_path("static") == "sfx.js"


def test_placing_the_module_is_not_using_it(game):
    root = _root(game)
    game_juice.ensure_sfx(root)
    (root / "node_modules" / "x").mkdir(parents=True)
    (root / "node_modules" / "x" / "a.js").write_text("new AudioContext()")
    assert not game_juice.has_sound(root)
    (root / "game.js").write_text("import { sfx } from './sfx.js'; function hop(){ sfx.jump(); }")
    assert game_juice.has_sound(root)


def test_a_silent_game_is_asked_once_and_a_game_with_sound_passes(game):
    loop = _loop(game)
    nudge = asyncio.run(mentor_lead.juice_gate(loop, "make a hopping game"))
    assert "SOUND: NONE YET" in nudge and "sfx.score()" in nudge
    assert loop.lead_fix_used == 1 and loop.lead_accepted is False and (_root(game) / "sfx.js").is_file()
    loop.lead_accepted = True
    assert asyncio.run(mentor_lead.juice_gate(loop, "make a hopping game")) is None        # asked once, no loop
    assert loop.juice == {"sound": False}
    (_root(game) / "game.js").write_text("import { sfx } from './sfx.js'; sfx.score();")
    fresh = _loop(game)
    assert asyncio.run(mentor_lead.juice_gate(fresh, "make a hopping game")) is None
    assert fresh.juice == {"sound": True} and fresh.lead_fix_used == 0


@pytest.mark.parametrize("change", [{"lead_accepted": False}, {"lead_fix_used": 9}])
def test_the_gate_waits_for_the_lead_and_respects_spent_rounds(game, change):
    assert asyncio.run(mentor_lead.juice_gate(_loop(game, **change), "make a hopping game")) is None


def test_apps_whisper_and_cloned_repos_are_left_alone(game):
    from daedalus import playground as pg
    from daedalus import task_effort

    app = asyncio.run(pg.create_project("Budget", kind="static"))["slug"]
    pg.write_file(app, "index.html", "<form><input name=amount></form>")
    assert asyncio.run(mentor_lead.juice_gate(_loop(app, "playground.vite"), "a budget tracker")) is None
    assert not (_root(app) / "sfx.js").exists()
    with task_effort.bind({"tier": "whisper"}):
        assert asyncio.run(mentor_lead.juice_gate(_loop(game), "make a hopping game")) is None
    root = _root(game)
    (root / ".daedalus").mkdir(exist_ok=True)
    (root / ".daedalus" / "upstream_manifest.json").write_text("{}")
    assert asyncio.run(mentor_lead.juice_gate(_loop(game), "make a hopping game")) is None
    assert not (root / "sfx.js").exists()


def test_a_game_turn_places_the_module_as_soon_as_the_project_is_known(game):
    from daedalus.build_loop import BuildLoop

    route = types.SimpleNamespace(kind="static", playbook=types.SimpleNamespace(id="playground.arcade"))
    loop = BuildLoop(route, "make a hopping game")
    loop.observe("write_file", {"slug": game, "path": "game.js"}, "wrote game.js", ok=True)
    assert loop.slug == game and (_root(game) / "sfx.js").is_file()


def test_the_writer_is_told_about_sound_and_look_for_games_only():
    from daedalus.tool_router import format_system, route

    game_sys = format_system(route("Build a small web game with a frog", kind="static"), user_message="frog game")
    app_sys = format_system(route("Build a budget tracker app", kind="vite-react"), user_message="budget tracker app")
    assert "sfx.score()" in game_sys and "SOUND AND LOOK" in game_sys and "SOUND AND LOOK" not in app_sys


def _browser_ok() -> bool:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            p.chromium.launch().close()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _browser_ok(), reason="Chromium/Playwright not available")
def test_the_sounds_play_in_a_real_browser_and_m_mutes(game):
    from playwright.sync_api import sync_playwright

    from daedalus.playground_browser import _serve_static

    root = _root(game)
    game_juice.ensure_sfx(root)
    (root / "index.html").write_text(
        "<script type=module>import { sfx } from './sfx.js'; window.sfx = sfx;"
        "addEventListener('keydown', e => { if (e.key === ' ') { sfx.jump(); sfx.score(); sfx.hit(); sfx.lose();"
        " sfx.win(); sfx.click(); sfx.powerup(); sfx.explode(); window.played = true; } });</script>")
    base, stop = _serve_static(root)
    errors: list[str] = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
            page = browser.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(base + "/")
            page.wait_for_function("() => !!window.sfx")
            page.keyboard.press("Space")
            page.wait_for_function("() => window.played === true")
            assert page.evaluate("() => window.sfx.muted") is False
            page.keyboard.press("m")
            assert page.evaluate("() => window.sfx.muted") is True
            assert page.evaluate("() => localStorage.getItem('sfx.muted')") == "1"
            browser.close()
    finally:
        stop()
    assert errors == []
