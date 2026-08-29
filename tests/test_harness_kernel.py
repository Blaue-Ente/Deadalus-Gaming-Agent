"""Characterization for kernel plan steps 2–9 — no live LLM."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path / "projects"))
    monkeypatch.delenv("DAEDALUS_USE_ADN_DB", raising=False)
    from daedalus.paths import data_root, playground_projects_dir
    data_root.cache_clear()
    playground_projects_dir.cache_clear()
    from daedalus.store import reset_sqlite_pool
    reset_sqlite_pool()
    yield tmp_path
    reset_sqlite_pool()
    data_root.cache_clear()
    playground_projects_dir.cache_clear()


def test_marker_maps_to_native():
    from daedalus.tool_protocol import MARKER_TO_NATIVE, request_from_marker
    req = request_from_marker("NEED_WEB", "tetris sprites")
    assert req is not None
    assert req.name == "web_search"
    assert req.args["query"] == "tetris sprites"
    assert req.source == "marker"
    assert MARKER_TO_NATIVE["NEED_PLAYGROUND_WRITE"] == "write_file"
    assert request_from_marker("NEED_MADE_UP", "x") is None


def test_need_scan_write_and_web():
    from daedalus.need_scan import scan_need_markers
    raw = (
        "<NEED_WEB: tetris sprites>\n"
        "<NEED_PLAYGROUND_WRITE: demo | index.html | hello>>>"
    )
    hit = scan_need_markers(raw)
    assert hit.web_q == "tetris sprites"
    assert hit.pg_write_matches
    assert hit.pg_write_matches[0][0].strip() == "demo"
    assert hit.has_markers


def test_overlay_refuses_model_without_provider():
    from daedalus.model_router import overlay_effort
    cfg = {
        "provider": "openrouter",
        "model": "owner-model",
        "api_key": "k",
        "base_url": "https://openrouter.ai/api/v1",
        "sdk": "openai",
    }
    out = overlay_effort(cfg, {"model": "llama3:8b", "tier": "epic", "model_source": "wardrobe-role"})
    assert out["model"] == "owner-model"
    assert out["provider"] == "openrouter"
    assert out["effort_skipped"] == "model-without-provider"


def test_overlay_same_model_ok():
    from daedalus.model_router import overlay_effort
    cfg = {"provider": "openrouter", "model": "x", "api_key": "k", "base_url": "https://x", "sdk": "openai"}
    out = overlay_effort(cfg, {"model": "x", "tier": "whisper", "model_source": "cheap"})
    assert out["model"] == "x"
    assert "effort_skipped" not in out
    assert out["effort"] == "whisper"


def test_overlay_atomic_when_provider_present(monkeypatch):
    from daedalus.model_router import ModelRoute, overlay_effort

    def fake_route(provider, model, *, source):
        return ModelRoute(
            provider=provider,
            model=model,
            base_url="http://127.0.0.1:11434/v1",
            api_key="",
            sdk="openai",
            local=True,
            source=source,
        )

    monkeypatch.setattr("daedalus.model_router._route_for_provider", fake_route)
    cfg = {
        "provider": "openrouter",
        "model": "cloud-model",
        "api_key": "secret",
        "base_url": "https://openrouter.ai/api/v1",
        "sdk": "openai",
    }
    out = overlay_effort(
        cfg,
        {"model": "qwen2.5-coder:7b", "provider": "ollama", "tier": "epic", "model_source": "wardrobe-role"},
    )
    assert out["provider"] == "ollama"
    assert out["model"] == "qwen2.5-coder:7b"
    assert out["api_key"] == ""
    assert out["base_url"] == "http://127.0.0.1:11434/v1"
    assert "effort_skipped" not in out


def test_resolve_model_registry_includes_provider(monkeypatch):
    from daedalus.task_effort import resolve_model

    monkeypatch.setattr("daedalus.llm_wardrobe.wants_local", lambda: False)
    monkeypatch.setattr(
        "daedalus.model_registry.select_for_task",
        lambda *a, **k: {"model_id": "quality-coder", "provider": "openrouter"},
    )
    out = resolve_model({"apply_model": True, "tier": "epic", "role": "reason"})
    assert out["model"] == "quality-coder"
    assert out["provider"] == "openrouter"


def test_run_engine_checkpoint_roundtrip(isolated):
    from daedalus.run_engine import (
        append_event,
        checkpoint_for_slug,
        load_checkpoint,
        load_events,
        save_checkpoint,
    )
    append_event("job-abc", "phase", {"n": 2, "label": "Строене"})
    save_checkpoint("job-abc", {"stage": 3, "plan": {"title": "x"}}, slug="snake")
    ckpt = load_checkpoint("job-abc")
    assert ckpt is not None
    assert ckpt["stage"] == 3
    assert ckpt["run_id"] == "job-abc"
    events = load_events("job-abc")
    assert events[-1]["event"] == "phase"
    pointed = checkpoint_for_slug("snake")
    assert pointed is not None
    assert pointed["stage"] == 3


def test_skip_is_not_ok():
    from daedalus.game_build_contract import skip_is_not_ok, verified
    skipped = skip_is_not_ok({"ok": True, "skipped": True})
    assert skipped["ok"] is False
    assert verified({"ok": True, "skipped": True}) is False
    assert verified({"ok": True, "skipped": False}) is True
    assert verified({"ok": False}) is False


def test_game_agent_bench_not_allowed():
    from daedalus.game_agent_bench import allowed, missing_gates, status
    st = status()
    assert st["allowed"] is False
    assert allowed() is False
    missing = missing_gates()
    assert "one_builder_pipeline" in missing
    assert "playwright_gameplay" in missing
    assert "uv_lock" in missing
    assert "owner_token" not in missing


def test_wiring_in_source():
    agent = (ROOT / "daedalus" / "agent.py").read_text(encoding="utf-8")
    assert "scan_need_markers" in agent
    assert "max_rounds=8" in agent
    cfg = (ROOT / "daedalus" / "llm_config.py").read_text(encoding="utf-8")
    assert "overlay_effort" in cfg
    kernel = (ROOT / "daedalus" / "kernel" / "full_autopilot.py").read_text(encoding="utf-8")
    assert "or bresult.get(\"skipped\")" not in kernel
    assert "skip_is_not_ok" in kernel
    assert "resume_from_stage" in kernel
    js = (ROOT / "daedalus" / "playground_game_scaffold.py").read_text(encoding="utf-8")
    assert "/api/v1/playground/projects/${slug}/assets/generate" in js
    ci = (ROOT / ".gitlab-ci.yml").read_text(encoding="utf-8")
    assert "ruff check" in ci
    jobs = (ROOT / "daedalus" / "autopilot_jobs.py").read_text(encoding="utf-8")
    assert "започни наново" not in jobs
    assert "checkpoint" in jobs


def test_quality_off_by_default(monkeypatch):
    monkeypatch.delenv("DAEDALUS_QUALITY", raising=False)
    from daedalus.game_build_contract import quality_enabled
    assert quality_enabled() is False
    monkeypatch.setenv("DAEDALUS_QUALITY", "1")
    assert quality_enabled() is True


def test_studio_asset_generate_route(isolated, monkeypatch):
    async def fake_gen(slug, **kwargs):
        return {"path": kwargs.get("path") or "assets/star.png", "bytes": 12, "prompt": kwargs.get("prompt")}

    monkeypatch.setattr("daedalus.playground_assets.generate_asset", fake_gen)
    from app.main import app
    client = TestClient(app)
    created = client.post(
        "/api/v1/playground/projects",
        json={"name": "Star", "kind": "static", "description": "test"},
    )
    assert created.status_code == 200
    slug = created.json()["project"]["slug"]
    missing = client.post(
        "/api/v1/playground/projects/no-such-game/assets/generate",
        json={"prompt": "star", "path": "assets/star.png"},
    )
    assert missing.status_code == 404
    res = client.post(
        f"/api/v1/playground/projects/{slug}/assets/generate",
        json={"prompt": "pixel star", "path": "assets/star.png"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["bytes"] == 12
    caps = client.get("/capabilities")
    names = caps.json()["capabilities"]
    assert "typed-tools" in names
    assert "atomic-router" in names
    assert "durable-runs" in names
    auto = client.get(f"/api/v1/playground/projects/{slug}/autopilot")
    assert auto.status_code == 200
    assert "checkpoint" in auto.json()
