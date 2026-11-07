"""Manual provider model catalog.

Refresh writes ``{DAEDALUS_DATA}/catalog/llm_models.json`` only.
It does not call ``set_config`` or mentor setters, and it does not change
``get_config`` / ``get_brain_config``.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import os
import threading
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

from .llm_config import (
    PROVIDER_DEFAULTS,
    PROVIDER_ENV_KEYS,
    is_loopback_gateway,
    list_preset_models,
    openai_compat_headers,
    provider_default_base_url,
)
from .local_llm import is_local_provider
from .paths import data_root

log = logging.getLogger(__name__)

STALE_AFTER = timedelta(days=7)
MAX_MODELS = 2000
MAX_BYTES = 2 * 1024 * 1024
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
_BASE_FIELD = frozenset({"custom", "alltoken", "novita", "openrouter"})
_DUMMY_KEYS = frozenset({"", "omniroute", "ollama", "lm-studio", "sk-no-key", "local"})

# Spec matrix plus Replit rows, which have no approved list adapter.
MANUAL_ONLY = frozenset({
    "novita",
    "alltoken",
    "poolside",
    "nvidia",
    "groq",
    "openai",
    "gemini",
    "anthropic",
    "custom",
    "replit_openai",
    "replit_gemini",
})

_lock = threading.Lock()
_generation: dict[str, int] = {}


class CatalogError(Exception):
    def __init__(self, reason: str, error: str = "") -> None:
        self.reason = reason
        self.error = error or reason
        super().__init__(reason)


def reset_state() -> None:
    """Clear in-process refresh generations. Tests use this between cases."""
    with _lock:
        _generation.clear()


def normalize_base_url(url: str) -> str:
    """Scheme, host, and ``/v1``. Drops userinfo, query, and fragment."""
    raw = (url or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "http://" + raw
    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    if scheme not in {"http", "https"}:
        return ""
    host = (parsed.hostname or "").lower()
    if not host:
        return ""
    port = _public_port(scheme, parsed.port)
    netloc = f"[{host}]" if ":" in host else host
    if port is not None:
        netloc = f"{netloc}:{port}"
    return f"{scheme}://{netloc}/v1"


def scope_key(provider: str, base_url: str) -> str:
    return f"{(provider or '').strip().lower()}:{normalize_base_url(base_url)}"


def catalog_view(*, now: datetime | None = None) -> dict[str, Any]:
    """Read merge for the UI. Does not fetch and does not write the brain."""
    stamp = _stamp(now)
    brain = _brain()
    provider = str(brain.get("provider") or "").strip().lower()
    base_raw = _effective_base(provider, brain)
    base = normalize_base_url(base_raw)
    key = scope_key(provider, base_raw) if provider else ""
    try:
        store = _load()
    except CatalogError as exc:
        view = _empty_view(provider, base, key, brain)
        view["reason"] = exc.reason
        return _scrub(view)
    return _scrub(_compose(store, provider, base, key, brain, stamp, reason=_static_reason(provider, base_raw)))


def refresh_catalog(
    *,
    now: datetime | None = None,
    transport: httpx.BaseTransport | None = None,
    probe: Callable[..., dict[str, Any]] | None = None,
    fetcher: Callable[[str, str], Any] | None = None,
) -> dict[str, Any]:
    """Fetch the effective provider into the cache file. Ignores any client body."""
    stamp = _stamp(now)
    brain = _brain()
    provider = str(brain.get("provider") or "").strip().lower()
    base_raw = _effective_base(provider, brain)
    base = normalize_base_url(base_raw)
    key = scope_key(provider, base_raw) if provider else ""
    try:
        _load()
    except CatalogError as exc:
        view = _empty_view(provider, base, key, brain)
        view["reason"] = exc.reason
        return _finish(view, ok=False, reason=exc.reason, wrote=False)

    kind, target, blocked = _allow(provider, base_raw)
    if blocked in {"unsupported_provider", "no_provider"}:
        view = catalog_view(now=stamp)
        return _finish(view, ok=False, reason=blocked, wrote=False)
    if blocked:
        gen = _begin(key)
        return _fail(key, gen, stamp, blocked, blocked)

    gen = _begin(key)
    try:
        if fetcher is not None:
            raw_models = fetcher(provider, base_raw)
        else:
            raw_models = _dispatch(kind, target, provider, transport=transport, probe=probe)
        models = _normalize_models(raw_models)
    except CatalogError as exc:
        return _fail(key, gen, stamp, exc.reason, exc.error)
    except Exception as exc:
        return _fail(key, gen, stamp, "http_error", _redact(str(exc) or "http_error"))

    try:
        wrote = _commit_success(key, gen, models, stamp)
    except CatalogError as exc:
        view = catalog_view(now=stamp)
        return _finish(view, ok=False, reason=exc.reason, wrote=False)
    view = catalog_view(now=stamp)
    if not wrote:
        return _finish(view, ok=True, reason="stale_response", wrote=False)
    return _finish(view, ok=True, reason="", wrote=True)


def _dispatch(
    kind: str,
    target: str,
    provider: str,
    *,
    transport: httpx.BaseTransport | None,
    probe: Callable[..., dict[str, Any]] | None,
) -> list[Any]:
    if not _network_permitted(kind, transport=transport, probe=probe):
        raise CatalogError("network_blocked", "network_blocked")
    if kind == "openrouter":
        return _fetch_openrouter(target, provider, transport=transport)
    return _fetch_local(target, probe=probe)


def _fetch_openrouter(
    url: str,
    provider: str,
    *,
    transport: httpx.BaseTransport | None,
) -> list[Any]:
    if url != OPENROUTER_MODELS_URL:
        raise CatalogError("url_not_allowed", "url_not_allowed")
    headers = {"Accept": "application/json"}
    headers.update(openai_compat_headers(provider))
    key = _server_key(provider)
    if key:
        headers["Authorization"] = f"Bearer {key}"
    try:
        with httpx.Client(transport=transport, follow_redirects=False, timeout=10.0) as client:
            response = client.get(url, headers=headers)
    except Exception as exc:
        raise CatalogError("http_error", _redact(str(exc) or "http_error")) from None
    if response.status_code in {301, 302, 303, 307, 308}:
        raise CatalogError("redirect", "redirect")
    if response.status_code >= 400:
        raise CatalogError("http_error", f"HTTP {response.status_code}")
    try:
        payload = response.json()
    except Exception as exc:
        raise CatalogError("malformed", "malformed") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise CatalogError("malformed", "malformed")
    return list(payload["data"])


def _fetch_local(url: str, *, probe: Callable[..., dict[str, Any]] | None) -> list[Any]:
    if not _loopback_url(url):
        raise CatalogError("url_not_allowed", "url_not_allowed")
    if probe is None:
        from .local_llm import probe_endpoint as probe
    try:
        result = probe(url, timeout=1.6)
    except Exception as exc:
        raise CatalogError("http_error", _redact(str(exc) or "probe failed")) from None
    if not isinstance(result, dict) or not result.get("ok"):
        detail = ""
        if isinstance(result, dict):
            detail = str(result.get("error") or "")
        raise CatalogError("http_error", _redact(detail or "probe failed"))
    return list(result.get("models") or [])


def _allow(provider: str, base_url: str) -> tuple[str, str, str]:
    name = (provider or "").strip().lower()
    if not name:
        return "", "", "no_provider"
    if name in MANUAL_ONLY:
        return "", "", "unsupported_provider"
    if name == "openrouter":
        default = provider_default_base_url("openrouter")
        if not _same_origin(base_url, default):
            return "", "", "url_not_allowed"
        return "openrouter", OPENROUTER_MODELS_URL, ""
    if is_local_provider(name) or is_loopback_gateway(name):
        if not _loopback_url(base_url):
            return "", "", "url_not_allowed"
        return "local", normalize_base_url(base_url), ""
    return "", "", "unsupported_provider"


def _static_reason(provider: str, base_url: str) -> str:
    _kind, _target, reason = _allow(provider, base_url)
    if reason in {"unsupported_provider", "no_provider", "url_not_allowed"}:
        return reason
    return ""


def _network_permitted(
    kind: str,
    *,
    transport: httpx.BaseTransport | None,
    probe: Callable[..., dict[str, Any]] | None,
) -> bool:
    if kind == "openrouter" and transport is not None:
        return True
    if kind == "local" and probe is not None:
        return True
    flag = (os.environ.get("DAEDALUS_CATALOG_NETWORK") or "").strip().lower()
    if flag in {"0", "false", "no", "off"}:
        return False
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    return True


def _normalize_models(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        raise CatalogError("malformed", "malformed")
    models: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw:
        if isinstance(item, str):
            mid = item.strip()
            name = mid
        elif isinstance(item, dict):
            mid = str(item.get("id") or "").strip()
            name = str(item.get("name") or mid).strip() or mid
        else:
            continue
        if not mid or mid in seen:
            continue
        seen.add(mid)
        models.append({"id": mid, "name": name})
    if len(models) > MAX_MODELS:
        raise CatalogError("oversize", "oversize")
    if not models:
        raise CatalogError("empty_catalog", "empty_catalog")
    return models


def _fail(key: str, gen: int, stamp: datetime, reason: str, error: str) -> dict[str, Any]:
    log.info("catalog refresh failed reason=%s", reason)
    wrote_attempt = _commit_failure(key, gen, stamp, reason, _redact(error, limit=240))
    view = catalog_view(now=stamp)
    if not wrote_attempt:
        return _finish(view, ok=True, reason="stale_response", wrote=False)
    return _finish(view, ok=False, reason=reason, wrote=False)


def _commit_success(key: str, gen: int, models: list[dict[str, str]], stamp: datetime) -> bool:
    with _lock:
        if _generation.get(key) != gen:
            return False
        try:
            store = _load()
        except CatalogError:
            return False
        updated = deepcopy(store)
        scope = updated.setdefault("scopes", {}).setdefault(key, {})
        previous = deepcopy(scope.get("last_success"))
        scope["last_success"] = {
            "fetched_at": _iso(stamp),
            "models": models,
            "count": len(models),
        }
        scope["last_attempt"] = {
            "fetched_at": _iso(stamp),
            "ok": True,
            "error": "",
            "reason": "",
        }
        try:
            _write(updated)
        except CatalogError as exc:
            if exc.reason != "oversize":
                raise
            reverted = deepcopy(store)
            bucket = reverted.setdefault("scopes", {}).setdefault(key, {})
            if previous is not None:
                bucket["last_success"] = previous
            elif "last_success" in bucket and store.get("scopes", {}).get(key, {}).get("last_success") is None:
                bucket.pop("last_success", None)
            bucket["last_attempt"] = {
                "fetched_at": _iso(stamp),
                "ok": False,
                "error": "oversize",
                "reason": "oversize",
            }
            _write(reverted)
            raise CatalogError("oversize", "oversize")
        return True


def _commit_failure(key: str, gen: int, stamp: datetime, reason: str, error: str) -> bool:
    with _lock:
        if _generation.get(key) != gen:
            return False
        try:
            store = _load()
        except CatalogError:
            return False
        updated = deepcopy(store)
        scope = updated.setdefault("scopes", {}).setdefault(key, {})
        scope["last_attempt"] = {
            "fetched_at": _iso(stamp),
            "ok": False,
            "error": _redact(error, limit=240),
            "reason": reason,
        }
        _write(updated)
        return True


def _begin(key: str) -> int:
    with _lock:
        _generation[key] = _generation.get(key, 0) + 1
        return _generation[key]


def cache_file() -> Any:
    return data_root() / "catalog" / "llm_models.json"


def _load() -> dict[str, Any]:
    path = cache_file()
    if not path.is_file():
        return {"version": 1, "scopes": {}}
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise CatalogError("cache_unreadable", "cache_unreadable") from exc
    if len(raw) > MAX_BYTES:
        raise CatalogError("cache_unreadable", "cache_unreadable")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CatalogError("cache_unreadable", "cache_unreadable") from exc
    if not isinstance(data, dict) or not isinstance(data.get("scopes"), dict):
        raise CatalogError("cache_unreadable", "cache_unreadable")
    data.setdefault("version", 1)
    return data


def _write(store: dict[str, Any]) -> None:
    raw = json.dumps(store, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(raw) > MAX_BYTES:
        raise CatalogError("oversize", "oversize")
    path = cache_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(".llm_models.json.tmp")
    try:
        tmp.write_bytes(raw)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _compose(
    store: dict[str, Any],
    provider: str,
    base: str,
    key: str,
    brain: dict[str, Any],
    stamp: datetime,
    *,
    reason: str,
) -> dict[str, Any]:
    scope = {}
    if key:
        found = store.get("scopes", {}).get(key)
        if isinstance(found, dict):
            scope = found
    success = _public_success(scope.get("last_success"))
    attempt = _public_attempt(scope.get("last_attempt"))
    ids = None if success is None else {row["id"] for row in success["models"]}
    effective = _effective(provider, base, brain)
    mentor = _mentor()
    mode = "manual_only" if reason == "unsupported_provider" else "catalog"
    return {
        "ok": True,
        "mode": mode,
        "reason": reason,
        "scope": {"provider": provider, "base_url": base, "key": key},
        "last_success": success,
        "last_attempt": attempt,
        "stale_hint": bool(success and _is_stale(str(success.get("fetched_at") or ""), stamp)),
        "catalog_outdated": bool(attempt and attempt.get("ok") is False),
        "pins": _pins(provider, ids),
        "effective_selection": effective,
        "mentor_selection": mentor,
        "warnings": _warnings(ids, effective, mentor),
        "providers": _providers(),
    }


def _empty_view(provider: str, base: str, key: str, brain: dict[str, Any]) -> dict[str, Any]:
    effective = _effective(provider, base, brain)
    reason = "unsupported_provider" if provider in MANUAL_ONLY else ""
    return {
        "ok": True,
        "mode": "manual_only" if reason else "catalog",
        "reason": reason,
        "scope": {"provider": provider, "base_url": base, "key": key},
        "last_success": None,
        "last_attempt": None,
        "stale_hint": False,
        "catalog_outdated": False,
        "pins": _pins(provider, None),
        "effective_selection": effective,
        "mentor_selection": _mentor(),
        "warnings": [],
        "providers": _providers(),
    }


def _finish(view: dict[str, Any], *, ok: bool, reason: str, wrote: bool) -> dict[str, Any]:
    out = dict(view)
    out["ok"] = ok
    out["reason"] = reason
    if reason == "unsupported_provider":
        out["mode"] = "manual_only"
    out["wrote_success"] = wrote
    return _scrub(out)


def _public_success(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    models = []
    for row in value.get("models") or []:
        if not isinstance(row, dict):
            continue
        mid = str(row.get("id") or "").strip()
        if not mid:
            continue
        name = str(row.get("name") or mid).strip() or mid
        models.append({"id": mid, "name": name})
    if not models:
        return None
    return {
        "fetched_at": str(value.get("fetched_at") or ""),
        "models": models,
        "count": len(models),
    }


def _public_attempt(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or "ok" not in value:
        return None
    return {
        "fetched_at": str(value.get("fetched_at") or ""),
        "ok": bool(value.get("ok")),
        "error": _redact(str(value.get("error") or ""), limit=240),
        "reason": str(value.get("reason") or ""),
    }


def _pins(provider: str, ids: set[str] | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not provider:
        return rows
    try:
        presets = list_preset_models()
    except Exception:
        return rows
    for item in presets:
        if str(item.get("provider") or "").strip().lower() != provider:
            continue
        mid = str(item.get("id") or "").strip()
        if not mid:
            continue
        rows.append({
            "id": mid,
            "label": str(item.get("label") or mid),
            "provider": provider,
            "confirmed": bool(ids is not None and mid in ids),
        })
    return rows


def _warnings(
    ids: set[str] | None,
    effective: dict[str, str],
    mentor: dict[str, str] | None,
) -> list[dict[str, str]]:
    if ids is None:
        return []
    found: list[dict[str, str]] = []
    model = (effective.get("model") or "").strip()
    if model and model not in ids:
        found.append({"code": "missing_from_cache", "target": "brain", "model": model})
    if mentor:
        other = (mentor.get("model") or "").strip()
        same = (mentor.get("provider") or "").strip().lower() == (effective.get("provider") or "").strip().lower()
        if other and same and other not in ids:
            found.append({"code": "missing_from_cache", "target": "mentor", "model": other})
    return found


def _providers() -> list[dict[str, Any]]:
    names = list(PROVIDER_DEFAULTS.keys())
    if "custom" not in names:
        names.append("custom")
    rows = []
    for name in names:
        rows.append({
            "id": name,
            "manual_only": name in MANUAL_ONLY,
            "show_base": name in _BASE_FIELD,
            "show_cache": name == "anthropic",
        })
    return rows


def _effective(provider: str, base: str, brain: dict[str, Any]) -> dict[str, str]:
    return {
        "provider": provider,
        "model": str(brain.get("model") or "").strip(),
        "base_url": base,
        "source": str(brain.get("source") or ""),
    }


def _brain() -> dict[str, Any]:
    try:
        from .llm_config import get_brain_config
        cfg = get_brain_config()
    except Exception:
        return {}
    return cfg if isinstance(cfg, dict) else {}


def _effective_base(provider: str, brain: dict[str, Any]) -> str:
    raw = str(brain.get("base_url") or "").strip()
    if raw:
        return raw
    return provider_default_base_url(provider)


def _mentor() -> dict[str, str] | None:
    try:
        from .mentor_helm import get_choice
        choice = get_choice()
    except Exception:
        return None
    if not isinstance(choice, dict):
        return None
    if not (choice.get("id") or choice.get("model") or choice.get("provider")):
        return None
    return {
        "id": str(choice.get("id") or ""),
        "provider": str(choice.get("provider") or ""),
        "model": str(choice.get("model") or ""),
    }


def _env_key(provider: str) -> str:
    env_name = PROVIDER_ENV_KEYS.get((provider or "").strip().lower(), "")
    if not env_name:
        return ""
    return (os.environ.get(env_name) or "").strip()


def _server_key(provider: str) -> str:
    """Key stays on the server. Browser refresh bodies are never read."""
    baked = str(_brain().get("api_key") or "").strip()
    if len(baked) >= 8 and baked not in _DUMMY_KEYS:
        return baked
    env = _env_key(provider)
    if len(env) >= 8 and env not in _DUMMY_KEYS:
        return env
    return ""


def _secret_values() -> list[str]:
    found: list[str] = []
    for env_name in PROVIDER_ENV_KEYS.values():
        val = (os.environ.get(env_name) or "").strip()
        if len(val) >= 8 and val not in _DUMMY_KEYS:
            found.append(val)
    extra = (os.environ.get("DAEDALUS_API_KEY") or "").strip()
    if len(extra) >= 8 and extra not in _DUMMY_KEYS:
        found.append(extra)
    try:
        brain = _brain()
    except Exception:
        brain = {}
    baked = str(brain.get("api_key") or "").strip()
    if len(baked) >= 8 and baked not in _DUMMY_KEYS:
        found.append(baked)
    return found


def _redact(text: str, *, limit: int | None = None) -> str:
    raw = str(text or "")
    for secret in _secret_values():
        raw = raw.replace(secret, "[redacted]")
    if limit is not None:
        raw = raw[:limit]
    return raw


def _scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items() if k != "api_key"}
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    if isinstance(value, str):
        return _redact(value)
    return value


def _stamp(now: datetime | None) -> datetime:
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def _iso(stamp: datetime) -> str:
    return stamp.astimezone(timezone.utc).isoformat()


def _parse_time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_stale(fetched_at: str, now: datetime) -> bool:
    parsed = _parse_time(fetched_at)
    if parsed is None:
        return False
    return (now - parsed) > STALE_AFTER


def _public_port(scheme: str, port: int | None) -> int | None:
    if port is None:
        return None
    if scheme == "https" and port == 443:
        return None
    if scheme == "http" and port == 80:
        return None
    return port


def _origin(url: str) -> tuple[str, str, int | None]:
    raw = (url or "").strip()
    if "://" not in raw:
        raw = "https://" + raw
    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    host = (parsed.hostname or "").lower()
    return scheme, host, _public_port(scheme, parsed.port)


def _same_origin(left: str, right: str) -> bool:
    a = _origin(left)
    b = _origin(right)
    return bool(a[1]) and a == b


def _host_is_loopback(hostname: str) -> bool:
    host = (hostname or "").strip().lower().rstrip(".")
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    if host == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return bool(ip.is_loopback)


def _loopback_url(url: str) -> bool:
    raw = (url or "").strip()
    if not raw:
        return False
    if "://" not in raw:
        raw = "http://" + raw
    parsed = urlparse(raw)
    if parsed.username or parsed.password:
        return False
    if (parsed.scheme or "").lower() not in {"http", "https"}:
        return False
    return _host_is_loopback(parsed.hostname or "")
