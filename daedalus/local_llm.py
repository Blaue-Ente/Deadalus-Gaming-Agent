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
                f"Local LLM ({p}) at {base} is not responding. "
                f"Start Ollama / LM Studio / llama-server / vLLM. {probed.get('error') or ''}"
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


# Ollama's OpenAI `/v1` route ignores `num_ctx`. The server default window is
# 4096 unless the Modelfile, `/api/ps`, or `OLLAMA_CONTEXT_LENGTH` says otherwise.
OLLAMA_DEFAULT_NUM_CTX = 4096

_NUM_CTX_RE = re.compile(r"(?i)\bnum_ctx\s+(\d+)")
_show_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def clear_probe_cache() -> None:
    """Drop cached `/v1/models` and `/api/show` results. Tests and retries."""
    _probe_cache.clear()
    _show_cache.clear()


def ollama_api_root(base_url: str) -> str:
    """Native Ollama root (no `/v1`) for `/api/show` and `/api/ps`."""
    v1 = _ensure_v1(base_url or "")
    if v1.endswith("/v1"):
        return v1[: -len("/v1")]
    return v1.rstrip("/")


def active_ollama_model() -> str:
    """Wardrobe active, then env, then the catalog default. Never a cloud id."""
    try:
        from .llm_wardrobe import active_model
        active = active_model()
        if active and not _looks_cloud_model_id(str(active)):
            return str(active)
    except Exception:
        pass
    for env_name in ("OLLAMA_MODEL", "DAEDALUS_MODEL"):
        raw = (os.environ.get(env_name) or "").strip()
        if raw and not _looks_cloud_model_id(raw):
            return raw
    return _DEFAULTS["ollama"]["default_model"]


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, float) and value > 0 and value == int(value):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            number = int(text)
            return number if number > 0 else None
    return None


def context_from_model_info(info: Any) -> int | None:
    """Training max from `model_info` keys ending in `context_length`."""
    if not isinstance(info, dict):
        return None
    found: list[int] = []
    for key, val in info.items():
        name = str(key)
        if name == "context_length" or name.endswith(".context_length"):
            number = _positive_int(val)
            if number:
                found.append(number)
    return max(found) if found else None


def num_ctx_from_parameters(parameters: Any) -> int | None:
    """Modelfile `num_ctx`. This is the configured window, not the training max."""
    if isinstance(parameters, dict):
        return _positive_int(parameters.get("num_ctx"))
    if isinstance(parameters, str):
        match = _NUM_CTX_RE.search(parameters)
        if match:
            return _positive_int(match.group(1))
    return None


def parse_ollama_show(payload: Any) -> dict[str, Any]:
    """Read capabilities and context fields from a `/api/show` body."""
    data = payload if isinstance(payload, dict) else {}
    raw_caps = data.get("capabilities")
    caps: list[str] = []
    if isinstance(raw_caps, list):
        for item in raw_caps:
            if isinstance(item, str) and item.strip():
                caps.append(item.strip())
            elif isinstance(item, dict) and item.get("name"):
                caps.append(str(item["name"]).strip())
    elif isinstance(raw_caps, str) and raw_caps.strip():
        caps = [raw_caps.strip()]
    lowered = {c.lower() for c in caps}
    return {
        "capabilities": caps,
        "tools": "tools" in lowered,
        "capabilities_reported": isinstance(raw_caps, list),
        "model_context": context_from_model_info(data.get("model_info")),
        "parameter_num_ctx": num_ctx_from_parameters(data.get("parameters")),
        "explicit_context": _positive_int(data.get("context_length")),
    }


def ps_context_length(payload: Any, model: str) -> int | None:
    """Loaded window from `GET /api/ps` (`ollama ps` CONTEXT column)."""
    if not isinstance(payload, dict):
        return None
    rows = payload.get("models")
    if not isinstance(rows, list):
        return None
    want = (model or "").strip()
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or row.get("model") or "")
        if want and want != name and want not in name and name not in want:
            continue
        number = _positive_int(row.get("context_length"))
        if number:
            return number
    return None


def env_context_length() -> int | None:
    """`OLLAMA_CONTEXT_LENGTH` in this process. The Ollama daemon has its own."""
    return _positive_int(os.environ.get("OLLAMA_CONTEXT_LENGTH"))


