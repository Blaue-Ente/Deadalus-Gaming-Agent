"""Provider catalog cache: fixtures only, no live provider sockets."""
from __future__ import annotations

import json
from datetime import timedelta, timezone

import httpx
import pytest

SECRET = "SECRET-KEY-SHOULD-NOT-LEAK"
NOW = __import__("datetime").datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)


def _walk_no_key(value):
    if isinstance(value, dict):
        assert "api_key" not in value
        for item in value.values():
            _walk_no_key(item)
    elif isinstance(value, list):
        for item in value:
            _walk_no_key(item)


def _clean(payload, path):
    _walk_no_key(payload)
    blob = json.dumps(payload)
    assert SECRET not in blob
    if path.is_file():
        text = path.read_text(encoding="utf-8")
        assert SECRET not in text
        assert "api_key" not in text
        assert not (path.parent / ".llm_models.json.tmp").exists()


@pytest.fixture
def catalog_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("DAEDALUS_CATALOG_NETWORK", "0")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("DAEDALUS_API_KEY", raising=False)
    monkeypatch.delenv("DAEDALUS_MENTOR_MODEL", raising=False)
    monkeypatch.delenv("DAEDALUS_MENTOR_PROVIDER", raising=False)
    from daedalus.paths import data_root
    data_root.cache_clear()
    from daedalus import model_catalog as mc
    mc.reset_state()
    state = {
        "brain": {
            "provider": "openrouter",
            "model": "saved/missing",
            "base_url": "https://openrouter.ai/api/v1",
            "api_key": SECRET,
            "source": "db",
        },
        "mentor": {"id": "custom", "provider": "openrouter", "model": "mentor/missing"},
    }

    def brain():
        return dict(state["brain"])

    def choice():
        return dict(state["mentor"])

    def forbid_config(*_args, **_kwargs):
        raise AssertionError("get_config")

    def forbid_save(*_args, **_kwargs):
        raise AssertionError("set_config")

    def forbid_mentor(*_args, **_kwargs):
        raise AssertionError("set_choice")

    monkeypatch.setattr("daedalus.llm_config.get_brain_config", brain)
    monkeypatch.setattr("daedalus.llm_config.get_config", forbid_config)
    monkeypatch.setattr("daedalus.llm_config.set_config", forbid_save)
    monkeypatch.setattr("daedalus.mentor_helm.get_choice", choice)
    monkeypatch.setattr("daedalus.mentor_helm.set_choice", forbid_mentor)
    yield state
    data_root.cache_clear()
    mc.reset_state()


def test_scope_drops_query_secrets():
    from daedalus.model_catalog import normalize_base_url, scope_key
    raw = "https://user:" + SECRET + "@openrouter.ai/api/v1/models?api_key=" + SECRET
    assert normalize_base_url(raw) == "https://openrouter.ai/v1"
    assert SECRET not in scope_key("openrouter", raw)
    assert scope_key("OpenRouter", "https://openrouter.ai/api/v1") == "openrouter:https://openrouter.ai/v1"


