"""Kernel build loop — plan drives the model; DoD is checked inside the round loop."""
from __future__ import annotations

import asyncio
import inspect
import re
from pathlib import Path

import pytest

from daedalus import native_tools
from daedalus.build_loop import BuildLoop
from daedalus.job_contract import evaluate, unrecovered_failure_indexes
from daedalus.tool_router import route, run_consult

SNAKE = (
    "Направи нова Playground игра snake: HTML canvas, стрелки, расте при ябълка, "
    "game over при стена/опашка. Една завършена среза."
)
SLERP = "Направи нова Playground 3D demo: камера с quaternion slerp към цел и look-at."


def _text(t: str) -> dict:
    return {"type": "text", "text": t}


def _use(tool: str, **args) -> dict:
    return {"type": "tool_use", "id": f"call_{tool}_{len(str(args))}", "name": tool, "input": args}


class ScriptedModel:
    """Replays one response per round and records what the kernel sent."""

    def __init__(self, rounds: list[list[dict]]):
        self.rounds = list(rounds)
        self.seen: list[list[dict]] = []

    async def __call__(self, system, messages, tools):
        self.seen.append([dict(m) for m in messages])
        if not self.rounds:
            return {"stop_reason": "end_turn", "content": [_text("Готово.")]}
        return {"stop_reason": "tool_use", "content": self.rounds.pop(0)}


def _ok_proof(slug, kind="static", **_k):
    from daedalus.visual_proof import ProofResult
    return ProofResult(
        ok=True, skipped=False, kind=kind or "static", slug=slug or "x",
        tool="browser", summary="fake render ok", frames=[],
    )


@pytest.fixture
def fake_tools(monkeypatch):
    """Native tools answer like the real ones would, without touching disk or LLM."""

    async def _exec(name, args, **_kw):
        if name == "playground_new":
            return f"── playground_new OK slug={args.get('name')} kind=static ──"
        if name == "write_file":
            return f"── write_file OK {args.get('slug')}/{args.get('path')} ({len(args.get('content') or '')} chars) ──"
        if name == "run_command":
            if "boom" in str(args.get("command")):
                return "── run_command FAILED exit=1 cwd=snake-game ──\nNo such file or directory"
            return "── run_command OK exit=0 cwd=/data/playground/snake-game ──\nHTML OK"
        if name == "math3d":
            return '── math3d quat_slerp OK ──\n{"ok": true, "verdict": "match"}'
        return f"── {name} OK ──"

    async def _proof(slug, kind="static"):
        return _ok_proof(slug, kind)

    async def _no_review(*_a, **_k):
        return ""

    monkeypatch.setattr(native_tools, "execute_native_tool", _exec)
    monkeypatch.setattr(native_tools, "_kernel_write_review", _no_review)
    monkeypatch.setattr("daedalus.visual_proof.run_visual_proof", _proof)
    return _exec


def _run(system, user, model, *, loop, max_rounds=4):
    return asyncio.run(
        native_tools.run_native_discovery_pass(
            system, user, model,
            memory=None, session_id="", read_file_fn=lambda *a, **k: {},
            max_rounds=max_rounds, tools=[{"name": "x"}], loop=loop,
        )
    )


def _user_texts(messages: list[dict]) -> list[str]:
    out: list[str] = []
    for m in messages:
        if m.get("role") != "user":
            continue
        c = m.get("content")
        if isinstance(c, str):
            out.append(c)
        elif isinstance(c, list):
            out.append("\n".join(
                str(b.get("text") or "") for b in c
                if isinstance(b, dict) and b.get("type") == "text"
            ))
    return out


def test_quiet_round_with_incomplete_plan_gets_plan_progress(fake_tools):
    rt = route(SNAKE)
    loop = BuildLoop(rt, SNAKE, fix_rounds=3)
    model = ScriptedModel([
        [_use("playground_new", name="snake-game", kind="static"),
         _use("run_command", command="boom", cwd="snake-game")],
        [_text("Let me explore the project structure.")],          # quiet, plan incomplete
        [_use("write_file", slug="snake-game", path="index.html", content="<canvas>"),
         _use("run_command", command="ls", slug="snake-game")],
        [_text("Готово: snake slice written.")],                     # quiet, plan complete
    ])
    text, log = _run("sys", SNAKE, model, loop=loop, max_rounds=3)

    assert loop.nudges == 1
    assert loop.stop_reason == "complete"
    assert loop.slug == "snake-game"
    nudges = [t for t in _user_texts(model.seen[-1]) if "PLAN PROGRESS" in t]
    assert len(nudges) == 1
    nudge = nudges[0]
    assert "☐ write: write_file REQUIRED" in nudge
    assert "run_command" in nudge and "FAILED" in nudge
    assert "NEXT: call write_file with slug=snake-game" in nudge
    assert "Няма запис на файлове" in nudge
    # a fix round extends the budget: 4 model rounds happened although max_rounds=3
    assert len(model.seen) == 4
    assert text and "NATIVE TOOL RESULTS" in text
    assert [r["tool"] for r in log] == [
        "playground_new", "run_command", "write_file", "run_command", "browser",
    ]
    assert loop.log_row()["complete"] is True
    assert "Plan complete" in loop.summary_block()
    assert loop.kernel_proof_ran is True