def effective_context(
    show: dict[str, Any],
    *,
    ps_context: int | None = None,
    env_ctx: int | None = None,
) -> tuple[int, str]:
    """Runtime window. Loaded `/api/ps` beats Modelfile, env, then the 4096 default.

    `model_info.context_length` is the training max. Ollama does not load that
    many tokens unless something above sets the window. The default is 4096.
    """
    if ps_context:
        chosen, source = int(ps_context), "loaded window (/api/ps)"
    elif show.get("parameter_num_ctx"):
        chosen, source = int(show["parameter_num_ctx"]), "Modelfile num_ctx"
    elif show.get("explicit_context"):
        chosen, source = int(show["explicit_context"]), "show context_length"
    elif env_ctx:
        chosen, source = int(env_ctx), "OLLAMA_CONTEXT_LENGTH"
    else:
        chosen, source = OLLAMA_DEFAULT_NUM_CTX, (
            "Ollama default 4096 (model not loaded, Modelfile has no num_ctx)"
        )
    model_max = show.get("model_context")
    if isinstance(model_max, int) and model_max > 0 and chosen > model_max:
        chosen = model_max
    return chosen, source


def tools_capability_warning(model: str, capabilities: list[str], *, reported: bool) -> str:
    shown = ", ".join(capabilities) if capabilities else "none"
    if reported:
        return (
            f"Ollama model {model} does not advertise the tools capability "
            f"(capabilities: {shown}). Tool calls will fail or come back as prose. "
            "Pull a tools-capable tag or use a cloud brain for builds."
        )
    return (
        f"Ollama model {model} did not report a tools capability "
        f"(capabilities: {shown}). Tool calls may fail silently. "
        f"Check `ollama show {model}` and use a tools-capable tag or a cloud brain for builds."
    )


def probe_ollama_model(
    model: str | None = None,
    base_url: str | None = None,
    *,
    timeout: float = 1.6,
) -> dict[str, Any]:
    """POST `/api/show` and, when the model is loaded, GET `/api/ps`.

    Never raises. Cached briefly like ``probe_endpoint``. A missing ``tools``
    capability is logged here so it is not a silent 400 later.
    """
    name = (model or active_ollama_model() or "").strip() or _DEFAULTS["ollama"]["default_model"]
    root = ollama_api_root(base_url or default_base_url("ollama"))
    cache_key = f"{root}|{name}"
    now = time.time()
    hit = _show_cache.get(cache_key)
    if hit and now - hit[0] < _PROBE_TTL_S:
        return dict(hit[1])

    result: dict[str, Any] = {
        "ok": False,
        "model": name,
        "base_url": root,
        "capabilities": [],
        "tools": False,
        "capabilities_reported": False,
        "context_length": None,
        "model_context": None,
        "context_source": "",
        "warning": "",
        "error": "",
    }
    show_payload: Any = None
    ps_payload: Any = None
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            show_resp = client.post(f"{root}/api/show", json={"model": name, "name": name})
            show_resp.raise_for_status()
            show_payload = show_resp.json()
            try:
                ps_resp = client.get(f"{root}/api/ps")
                if ps_resp.status_code == 200:
                    ps_payload = ps_resp.json()
            except Exception as exc:
                log.debug("ollama /api/ps skipped: %s", exc)
    except Exception as exc:
        result["error"] = str(exc)[:240]
        result["warning"] = (
            f"Could not read Ollama model {name} via /api/show at {root}. {result['error']}"
        ).strip()
        _show_cache[cache_key] = (now, result)
        return dict(result)

    parsed = parse_ollama_show(show_payload)
    loaded = ps_context_length(ps_payload, name)
    ctx, source = effective_context(parsed, ps_context=loaded, env_ctx=env_context_length())
    warning = ""
    if not parsed["tools"]:
        warning = tools_capability_warning(
            name, parsed["capabilities"], reported=bool(parsed["capabilities_reported"]),
        )
        log.warning("%s", warning)
    result = {
        "ok": True,
        "model": name,
        "base_url": root,
        "capabilities": parsed["capabilities"],
        "tools": bool(parsed["tools"]),
        "capabilities_reported": bool(parsed["capabilities_reported"]),
        "context_length": ctx,
        "model_context": parsed["model_context"],
        "context_source": source,
        "warning": warning,
        "error": "",
    }
    _show_cache[cache_key] = (now, result)
    return dict(result)


def local_requested() -> bool:
    """True when the owner asked for a local brain for the whole process.

    Order: explicit env flags, then wardrobe mode, then DAEDALUS_PROVIDER.
    Wardrobe ``cloud`` is an explicit Studio/Connectors click — it wins over a
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
