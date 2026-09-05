"""S7 — the harness compensates for a weak builder: forgiving tool arguments,
right tool for the job, and a Mentor that steps in before the budget is gone."""
from __future__ import annotations

import asyncio
import json
import os

import pytest

from daedalus import native_tools
from daedalus.build_context import build_user_content
from daedalus.build_loop import BuildLoop
from daedalus.build_mentor import BuildMentor, MentorAdvice, mentor_enabled
from daedalus.job_contract import unrecovered_failure_indexes
from daedalus.math3d import run_action as math3d
from daedalus.tool_args import head_keys, parse
from daedalus.tool_router import route, run_consult

SLERP = "Направи нова Playground 3D demo: камера с quaternion slerp към цел и look-at."
SNAKE = "Направи нова Playground игра snake: HTML canvas, стрелки, расте при ябълка. Една завършена среза."
CROWD = "Направи нова Playground demo: 80 тела с octree broadphase, canvas 3D crowd physics."


def _text(t: str) -> dict:
    return {"type": "text", "text": t}


def _use(tool: str, **args) -> dict:
    return {"type": "tool_use", "id": f"call_{tool}_{len(str(args))}", "name": tool, "input": args}


class ScriptedModel:
    def __init__(self, rounds):
        self.rounds = list(rounds)
        self.seen: list[list[dict]] = []

    async def __call__(self, system, messages, tools):
        self.seen.append([dict(m) for m in messages])
        if not self.rounds:
            return {"stop_reason": "end_turn", "content": [_text("Готово.")]}
        return {"stop_reason": "tool_use", "content": self.rounds.pop(0)}


@pytest.fixture
def fake_tools(monkeypatch):
    calls: list[tuple[str, dict]] = []

    async def _exec(name, args, **_kw):
        calls.append((name, dict(args)))
        if name == "write_file":
            return f"── write_file OK {args.get('slug')}/{args.get('path')} ──"
        if name == "math3d":
            r = math3d(str(args.get("action") or "catalog"), **{k: v for k, v in args.items() if k != "action"})
            return f"── math3d {args.get('action')} {'OK' if r.get('ok') else 'FAILED'} ──\n{json.dumps(r)}"
        if name == "read_file":
            return f"── read_file {args.get('slug')}/{args.get('path')} ──\n<canvas></canvas>"
        if name == "game_lint":
            return f"── game_lint check {args.get('slug')}/{args.get('path')} OK ──"
        return f"── {name} OK ──"

    monkeypatch.setattr(native_tools, "execute_native_tool", _exec)
    return calls


def _run(user, model, *, loop, max_rounds=4):
    return asyncio.run(
        native_tools.run_native_discovery_pass(
            "sys", user, model,
            memory=None, session_id="", read_file_fn=lambda *a, **k: {},
            max_rounds=max_rounds, tools=[{"name": "x"}], loop=loop,
        )
    )


def _user_blocks(messages) -> list[str]:
    out = []
    for m in messages:
        if m["role"] != "user":
            continue
        if isinstance(m["content"], str):
            out.append(m["content"])
        else:
            out.extend(str(b.get("text") or b.get("content") or "") for b in m["content"])
    return out


# ── math3d: the tool meets the model half-way ─────────────────────────────

def test_math3d_perspective_accepts_the_shapes_models_actually_send():
    named = math3d("perspective", fovy=1.0472, aspect=1, near=0.1, far=100)
    assert named["ok"]
    as_eye = math3d("perspective", eye="[1.0472, 1, 0.1, 100]")        # GLM 5.2 / Kimi shape
    assert as_eye["ok"] and as_eye["value"] == named["value"]
    assert "fovy, aspect, near, far" in as_eye["note"]
    degrees = math3d("perspective", eye=[60, 1, 0.1, 100])
    assert degrees["ok"] and "degrees" in degrees["note"]
    assert abs(degrees["value"][5] - named["value"][5]) < 1e-3
    wrong = math3d("perspective", eye=[0, 0, 5])
    assert not wrong["ok"]
    assert wrong["expected"].startswith("perspective(fovy, aspect, near, far)")
    assert '"action": "perspective"' in wrong["expected"]
    cat = math3d("catalog")
    assert "perspective(fovy, aspect, near, far)" in cat["signatures"]["perspective"]
    assert "quat_slerp(q0, q1, t)" in cat["signatures"]["quat_slerp"]
    spec = next(t for t in native_tools.NATIVE_TOOL_SPECS if t["name"] == "math3d")
    for key in ("fovy", "aspect", "near", "far", "axis", "angle", "min", "max"):
        assert key in spec["input_schema"]["properties"]


