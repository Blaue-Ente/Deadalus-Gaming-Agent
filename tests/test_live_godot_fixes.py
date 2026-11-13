"""Two faults the live Godot test found on Windows (owner 2026-11-13)."""
from __future__ import annotations

from types import SimpleNamespace


def test_the_bridge_keeps_answering_while_the_game_pauses_its_tree():
    """A start menu that pauses the tree paused the bridge too: Daedalus saw pong null and no nodes."""
    from daedalus.godot_bridge import BRIDGE_GDSCRIPT

    ready = BRIDGE_GDSCRIPT.split("func _ready() -> void:", 1)[1].split("\nfunc ", 1)[0]
    assert "process_mode = Node.PROCESS_MODE_ALWAYS" in ready
    assert ready.index("PROCESS_MODE_ALWAYS") < ready.index("get_cmdline_user_args")


def test_a_writer_tool_round_reaches_the_run_ledger(monkeypatch):
    """The writer's tool rounds were billed but never traced: 11 files written showed as 3 calls and $0.02."""
    from daedalus import llm_client, run_budget
    from daedalus.harness import gateway, runs

    seen: list[dict] = []
    monkeypatch.setattr(run_budget, "note_usage", lambda *a, **k: None)
    monkeypatch.setattr(runs, "current_run_id", lambda: "run-7")
    gateway.add_trace_sink(seen.append)
    try:
        usage = SimpleNamespace(prompt_tokens=1200, completion_tokens=800, cost=0.0042)
        llm_client._meter(SimpleNamespace(usage=usage), model="anthropic/claude-haiku-5.5", role="writer",
                          intent="tools", provider="openrouter")
    finally:
        gateway.remove_trace_sink(seen.append)
    assert len(seen) == 1
    ev = seen[0]
    assert (ev["intent"], ev["provider"], ev["model"], ev["run_id"]) == (
        "tools", "openrouter", "anthropic/claude-haiku-5.5", "run-7")
    assert ev["prompt_tokens"] == 1200 and ev["completion_tokens"] == 800


def test_a_broken_trace_never_fails_a_billed_call(monkeypatch):
    from daedalus import llm_client, run_budget
    from daedalus.harness import gateway

    monkeypatch.setattr(run_budget, "note_usage", lambda *a, **k: None)
    monkeypatch.setattr(gateway, "_emit", lambda ev: (_ for _ in ()).throw(RuntimeError("sink down")))
    parsed = llm_client._meter(SimpleNamespace(usage=None), model="m", role="writer")
    assert isinstance(parsed, dict)
