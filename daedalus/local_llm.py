"""First-class local LLM backends — Ollama, LM Studio, llama.cpp, vLLM.

Mirrors Hermes Agent's local-endpoint model: OpenAI-compatible `/v1`, no API
key on loopback. Cloud keys stay optional. Probe results are cached briefly so
``get_config()`` stays cheap.
"""
from __future__ import annotations

import logging
import os
import re
import time
from typing import Any
from urllib.parse import urlparse

import httpx

log = logging.getLogger(__name__)

LOCAL_PROVIDERS = frozenset({"ollama", "lmstudio", "llamacpp", "llama.cpp", "vllm", "local"})

_DEFAULTS: dict[str, dict[str, str]] = {
    "ollama": {
        "base_url": "http://127.0.0.1:11434/v1",
        "default_model": "qwen2.5-coder:7b",
        "dummy_key": "ollama",
    },
    "lmstudio": {
        "base_url": "http://127.0.0.1:1234/v1",
        "default_model": "local-model",
        "dummy_key": "lm-studio",
    },
    "llamacpp": {
        "base_url": "http://127.0.0.1:8080/v1",
        "default_model": "local-model",
        "dummy_key": "sk-no-key",
    },
    "vllm": {
        "base_url": "http://127.0.0.1:8000/v1",
        "default_model": "local-model",
        "dummy_key": "local",
    },
}
_DEFAULTS["llama.cpp"] = _DEFAULTS["llamacpp"]
_DEFAULTS["local"] = _DEFAULTS["ollama"]

_PROBE_TTL_S = 20.0
_probe_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def normalize_provider(name: str) -> str:
    p = re.sub(r"[\s_]+", "-", (name or "").strip().lower())
    aliases = {
        "llama.cpp": "llamacpp",
        "llama-cpp": "llamacpp",
        "llamacpp": "llamacpp",
        "lm-studio": "lmstudio",
        "lmstudio": "lmstudio",
        "local": "ollama",
    }
    return aliases.get(p, p)


def is_local_provider(provider: str) -> bool:
    return normalize_provider(provider) in LOCAL_PROVIDERS


def _ensure_v1(url: str) -> str:
    u = (url or "").strip().rstrip("/")
    if not u:
        return u
    if u.endswith("/v1"):
        return u
    # OLLAMA_HOST is often host:port without scheme or /v1
    if "://" not in u:
        u = "http://" + u
    parsed = urlparse(u)
    path = (parsed.path or "").rstrip("/")
    if path in ("", "/") or path == "/api":
        return f"{parsed.scheme}://{parsed.netloc}/v1"
    if not path.endswith("/v1"):
        return u + "/v1"
    return u


def default_base_url(provider: str) -> str:
    p = normalize_provider(provider)
    if p == "ollama":
        host = (os.environ.get("OLLAMA_HOST") or os.environ.get("OLLAMA_BASE_URL") or "").strip()
        if host:
            return _ensure_v1(host)
    env_url = (os.environ.get("DAEDALUS_BASE_URL") or "").strip()
    if env_url and p in LOCAL_PROVIDERS:
        return _ensure_v1(env_url)
    cfg = _DEFAULTS.get(p) or _DEFAULTS["ollama"]
    return cfg["base_url"]


def dummy_api_key(provider: str) -> str:
    p = normalize_provider(provider)
    env_key = (os.environ.get("DAEDALUS_API_KEY") or os.environ.get("OLLAMA_API_KEY")
               or os.environ.get("LM_API_KEY") or "").strip()
    if env_key:
        return env_key
    return (_DEFAULTS.get(p) or _DEFAULTS["ollama"])["dummy_key"]


def _models_url(base_url: str) -> str:
    return _ensure_v1(base_url) + "/models"


def probe_endpoint(base_url: str, *, timeout: float = 1.6) -> dict[str, Any]:
    """GET /models. Cached. Never raises."""
    url = _models_url(base_url)
    now = time.time()
    hit = _probe_cache.get(url)
    if hit and now - hit[0] < _PROBE_TTL_S:
        return dict(hit[1])
    result: dict[str, Any] = {"ok": False, "base_url": _ensure_v1(base_url), "models": [], "error": ""}
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
            data = resp.json()
        models: list[str] = []
        raw = data.get("data") if isinstance(data, dict) else data
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict) and item.get("id"):
                    models.append(str(item["id"]))
                elif isinstance(item, str):
                    models.append(item)
        result = {
            "ok": True,
            "base_url": _ensure_v1(base_url),
            "models": models,
            "error": "",
        }
    except Exception as exc:
        result["error"] = str(exc)[:240]
    _probe_cache[url] = (now, result)
    return dict(result)