def test_loop_stops_when_fix_budget_is_spent(fake_tools):
    rt = route(SNAKE)
    loop = BuildLoop(rt, SNAKE, fix_rounds=2)
    model = ScriptedModel([[_text("I would write index.html.")] for _ in range(6)])
    text, log = _run("sys", SNAKE, model, loop=loop, max_rounds=4)

    assert text is None and log == []
    assert loop.nudges == 2
    assert loop.stop_reason == "fix budget exhausted"
    assert len(model.seen) == 3  # first quiet + 2 nudged retries
    row = loop.log_row()
    assert row["complete"] is False
    assert any("write" in m for m in row["missing"])
    summary = loop.summary_block()
    assert "NOT complete" in summary and "Няма запис на файлове" in summary


def test_fix_rounds_zero_keeps_old_behaviour(fake_tools):
    rt = route(SNAKE)
    loop = BuildLoop(rt, SNAKE, fix_rounds=0)
    model = ScriptedModel([[_text("nothing")]])
    text, log = _run("sys", SNAKE, model, loop=loop)
    assert text is None and log == []
    assert loop.nudges == 0 and loop.stop_reason == "passive"
    assert len(model.seen) == 1


def test_slerp_nudge_names_the_missing_math3d_step(fake_tools):
    rt = route(SLERP)
    consult_log, _ = run_consult(rt)
    loop = BuildLoop(rt, SLERP, seed_log=consult_log, fix_rounds=3)
    loop.observe("write_file", {"slug": "cam", "path": "index.html"}, "── write_file OK cam/index.html ──", ok=True)
    v = loop.verdict("Готово.")
    assert v.complete is False
    assert any("math3d" in m for m in v.missing_steps)
    assert v.next_step is not None and v.next_step.tool == "math3d"
    block = loop.progress_block(v)
    assert "✓ canon_glm: game_canon" in block         # auto consult step counts as done
    assert "☐ math: math3d REQUIRED" in block
    assert "NEXT: call math3d with op=quat_slerp" in block
    loop.observe("math3d", {"op": "quat_slerp"}, "── math3d quat_slerp OK ──", ok=True)
    assert loop.verdict("Готово.").complete is False
    assert any("visual proof" in b.lower() or "работещата игра" in b for b in loop.last_verdict.blockers)
    loop.observe("browser", {"slug": "cam", "action": "kernel_proof"}, "── visual_proof OK ──", ok=True)
    assert loop.verdict("Готово.").complete is True


def test_budget_exhausted_while_still_calling_tools_is_reported(fake_tools):
    rt = route(SNAKE)
    loop = BuildLoop(rt, SNAKE, fix_rounds=3)
    model = ScriptedModel([[_use("playground_new", name="snake-game")] for _ in range(5)])
    _run("sys", SNAKE, model, loop=loop, max_rounds=2)
    assert loop.stop_reason == "round budget exhausted"
    assert loop.log_row()["complete"] is False
    assert loop.nudges == 0


def test_failed_call_repaired_later_is_not_a_blocker():
    calls = [
        {"tool": "write_file", "ok": True},
        {"tool": "run_command", "ok": False},
        {"tool": "run_command", "ok": True},
    ]
    assert unrecovered_failure_indexes(calls) == []
    assert evaluate(SNAKE, "Готово.", tool_calls=calls, kind="static").complete is True
    stuck = calls[:2]
    assert unrecovered_failure_indexes(stuck) == [1]
    assert evaluate(SNAKE, "Готово.", tool_calls=stuck, kind="static").complete is False


def test_strict_contract_scores_plan_without_done_claim():
    calls = [{"tool": "write_file", "ok": True}]
    relaxed = evaluate(SLERP, "still working", tool_calls=calls, kind="static")
    assert "task_plan" not in relaxed.checks
    strict = evaluate(SLERP, "still working", tool_calls=calls, kind="static", strict=True)
    assert strict.checks.get("task_plan") is False
    assert strict.complete is False


def test_agent_attaches_build_loop_to_the_native_pass():
    src = Path("daedalus/agent.py").read_text(encoding="utf-8")
    assert "from .build_loop import BuildLoop" in src
    assert re.search(r"run_native_discovery_pass\([\s\S]{0,600}loop=_loop", src)
    assert "_loop.summary_block()" in src
    sig = inspect.signature(native_tools.run_native_discovery_pass)
    assert "loop" in sig.parameters