# ── tool arguments: repair, or say exactly why not ────────────────────────

def test_tool_args_repairs_common_breakage_and_detects_truncation():
    good = parse('{"slug": "s", "path": "index.html", "content": "x"}')
    assert good.ok and not good.repaired and good.args["path"] == "index.html"
    fenced = parse('```json\n{"slug": "s", "path": "a.js", "content": "x"}\n```')
    assert fenced.ok and fenced.repaired == "code fence removed"
    newline = parse('{"slug": "s", "path": "a.js", "content": "line1\nline2"}')
    assert newline.ok and newline.args["content"] == "line1\nline2"
    trailing = parse('{"slug": "s", "path": "a.js", "content": "x",}')
    assert trailing.ok and "trailing comma" in trailing.repaired
    cut = parse('{"slug": "crowd-demo", "path": "index.html", "content": "<!DOCTYPE html>\\n<html>')
    assert not cut.ok and cut.truncated
    assert "cut off" in cut.note() and "style.css" in cut.note()
    assert head_keys(cut.args["raw"]) == {"slug": "crowd-demo", "path": "index.html"}


def test_broken_write_is_logged_against_its_file_and_repaired_by_the_good_one(fake_tools):
    rt = route(SNAKE)
    loop = BuildLoop(rt, SNAKE, fix_rounds=1)
    broken = {"type": "tool_use", "id": "c1", "name": "write_file",
              "input": '{"slug": "snake-game", "path": "index.html", "content": "<!DOCTYPE'}
    model = ScriptedModel([
        [_use("playground_new", name="snake-game", kind="static"), broken],
        [_use("write_file", slug="snake-game", path="index.html", content="<canvas>")],
        [_text("Готово.")],
    ])
    text, log = _run(SNAKE, model, loop=loop)
    assert [c[0] for c in fake_tools] == ["playground_new", "write_file"]   # broken call never executed
    failed = [o for o in loop.observations if o.tool == "write_file" and not o.ok]
    assert failed and failed[0].args.get("path") == "index.html"
    assert "cut off" in failed[0].head
    assert loop.unrecovered_failures() == []
    assert loop.stop_reason == "complete"


def test_failure_without_a_file_is_repaired_by_any_later_good_call():
    calls = [
        {"tool": "write_file", "ok": False, "input": {"raw": "{...", "slug": "s"}},
        {"tool": "write_file", "ok": True, "input": {"slug": "s", "path": "index.html"}},
    ]
    assert unrecovered_failure_indexes(calls) == []
    strict = [
        {"tool": "write_file", "ok": False, "input": {"slug": "s", "path": "game.js"}},
        {"tool": "write_file", "ok": True, "input": {"slug": "s", "path": "index.html"}},
    ]
    assert unrecovered_failure_indexes(strict) == [0]


# ── shell: refusals are not failures, inspection goes to the right tool ────

def test_shell_policy_refusal_is_blocked_not_failed(tmp_path, monkeypatch):
    from daedalus.paths import playground_projects_dir

    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path))
    monkeypatch.setenv("DAEDALUS_SHELL_ISOLATION", "1")
    monkeypatch.delenv("DAEDALUS_SHELL_LEGACY", raising=False)
    playground_projects_dir.cache_clear()
    (tmp_path / "snake-game").mkdir()
    try:
        out = asyncio.run(native_tools.execute_native_tool(
            "run_command", {"command": "cat index.html | head -20", "slug": "snake-game"},
            memory=None, session_id="playground:snake-game", read_file_fn=lambda *a, **k: {},
        ))
    finally:
        playground_projects_dir.cache_clear()
    assert "run_command BLOCKED" in out and "FAILED" not in out
    assert "read_file" in out and "game_lint" in out


def test_loop_reroutes_inspection_commands_to_read_file_and_game_lint(fake_tools):
    rt = route(SNAKE)
    loop = BuildLoop(rt, SNAKE, fix_rounds=1)
    loop.slug = "snake-game"
    assert loop.reroute("run_command", {"command": "cat index.html | head -20"})[0] == "read_file"
    assert loop.reroute("run_command", {"command": "head -20 game.js", "slug": "snake-game"})[1] == {
        "path": "game.js", "slug": "snake-game",
    }
    tool, args, note = loop.reroute("run_command", {"command": "node --check index.html"})
    assert tool == "game_lint" and args["action"] == "check" and "game_lint" in note
    assert loop.reroute("run_command", {"command": "npm test"})[0] == "run_command"

    model = ScriptedModel([
        [_use("write_file", slug="snake-game", path="index.html", content="<canvas>"),
         _use("run_command", command="cat index.html", slug="snake-game")],
        [_text("Готово.")],
    ])
    _run(SNAKE, model, loop=loop)
    assert [c[0] for c in fake_tools] == ["write_file", "read_file"]
    results = _user_blocks(model.seen[-1])
    assert any("→ read_file path=index.html" in r for r in results)


