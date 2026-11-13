"""No model set up yet is an empty state, not an error (PR1, 2026-11-13): the mentor catalog still lists its entries,
offline mode gives the previous backend back, a dead local server is not "ready", and worker-config and llm/status
answer 200. Nothing here calls the network: the local probe and the provider lookups are monkeypatched."""
from __future__ import annotations

import pytest

from daedalus import llm_config, llm_wardrobe, local_llm, mentor_helm, onboarding


def _no_provider(*_a, **_k):
    raise RuntimeError("No LLM provider configured.")


@pytest.fixture
def no_provider(monkeypatch):
    monkeypatch.setattr(llm_config, "get_config", _no_provider)
    monkeypatch.setattr(llm_config, "get_brain_config", lambda: {"provider": "", "model": ""})
    monkeypatch.setattr(local_llm, "probe_endpoint", lambda *a, **k: {"ok": False, "models": [], "error": "off"})


@pytest.fixture
def wardrobe(monkeypatch, tmp_path):
    path = tmp_path / "wardrobe.json"
    monkeypatch.setattr(llm_wardrobe, "_path", lambda: path)
    return path


def test_the_mentor_catalog_lists_every_entry_with_no_provider(no_provider):
    rows = mentor_helm.catalog_public()
    assert rows, "the catalog must never be empty because the brain is unset"
    assert len(rows) == len(mentor_helm.CATALOG)
    assert all("available" in r for r in rows)
    assert mentor_helm._brain_ready() is False


def test_offline_on_then_off_restores_the_previous_backend(wardrobe):
    llm_wardrobe.save({"backend": "cloud", "active": None, "offline": False, "roles": {}})
    llm_wardrobe.set_offline(True)
    assert llm_wardrobe.load()["backend"] == "ollama" and llm_wardrobe.load()["offline"] is True
    out = llm_wardrobe.set_offline(False)
    assert out["backend"] == "cloud" and llm_wardrobe.load()["offline"] is False
    assert "backend_before_offline" not in llm_wardrobe.load()


def test_offline_off_without_a_saved_backend_falls_back_to_cloud(wardrobe):
    llm_wardrobe.save({"backend": "ollama", "active": None, "offline": True, "roles": {}})
    llm_wardrobe.set_offline(False)
    assert llm_wardrobe.load()["backend"] == "cloud"


def test_a_fresh_wardrobe_goes_offline_and_back_to_auto(wardrobe):
    llm_wardrobe.set_offline(True)
    assert llm_wardrobe.load()["backend"] == "ollama"
    llm_wardrobe.set_offline(False)
    assert llm_wardrobe.load()["backend"] == "auto"


def test_offline_keeps_an_explicit_backend_it_did_not_replace(wardrobe):
    llm_wardrobe.save({"backend": "openrouter", "active": None, "offline": False, "roles": {}})
    llm_wardrobe.set_offline(True)
    assert llm_wardrobe.load()["backend"] == "openrouter"
    llm_wardrobe.set_offline(False)
    assert llm_wardrobe.load()["backend"] == "openrouter"


def test_a_local_brain_that_does_not_answer_is_not_ok(monkeypatch):
    monkeypatch.setattr(llm_config, "get_brain_config",
                        lambda: {"provider": "ollama", "model": "qwen3-coder:30b", "local": True})
    monkeypatch.setattr(local_llm, "probe_endpoint", lambda *a, **k: {"ok": False, "models": [], "error": "refused"})
    brain = onboarding._brain()
    assert brain["ok"] is False and brain["reachable"] is False and brain["local"] is True


def test_a_local_brain_that_answers_is_ok(monkeypatch):
    monkeypatch.setattr(llm_config, "get_brain_config",
                        lambda: {"provider": "ollama", "model": "qwen3-coder:30b", "local": True})
    monkeypatch.setattr(local_llm, "probe_endpoint", lambda *a, **k: {"ok": True, "models": ["qwen3-coder:30b"]})
    brain = onboarding._brain()
    assert brain["ok"] is True and brain["reachable"] is True


def test_a_cloud_brain_is_not_probed(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("a cloud brain must not probe a local server")
    monkeypatch.setattr(llm_config, "get_brain_config",
                        lambda: {"provider": "openrouter", "model": "anthropic/claude-haiku-5.5", "api_key": "k"})
    monkeypatch.setattr(local_llm, "probe_endpoint", boom)
    brain = onboarding._brain()
    assert brain["ok"] is True and "reachable" not in brain


def test_the_onboarding_text_for_a_down_server_exists_in_both_languages():
    from tests.test_onboarding import _strings
    assert "onb.brainDown" in _strings("en") and "onb.brainDown" in _strings("bg")


def test_worker_config_is_200_with_no_provider(no_provider):
    from fastapi.testclient import TestClient

    from app.main import app

    res = TestClient(app).get("/admin/daedalus/worker-config")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True and body["configured"] is False
    assert isinstance(body["slots"], dict) and isinstance(body["presets"], (list, dict))


def test_llm_status_is_200_and_not_configured_with_no_provider(no_provider):
    from fastapi.testclient import TestClient

    from app.main import app

    res = TestClient(app).get("/api/v1/llm/status")
    assert res.status_code == 200
    body = res.json()
    assert body == {"ok": True, "configured": False, "reason": "No LLM provider is configured."}