def test_failed_visual_proof_nudges_a_fix(fake_tools, monkeypatch):
    from daedalus.visual_proof import ProofResult

    async def _blank(slug, kind="static"):
        return ProofResult(
            ok=False, skipped=False, kind=kind, slug=slug, tool="browser",
            summary="blank screen", blank_screen=True, frames=[],
        )

    monkeypatch.setattr("daedalus.visual_proof.run_visual_proof", _blank)
    rt = route(SNAKE)
    loop = BuildLoop(rt, SNAKE, fix_rounds=3)
    model = ScriptedModel([
        [_use("write_file", slug="snake-game", path="index.html", content="<canvas>")],
        [_text("I wrote the game.")],
    ])
    _run("sys", SNAKE, model, loop=loop, max_rounds=3)
    assert loop.kernel_proof_ran is True
    assert loop.log_row()["complete"] is False
    nudges = [t for t in _user_texts(model.seen[-1]) if "PLAN PROGRESS" in t or "VISUAL PROOF" in t]
    blob = "\n".join(nudges)
    assert "VISUAL PROOF" in blob
    assert "write_file" in blob
    assert loop.stop_reason != "complete"


def test_missing_chromium_is_not_a_pass(fake_tools, monkeypatch):
    from daedalus.visual_proof import ProofResult

    async def _gap(slug, kind="static"):
        return ProofResult(
            ok=False, skipped=True, kind=kind, slug=slug, tool="browser",
            summary="Chromium missing", honest_gap="chromium_missing",
        )

    monkeypatch.setattr("daedalus.visual_proof.run_visual_proof", _gap)
    rt = route(SNAKE)
    loop = BuildLoop(rt, SNAKE, fix_rounds=3)
    model = ScriptedModel([
        [_use("write_file", slug="snake-game", path="index.html", content="<canvas>")],
        [_text("Готово.")],
    ])
    _run("sys", SNAKE, model, loop=loop, max_rounds=3)
    assert loop.stop_reason == "visual proof unavailable"
    assert loop.log_row()["complete"] is False
    assert loop.proof_gap == "chromium_missing"
    assert any("Chromium" in b for b in (loop.last_verdict.blockers if loop.last_verdict else []))


def test_visual_proof_off_skips_kernel_open(monkeypatch):
    monkeypatch.setenv("DAEDALUS_VISUAL_PROOF", "0")
    rt = route(SNAKE)
    loop = BuildLoop(rt, SNAKE, fix_rounds=3)
    loop.observe("write_file", {"slug": "snake-game", "path": "index.html"}, "── write_file OK ──", ok=True)
    assert loop.wants_kernel_proof() is False
    assert loop.verdict("Готово.").complete is True


def test_kernel_runs_visual_proof_after_writes(fake_tools):
    rt = route(SNAKE)
    loop = BuildLoop(rt, SNAKE, fix_rounds=3)
    model = ScriptedModel([
        [_use("write_file", slug="snake-game", path="game.js", content="let x=1;")],
        [_text("done writing")],
    ])
    text, log = _run("sys", SNAKE, model, loop=loop, max_rounds=3)
    assert loop.kernel_proof_ran is True
    assert any(r.get("tool") == "browser" for r in log)
    assert loop.stop_reason == "complete"
    assert text and "VISUAL PROOF" in text


def test_need_worker_quiet_round_keeps_building(fake_tools, monkeypatch):
    monkeypatch.setenv("DAEDALUS_WORKERS", "1")

    async def fake_workers(calls):
        assert calls and calls[0]["slot"] == "fast"
        return [{
            "slot": "fast",
            "model": "minimax/minimax-m2.7",
            "ok": True,
            "task_preview": "WebGL traps",
            "result": "- transpose=true is INVALID_VALUE on WebGL1",
        }]

    monkeypatch.setattr("daedalus.workers.call_workers_parallel", fake_workers)
    rt = route(SLERP)
    loop = BuildLoop(rt, SLERP, fix_rounds=2)
    model = ScriptedModel([
        [_text("<NEED_WORKER: fast | list WebGL1 traps for this greybox>")],
        [_use("math3d", op="quat_slerp", t=0.5),
         _use("write_file", slug="slerp-lookat", path="index.html", content="<canvas>")],
        [_text("Готово: greybox written.")],
    ])
    text, log = _run("sys", SLERP, model, loop=loop, max_rounds=4)
    workers_turns = [t for t in _user_texts(sum(model.seen, [])) if "WORKERS (kernel ran NEED_WORKER)" in t]
    assert workers_turns
    assert loop.worker_log
    row = loop.log_row()
    assert row["workers"]
    assert row["workers"][0]["slot"] == "fast"
    assert row["workers"][0]["ok"] is True
    assert any(r.get("tool") == "math3d" for r in log)
    assert loop.stop_reason == "complete"
    assert text and "NATIVE TOOL RESULTS" in text
