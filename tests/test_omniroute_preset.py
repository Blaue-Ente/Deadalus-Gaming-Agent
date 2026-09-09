"""OmniRoute sidecar — $0 auto combos, no vendored gateway, no paid leak."""
from __future__ import annotations

import pytest


@pytest.fixture
def harness_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path / "projects"))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("ALLTOKEN_API_KEY", raising=False)
    monkeypatch.delenv("NOVITA_API_KEY", raising=False)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.delenv("DAEDALUS_PROVIDER", raising=False)
    monkeypatch.delenv("DAEDALUS_MODEL", raising=False)
    monkeypatch.delenv("DAEDALUS_API_KEY", raising=False)
    monkeypatch.delenv("DAEDALUS_LOCAL_LLM", raising=False)
    monkeypatch.delenv("DAEDALUS_OFFLINE", raising=False)
    monkeypatch.delenv("OMNIROUTE_API_KEY", raising=False)
    from daedalus.paths import data_root, playground_projects_dir
    data_root.cache_clear()
    playground_projects_dir.cache_clear()
    yield tmp_path
    data_root.cache_clear()
    playground_projects_dir.cache_clear()


def test_omniroute_is_loopback_not_ollama():
    from daedalus.llm_config import is_loopback_gateway, PROVIDER_DEFAULTS
    from daedalus.local_llm import is_local_provider

    assert is_loopback_gateway("omniroute")
    assert not is_local_provider("omniroute")
    pd = PROVIDER_DEFAULTS["omniroute"]
    assert pd["base_url"] == "http://127.0.0.1:20128/v1"
    assert pd["default_model"] == "auto"
    assert pd["sdk"] == "openai"


def test_omniroute_preset_uses_auto_channels():
    from daedalus.llm_presets import get_preset, is_omniroute_auto_id, list_presets

    p = get_preset("omniroute")
    assert p is not None
    for slot in ("master", "fast", "balanced", "deep"):
        assert p[slot]["provider"] == "omniroute"
        assert is_omniroute_auto_id(p[slot]["model"])
    assert p["fast"]["model"] == "auto/fast"
    assert p["deep"]["model"] == "auto/coding"
    assert any(row["id"] == "omniroute" for row in list_presets())


def test_apply_omniroute_needs_no_cloud_key(harness_tmp):
    from daedalus.llm_config import get_config
    from daedalus.llm_presets import apply_preset, is_omniroute_auto_id
    from daedalus.llm_router_config import router_write_context
    from daedalus.workers import get_worker_config

    with router_write_context():
        out = apply_preset("omniroute")
    assert out["ok"] is True
    assert out["preset_id"] == "omniroute"

    cfg = get_config()
    assert cfg["provider"] == "omniroute"
    assert cfg["model"] == "auto"
    assert cfg["base_url"].endswith(":20128/v1")
    assert cfg["api_key"]

    wc = get_worker_config()
    for slot in ("fast", "balanced", "deep", "planner", "coder", "reviewer", "analysis", "claude"):
        assert wc[slot]["provider"] == "omniroute", slot
        assert is_omniroute_auto_id(wc[slot]["model"]), (slot, wc[slot]["model"])
    assert wc["coder"]["model"] == "auto/coding"
    assert wc["fast"]["model"] == "auto/fast"


def test_env_omniroute_without_key(harness_tmp, monkeypatch):
    monkeypatch.setenv("DAEDALUS_PROVIDER", "omniroute")
    from daedalus.llm_config import get_config
    from daedalus.workers import get_worker_config

    cfg = get_config()
    assert cfg["provider"] == "omniroute"
    assert cfg["model"] == "auto"
    assert cfg["source"] == "env_loopback"

    wc = get_worker_config()
    assert all(sc["provider"] == "omniroute" for sc in wc.values())


def test_mesh_does_not_intercept_omniroute():
    from daedalus.daedalus_mesh import _MESH_NATIVE_PROVIDERS, should_route_worker_via_mesh

    assert "omniroute" not in _MESH_NATIVE_PROVIDERS
    assert should_route_worker_via_mesh("omniroute", "auto") is False


def test_omniroute_in_preset_library():
    from daedalus.preset_library import CHOSEN_IDS, library

    assert "omniroute" in CHOSEN_IDS
    row = next(r for r in library() if r["id"] == "omniroute")
    assert row["scores"]["economy"] >= 90


def test_omniroute_spend_envelope_does_not_block(monkeypatch):
    import daedalus.run_budget as rb

    monkeypatch.setattr(rb, "exhausted", lambda: True)
    assert rb.would_block("omniroute") is False
    assert rb.would_block("openrouter") is True
