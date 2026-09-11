"""ContextAssembler — retrieve / compose front matter. No live LLM."""
from __future__ import annotations

import ast
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from daedalus.context_assembler import (
    FUSED_TOP_K,
    AssembleRequest,
    AssembledPrompt,
    ContextAssembler,
    RetrievedContext,
    format_injected_context,
    partition_fused,
)
from tests.characterization_fakes import (
    PLAYGROUND_SID,
    FakeMem0,
    FakeMemory,
    ScriptedClaude,
    bare_daedalus,
    drain_background,
    isolate_paths,
    stub_navigate_chat,
    stub_native_pass,
    stub_tools_quiet,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def char_env(tmp_path, monkeypatch):
    isolate_paths(tmp_path, monkeypatch)
    monkeypatch.setattr("daedalus.navigator.navigate", stub_navigate_chat)
    yield tmp_path


def _entry(eid: str, content: str, source: str, **meta) -> SimpleNamespace:
    return SimpleNamespace(id=eid, content=content, metadata=meta, source=source)


def test_format_injected_context_empty():
    assert format_injected_context([], [], [], []) == ""


def test_format_injected_context_headers_and_clips():
    blob = format_injected_context(
        knowledge=[{
            "content": "K" * 2000,
            "metadata": {"category": "skill", "title": "T", "source": "src"},
        }],
        memory=[{
            "content": "M" * 1200,
            "metadata": {"role": "user", "timestamp": "2026-01-01T00:00:00Z", "session_id": "abcdefghi"},
        }],
        artifacts=[{
            "metadata": {"filename": "a.py", "language": "py", "summary": "S" * 400},
        }],
        session_recent=[{
            "content": "R" * 1200,
            "metadata": {"role": "assistant", "timestamp": "2026-01-02T00:00:00Z"},
        }],
        lessons=[{"content": "L" * 400, "metadata": {"kind": "general"}}],
        episodes=[{
            "content": "E" * 2000,
            "metadata": {"session_id": "sessionxxxx", "ended_at": "2026-01-03T00:00:00Z", "turn_count": 3},
        }],
        mesh_block="MESH-BLOCK",
    )
    assert "MESH-BLOCK" in blob
    assert "LESSONS YOU LEARNED" in blob
    assert "EPISODIC MEMORIES" in blob
    assert "RELEVANT IYVERIS KNOWLEDGE" in blob
    assert "RELEVANT PAST MEMORY" in blob
    assert "SIMILAR PAST ARTIFACTS" in blob
    assert "RECENT TURNS IN THIS SESSION" in blob
    assert "L" * 240 in blob
    assert "L" * 241 not in blob
    assert "E" * 1500 in blob
    assert "E" * 1501 not in blob
    assert "K" * 1200 in blob
    assert "K" * 1201 not in blob
    assert "M" * 800 in blob
    assert "M" * 801 not in blob
    assert "R" * 800 in blob
    assert "R" * 801 not in blob
    assert "S" * 200 in blob
    assert "S" * 201 not in blob


def test_partition_fused_buckets_and_episodic_promotion():
    knowledge, past_memory, lessons, episodic = partition_fused([
        _entry("g", "global-doc", "global"),
        _entry("l", "lesson-doc", "lesson"),
        _entry("e", "episode-doc", "episodic"),
        _entry("s", "sem-doc", "semantic"),
        _entry("w", "work-doc", "working"),
        _entry("h", "hyb-doc", "hybrid"),
    ])
    assert [r["id"] for r in knowledge] == ["g"]
    assert [r["id"] for r in lessons] == ["l"]
    assert [r["id"] for r in episodic] == ["e"]
    assert [r["id"] for r in past_memory] == ["s", "w", "h"]
    assert all(r["distance"] is None for r in knowledge + past_memory + lessons + episodic)


def test_retrieve_promotes_episodic_into_lessons_and_uses_top_k_14(char_env):
    seen: list[int] = []

    class _Mem0(FakeMem0):
        def fused_context(self, session_id, query, top_k=12, **_k):
            seen.append(top_k)
            return [
                _entry("g", "know", "global"),
                _entry("e", "epi", "episodic", kind="fact"),
                _entry("s", "past", "semantic"),
            ]

    assembler = ContextAssembler(FakeMemory(), _Mem0())
    retrieved = asyncio.run(assembler.retrieve(AssembleRequest(
        user_message="hello world",
        session_id="chat-plain",
        persona="p",
    )))
    assert seen == [FUSED_TOP_K]
    assert FUSED_TOP_K == 14
    assert len(retrieved.knowledge) == 1
    assert len(retrieved.past_memory) == 1
    assert [r["content"] for r in retrieved.lessons] == ["epi"]
    assert retrieved.query == "hello world"
    assert retrieved.retrieval_counts()["knowledge_used"] == 1
    assert retrieved.retrieval_counts()["lessons_used"] == 1


def test_retrieve_steering_prepends_owner_followup(char_env, monkeypatch):
    monkeypatch.setattr(
        "daedalus.context_assembler.pop_steering_messages",
        lambda _sid: "STEER-NOW",
    )
    assembler = ContextAssembler(FakeMemory(), FakeMem0())
    retrieved = asyncio.run(assembler.retrieve(AssembleRequest(
        user_message="original ask",
        session_id="chat-plain",
    )))
    assert retrieved.user_message.startswith("STEER-NOW")
    assert retrieved.user_message.endswith("original ask")


def test_retrieve_compaction_over_threshold(char_env):
    mem = FakeMemory()
    long = [{"content": "turn-body " * 80, "metadata": {"role": "user", "timestamp": "2026-01-01T00:00:00Z"}}] * 10
    mem.session_history = lambda *_a, **_k: list(long)  # type: ignore[method-assign]
    assembler = ContextAssembler(mem, FakeMem0())
    retrieved = asyncio.run(assembler.retrieve(AssembleRequest(
        user_message="continue",
        session_id="chat-long",
    )))
    assert retrieved.compaction_block
    assert "COMPACTED SESSION CONTEXT" in retrieved.compaction_block
    assert len(retrieved.session_recent) == 4


def test_retrieve_compaction_under_threshold(char_env):
    mem = FakeMemory()
    short = [{"content": "hi", "metadata": {"role": "user", "timestamp": "2026-01-01T00:00:00Z"}}] * 2
    mem.session_history = lambda *_a, **_k: list(short)  # type: ignore[method-assign]
    assembler = ContextAssembler(mem, FakeMem0())
    retrieved = asyncio.run(assembler.retrieve(AssembleRequest(
        user_message="hi",
        session_id="chat-short",
    )))
    assert retrieved.compaction_block is None
    assert len(retrieved.session_recent) == 2


def test_compose_block_order(char_env):
    assembler = ContextAssembler(FakeMemory(), FakeMem0())
    retrieved = RetrievedContext(
        user_message="owner-ask-zzz",
        query="owner-ask-zzz",
        briefing_block="BRIEFING-AAA",
        injected="INJECTED-BBB",
        inbox_block="INBOX-CCC",
        compaction_block="COMPACT-DDD",
        briefing_dict={"intent": "chat", "hint": ""},
        conv_mode="auto",
    )
    assembled = asyncio.run(assembler.compose(
        AssembleRequest(user_message="owner-ask-zzz", session_id="chat-plain", persona="PERSONA-P"),
        retrieved,
    ))
    text = assembled.user_content
    idx = [
        text.find("BRIEFING-AAA"),
        text.find("INJECTED-BBB"),
        text.find("INBOX-CCC"),
        text.find("COMPACT-DDD"),
        text.find("━━━ NEW REQUEST FROM OWNER ━━━"),
        text.find("owner-ask-zzz"),
    ]
    assert all(i >= 0 for i in idx), text[:800]
    assert idx == sorted(idx)
    assert assembled.system_prompt.startswith("PERSONA-P")
    assert isinstance(assembled, AssembledPrompt)


def test_compose_fail_open_when_awareness_raises(char_env, monkeypatch):
    async def _boom(*_a, **_k):
        raise RuntimeError("awareness down")

    monkeypatch.setattr("daedalus.awareness.build_project_awareness", _boom)
    assembler = ContextAssembler(FakeMemory(), FakeMem0())
    retrieved = RetrievedContext(
        user_message="just chatting",
        query="just chatting",
        briefing_dict={"intent": "chat", "hint": ""},
        conv_mode="auto",
    )
    assembled = asyncio.run(assembler.compose(
        AssembleRequest(user_message="just chatting", session_id="chat-plain", persona="p"),
        retrieved,
    ))
    assert "just chatting" in assembled.user_content
    assert "NEW REQUEST FROM OWNER" not in assembled.user_content


def test_compose_not_required_on_cheap_path(char_env, monkeypatch):
    cheap_hits: list[str] = []

    async def _cheap_complete(**_k):
        cheap_hits.append("mesh")
        return {"reply": "cheap-hello"}

    async def _compose_boom(self, *_a, **_k):
        raise AssertionError("compose must not run on cheap path")

    monkeypatch.setattr("daedalus.navigator.navigate", stub_navigate_chat)
    monkeypatch.setattr("daedalus.agent.run_native_discovery_pass", stub_native_pass)
    monkeypatch.setattr("daedalus.agent._call_claude_with_tools", stub_tools_quiet)
    monkeypatch.setattr("daedalus.llm_config.persona_via_master_enabled", lambda: False)
    monkeypatch.setattr("daedalus.llm_config.is_nvidia_free_only", lambda: False)
    monkeypatch.setattr("daedalus.mode.get_mode", lambda: "auto")
    monkeypatch.setattr("daedalus.daedalus_mesh.use_mesh_router", lambda: True)
    monkeypatch.setattr("daedalus.daedalus_mesh.complete", _cheap_complete)
    monkeypatch.setattr("daedalus.context_assembler.ContextAssembler.compose", _compose_boom)
    agent = bare_daedalus()
    monkeypatch.setattr("daedalus.agent._call_claude", ScriptedClaude(["full-path-should-not-run"]))
    try:
        out = asyncio.run(agent.chat(message="hello there friend", session_id="chat-plain"))
    finally:
        asyncio.run(drain_background(agent))
    assert cheap_hits == ["mesh"]
    assert out["reply"] == "cheap-hello"
    assert out["context"].get("routed_to") in {"mesh_router_chat", "chat_fast_path"}
    assert out["context"]["knowledge_used"] == 0


def test_native_tools_and_legacy_adapter_do_not_import_assembler():
    for rel in ("daedalus/native_tools.py", "daedalus/legacy_adapter.py"):
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "daedalus.context_assembler"
                    assert "context_assembler" not in alias.name
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module != "daedalus.context_assembler"
                assert not node.module.endswith(".context_assembler")
                assert "context_assembler" not in (node.module or "")
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    assert alias.name != "context_assembler"
                    assert alias.name != "ContextAssembler"


def test_agent_imports_assembler_and_reexports_formatter():
    src = (ROOT / "daedalus" / "agent.py").read_text(encoding="utf-8")
    assert "from .context_assembler import format_injected_context as _format_injected_context" in src
    assert "assembler.retrieve" in src
    assert "assembler.compose" in src
    from daedalus.agent import _format_injected_context
    assert _format_injected_context is format_injected_context


def test_import_assembler_does_not_load_agent():
    env_code = (
        "import daedalus.context_assembler as ca\n"
        "import sys\n"
        "bad = [n for n in sys.modules if n == 'daedalus.agent' or n.startswith('daedalus.agent.')]\n"
        "assert not bad, bad\n"
        "assert ca.FUSED_TOP_K == 14\n"
    )
    import os
    import subprocess

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
