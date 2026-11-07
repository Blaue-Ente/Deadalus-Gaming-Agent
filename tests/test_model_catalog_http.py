"""Catalog HTTP routes never write the brain and never call a live provider."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

SECRET = "SECRET-KEY-SHOULD-NOT-LEAK"


def _walk_no_key(value):
    if isinstance(value, dict):
        assert "api_key" not in value
        for item in value.values():
            _walk_no_key(item)
    elif isinstance(value, list):
        for item in value:
            _walk_no_key(item)


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path / "projects"))
    monkeypatch.setenv("DAEDALUS_CATALOG_NETWORK", "0")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("DAEDALUS_API_KEY", raising=False)
    monkeypatch.delenv("DAEDALUS_MENTOR_MODEL", raising=False)
    from daedalus.paths import data_root, playground_projects_dir
    data_root.cache_clear()
    playground_projects_dir.cache_clear()
    from daedalus.model_catalog import reset_state
    reset_state()
    state = {
        "brain": {
            "provider": "openrouter",
            "model": "saved/missing",
            "base_url": "https://openrouter.ai/api/v1?api_key=" + SECRET,
            "api_key": SECRET,
            "source": "db",
        },
        "saves": 0,
        "mentors": 0,
    }
    monkeypatch.setattr("daedalus.llm_config.get_brain_config", lambda: dict(state["brain"]))
    monkeypatch.setattr("daedalus.mentor_helm.get_choice", lambda: {
        "id": "custom", "provider": "openrouter", "model": "mentor/missing",
    })

    def save(*_args, **_kwargs):
        state["saves"] += 1
        raise AssertionError("set_config")

    def mentor(*_args, **_kwargs):
        state["mentors"] += 1
        raise AssertionError("set_choice")

    monkeypatch.setattr("daedalus.llm_config.set_config", save)
    monkeypatch.setattr("daedalus.mentor_helm.set_choice", mentor)
    yield state
    data_root.cache_clear()
    reset_state()


def test_catalog_requires_owner_token(isolated, raw_http):
    from app.main import app
    client = TestClient(app)
    denied_get = client.get("/api/v1/llm/catalog")
    denied_post = client.post("/api/v1/llm/catalog/refresh", json={"api_key": SECRET, "provider": "ollama"})
    assert denied_get.status_code == 401
    assert denied_post.status_code == 401
    assert SECRET not in denied_get.text
    assert SECRET not in denied_post.text
    assert isolated["saves"] == 0
    assert isolated["mentors"] == 0


def test_refresh_uses_effective_provider_and_hides_secrets(isolated, monkeypatch):
    from daedalus.model_catalog import cache_file
    from app.main import app
    seen = []

    def fetcher(provider, base):
        seen.append((provider, base))
        return [{"id": "from-effective", "name": "Effective"}]

    real = __import__("daedalus.model_catalog", fromlist=["refresh_catalog"]).refresh_catalog

    def wrapped(**kwargs):
        kwargs["fetcher"] = fetcher
        return real(**kwargs)

    monkeypatch.setattr("daedalus.model_catalog.refresh_catalog", wrapped)
    client = TestClient(app)
    res = client.post(
        "/api/v1/llm/catalog/refresh",
        json={"provider": "ollama", "api_key": SECRET, "model": "should-not-save"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["wrote_success"] is True
    assert seen == [("openrouter", "https://openrouter.ai/api/v1?api_key=" + SECRET)]
    assert body["last_success"]["models"][0]["id"] == "from-effective"
    assert body["scope"]["base_url"] == "https://openrouter.ai/v1"
    assert SECRET not in res.text
    _walk_no_key(body)
    assert SECRET not in cache_file().read_text(encoding="utf-8")
    assert isolated["saves"] == 0
    assert isolated["mentors"] == 0


def test_failed_refresh_preserves_cache_and_manual_only_skips_dispatch(isolated, monkeypatch):
    from daedalus.model_catalog import cache_file, refresh_catalog
    from app.main import app

    def good(_provider, _base):
        return [{"id": "kept", "name": "Kept"}]

    monkeypatch.setattr(
        "daedalus.model_catalog.refresh_catalog",
        lambda **kwargs: refresh_catalog(fetcher=good),
    )
    client = TestClient(app)
    first = client.post("/api/v1/llm/catalog/refresh")
    assert first.status_code == 200
    assert first.json()["last_success"]["models"][0]["id"] == "kept"
    snapshot = cache_file().read_text(encoding="utf-8")

    def bad(_provider, _base):
        raise RuntimeError("HTTP down " + SECRET)

    monkeypatch.setattr(
        "daedalus.model_catalog.refresh_catalog",
        lambda **kwargs: refresh_catalog(fetcher=bad),
    )
    second = client.post("/api/v1/llm/catalog/refresh", json={"api_key": SECRET})
    assert second.status_code == 200
    body = second.json()
    assert body["ok"] is False
    assert body["last_success"]["models"][0]["id"] == "kept"
    assert body["catalog_outdated"] is True
    assert SECRET not in second.text
    _walk_no_key(body)
    assert "kept" in cache_file().read_text(encoding="utf-8")

    calls = {"n": 0}

    def dispatch(*_args, **_kwargs):
        calls["n"] += 1
        raise AssertionError("dispatched")

    monkeypatch.setattr("daedalus.model_catalog._dispatch", dispatch)
    monkeypatch.setattr("daedalus.model_catalog.refresh_catalog", refresh_catalog)
    isolated["brain"]["provider"] = "nvidia"
    isolated["brain"]["base_url"] = "https://integrate.api.nvidia.com/v1"
    manual = client.post("/api/v1/llm/catalog/refresh", json={"provider": "openrouter", "api_key": SECRET})
    assert manual.status_code == 200
    assert manual.json()["reason"] == "unsupported_provider"
    assert manual.json()["mode"] == "manual_only"
    assert calls["n"] == 0
    assert SECRET not in manual.text
    listed = client.get("/api/v1/llm/catalog")
    assert listed.status_code == 200
    view = listed.json()
    assert view["mode"] == "manual_only"
    assert view["effective_selection"]["model"] == "saved/missing"
    _walk_no_key(view)
    assert isolated["saves"] == 0
    assert isolated["mentors"] == 0
    assert snapshot