# ── verify: demos are not arcade games ────────────────────────────────────

def test_demo_playbooks_skip_the_arcade_fun_gate():
    from daedalus.playtest import _heuristic

    demo_html = "<canvas></canvas><script>function frame(){requestAnimationFrame(frame)}</script>"
    game = _heuristic(demo_html)
    demo = _heuristic(demo_html, demo=True)
    assert any("win/lose" in b for b in game["blockers"])
    assert demo["blockers"] == []
    assert BuildLoop(route(CROWD), CROWD, fix_rounds=0).is_demo is True
    assert BuildLoop(route(SLERP), SLERP, fix_rounds=0).is_demo is True
    assert BuildLoop(route(SNAKE), SNAKE, fix_rounds=0).is_demo is False


def test_css_hover_does_not_make_a_snake_game_a_racer():
    from daedalus.playtest import _heuristic

    snake_html = (
        "<style>button:hover{color:red}</style><canvas></canvas><script>"
        "document.addEventListener('keydown',()=>{});setInterval(tick,100);"
        "let score=0;function gameOver(){}</script>"
    )
    assert _heuristic(snake_html)["blockers"] == []
    assert _heuristic(snake_html, ask=SNAKE)["blockers"] == []
    racer = _heuristic(snake_html, ask="Направи hover racer с писта и boost pads")
    assert any("wayfinding" in b for b in racer["blockers"])


def test_heuristic_scans_whole_files_not_the_judge_snapshot(tmp_path, monkeypatch):
    from daedalus.paths import playground_projects_dir
    from daedalus.playtest import _heuristic, _project_snapshot, _signal_source

    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path))
    playground_projects_dir.cache_clear()
    proj = tmp_path / "slerp-demo"
    proj.mkdir()
    (proj / "index.html").write_text("<canvas></canvas><script src=game.js></script>", encoding="utf-8")
    (proj / "game.js").write_text("// header\n" * 400 + "function frame(){requestAnimationFrame(frame)}\n", encoding="utf-8")
    try:
        assert _heuristic(_project_snapshot("slerp-demo"), demo=True)["blockers"] == ["Не се вижда game loop."]
        assert _heuristic(_signal_source("slerp-demo"), demo=True)["blockers"] == []
    finally:
        playground_projects_dir.cache_clear()


def test_missing_browser_is_blocked_not_failed(monkeypatch):
    from daedalus import browser_session

    async def _no_browser(action, **_kw):
        return {"ok": False, "error": "playwright_unavailable", "skipped": True}

    monkeypatch.setattr(browser_session, "run_action", _no_browser)
    out = asyncio.run(native_tools.execute_native_tool(
        "browser", {"action": "goto", "url": "/x", "slug": "s"},
        memory=None, session_id="", read_file_fn=lambda *a, **k: {},
    ))
    assert "browser BLOCKED" in out and "FAILED" not in out and "Do not retry browser" in out


def test_heavy_eng_check_honours_negations():
    from daedalus.game_engineer import looks_like_heavy_eng

    assert looks_like_heavy_eng("Направи Snake на canvas. Не прави 3D и не ползвай физика.") is False
    assert looks_like_heavy_eng(SLERP) is True


# ── RAG reaches the builder without eating the ask ────────────────────────

def test_build_user_content_puts_the_ask_first_and_bounds_knowledge():
    knowledge = [{"content": "SLERP: normalise, dot<0 → negate q1. " * 60,
                  "metadata": {"title": "transform_math.md"}} for _ in range(10)]
    lessons = [{"content": "write index.html before exploring", "metadata": {"kind": "build"}}]
    text = build_user_content(SLERP, knowledge=knowledge, lessons=lessons, dod_block="━━━ DoD ━━━\nfiles exist")
    assert text.startswith("━━━ OWNER REQUEST ━━━\n" + SLERP)
    assert text.count("── transform_math.md ──") == 4
    assert "LESSONS FROM EARLIER BUILDS" in text and "━━━ DoD ━━━" in text
    assert len(text) <= 14_000
    tiny = build_user_content(SLERP, knowledge=knowledge, budget=len(SLERP) + 40)
    assert tiny.strip().endswith(SLERP)  # knowledge dropped whole, ask intact


