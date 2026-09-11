"""Shared fakes for P0.01 characterization tests. No live LLM, no Chroma."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from daedalus.navigator_vocab import Briefing


PLAYGROUND_SID = "playground:char-01"

CHAT_RETURN_KEYS = (
    "agent",
    "session_id",
    "for_self",
    "reply",
    "context",
    "pending_proposal",
    "task_events",
    "model",
    "effort",
    "elapsed_seconds",
    "timestamp",
    "complete",
    "dod",
    "run_budget",
)


class FakeMemory:
    def __init__(self) -> None:
        self.turns: list[tuple[str, str, str]] = []
        self.discoveries: list[dict[str, Any]] = []

    def find_similar_artifacts(self, *_a, **_k) -> list:
        return []

    def session_history(self, *_a, **_k) -> list:
        return []

    def retrieve_episodes(self, *_a, **_k) -> list:
        return []

    def capture_turn(self, session_id: str, role: str, content: str, **_k) -> None:
        self.turns.append((session_id, role, content))

    def capture_artifact(self, **_k) -> None:
        return None

    def store_discovery(self, **kwargs) -> None:
        self.discoveries.append(kwargs)

    def stats(self) -> dict[str, Any]:
        return {"fake": True, "turns": len(self.turns)}

    def clear_session(self, session_id: str) -> int:
        n = sum(1 for s, _r, _c in self.turns if s == session_id)
        self.turns = [t for t in self.turns if t[0] != session_id]
        return n


class FakeMem0:
    def __init__(self) -> None:
        self.captured: list[tuple[str, str, str]] = []

    def fused_context(self, *_a, **_k) -> list:
        return []

    def capture(self, session_id: str, role: str, content: str, **_k) -> None:
        self.captured.append((session_id, role, content))

    def attach_llm_extractor(self, *_a, **_k) -> None:
        return None

    def stats(self) -> dict[str, Any]:
        return {"fake": True}


class ScriptedClaude:
    """Queue of `_call_claude` replies. An Exception item is raised."""

    def __init__(self, replies: list[Any] | None = None) -> None:
        self.replies = list(replies or [])
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, system: str, messages: list, **kwargs) -> str:
        self.calls.append({"system": system, "messages": list(messages), "kwargs": dict(kwargs)})
        if not self.replies:
            return "Готово."
        item = self.replies.pop(0)
        if isinstance(item, BaseException):
            raise item
        return str(item)

    def user_blobs(self) -> list[str]:
        out: list[str] = []
        for call in self.calls:
            for msg in call["messages"]:
                c = msg.get("content")
                if isinstance(c, str):
                    out.append(c)
                elif isinstance(c, list):
                    out.append(
                        "\n".join(
                            str(b.get("text") or "")
                            for b in c
                            if isinstance(b, dict)
                        )
                    )
        return out


class ScriptedTools:
    """Replays native-tool rounds for `run_native_discovery_pass`."""

    def __init__(self, rounds: list[list[dict]]) -> None:
        self.rounds = list(rounds)
        self.seen: list[list[dict]] = []

    async def __call__(self, system, messages, tools):
        self.seen.append([dict(m) for m in messages])
        if not self.rounds:
            return {"stop_reason": "end_turn", "content": [_text("Готово.")]}
        return {"stop_reason": "tool_use", "content": self.rounds.pop(0)}


def _text(t: str) -> dict:
    return {"type": "text", "text": t}


def _use(tool: str, **args) -> dict:
    return {"type": "tool_use", "id": f"call_{tool}_{len(str(args))}", "name": tool, "input": args}


def briefing(*, intent: str = "code_change", tools: list[str] | None = None, **kw) -> Briefing:
    return Briefing(
        intent=intent,
        tools=list(tools or []),
        budget=kw.pop("budget", "normal"),
        hint=kw.pop("hint", "characterization"),
        fast_path=True,
        **kw,
    )


async def stub_navigate_build(*_a, **_k) -> Briefing:
    return briefing(intent="code_change", tools=["write_file"])


async def stub_navigate_chat(*_a, **_k) -> Briefing:
    return briefing(intent="chat", tools=[], budget="short", hint="smalltalk")


async def stub_native_pass(*_a, **_k):
    return None, []


async def stub_tools_quiet(system, messages, tools):
    return {"stop_reason": "end_turn", "content": [_text("ok")]}


def isolate_paths(tmp_path, monkeypatch) -> None:
    """Point data / playground / kv at tmp. Safe for modules already imported."""
    data = tmp_path / "data"
    projects = tmp_path / "projects"
    data.mkdir(parents=True, exist_ok=True)
    projects.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(data))
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(projects))
    monkeypatch.setenv("DAEDALUS_MENTOR", "0")
    monkeypatch.setenv("DAEDALUS_VISUAL_PROOF", "0")
    monkeypatch.setenv("DAEDALUS_RUN_BUDGET", "off")
    monkeypatch.setenv("DAEDALUS_NATIVE_TOOLS", "1")
    monkeypatch.delenv("DAEDALUS_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("ALLTOKEN_API_KEY", raising=False)
    monkeypatch.delenv("NOVITA_API_KEY", raising=False)

    from daedalus.paths import data_root, playground_projects_dir, product_root, repo_root
    data_root.cache_clear()
    playground_projects_dir.cache_clear()
    product_root.cache_clear()
    repo_root.cache_clear()

    from daedalus.store import reset_sqlite_pool
    reset_sqlite_pool()

    import daedalus.playground as pg
    pg.PROJECTS_DIR = playground_projects_dir()

    import daedalus.self_improve as si
    si.DATA_ROOT = data
    si.PROPOSALS_DIR = data / "proposals"
    si.BACKUPS_DIR = data / "backups"
    si.SKILLS_FILE = data / "skills.json"
    si.AUDIT_LOG = data / "self_improve_audit.log"
    si.PROPOSALS_DIR.mkdir(parents=True, exist_ok=True)
    si.BACKUPS_DIR.mkdir(parents=True, exist_ok=True)

    import daedalus.task_state as ts
    ts._DATA_ROOT = data
    ts._STATE_FILE = data / "task-state.json"


def rebind_self_improve_roots(tmp_path, monkeypatch) -> None:
    """Approve/propose write under tmp, never the real checkout."""
    import daedalus.self_improve as si

    root = tmp_path / "proposal-root"
    (root / "tests").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(si, "PROJECT_ROOT", root)
    monkeypatch.setattr(si, "_GIT_ROOT", root)

    async def _no_snap(_pid: str) -> dict:
        return {"ok": True, "sha": None, "error": None}

    def _no_snap_sync(pid: str) -> dict:
        return {"ok": True, "sha": None, "error": None}

    monkeypatch.setattr(si, "_git_snapshot", _no_snap_sync)


def bare_daedalus():
    """`Daedalus` without singleton / Chroma / knowledge load."""
    from daedalus.agent import Daedalus

    agent = object.__new__(Daedalus)
    agent.memory = FakeMemory()
    agent.knowledge = SimpleNamespace(load_all=lambda *a, **k: {})
    agent.mem0 = FakeMem0()
    agent._knowledge_loaded = True
    agent.persona = "characterization persona"
    agent.name = "Daedalus"
    agent._background_tasks = set()
    return agent


async def drain_background(agent) -> None:
    tasks = list(getattr(agent, "_background_tasks", ()) or ())
    for t in tasks:
        t.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def user_text_from_messages(messages: list[dict]) -> str:
    bits: list[str] = []
    for m in messages:
        c = m.get("content")
        if isinstance(c, str):
            bits.append(c)
        elif isinstance(c, list):
            bits.append(
                "\n".join(str(b.get("text") or "") for b in c if isinstance(b, dict))
            )
    return "\n".join(bits)
