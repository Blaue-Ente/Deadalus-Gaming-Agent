"""Availability-aware model profiles and runtime fallback policy."""
from __future__ import annotations

import pytest

from daedalus.model_router import ModelRoute


def _resource(
    provider: str,
    *,
    kind: str,
    models: list[dict],
    selected: bool = False,
) -> dict:
    return {
        "id": provider,
        "kind": kind,
        "configured": True,
        "selected": selected,
        "models": models,
    }


def test_profiles_use_selected_route_and_installed_local_models_only():
    from daedalus.routing_profiles import available_profiles

    resources = [
        _resource(
            "ollama",
            kind="local",
            selected=True,
            models=[
                {"id": "not-installed", "source": "preset", "tier": "power"},
                {"id": "installed-coder", "source": "discovered", "selected": True},
            ],
        ),
        _resource(
            "openrouter",
            kind="gateway",
            models=[
                {"id": "fast-cloud", "source": "preset", "tier": "fast"},
                {"id": "power-cloud", "source": "preset", "tier": "power"},
            ],
        ),
    ]
    profiles = {profile["id"]: profile for profile in available_profiles(resources)}
    assert profiles["auto"]["slots"]["master"] == {
        "provider": "ollama",
        "model": "installed-coder",
    }
    assert profiles["private"]["available"] is True
    private_models = {slot["model"] for slot in profiles["private"]["slots"].values()}
    assert private_models == {"installed-coder"}
    assert profiles["maximum-quality"]["slots"]["master"]["model"] == "power-cloud"


def test_private_profile_is_unavailable_without_discovered_local_model():
    from daedalus.routing_profiles import available_profiles

    resources = [
        _resource(
            "ollama",
            kind="local",
            models=[{"id": "catalog-only", "source": "preset", "tier": "fast"}],
        ),
    ]
    private = next(
        profile for profile in available_profiles(resources)
        if profile["id"] == "private"
    )
    assert private["available"] is False


def test_private_fallback_never_crosses_to_cloud(monkeypatch):
    import daedalus.model_router as router

    rows = [
        _resource(
            "ollama",
            kind="local",
            models=[{"id": "installed", "source": "discovered"}],
        ),
        _resource(
            "openrouter",
            kind="gateway",
            models=[{"id": "cloud", "source": "preset"}],
        ),
    ]
    monkeypatch.setattr("daedalus.provider_catalog.resources", lambda probe_local=True: rows)
    monkeypatch.setattr("daedalus.local_llm.offline_requested", lambda: False)
    monkeypatch.setattr(
        "daedalus.model_registry.get_active_mix",
        lambda: {"source_id": "private", "label": "Private"},
    )
    monkeypatch.setattr(
        router,
        "_route_for_provider",
        lambda provider, model, source: ModelRoute(
            provider=provider,
            model=model,
            base_url=f"http://{provider}",
            api_key="key",
            source=source,
        ),
    )
    routes = router.fallback_routes({"provider": "ollama", "model": "primary"})
    assert [route.provider for route in routes] == ["ollama"]


@pytest.mark.asyncio
async def test_client_uses_fallback_after_transient_failure(monkeypatch):
    from daedalus import llm_client

    primary = {
        "provider": "ollama",
        "model": "primary",
        "base_url": "http://ollama",
        "api_key": "ollama",
        "sdk": "openai",
    }
    fallback = ModelRoute(
        provider="openrouter",
        model="fallback",
        base_url="https://openrouter.example/v1",
        api_key="cloud-key",
    )
    calls: list[str] = []

    async def fake_complete_route(cfg, **kwargs):
        calls.append(cfg["provider"])
        if cfg["provider"] == "ollama":
            raise TimeoutError("local runtime unavailable")
        return {"ok": True, "provider": cfg["provider"], "model": cfg["model"], "reply": "ok"}

    monkeypatch.setattr("daedalus.llm_config.get_config", lambda: primary)
    monkeypatch.setattr("daedalus.model_router.fallback_routes", lambda cfg: [fallback])
    monkeypatch.setattr(llm_client, "_complete_route", fake_complete_route)

    result = await llm_client.complete(user="hello")
    assert calls == ["ollama", "openrouter"]
    assert result["provider"] == "openrouter"
    assert result["fallback"]["from_provider"] == "ollama"


@pytest.mark.asyncio
async def test_client_does_not_fallback_on_auth_error(monkeypatch):
    from daedalus import llm_client

    class AuthError(Exception):
        status_code = 401

    async def fail(cfg, **kwargs):
        raise AuthError("invalid credentials")

    monkeypatch.setattr(
        "daedalus.llm_config.get_config",
        lambda: {"provider": "openrouter", "model": "model", "api_key": "bad"},
    )
    monkeypatch.setattr(
        "daedalus.model_router.fallback_routes",
        lambda cfg: pytest.fail("fallback should not be resolved"),
    )
    monkeypatch.setattr(llm_client, "_complete_route", fail)

    with pytest.raises(AuthError):
        await llm_client.complete(user="hello")