def test_agent_builder_receives_build_user_content_not_a_prefix():
    import inspect

    from daedalus import agent

    src = inspect.getsource(agent.Daedalus._build_context_and_call)
    assert "build_user_content(" in src
    assert "user_content[:8000]" in src  # recon path keeps the old string
    assert "_build_user," in src
    assert "and not _build_wrapped" in src          # no post-hoc self-review on wrapped builds
    assert "BuildMentor(_call_claude" in src        # the mentor is wired with recall + consult


# ── Mentor ────────────────────────────────────────────────────────────────

class ScriptedMentor:
    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts: list[str] = []

    async def __call__(self, system, messages, *, role=""):
        assert role == "mentor"
        self.prompts.append(messages[0]["content"])
        return self.replies.pop(0) if self.replies else "{}"


def test_mentor_steps_in_on_a_failure_streak_and_runs_the_consult(fake_tools, monkeypatch):
    monkeypatch.delenv("DAEDALUS_MENTOR_MAX_CALLS", raising=False)
    rt = route(SLERP)
    consult_log, _ = run_consult(rt)
    scripted = ScriptedMentor([json.dumps({
        "diagnosis": "perspective takes fovy/aspect/near/far, not eye.",
        "instruction": "Stop retrying. Use the matrix below and write index.html.",
        "consult": {"tool": "math3d", "args": {"action": "perspective", "fovy": 1.0472, "aspect": 1.333, "near": 0.1, "far": 100}},
    })])

    async def _consult(tool, args):
        return await native_tools.execute_native_tool(tool, args, memory=None, session_id="", read_file_fn=None)

    mentor = BuildMentor(scripted, recall_fn=lambda q, k: ["perspective: fovy radians"], consult_fn=_consult)
    loop = BuildLoop(rt, SLERP, seed_log=consult_log, fix_rounds=2, mentor=mentor)
    loop.slug = "slerp-demo"
    bad = {"action": "perspective", "eye": [0, 0, 5]}
    model = ScriptedModel([
        [_use("math3d", **bad), _use("math3d", **dict(bad, eye=[0, 0, 6]))],   # streak of 2
        [_use("write_file", slug="slerp-demo", path="index.html", content="<canvas>")],
        [_text("Готово.")],
    ])
    _run(SLERP, model, loop=loop, max_rounds=4)

    assert mentor.calls == 1
    prompt = scripted.prompts[0]
    assert "FOCUS — math3d failed" in prompt and '"fovy"' in prompt      # schema went to the mentor
    assert "KNOWLEDGE (RAG" in prompt and "fovy radians" in prompt
    blocks = _user_blocks(model.seen[1])
    mentor_block = next(b for b in blocks if "MENTOR" in b)
    assert "Diagnosis: perspective takes fovy" in mentor_block
    assert "Do now: Stop retrying" in mentor_block
    assert "Mentor ran math3d" in mentor_block and '"ok": true' in mentor_block
    consult_obs = [o for o in loop.observations if o.args.get("mentor")]
    assert len(consult_obs) == 1 and consult_obs[0].ok
    assert loop.stop_reason == "complete"
    row = loop.log_row()
    assert row["mentor"][0]["consult"] == "math3d" and row["mentor"][0]["trigger"] == "streak"


def test_mentor_respects_budget_dedupes_and_fails_open(fake_tools):
    rt = route(SNAKE)

    class Boom:
        async def __call__(self, *a, **k):
            raise RuntimeError("provider down")

    mentor = BuildMentor(Boom(), max_calls=1)
    loop = BuildLoop(rt, SNAKE, fix_rounds=2, mentor=mentor)
    model = ScriptedModel([
        [_text("I would write index.html.")],
        [_text("Still thinking.")],
        [_text("Hmm.")],
    ])
    _run(SNAKE, model, loop=loop, max_rounds=3)
    assert loop.nudges == 2 and loop.stop_reason == "fix budget exhausted"   # deterministic nudge still ran
    assert mentor.calls == 1 and mentor.advice == []                          # one try, then out of budget
    nudges = [b for b in _user_blocks(model.seen[-1]) if "PLAN PROGRESS" in b]
    assert len(nudges) == 2 and all("MENTOR" not in n for n in nudges)

    quiet = BuildMentor(ScriptedMentor(['{"instruction": "Write index.html now."}'] * 3), max_calls=3)
    loop2 = BuildLoop(rt, SNAKE, fix_rounds=1, mentor=quiet)
    model2 = ScriptedModel([[_text("…")], [_text("…")]])
    _run(SNAKE, model2, loop=loop2, max_rounds=2)
    assert quiet.calls == 1
    nudge = next(b for b in _user_blocks(model2.seen[-1]) if "PLAN PROGRESS" in b)
    assert "Do now: Write index.html now." in nudge


