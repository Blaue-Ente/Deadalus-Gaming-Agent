"""F2 — gameplay bot, pixel-diff expects, balance sim, play-check."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from daedalus.balance_sim import run_for_project, simulate
from daedalus.game_studio_gates import run_gates, run_play_check
from daedalus.playground_gameplay_verify import gameplay_passed
from daedalus.playtest_scenario import (
    default_scenario,
    eval_expect,
    fingerprints_differ,
    load_scenario,
    scenario_checks,
    write_default_if_missing,
)
from daedalus.playground_racer_scaffold import write_racer_scaffold


def test_racer_default_scenario_require_lose_not_win():
    sc = default_scenario("vite", racer=True)
    assert sc["require_lose"] is True
    assert sc["require_win"] is False
    assert sc["fps_budget"] == 24
    ops = [s["op"] for s in sc["steps"]]
    assert "hold_keys" in ops


def test_eval_expect_and_fingerprints():
    assert fingerprints_differ("1,2,3", "1,2,9")
    assert not fingerprints_differ("1,2,3", "1,2,3")
    assert not fingerprints_differ("", "1,2,3")
    assert not fingerprints_differ(b"abc", b"abc")
    assert fingerprints_differ(b"abc", b"abd")
    ok, _ = eval_expect({"canvas_changed": True}, {"canvas_changed": True})
    assert ok
    ok, detail = eval_expect({"canvas_changed": False}, {"canvas_changed": True})
    assert not ok
    assert "canvas" in detail
    ok, _ = eval_expect({"lose": True}, {"lose": True})
    assert ok
    ok, detail = eval_expect({"lose": False}, {"lose": True})
    assert not ok
    assert "lose" in detail


def test_require_lose_without_lose_fails_gameplay_passed():
    sc = default_scenario("vite", racer=True)
    bot = {
        "saw_canvas_changed": True,
        "saw_lose": False,
        "saw_win": False,
        "fps": 40,
        "frames": 30,
        "fps_reliable": True,
        "memory_mb": None,
        "steps": [],
    }
    extra = scenario_checks(bot, sc)
    checks = [
        {"id": "canvas_or_visual", "ok": True},
        {"id": "post_start_state", "ok": True},
        *extra,
    ]
    assert gameplay_passed(checks, "M0") is False
    bot["saw_lose"] = True
    extra = scenario_checks(bot, sc)
    checks = [
        {"id": "canvas_or_visual", "ok": True},
        {"id": "post_start_state", "ok": True},
        *extra,
    ]
    assert gameplay_passed(checks, "M0") is True


def test_require_win_without_win_fails():
    sc = default_scenario("static", racer=False)
    sc["require_win"] = True
    bot = {
        "saw_canvas_changed": True,
        "saw_lose": False,
        "saw_win": False,
        "fps": 40,
        "frames": 20,
        "fps_reliable": True,
        "memory_mb": 10,
        "steps": [],
    }
    checks = [
        {"id": "canvas_or_visual", "ok": True},
        {"id": "post_start_state", "ok": True},
        *scenario_checks(bot, sc),
    ]
    assert gameplay_passed(checks) is False


def test_thin_fps_sample_does_not_fail():
    sc = default_scenario("static")
    bot = {
        "saw_canvas_changed": True,
        "saw_lose": False,
        "saw_win": False,
        "fps": 2,
        "frames": 3,
        "fps_reliable": False,
        "memory_mb": None,
        "steps": [],
    }
    checks = [
        {"id": "canvas_or_visual", "ok": True},
        {"id": "post_start_state", "ok": True},
        *scenario_checks(bot, sc),
    ]
    assert gameplay_passed(checks) is True
    fps = next(c for c in checks if c["id"] == "fps_budget")
    assert fps["ok"] is True
    assert fps.get("warn") is True


def test_reliable_low_fps_fails():
    sc = default_scenario("static")
    bot = {
        "saw_canvas_changed": True,
        "saw_lose": False,
        "saw_win": False,
        "fps": 8,
        "frames": 40,
        "fps_reliable": True,
        "memory_mb": None,
        "steps": [],
    }
    checks = [
        {"id": "canvas_or_visual", "ok": True},
        {"id": "post_start_state", "ok": True},
        *scenario_checks(bot, sc),
    ]
    assert gameplay_passed(checks) is False


def test_write_racer_scaffold_includes_scenario(tmp_path: Path):
    written = write_racer_scaffold(tmp_path / "cup", "Cup", "cup", "F-Zero")
    assert "playtest/scenario.json" in written
    sc, err = load_scenario(tmp_path / "cup")
    assert err == ""
    assert sc is not None
    assert sc["require_lose"] is True
    main = (tmp_path / "cup" / "src" / "main.js").read_text(encoding="utf-8")
    assert "__DAEDALUS_STATE__" in main
    assert "__GAME__" in main


def test_write_default_if_missing_does_not_clobber(tmp_path: Path):
    path = write_default_if_missing(tmp_path, "static", racer=False)
    original = path.read_text(encoding="utf-8")
    path.write_text('{"version":1,"steps":[{"op":"wait","ms":1}],"require_win":true}\n', encoding="utf-8")
    write_default_if_missing(tmp_path, "static", racer=False)
    sc, err = load_scenario(tmp_path)
    assert err == ""
    assert sc is not None
    assert sc["require_win"] is True
    assert original != path.read_text(encoding="utf-8")


def test_balance_sim_degenerate_gold_only_up():
    out = simulate({
        "starting": {"gold": 1},
        "actions": [{"name": "mine", "p_win": 1.0, "reward": {"gold": 5}}],
        "rounds": 80,
        "max_steps": 20,
        "win_when": {"gold": 10_000},
        "seed": 1,
    })
    assert out["ok"] is False
    blob = " ".join(out["degenerate"])
    assert "only grows" in blob
    assert "never win" in blob


def test_balance_sim_healthy_fight_shop():
    out = simulate({
        "starting": {"gold": 22, "hp": 40},
        "actions": [
            {"name": "fight", "p_win": 0.55, "cost": {"hp": 10}, "reward": {"gold": 14}},
            {"name": "shop", "p_win": 1.0, "cost": {"gold": 16}, "reward": {"hp": 18}},
        ],
        "rounds": 400,
        "max_steps": 50,
        "win_when": {"gold": 80},
        "lose_when": {"hp": 0},
        "seed": 7,
    })
    assert out["ok"] is True, out["detail"]
    assert 0 < out["win_rate"] < 1
    assert out["lose_rate"] > 0


def test_balance_sim_always_win_fails():
    out = simulate({
        "starting": {"gold": 0, "hp": 100},
        "actions": [{"name": "loot", "p_win": 1.0, "reward": {"gold": 50}}],
        "rounds": 40,
        "max_steps": 5,
        "win_when": {"gold": 20},
        "lose_when": {"hp": 0},
        "seed": 1,
    })
    assert out["ok"] is False
    assert any("always win" in d or "only grows" in d for d in out["degenerate"])


def test_balance_file_on_disk(tmp_path: Path):
    spec = {
        "starting": {"gold": 1},
        "actions": [{"name": "mine", "reward": {"gold": 3}}],
        "rounds": 30,
        "max_steps": 10,
        "win_when": {"gold": 5000},
    }
    (tmp_path / "playtest").mkdir()
    (tmp_path / "playtest" / "balance.json").write_text(json.dumps(spec), encoding="utf-8")
    out = run_for_project(tmp_path)
    assert out is not None
    assert out["ok"] is False
    assert run_for_project(tmp_path / "empty") is None


def test_play_check_pre_build_does_not_block():
    ctx = {
        "gdd": "GDD " + ("x" * 80),
        "config": {
            "studio_preset": "racing",
            "kind": "vite",
            "brainstorm": {"genre": "racing", "idea": "F-Zero night city"},
            "build_mode": "guided",
        },
        "plan": {"stages": [{"title": "boot", "goal": "menu"}] * 3},
        "project": {"kind": "vite", "slug": "mist"},
    }
    row = run_play_check(ctx)
    assert row["passed"] is True
    assert row["blocking"] is False
    combined = run_gates(ctx)
    assert combined["blocking"] is False
    assert combined["gates"]["play-check"]["passed"] is True


def test_play_check_racer_on_disk_without_scenario_fails(tmp_path: Path):
    (tmp_path / "src" / "racer").mkdir(parents=True)
    (tmp_path / "src" / "racer" / "vehicle.js").write_text("export function createVehicle() {}", encoding="utf-8")
    (tmp_path / "index.html").write_text("<canvas></canvas>", encoding="utf-8")
    ctx = {
        "workspace": str(tmp_path),
        "config": {"studio_preset": "racing", "kind": "vite", "build_mode": "guided"},
        "project": {"kind": "vite", "slug": "ghost"},
        "plan": {"stages": []},
        "gdd": "",
    }
    row = run_play_check(ctx)
    assert row["passed"] is False
    assert row["blocking"] is True


def test_play_check_racer_with_scenario_passes(tmp_path: Path):
    write_racer_scaffold(tmp_path, "Mist", "mist", "F-Zero")
    ctx = {
        "workspace": str(tmp_path),
        "config": {"studio_preset": "racing", "kind": "vite", "build_mode": "guided"},
        "project": {"kind": "vite", "slug": "mist"},
        "gdd": "",
    }
    row = run_play_check(ctx)
    assert row["passed"] is True
    assert row["blocking"] is False


def test_play_check_live_lose_gate():
    ctx = {
        "config": {"kind": "vite", "studio_preset": "racing"},
        "project": {"kind": "vite", "slug": "mist"},
        "gameplay": {
            "ok": False,
            "skipped": False,
            "checks": [
                {
                    "id": "scenario_lose",
                    "ok": False,
                    "detail": "require_lose but bot never saw fail/lose",
                    "required": True,
                },
            ],
        },
    }
    row = run_play_check(ctx)
    assert row["blocking"] is True
    lose = next(c for c in row["checks"] if c["id"] == "scenario_lose")
    assert lose["level"] == "fail"


def test_godot_play_check_is_na():
    row = run_play_check({
        "config": {"kind": "godot"},
        "project": {"kind": "godot", "slug": "dungeon"},
    })
    assert row["passed"] is True
    assert row["blocking"] is False


@pytest.mark.skipif(
    __import__("daedalus.playground_browser", fromlist=["_find_chrome"])._find_chrome() is None,
    reason="Chromium not on PATH",
)
def test_chromium_tiny_canvas_bot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    pytest.importorskip("playwright")
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path / "projects"))
    from daedalus.paths import playground_projects_dir
    playground_projects_dir.cache_clear()
    slug = "tiny-bot"
    root = playground_projects_dir() / slug
    root.mkdir(parents=True)
    (root / "index.html").write_text(
        """<!DOCTYPE html><html><body>
<canvas id="c" width="64" height="64"></canvas>
<script>
let n = 0;
const c = document.getElementById('c');
const ctx = c.getContext('2d');
function draw() {
  ctx.fillStyle = n ? 'hsl(' + (n % 360) + ',70%,40%)' : '#111';
  ctx.fillRect(0,0,64,64);
  requestAnimationFrame(draw);
}
draw();
window.addEventListener('keydown', () => { n += 40; });
window.__DAEDALUS_STATE__ = { failed: false };
</script></body></html>
""",
        encoding="utf-8",
    )
    import asyncio
    from daedalus.playground_gameplay_verify import gameplay_verify_app
    result = asyncio.run(gameplay_verify_app(slug, "static", milestone_id="M0"))
    if result.get("skipped"):
        pytest.skip(result.get("summary") or "playwright skipped")
    ids = {c["id"]: c for c in result.get("checks") or []}
    assert ids.get("canvas_or_visual", {}).get("ok")
    assert ids.get("scenario_canvas_changed", {}).get("ok")
    assert result.get("ok") is True