def probe_all() -> list[dict[str, Any]]:
    """Probe every known local backend. Used by Studio / CLI."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for name in ("ollama", "lmstudio", "llamacpp", "vllm"):
        base = default_base_url(name)
        if base in seen:
            continue
        seen.add(base)
        probed = probe_endpoint(base)
        probed["provider"] = name
        rows.append(probed)
    extra = (os.environ.get("DAEDALUS_BASE_URL") or "").strip()
    if extra:
        base = _ensure_v1(extra)
        if base not in seen:
            probed = probe_endpoint(base)
            probed["provider"] = "custom"
            rows.append(probed)
    return rows


def first_healthy_local() -> dict[str, Any] | None:
    for row in probe_all():
        if row.get("ok"):
            return row
    return None


def _looks_cloud_model_id(name: str) -> bool:
    """Gateway ids (OpenRouter etc.) must not be sent to Ollama."""
    n = (name or "").strip().lower()
    if not n:
        return False
    if n.endswith(":free") or n.endswith("/free"):
        return True
    return n.startswith((
        "poolside/", "openai/", "anthropic/", "google/", "openrouter/",
        "meta-llama/", "deepseek/", "mistralai/", "x-ai/", "nvidia/",
    ))


def _pick_local_model(probed_models: list[str] | None, provider: str) -> str:
    try:
        from .llm_wardrobe import active_model
        active = active_model()
        if active:
            return str(active)
    except Exception:
        pass
    for env_name in ("OLLAMA_MODEL", "DAEDALUS_MODEL"):
        raw = (os.environ.get(env_name) or "").strip()
        if raw and not _looks_cloud_model_id(raw):
            return raw
    if probed_models:
        return str(probed_models[0])
    return (_DEFAULTS.get(provider) or _DEFAULTS["ollama"])["default_model"]


def resolve_local_config(provider: str | None = None) -> dict[str, Any] | None:
    """Build a get_config()-shaped dict for a local backend, or None if unreachable.

    If ``provider`` is set, use that backend even if probe failed (so CLI/UI can
    still show the intended target). If unset, pick the first healthy probe.
    Wardrobe's active model beats a leftover cloud ``DAEDALUS_MODEL`` so Ollama
    is not sent an OpenRouter id.
    """
    p = normalize_provider(provider or os.environ.get("DAEDALUS_PROVIDER") or "")
    if p and p not in LOCAL_PROVIDERS and p != "custom":
        p = ""

    if p in LOCAL_PROVIDERS:
        base = default_base_url(p)
        probed = probe_endpoint(base)
        model = _pick_local_model(probed.get("models") or [], p)
        return {
            "provider": p if p != "local" else "ollama",
            "model": model,
            "api_key": dummy_api_key(p or "ollama"),
            "base_url": base,
            "sdk": "openai",
            "use_cache": False,
            "source": "local",
            "local": True,
            "reachable": bool(probed.get("ok")),
            "warning": "" if probed.get("ok") else (
                f"Локалният LLM ({p}) на {base} не отговаря. "
                f"Пусни Ollama / LM Studio / llama-server / vLLM. {probed.get('error') or ''}"
            ).strip(),
        }

    healthy = first_healthy_local()
    if not healthy:
        return None
    p = str(healthy.get("provider") or "ollama")
    model = _pick_local_model(healthy.get("models") or [], p)
    return {
        "provider": p,
        "model": model,
        "api_key": dummy_api_key(p),
        "base_url": healthy["base_url"],
        "sdk": "openai",
        "use_cache": False,
        "source": "local_probe",
        "local": True,
        "reachable": True,
        "warning": "",
    }


def local_requested() -> bool:
    """True when the owner asked for a local brain for the whole process.

    Order: explicit env flags, then wardrobe mode, then DAEDALUS_PROVIDER.
    Wardrobe ``cloud`` is an explicit Studio/Свързаност click — it wins over a
    leftover ``DAEDALUS_PROVIDER=ollama`` in secrets unless LOCAL_LLM/OFFLINE is on.
    """
    flag = (os.environ.get("DAEDALUS_LOCAL_LLM") or "").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        return True
    if (os.environ.get("DAEDALUS_OFFLINE") or "").strip().lower() in ("1", "true", "yes", "on"):
        return True
    try:
        from .llm_wardrobe import load
        data = load()
        backend = str(data.get("backend") or "auto").lower()
        if backend == "cloud":
            return False
        if data.get("offline") or data.get("active") or backend in LOCAL_PROVIDERS:
            return True
    except Exception:
        pass
    provider = normalize_provider(os.environ.get("DAEDALUS_PROVIDER") or "")
    return provider in LOCAL_PROVIDERS