def test_mentor_stop_verdict_ends_the_loop_and_is_visible():
    rt = route(SNAKE)
    scripted = ScriptedMentor([json.dumps({
        "diagnosis": "No canvas tool available in this jail.",
        "instruction": "Report that the build cannot proceed.",
        "stop": True,
    })])
    mentor = BuildMentor(scripted, max_calls=2)
    loop = BuildLoop(rt, SNAKE, fix_rounds=2, mentor=mentor)
    nudge = asyncio.run(loop.on_quiet("I cannot."))
    assert nudge and "Mentor verdict: not achievable" in nudge
    assert loop.stop_reason == "mentor: not achievable"


def test_mentor_prompt_is_compact_and_never_leaks_file_content():
    rt = route(SNAKE)
    loop = BuildLoop(rt, SNAKE, fix_rounds=1, mentor=BuildMentor(ScriptedMentor([])))
    loop.observe("write_file", {"slug": "s", "path": "index.html", "content": "X" * 5000},
                 "── write_file OK s/index.html ──", ok=True)
    loop.observe("game_lint", {"slug": "s", "path": "index.html", "kernel": True},
                 "── game_lint check FAILED ── duplicate declaration gameLoopId", ok=False)
    loop.verdict("")
    prompt = loop.mentor.prompt(loop, "quiet#1", None)
    assert "XXXX" not in prompt
    assert "KERNEL VERDICT" in prompt and "FILES WRITTEN: s/index.html" in prompt
    assert len(prompt) < 6000


def test_time_budget_ends_the_build_with_an_honest_stop(fake_tools):
    rt = route(SNAKE)
    loop = BuildLoop(rt, SNAKE, fix_rounds=3, time_budget_s=0.001)
    loop.started_at -= 10  # the budget is already spent when the first round would start
    model = ScriptedModel([[_use("write_file", slug="snake-game", path="index.html", content="<canvas>")]])
    text, log = _run(SNAKE, model, loop=loop, max_rounds=4)
    assert model.seen == []                     # no model round was started
    assert loop.stop_reason == "time budget exhausted"
    assert "time budget exhausted" in loop.summary_block()
    assert loop.log_row()["elapsed_s"] >= 10

    # clock runs out right after the write → the kernel still verifies the file
    loop3 = BuildLoop(rt, SNAKE, fix_rounds=3, time_budget_s=1000)
    loop3.observe("playground_new", {"name": "snake-game"}, "── playground_new OK slug=snake-game ──", ok=True)
    loop3.observe("write_file", {"slug": "snake-game", "path": "index.html"}, "── write_file OK ──", ok=True)
    loop3.started_at -= 2000
    seen_verify: list[str] = []

    async def _verify():
        seen_verify.append("ran")
        return True

    loop3.run_verify = _verify  # type: ignore[method-assign]
    asyncio.run(loop3.finalize("time budget exhausted"))
    assert seen_verify == ["ran"]

    # quiet with time left → nudge; quiet with time spent → stop, no free rounds
    loop2 = BuildLoop(rt, SNAKE, fix_rounds=3, time_budget_s=1000)
    assert asyncio.run(loop2.on_quiet("…")) is not None
    loop2.started_at -= 2000
    assert asyncio.run(loop2.on_quiet("…")) is None
    assert loop2.stop_reason == "time budget exhausted"
    assert BuildLoop(rt, SNAKE, fix_rounds=0, time_budget_s=0).out_of_time() is False


def test_mentor_env_flags():
    assert mentor_enabled() is True
    os.environ["DAEDALUS_BUILD_MENTOR"] = "0"
    try:
        assert mentor_enabled() is False
    finally:
        del os.environ["DAEDALUS_BUILD_MENTOR"]
    from daedalus.llm_config import MODEL_ROLES, role_model

    assert "mentor" in MODEL_ROLES
    os.environ["DAEDALUS_MODEL_MENTOR"] = "anthropic/claude-sonnet-4.5"
    try:
        assert role_model("mentor") == "anthropic/claude-sonnet-4.5"
    finally:
        del os.environ["DAEDALUS_MODEL_MENTOR"]


def test_wrap_up_ledger_mentions_the_mentor():
    from daedalus.build_wrap_up import _ledger

    rt = route(SNAKE)
    mentor = BuildMentor(ScriptedMentor([]))
    mentor.advice.append(MentorAdvice("streak", "perspective args were wrong", "use fovy"))
    loop = BuildLoop(rt, SNAKE, fix_rounds=1, mentor=mentor)
    loop.verdict("")
    assert "Mentor stepped in 1×: perspective args were wrong" in _ledger(loop, [])
