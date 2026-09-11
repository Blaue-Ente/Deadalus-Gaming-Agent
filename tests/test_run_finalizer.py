"""RunFinalizer — after-loop wrap + chat payload. No live LLM."""
from __future__ import annotations

import ast
import asyncio
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from daedalus.run_finalizer import (
    AfterLoopRequest,
    ChatFinishRequest,
    RunFinalizer,
    process_task_markers,
    scrape_pending_proposal,
    strip_need_markers,
)
from tests.characterization_fakes import (
    CHAT_RETURN_KEYS,
    FakeMem0,
    FakeMemory,
)

ROOT = Path(__file__).resolve().parents[1]
SNAKE = (
    "Направи нова Playground игра snake: HTML canvas, стрелки, расте при ябълка, "
    "game over при стена/опашка. Една завършена среза."
)


def test_import_run_finalizer_does_not_load_agent():
    env_code = (
        "import daedalus.run_finalizer as rf\n"
        "import sys\n"
        "bad = [n for n in sys.modules if n == 'daedalus.agent' or n.startswith('daedalus.agent.')]\n"
        "assert not bad, bad\n"
        "assert rf.RunFinalizer is not None\n"
    )
    env = os.environ.copy()
    env["DAEDALUS_STANDALONE"] = "1"
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env.get("PYTHONPATH", ""))
    result = subprocess.run(
        [sys.executable, "-c", env_code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_leaf_does_not_import_agent_or_leftover_ladder():
    tree = ast.parse((ROOT / "daedalus" / "run_finalizer.py").read_text(encoding="utf-8"))
    forbidden_modules = {
        "daedalus.agent",
        "daedalus.native_tools",
        "daedalus.tool_loop",
        "daedalus.batch_verify",
        "daedalus.kernel.full_autopilot",
        "daedalus.game_studio_verify",
        "daedalus.need_scan",
    }
    forbidden_names = {"Daedalus", "ToolLoop", "execute_verify", "NEED_VERIFY"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in forbidden_modules
                assert not alias.name.startswith("daedalus.agent.")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert module not in forbidden_modules
            assert not module.startswith("daedalus.agent")
            for alias in node.names:
                assert alias.name not in forbidden_names


def test_siblings_do_not_import_run_finalizer():
    for rel in (
        "daedalus/tool_loop.py",
        "daedalus/verification_controller.py",
        "daedalus/legacy_adapter.py",
        "daedalus/model_gateway.py",
        "daedalus/context_assembler.py",
    ):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "run_finalizer" not in src
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert node.module != "daedalus.run_finalizer"
                for alias in node.names:
                    assert alias.name != "RunFinalizer"


def test_agent_wires_after_loop_and_finish_chat():
    src = (ROOT / "daedalus" / "agent.py").read_text(encoding="utf-8")
    assert "from .run_finalizer import AfterLoopRequest, ChatFinishRequest, RunFinalizer" in src
    assert "RunFinalizer().after_loop" in src
    assert "RunFinalizer().finish_chat" in src
    assert "process_mode_marker_only" in src
    assert "def process_task_markers" not in src
    assert "empty-reply guard" not in src


def test_cheap_path_marker_helper_stays_on_daedalus():
    src = (ROOT / "daedalus" / "agent.py").read_text(encoding="utf-8")
    assert "def process_mode_marker_only" in src
    fin = (ROOT / "daedalus" / "run_finalizer.py").read_text(encoding="utf-8")
    assert "def process_mode_marker_only" not in fin


def test_strip_need_markers_inline_and_blocks():
    raw = "Here <NEED_FOO: bar> leftover\n<NEED_PROPOSE_START>\npatch\n</NEED_PROPOSE_START>"
    cleaned = strip_need_markers(raw)
    assert "<NEED_" not in cleaned
    assert "Here" in cleaned and "leftover" in cleaned


def test_scrape_pending_proposal():
    assert scrape_pending_proposal("no proposal") is None
    assert scrape_pending_proposal(
        "Proposal ID : prop_deadbeef_aa11bb22\nFile : daedalus/foo.py\n"
    ) == {"proposal_id": "prop_deadbeef_aa11bb22", "file_path": "daedalus/foo.py"}


def test_process_task_markers_empty():
    assert process_task_markers("", "sess") == ("", [])
    assert process_task_markers(None, "sess") == (None, [])  # type: ignore[arg-type]


def test_process_task_markers_open_done_mode(monkeypatch):
    opened: list[tuple[str, str]] = []
    updated: list[dict] = []

    monkeypatch.setattr(
        "daedalus.run_finalizer._continuity.create_task",
        lambda title, summary, session_id="": opened.append((title, summary)) or {"id": "t1", "title": title},
    )
    monkeypatch.setattr(
        "daedalus.run_finalizer._continuity.find_task_by_hint",
        lambda hint: {"id": "t1", "title": hint, "summary": "s"},
    )
    monkeypatch.setattr(
        "daedalus.run_finalizer._continuity.update_task",
        lambda *a, **k: updated.append(k) or {"id": "t1", "status": k.get("status")},
    )
    modes: list[str] = []
    monkeypatch.setattr("daedalus.run_finalizer._mode.set_mode", lambda m: modes.append(m))
    raw = (
        "<TASK_OPEN: maze | first slice>\n"
        "<TASK_DONE: maze>\n"
        "<NEED_MODE: talk>\n"
        "hello"
    )
    cleaned, events = process_task_markers(raw, "session-xyz")
    assert cleaned == "hello"
    assert [e["action"] for e in events] == ["open", "done", "mode"]
    assert opened == [("maze", "first slice")]
    assert updated[0]["status"] == "done"
    assert modes == ["talk"]


def _after(
    *,
    raw: str = "short reply",
    session_id: str = "sess-finalizer",
    user_message: str = "hello there",
    expect_json: bool = False,
    for_self: bool = False,
    tool_calls: list | None = None,
    leftover_iterations: int = 0,
    started_at: float | None = None,
    briefing_dict: dict | None = None,
    retrieval: dict | None = None,
    loop=None,
    memory=None,
    mem0=None,
    background_tasks=None,
    complete_fn=None,
    clear_live=None,
) -> AfterLoopRequest:
    async def _complete(*_a, **_k):
        return "reflected"

    return AfterLoopRequest(
        raw=raw,
        session_id=session_id,
        user_message=user_message,
        expect_json=expect_json,
        for_self=for_self,
        tool_calls=list(tool_calls or []),
        leftover_iterations=leftover_iterations,
        started_at=time.time() if started_at is None else started_at,
        briefing_dict=briefing_dict,
        retrieval=retrieval or {},
        loop=loop,
        memory=memory or FakeMemory(),
        mem0=mem0 or FakeMem0(),
        background_tasks=background_tasks if background_tasks is not None else set(),
        complete_fn=complete_fn or _complete,
        clear_live=clear_live or (lambda *_a, **_k: None),
    )


def test_after_loop_empty_reply_uses_cheap_wrap(monkeypatch):
    hits: list[str] = []

    async def _cheap(self, req, **_k):
        hits.append(req.role)
        return SimpleNamespace(text="синтез от tools", routed_via="cheap", model="haiku", cost_usd=0.0)

    monkeypatch.setattr("daedalus.model_gateway.ModelGateway.complete_cheap", _cheap)
    mem = FakeMemory()
    mem0 = FakeMem0()
    out = asyncio.run(RunFinalizer().after_loop(_after(
        raw="<NEED_READ_FILE: foo.py>",
        tool_calls=[{"tool": "read_file", "ok": True, "query": "foo.py"}],
        leftover_iterations=2,
        memory=mem,
        mem0=mem0,
        retrieval={"knowledge": [1], "mesh_block": "m"},
    )))
    assert hits == ["wrap"]
    assert out.raw == "синтез от tools"
    assert out.summary["tool_iterations"] == 3
    assert out.summary["knowledge_used"] == 1
    assert out.summary["mesh_aware"] is True
    assert len(mem.turns) == 2
    assert len(mem0.captured) == 2


def test_after_loop_empty_reply_empty_cheap_uses_short_fallback(monkeypatch):
    async def _cheap(self, req, **_k):
        return SimpleNamespace(text="  ", routed_via="cheap", model="haiku", cost_usd=0.0)

    monkeypatch.setattr("daedalus.model_gateway.ModelGateway.complete_cheap", _cheap)
    out = asyncio.run(RunFinalizer().after_loop(_after(
        raw="<NEED_WEB: q>",
        tool_calls=[{"tool": "web_search", "ok": True, "query": "q"}],
    )))
    assert "какво конкретно искаш." in out.raw
    assert "да направя" not in out.raw


def test_after_loop_empty_reply_exception_uses_long_fallback(monkeypatch):
    async def _cheap(self, req, **_k):
        raise RuntimeError("no wrap")

    monkeypatch.setattr("daedalus.model_gateway.ModelGateway.complete_cheap", _cheap)
    out = asyncio.run(RunFinalizer().after_loop(_after(
        raw="<NEED_WEB: q>",
        tool_calls=[{"tool": "web_search", "ok": True, "query": "q"}],
    )))
    assert "какво конкретно искаш да направя." in out.raw


def test_after_loop_strips_need_markers():
    out = asyncio.run(RunFinalizer().after_loop(_after(
        raw="Here <NEED_FOO: bar> leftover",
    )))
    assert "<NEED_FOO" not in out.raw
    assert "Here" in out.raw and "leftover" in out.raw


def test_after_loop_read_first_hint_does_not_append(monkeypatch):
    monkeypatch.setattr(
        "daedalus.run_finalizer.needs_read_first_hint",
        lambda *_a, **_k: "\n⚠️ READ-FIRST: would have been appended",
    )
    out = asyncio.run(RunFinalizer().after_loop(_after(raw="INSERT INTO x VALUES (1)")))
    assert "READ-FIRST" not in out.raw
    assert out.raw == "INSERT INTO x VALUES (1)"


def test_after_loop_review_gate_skips_and_appends(monkeypatch):
    hits: list[str] = []

    async def _crit(*_a, **_k):
        hits.append("crit")
        return {"ok": False, "issue": "promised write", "suggested_fix": "emit NEED_"}

    monkeypatch.setattr("daedalus.run_finalizer.review_gate_critique", _crit)
    body = "x" * 90
    started = time.time() - 25
    tools = [{"tool": "a", "ok": True}, {"tool": "b", "ok": True}, {"tool": "c", "ok": True}]

    skipped = asyncio.run(RunFinalizer().after_loop(_after(
        raw=body, for_self=True, tool_calls=tools, started_at=started,
    )))
    assert hits == []
    assert skipped.raw == body

    skipped = asyncio.run(RunFinalizer().after_loop(_after(
        raw=body, expect_json=True, tool_calls=tools, started_at=started,
    )))
    assert hits == []

    skipped = asyncio.run(RunFinalizer().after_loop(_after(
        raw=body + " [fallback: x]", tool_calls=tools, started_at=started,
    )))
    assert hits == []

    skipped = asyncio.run(RunFinalizer().after_loop(_after(
        raw="short", tool_calls=tools, started_at=started,
    )))
    assert hits == []

    skipped = asyncio.run(RunFinalizer().after_loop(_after(
        raw=body, tool_calls=[{"tool": "a", "ok": True}], started_at=started,
    )))
    assert hits == []

    skipped = asyncio.run(RunFinalizer().after_loop(_after(
        raw=body, tool_calls=tools, started_at=time.time(),
    )))
    assert hits == []

    flagged = asyncio.run(RunFinalizer().after_loop(_after(
        raw=body, tool_calls=tools, started_at=started,
    )))
    assert hits == ["crit"]
    assert "Self-review бележка: promised write" in flagged.raw
    assert "Предложение: emit NEED_" in flagged.raw


def test_after_loop_question_budget_and_intent_and_outcome(monkeypatch):
    outcomes: list[dict] = []
    monkeypatch.setattr(
        "daedalus.run_finalizer.question_budget_check_and_increment",
        lambda *_a, **_k: (False, "Вече попитах веднъж"),
    )
    monkeypatch.setattr(
        "daedalus.run_finalizer.detect_intent_without_action",
        lambda *_a, **_k: {"detected": True, "phrase": "ще проверя в базата"},
    )
    monkeypatch.setattr(
        "daedalus.run_finalizer.record_outcome",
        lambda sid, **kw: outcomes.append({"sid": sid, **kw}),
    )
    out = asyncio.run(RunFinalizer().after_loop(_after(
        raw="ще проверя в базата сега?",
        leftover_iterations=3,
        briefing_dict={"intent": "code_change"},
    )))
    assert out.raw.startswith("Вече попитах веднъж")
    assert "self-check" in out.raw
    assert outcomes
    assert outcomes[0]["intent_without_action"] is True
    assert outcomes[0]["intent"] == "code_change"
    assert outcomes[0]["tool_iterations"] == 0


def test_after_loop_skips_reflection_when_for_self():
    tasks: set = set()

    async def _complete(*_a, **_k):
        raise AssertionError("reflection must not run")

    asyncio.run(RunFinalizer().after_loop(_after(
        raw="ok",
        for_self=True,
        background_tasks=tasks,
        complete_fn=_complete,
    )))
    assert tasks == set()


def test_after_loop_summary_from_loop():
    loop = SimpleNamespace(kind="static", browser_ok_for_dod=lambda: True)
    out = asyncio.run(RunFinalizer().after_loop(_after(
        raw="ok",
        tool_calls=[{"tool": "write_file", "ok": True}],
        leftover_iterations=1,
        loop=loop,
        retrieval={"knowledge": [1, 2], "episodes": [1]},
    )))
    assert out.summary["build_kind"] == "static"
    assert out.summary["browser_ok"] is True
    assert out.summary["tool_iterations"] == 2
    assert out.summary["knowledge_used"] == 2
    assert out.summary["episodes_used"] == 1


def _finish(
    *,
    raw: str = "shape-ok",
    summary: dict | None = None,
    message: str = "ping",
    session_id: str = "chat-fin",
    for_self: bool = False,
    started: datetime | None = None,
    effort_dec: dict | None = None,
    budget_plan: dict | None = None,
    background_tasks=None,
    chat_fn=None,
    phase_save_fn=None,
    phase_clear_fn=None,
) -> ChatFinishRequest:
    async def _chat(*_a, **_k):
        return {"reply": "self-improve"}

    return ChatFinishRequest(
        raw=raw,
        summary=summary or {"tool_calls": []},
        message=message,
        session_id=session_id,
        for_self=for_self,
        started=started or datetime.now(timezone.utc),
        effort_dec=effort_dec or {"tier": "whisper", "label": "w"},
        budget_plan=budget_plan or {"plan": "default"},
        background_tasks=background_tasks if background_tasks is not None else set(),
        chat_fn=chat_fn or _chat,
        phase_save_fn=phase_save_fn or (lambda **_k: None),
        phase_clear_fn=phase_clear_fn or (lambda *_a, **_k: None),
    )


def test_finish_chat_payload_keys_and_proposal(monkeypatch):
    monkeypatch.setattr(
        "daedalus.run_budget.status",
        lambda: {"spent_usd": 0.01, "active": True},
    )
    out = asyncio.run(RunFinalizer().finish_chat(_finish(
        raw="Proposal ID : prop_deadbeef_aa11bb22\nFile : daedalus/foo.py\nReady.",
        message="show the proposal",
        effort_dec={"tier": "craft", "label": "c", "role": "writer"},
    )))
    payload = out.payload
    for key in CHAT_RETURN_KEYS:
        assert key in payload, f"missing {key}"
    assert payload["agent"] == "Daedalus"
    assert payload["pending_proposal"] == {
        "proposal_id": "prop_deadbeef_aa11bb22",
        "file_path": "daedalus/foo.py",
    }
    assert payload["effort"]["tier"] == "craft"
    assert payload["run_budget"]["spent_usd"] == 0.01
    assert payload["complete"] is True


def test_finish_chat_dod_nudge_and_failure_draft(monkeypatch):
    drafts: list[str] = []
    monkeypatch.setattr(
        "daedalus.learned_skills.draft_from_failure",
        lambda **k: drafts.append("fail") or None,
    )
    monkeypatch.setattr(
        "daedalus.learned_skills.draft_from_success",
        lambda **k: drafts.append("ok"),
    )
    out = asyncio.run(RunFinalizer().finish_chat(_finish(
        raw="Готово.",
        message=SNAKE,
        summary={"tool_calls": [], "build_kind": "static"},
    )))
    assert "DEFINITION OF DONE" in out.payload["reply"]
    assert out.payload["complete"] is False
    assert drafts == ["fail"]


def test_finish_chat_success_draft_on_coding_complete(monkeypatch):
    drafts: list[str] = []
    monkeypatch.setattr(
        "daedalus.learned_skills.draft_from_success",
        lambda **k: drafts.append(k.get("kind")),
    )
    monkeypatch.setattr(
        "daedalus.learned_skills.draft_from_failure",
        lambda **k: drafts.append("fail"),
    )
    out = asyncio.run(RunFinalizer().finish_chat(_finish(
        raw="Готово.",
        message=SNAKE,
        summary={
            "tool_calls": [{"tool": "write_file", "ok": True}],
            "build_kind": "static",
        },
    )))
    assert out.payload["complete"] is True
    assert drafts == ["chat"]
    assert "DEFINITION OF DONE" not in out.payload["reply"]


def test_finish_chat_phase_save_and_clear(monkeypatch):
    saved: list[dict] = []
    cleared: list[str] = []
    monkeypatch.setattr(
        "daedalus.run_finalizer.process_task_markers",
        lambda raw, sid: (raw, [{"action": "done", "ok": True}]),
    )
    out = asyncio.run(RunFinalizer().finish_chat(_finish(
        raw="ok",
        summary={"tool_calls": [{"tool": "set_progress", "pct": 0.4, "task": "maze", "msg": "mid"}]},
        phase_save_fn=lambda **k: saved.append(k),
        phase_clear_fn=lambda sid: cleared.append(sid),
        session_id="sess-phase",
    )))
    assert saved == [{"session_id": "sess-phase", "pct": 0.4, "task_name": "maze", "msg": "mid"}]
    assert cleared == ["sess-phase"]
    assert out.payload["task_events"][0]["action"] == "done"


def test_finish_chat_self_improve_gate(monkeypatch):
    chats: list[dict] = []

    async def _chat(prompt, for_self=False, **_k):
        chats.append({"prompt": prompt, "for_self": for_self})
        return {"reply": "fix"}

    started = datetime.now(timezone.utc) - timedelta(seconds=120)
    tools = [{"tool": "a", "ok": False}, {"tool": "b", "ok": False}, {"tool": "c", "ok": True}]

    async def _run(*, for_self: bool) -> set:
        tasks: set = set()
        await RunFinalizer().finish_chat(_finish(
            raw="ok",
            summary={"tool_calls": tools},
            started=started,
            chat_fn=_chat,
            background_tasks=tasks,
            for_self=for_self,
        ))
        if tasks:
            await asyncio.gather(*list(tasks), return_exceptions=True)
        return tasks

    scheduled = asyncio.run(_run(for_self=False))
    assert scheduled
    assert chats and chats[0]["for_self"] is True

    chats.clear()
    skipped = asyncio.run(_run(for_self=True))
    assert chats == []
    assert skipped == set()


def test_finish_chat_accepts_via_verification_controller(monkeypatch):
    hits: list[str] = []
    from daedalus.contracts import DodVerdict
    from daedalus.verification_controller import VerificationController

    real_accept = VerificationController.accept

    def _accept(self, req, stages=None):
        hits.append(req.ask)
        return real_accept(self, req, stages)

    monkeypatch.setattr(VerificationController, "accept", _accept)
    asyncio.run(RunFinalizer().finish_chat(_finish(raw="Добре.", message="как си днес?")))
    assert hits == ["как си днес?"]