def test_openrouter_fixture_updates_success_and_keeps_key_off_disk(catalog_env, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", SECRET)
    from daedalus.model_catalog import OPENROUTER_MODELS_URL, cache_file, refresh_catalog
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert str(request.url) == OPENROUTER_MODELS_URL
        assert request.headers["authorization"] == "Bearer " + SECRET
        assert request.headers["http-referer"]
        return httpx.Response(200, json={"data": [{"id": "a/b", "name": "AB"}, {"id": "a/b", "name": "dup"}]})

    out = refresh_catalog(now=NOW, transport=httpx.MockTransport(handler))
    assert out["ok"] is True
    assert out["wrote_success"] is True
    assert out["last_attempt"]["ok"] is True
    assert out["last_success"]["models"] == [{"id": "a/b", "name": "AB"}]
    assert out["last_success"]["count"] == 1
    assert len(seen) == 1
    assert cache_file().is_file()
    _clean(out, cache_file())


def test_redirect_is_not_followed_and_keeps_success(catalog_env):
    from daedalus.model_catalog import CatalogError, cache_file, refresh_catalog
    refresh_catalog(now=NOW, fetcher=lambda *_a: [{"id": "kept", "name": "Kept"}])
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        assert request.url.host == "openrouter.ai"
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest"})

    out = refresh_catalog(now=NOW, transport=httpx.MockTransport(handler))
    assert calls["n"] == 1
    assert out["ok"] is False
    assert out["reason"] == "redirect"
    assert out["wrote_success"] is False
    assert out["last_success"]["models"][0]["id"] == "kept"
    assert out["catalog_outdated"] is True
    _clean(out, cache_file())


def test_empty_malformed_and_http_error_preserve_success(catalog_env):
    from daedalus.model_catalog import cache_file, refresh_catalog
    refresh_catalog(now=NOW, fetcher=lambda *_a: [{"id": "kept", "name": "Kept"}])
    empty = refresh_catalog(now=NOW, fetcher=lambda *_a: [])
    assert empty["reason"] == "empty_catalog"
    assert empty["last_success"]["models"][0]["id"] == "kept"
    bad = refresh_catalog(now=NOW, fetcher=lambda *_a: {"data": "nope"})
    assert bad["reason"] == "malformed"
    assert bad["last_success"]["models"][0]["id"] == "kept"

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": SECRET, "api_key": SECRET})

    failed = refresh_catalog(now=NOW, transport=httpx.MockTransport(handler))
    assert failed["reason"] == "http_error"
    assert failed["last_attempt"]["error"] == "HTTP 500"
    assert failed["last_success"]["models"][0]["id"] == "kept"
    assert failed["catalog_outdated"] is True
    assert failed["stale_hint"] is False
    _clean(failed, cache_file())


def test_oversize_count_and_bytes_keep_prior_success(catalog_env):
    from daedalus.model_catalog import MAX_MODELS, cache_file, refresh_catalog
    refresh_catalog(now=NOW, fetcher=lambda *_a: [{"id": "kept", "name": "Kept"}])
    too_many = [{"id": f"m{i}", "name": "n"} for i in range(MAX_MODELS + 1)]
    counted = refresh_catalog(now=NOW, fetcher=lambda *_a: too_many)
    assert counted["reason"] == "oversize"
    assert counted["last_success"]["models"][0]["id"] == "kept"
    huge = [{"id": f"m{i}", "name": "n" * 1500} for i in range(MAX_MODELS)]
    wide = refresh_catalog(now=NOW, fetcher=lambda *_a: huge)
    assert wide["reason"] == "oversize"
    assert wide["wrote_success"] is False
    assert wide["last_success"]["models"] == [{"id": "kept", "name": "Kept"}]
    assert cache_file().stat().st_size <= 2 * 1024 * 1024
    _clean(wide, cache_file())


def test_stale_hint_is_informational(catalog_env):
    from daedalus.model_catalog import STALE_AFTER, catalog_view, refresh_catalog
    refresh_catalog(now=NOW, fetcher=lambda *_a: [{"id": "a/b", "name": "AB"}])
    fresh = catalog_view(now=NOW + STALE_AFTER)
    assert fresh["stale_hint"] is False
    assert fresh["ok"] is True
    stale = catalog_view(now=NOW + STALE_AFTER + timedelta(seconds=1))
    assert stale["stale_hint"] is True
    assert stale["last_success"]["models"][0]["id"] == "a/b"


def test_provider_switch_reads_only_that_scope(catalog_env):
    from daedalus.model_catalog import catalog_view, refresh_catalog

    def fetcher(provider, _base):
        return [{"id": provider + "-model", "name": provider}]

    refresh_catalog(now=NOW, fetcher=fetcher)
    catalog_env["brain"] = {
        "provider": "ollama",
        "model": "qwen",
        "base_url": "http://127.0.0.1:11434/v1",
        "api_key": "",
        "source": "db_local",
    }
    refresh_catalog(now=NOW, fetcher=fetcher)
    view = catalog_view(now=NOW)
    ids = [row["id"] for row in view["last_success"]["models"]]
    assert ids == ["ollama-model"]
    assert view["scope"]["provider"] == "ollama"
    assert "openrouter-model" not in json.dumps(view["last_success"])


def test_older_refresh_does_not_replace_newer_success(catalog_env):
    from daedalus.model_catalog import refresh_catalog
    state = {"ran": False, "inner": None}

    def fetcher(_provider, _base):
        if not state["ran"]:
            state["ran"] = True
            state["inner"] = refresh_catalog(
                now=NOW,
                fetcher=lambda *_a: [{"id": "newer", "name": "Newer"}],
            )
            return [{"id": "older", "name": "Older"}]
        return [{"id": "newer", "name": "Newer"}]

    outer = refresh_catalog(now=NOW, fetcher=fetcher)
    assert state["inner"]["wrote_success"] is True
    assert state["inner"]["last_success"]["models"][0]["id"] == "newer"
    assert outer["wrote_success"] is False
    assert outer["reason"] == "stale_response"
    assert outer["last_success"]["models"][0]["id"] == "newer"


def test_missing_effective_and_mentor_ids_warn_without_swap(catalog_env):
    from daedalus.model_catalog import catalog_view, refresh_catalog
    out = refresh_catalog(now=NOW, fetcher=lambda *_a: [{"id": "other/one", "name": "Other"}])
    assert out["effective_selection"]["model"] == "saved/missing"
    codes = {(row["target"], row["model"]) for row in out["warnings"]}
    assert ("brain", "saved/missing") in codes
    assert ("mentor", "mentor/missing") in codes
    again = catalog_view(now=NOW)
    assert again["effective_selection"]["model"] == "saved/missing"
    assert again["mentor_selection"]["model"] == "mentor/missing"


def test_manual_only_and_blocked_hosts_do_not_fetch(catalog_env):
    from daedalus.model_catalog import cache_file, refresh_catalog

    def boom(*_args, **_kwargs):
        raise AssertionError("fetched")

    catalog_env["brain"]["provider"] = "nvidia"
    catalog_env["brain"]["base_url"] = "https://integrate.api.nvidia.com/v1"
    manual = refresh_catalog(now=NOW, fetcher=boom, probe=boom)
    assert manual["ok"] is False
    assert manual["mode"] == "manual_only"
    assert manual["reason"] == "unsupported_provider"
    assert manual["wrote_success"] is False
    assert not cache_file().is_file()

    catalog_env["brain"] = {
        "provider": "ollama",
        "model": "qwen",
        "base_url": "http://169.254.169.254/v1",
        "api_key": "",
        "source": "db_local",
    }
    blocked = refresh_catalog(now=NOW, probe=boom)
    assert blocked["reason"] == "url_not_allowed"
    catalog_env["brain"]["base_url"] = "http://10.1.2.3:11434/v1"
    assert refresh_catalog(now=NOW, probe=boom)["reason"] == "url_not_allowed"
    catalog_env["brain"]["provider"] = "omniroute"
    catalog_env["brain"]["base_url"] = "http://example.com/v1"
    assert refresh_catalog(now=NOW, probe=boom)["reason"] == "url_not_allowed"
    catalog_env["brain"] = {
        "provider": "openrouter",
        "model": "saved/missing",
        "base_url": "https://evil.example/v1",
        "api_key": SECRET,
        "source": "db",
    }

    def transport_boom(_request):
        raise AssertionError("openrouter called")

    refused = refresh_catalog(now=NOW, transport=httpx.MockTransport(transport_boom))
    assert refused["reason"] == "url_not_allowed"


def test_loopback_probe_is_used_for_ollama_and_omniroute(catalog_env):
    from daedalus.model_catalog import refresh_catalog
    seen = []

    def probe(url, timeout=1.6):
        seen.append((url, timeout))
        return {"ok": True, "models": ["qwen2.5"], "error": ""}

    catalog_env["brain"] = {
        "provider": "ollama",
        "model": "qwen2.5",
        "base_url": "http://127.0.0.1:11434/v1",
        "api_key": "",
        "source": "db_local",
    }
    out = refresh_catalog(now=NOW, probe=probe)
    assert seen[0][0] == "http://127.0.0.1:11434/v1"
    assert out["last_success"]["models"][0]["id"] == "qwen2.5"
    assert out["warnings"] == []
    catalog_env["brain"] = {
        "provider": "omniroute",
        "model": "auto",
        "base_url": "http://127.0.0.1:20128/v1",
        "api_key": "",
        "source": "db",
    }
    refresh_catalog(now=NOW, probe=probe)
    assert seen[1][0] == "http://127.0.0.1:20128/v1"


def test_exception_text_and_unreadable_cache_are_redacted(catalog_env):
    from daedalus.model_catalog import cache_file, refresh_catalog
    refresh_catalog(now=NOW, fetcher=lambda *_a: [{"id": "kept", "name": "Kept"}])
    def blow(_provider, _base):
        raise RuntimeError("boom " + SECRET)

    blown = refresh_catalog(now=NOW, fetcher=blow)
    assert SECRET not in json.dumps(blown)
    assert blown["last_success"]["models"][0]["id"] == "kept"
    assert "boom" in blown["last_attempt"]["error"]
    assert SECRET not in blown["last_attempt"]["error"]
    path = cache_file()
    original = path.read_bytes()
    path.write_bytes(b"not-json")

    def boom(*_args, **_kwargs):
        raise AssertionError("fetched")

    unread = refresh_catalog(now=NOW, fetcher=boom)
    assert unread["reason"] == "cache_unreadable"
    assert path.read_bytes() == b"not-json"
    path.write_bytes(original)


def test_pytest_refuses_a_live_client(catalog_env, monkeypatch):
    from daedalus import model_catalog as mc

    class Boom:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("live client")

    monkeypatch.setattr(mc.httpx, "Client", Boom)
    monkeypatch.setattr("daedalus.local_llm.probe_endpoint", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("probe")))
    out = mc.refresh_catalog(now=NOW)
    assert out["reason"] == "network_blocked"
    assert out["wrote_success"] is False
    _clean(out, mc.cache_file())


def test_pins_are_confirmed_only_when_cached(catalog_env):
    from daedalus.model_catalog import catalog_view, refresh_catalog
    before = catalog_view(now=NOW)
    assert before["last_success"] is None
    if before["pins"]:
        assert all(pin["confirmed"] is False for pin in before["pins"])
    pin_id = before["pins"][0]["id"] if before["pins"] else "repo/pin"
    refresh_catalog(now=NOW, fetcher=lambda *_a: [{"id": pin_id, "name": "Pinned"}])
    after = catalog_view(now=NOW)
    matched = [pin for pin in after["pins"] if pin["id"] == pin_id]
    if before["pins"]:
        assert matched[0]["confirmed"] is True
        others = [pin for pin in after["pins"] if pin["id"] != pin_id]
        assert all(pin["confirmed"] is False for pin in others)
