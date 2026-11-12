"""OpenRouter :free preset — $0 models, no paid Claude leak on apply."""
from __future__ import annotations

import pytest


@pytest.fixture
def harness_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path / "projects"))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-not-real")
    monkeypatch.delenv("DAEDALUS_PROVIDER", raising=False)
    monkeypatch.delenv("DAEDALUS_MODEL", raising=False)
    monkeypatch.delenv("DAEDALUS_API_KEY", raising=False)
    monkeypatch.delenv("DAEDALUS_LOCAL_LLM", raising=False)
    monkeypatch.delenv("DAEDALUS_OFFLINE", raising=False)
    from daedalus.paths import data_root, playground_projects_dir
    data_root.cache_clear()
    playground_projects_dir.cache_clear()
    yield tmp_path
    data_root.cache_clear()
    playground_projects_dir.cache_clear()


def test_or_free_models_are_zero_dollar():
    from daedalus.llm_presets import get_preset, is_openrouter_free_id, list_presets

    p = get_preset("or_free")
    assert p is not None
    assert p["id"] == "or_free"
    for slot in ("master", "fast", "balanced", "deep"):
        assert p[slot]["provider"] == "openrouter"
        assert is_openrouter_free_id(p[slot]["model"])
        assert "claude" not in p[slot]["model"].lower()
        assert ":free" in p[slot]["model"] or p[slot]["model"] == "openrouter/free"
    assert any(row["id"] == "or_free" for row in list_presets())


def test_apply_or_free_pins_pipeline_slots_off_paid_defaults(harness_tmp):
    from daedalus.llm_config import get_config, openai_compat_headers
    from daedalus.llm_presets import apply_preset, is_openrouter_free_id
    from daedalus.llm_router_config import router_write_context
    from daedalus.workers import get_worker_config

    with router_write_context():
        out = apply_preset("or_free")
    assert out["ok"] is True
    assert out["preset_id"] == "or_free"

    cfg = get_config()
    assert cfg["provider"] == "openrouter"
    assert is_openrouter_free_id(cfg["model"])
    from daedalus.llm_config import OPENROUTER_DEFAULT_MODEL
    assert cfg["model"] != OPENROUTER_DEFAULT_MODEL

    wc = get_worker_config()
    for slot in ("fast", "balanced", "deep", "planner", "coder", "reviewer", "analysis", "claude"):
        assert wc[slot]["provider"] == "openrouter", slot
        assert is_openrouter_free_id(wc[slot]["model"]), (slot, wc[slot]["model"])
        assert "claude-sonnet" not in wc[slot]["model"]
        assert "claude-opus" not in wc[slot]["model"]

    headers = openai_compat_headers("openrouter")
    assert headers.get("X-Title") == "Daedalus"
    assert "HTTP-Referer" in headers
    assert openai_compat_headers("novita") == {}


def test_builder_coder_falls_back_when_deep_ungranted(harness_tmp):
    from daedalus.builder_runtime import builder_slot_for_role
    from daedalus.kernel.orchestrator import AgentRole
    from daedalus.outbox import WORKER_SLOT_PERMISSIONS

    assert builder_slot_for_role(AgentRole.CODER) == "coder"
    assert builder_slot_for_role(AgentRole.GAME_LOGIC) == "coder"
    assert builder_slot_for_role(AgentRole.REVIEWER) == "reviewer"
    assert builder_slot_for_role(AgentRole.PLANNER) == "balanced"
    assert "coder" not in WORKER_SLOT_PERMISSIONS
    assert "reviewer" not in WORKER_SLOT_PERMISSIONS


def test_static_scaffold_escapes_canvas_in_description(harness_tmp, tmp_path):
    from daedalus.playground import _write_scaffold

    dest = tmp_path / "esc"
    dest.mkdir()
    _write_scaffold(dest, "static", "X", "x", "Must have a <canvas> element.")
    html = (dest / "index.html").read_text(encoding="utf-8")
    assert "<canvas>" not in html.lower()
    assert "&lt;canvas&gt;" in html.lower()


def test_rate_limited_detects_openrouter_429():
    from daedalus.workers import _is_rate_limited

    class Fake:
        status_code = 429

        def __str__(self):
            return "Error"

    assert _is_rate_limited(Fake())
    assert _is_rate_limited(RuntimeError("HTTP Error 429: Too Many Requests"))
    assert not _is_rate_limited(RuntimeError("timeout"))


def test_extract_coder_files_accepts_html_fence():
    from daedalus.kernel.full_autopilot import extract_coder_files

    raw = "Here you go:\n```html\n<!DOCTYPE html>\n<html><body><canvas></canvas></body></html>\n```\n"
    files = extract_coder_files(raw, "demo")
    assert "index.html" in files
    assert "<canvas>" in files["index.html"]

    named = "```index.html\n<html><canvas id='g'></canvas></html>\n```"
    files = extract_coder_files(named, "demo")
    assert "<canvas id='g'>" in files["index.html"]

    blocks = "===FILE: index.html===\n<html>ok</html>\n===END==="
    files = extract_coder_files(blocks, "demo")
    assert files["index.html"] == "<html>ok</html>"


def test_or_free_in_preset_library():
    from daedalus.preset_library import CHOSEN_IDS, library

    assert "or_free" in CHOSEN_IDS
    ids = {row["id"] for row in library()}
    assert "or_free" in ids
    row = next(r for r in library() if r["id"] == "or_free")
    assert row["scores"]["economy"] >= 90


def test_call_with_tools_lives_outside_agent():
    """Kernel tool rounds must not require Chroma just to talk to OpenRouter."""
    from pathlib import Path

    import daedalus.llm_client as client
    assert callable(client.call_with_tools)
    src = Path("daedalus/agent.py").read_text(encoding="utf-8")
    assert "from .llm_client import call_with_tools" in src


def test_openrouter_tool_rounds_require_parameters():
    from daedalus.llm_config import openai_compat_extra_body

    assert openai_compat_extra_body("openrouter", with_tools=True) == {
        "usage": {"include": True},
        "provider": {"require_parameters": True},
    }
    assert openai_compat_extra_body("openrouter", with_tools=False) == {
        "usage": {"include": True},
    }
    assert openai_compat_extra_body("novita", with_tools=True) == {}
