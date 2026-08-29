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
    assert cfg["model"] != "anthropic/claude-sonnet-4-5"

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


def test_rate_limited_detects_openrouter_429():
    from daedalus.workers import _is_rate_limited

    class Fake:
        status_code = 429

        def __str__(self):
            return "Error"

    assert _is_rate_limited(Fake())
    assert _is_rate_limited(RuntimeError("HTTP Error 429: Too Many Requests"))
    assert not _is_rate_limited(RuntimeError("timeout"))


def test_or_free_in_preset_library():
    from daedalus.preset_library import CHOSEN_IDS, library

    assert "or_free" in CHOSEN_IDS
    ids = {row["id"] for row in library()}
    assert "or_free" in ids
    row = next(r for r in library() if r["id"] == "or_free")
    assert row["scores"]["economy"] >= 90
