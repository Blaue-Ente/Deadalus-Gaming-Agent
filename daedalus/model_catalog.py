"""Live model catalog + auto-assignment of build / mentor / wrap-up roles.

OpenRouter ``GET /api/v1/models`` is the source of truth when an OpenRouter
key is present (cached under ``$DAEDALUS_DATA/model_catalog.json``). Other
providers contribute their ``PRESET_MODELS`` / defaults. Auto-assign picks:

* **build** — a tool-calling model under a price cap (the harness compensates
  for a weaker coder);
* **mentor** — Claude Haiku by default, upgraded to Sonnet when a typical
  build with the chosen builder is estimated under
  ``DAEDALUS_MENTOR_UPGRADE_BELOW_USD`` (10 ¢);
* **wrap_up** — the cheapest usable model among keyed providers.

Network failure falls back to a static table so Studio still has a picker.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Any

log = logging.getLogger(__name__)

CACHE_TTL_S = 6 * 3600
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
TYPICAL_BUILD_IN = 80_000
TYPICAL_BUILD_OUT = 40_000
DEFAULT_UPGRADE_USD = 0.10
BUILD_MAX_PROMPT_PER_M = 2.50
BUILD_MAX_COMPLETION_PER_M = 10.0

PREFERRED_BUILD = (
    "z-ai/glm-5.2",
    "moonshotai/kimi-k2.5",
    "moonshotai/kimi-k2",
    "deepseek/deepseek-chat-v3.1",
    "deepseek/deepseek-v3.2",
    "qwen/qwen3-coder-plus",
    "qwen/qwen3-coder-480b-a35b-instruct",
)
HAIKU_IDS = (
    "anthropic/claude-haiku-4.5",
    "anthropic/claude-haiku-4-5",
    "claude-haiku-4-5",
    "claude-haiku-4.5",
)
SONNET_IDS = (
    "anthropic/claude-sonnet-4.6",
    "anthropic/claude-sonnet-4-6",
    "anthropic/claude-sonnet-4.5",
    "anthropic/claude-sonnet-4-5",
    "claude-sonnet-4-6",
    "claude-sonnet-4.6",
    "claude-sonnet-4-5",
    "claude-sonnet-4.5",
)
# Native Anthropic ids → OpenRouter ids when we only have an OpenRouter key.
TO_OPENROUTER = {
    "claude-haiku-4-5": "anthropic/claude-haiku-4.5",
    "claude-haiku-4.5": "anthropic/claude-haiku-4.5",
    "claude-sonnet-4-6": "anthropic/claude-sonnet-4.6",
    "claude-sonnet-4.6": "anthropic/claude-sonnet-4.6",
    "claude-sonnet-4-5": "anthropic/claude-sonnet-4.5",
    "claude-sonnet-4.5": "anthropic/claude-sonnet-4.5",
    "claude-opus-4-7": "anthropic/claude-opus-4.7",
    "claude-opus-4.7": "anthropic/claude-opus-4.7",
}


@dataclass
class CatalogModel:
    id: str
    provider: str
    name: str = ""
    prompt_per_m: float = 0.0
    completion_per_m: float = 0.0
    context: int = 0
    max_output: int = 0
    tools: bool = False
    source: str = "static"

    def as_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["build_usd"] = round(self.estimate_build_usd(), 4)
        row["label"] = self.label()
        return row

    def label(self) -> str:
        name = self.name or self.id
        if self.prompt_per_m or self.completion_per_m:
            return f"{name}  ${self.prompt_per_m:g}/${self.completion_per_m:g} per 1M"
        return name

    def estimate_build_usd(self, n_in: int = TYPICAL_BUILD_IN, n_out: int = TYPICAL_BUILD_OUT) -> float:
        return (self.prompt_per_m * n_in + self.completion_per_m * n_out) / 1_000_000


@dataclass
class RolePick:
    role: str
    provider: str
    model: str
    mode: str = "auto"
    why: str = ""
    build_usd: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# Prices are USD per 1M tokens. Enough for tests and offline Studio.
_FALLBACK: tuple[CatalogModel, ...] = (
    CatalogModel("z-ai/glm-5.2", "openrouter", "GLM 5.2", 0.35, 1.40, 200_000, 16_384, True, "fallback"),
    CatalogModel("moonshotai/kimi-k2.5", "openrouter", "Kimi K2.5", 0.45, 2.20, 256_000, 16_384, True, "fallback"),
    CatalogModel("deepseek/deepseek-v3.2", "openrouter", "DeepSeek V3.2", 0.27, 1.10, 128_000, 16_384, True, "fallback"),
    CatalogModel("deepseek/deepseek-v3.2", "novita", "DeepSeek V3.2", 0.27, 1.10, 128_000, 8_192, True, "fallback"),
    CatalogModel("anthropic/claude-haiku-4.5", "openrouter", "Claude Haiku 4.5", 1.00, 5.00, 200_000, 8_192, True, "fallback"),
    CatalogModel("anthropic/claude-sonnet-4.6", "openrouter", "Claude Sonnet 4.6", 3.00, 15.00, 200_000, 16_384, True, "fallback"),
    CatalogModel("anthropic/claude-sonnet-4.5", "openrouter", "Claude Sonnet 4.5", 3.00, 15.00, 200_000, 16_384, True, "fallback"),
    CatalogModel("claude-haiku-4-5", "anthropic", "Claude Haiku 4.5", 1.00, 5.00, 200_000, 8_192, True, "fallback"),
    CatalogModel("claude-sonnet-4-6", "anthropic", "Claude Sonnet 4.6", 3.00, 15.00, 200_000, 16_384, True, "fallback"),
    CatalogModel("meta-llama/llama-3.1-8b-instruct", "openrouter", "Llama 3.1 8B", 0.02, 0.05, 128_000, 8_192, False, "fallback"),
    CatalogModel("meta-llama/llama-3.1-8b-instruct", "novita", "Llama 3.1 8B", 0.02, 0.02, 128_000, 8_192, False, "fallback"),
    CatalogModel("google/gemini-2.0-flash-001", "openrouter", "Gemini 2.0 Flash", 0.10, 0.40, 1_000_000, 8_192, True, "fallback"),
    CatalogModel("qwen/qwen3-next-80b-a3b-instruct", "nvidia", "Qwen3-Next 80B", 0.0, 0.0, 128_000, 8_192, True, "fallback"),
)


def upgrade_below_usd() -> float:
    raw = (os.environ.get("DAEDALUS_MENTOR_UPGRADE_BELOW_USD") or "").strip()
    if not raw:
        return DEFAULT_UPGRADE_USD
    try:
        return max(0.0, float(raw))
    except ValueError:
        return DEFAULT_UPGRADE_USD


def keyed_providers() -> list[str]:
    from .llm_config import PROVIDER_DEFAULTS, resolve_provider_api_key
    from .local_llm import is_local_provider

    out: list[str] = []
    for p in PROVIDER_DEFAULTS:
        if is_local_provider(p):
            continue
        if resolve_provider_api_key(p):
            out.append(p)
    try:
        from .local_llm import probe_all
        for row in probe_all() or []:
            if row.get("ok") and row.get("provider") and row["provider"] not in out:
                out.append(str(row["provider"]))
    except Exception:
        pass
    return out


def _cache_path():
    from .paths import data_root
    return data_root() / "model_catalog.json"


def _read_cache() -> dict[str, Any] | None:
    try:
        path = _cache_path()
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if time.time() - float(data.get("fetched_at") or 0) > CACHE_TTL_S:
            return None
        return data
    except Exception:
        return None


def _write_cache(payload: dict[str, Any]) -> None:
    try:
        path = _cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        log.debug("model catalog cache write skipped: %s", exc)


def _per_m(raw: Any) -> float:
    try:
        n = float(raw or 0)
    except (TypeError, ValueError):
        return 0.0
    # OpenRouter quotes USD per token; anything ≥ 0.01 is already per-million.
    if 0 < n < 0.01:
        return round(n * 1_000_000, 6)
    return round(n, 6)


def _from_openrouter(row: dict[str, Any]) -> CatalogModel:
    pricing = row.get("pricing") or {}
    top = row.get("top_provider") or {}
    params = {str(p).lower() for p in (row.get("supported_parameters") or [])}
    mid = str(row.get("id") or "")
    return CatalogModel(
        id=mid,
        provider="openrouter",
        name=str(row.get("name") or mid),
        prompt_per_m=_per_m(pricing.get("prompt")),
        completion_per_m=_per_m(pricing.get("completion")),
        context=int(row.get("context_length") or top.get("context_length") or 0),
        max_output=int(top.get("max_completion_tokens") or 0),
        tools="tools" in params or "tool_choice" in params,
        source="openrouter",
    )


def fetch_openrouter(*, force: bool = False, timeout_s: float = 12.0) -> list[CatalogModel]:
    if not force:
        cached = _read_cache()
        if cached and cached.get("models"):
            return [CatalogModel(**m) for m in cached["models"] if m.get("provider") == "openrouter"]
    from .llm_config import resolve_provider_api_key

    key = resolve_provider_api_key("openrouter")
    if not key:
        return []
    req = urllib.request.Request(
        OPENROUTER_MODELS_URL,
        headers={"Authorization": f"Bearer {key}", "User-Agent": "Daedalus"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        log.warning("OpenRouter model catalog unavailable: %s", exc)
        cached = _read_cache()
        if cached and cached.get("models"):
            return [CatalogModel(**m) for m in cached["models"] if m.get("provider") == "openrouter"]
        return []
    models = [_from_openrouter(r) for r in (body.get("data") or []) if r.get("id")]
    _write_cache({"fetched_at": time.time(), "models": [asdict(m) for m in models]})
    return models


def _from_presets() -> list[CatalogModel]:
    from .llm_config import PRESET_MODELS, PROVIDER_DEFAULTS, is_replit_preset

    out: list[CatalogModel] = []
    seen: set[tuple[str, str]] = set()
    for row in PRESET_MODELS:
        if is_replit_preset(row):
            continue
        provider = str(row.get("provider") or "")
        mid = str(row.get("id") or "")
        if not provider or not mid or (provider, mid) in seen:
            continue
        seen.add((provider, mid))
        pd = PROVIDER_DEFAULTS.get(provider) or {}
        out.append(CatalogModel(
            id=mid,
            provider=provider,
            name=str(row.get("label") or mid),
            tools=True,
            source="preset",
            max_output=8192 if pd.get("sdk") else 0,
        ))
    return out


def all_models(*, refresh: bool = False) -> list[CatalogModel]:
    rows = list(_FALLBACK)
    seen = {(m.provider, m.id): m for m in rows}
    for m in fetch_openrouter(force=refresh) + _from_presets():
        key = (m.provider, m.id)
        old = seen.get(key)
        if old is not None:
            # Live OpenRouter replaces fallback. A zero-price preset does not
            # wipe published rates we already know.
            if (
                m.source == "preset"
                and not (m.prompt_per_m or m.completion_per_m)
                and (old.prompt_per_m or old.completion_per_m)
            ):
                continue
            rows = [x for x in rows if (x.provider, x.id) != key]
        seen[key] = m
        rows.append(m)
    return rows


def find(model_id: str, provider: str = "") -> CatalogModel | None:
    mid = (model_id or "").strip()
    want = (provider or "").strip().lower()
    if not mid:
        return None
    for m in all_models():
        if m.id == mid and (not want or m.provider == want):
            return m
    return None


def max_output_tokens(model_id: str, provider: str = "", *, default: int = 16_384) -> int:
    m = find(model_id, provider)
    if m is None or not m.max_output:
        return default
    return max(1024, min(int(m.max_output), 32_768))


def estimate_build_usd(model_id: str, provider: str = "") -> float:
    m = find(model_id, provider)
    if m is None:
        return 0.05
    return m.estimate_build_usd()


def list_catalog(
    *,
    q: str = "",
    provider: str = "",
    tools_only: bool = False,
    keyed_only: bool = True,
    limit: int = 80,
    refresh: bool = False,
) -> dict[str, Any]:
    keys = set(keyed_providers())
    needle = q.strip().lower()
    want = provider.strip().lower()
    rows: list[CatalogModel] = []
    for m in all_models(refresh=refresh):
        if keyed_only and keys and m.provider not in keys:
            continue
        if want and m.provider != want:
            continue
        if tools_only and not m.tools:
            continue
        hay = f"{m.id} {m.name} {m.provider}".lower()
        if needle and needle not in hay:
            continue
        rows.append(m)
    rows.sort(key=_wrap_sort_key)
    return {
        "ok": True,
        "providers": sorted(keys),
        "n": min(len(rows), max(1, limit)),
        "total": len(rows),
        "models": [m.as_dict() for m in rows[: max(1, min(limit, 200))]],
        "upgrade_below_usd": upgrade_below_usd(),
    }


def _wrap_sort_key(m: CatalogModel) -> tuple[float, str]:
    """Cheapest real model first. OpenRouter sentinels are -$1; presets with
    no rates are unknown, not free."""
    if m.prompt_per_m < 0 or m.completion_per_m < 0:
        return (1e9, m.id)
    if m.source == "preset" and m.prompt_per_m == 0 and m.completion_per_m == 0:
        return (1e8, m.id)
    return (m.prompt_per_m + m.completion_per_m, m.id)


def _usable_wrap(m: CatalogModel) -> bool:
    if m.prompt_per_m < 0 or m.completion_per_m < 0:
        return False
    if m.source == "preset" and m.prompt_per_m == 0 and m.completion_per_m == 0:
        return False
    return True


def _pick(models: list[CatalogModel], ids: tuple[str, ...], providers: set[str]) -> CatalogModel | None:
    by_id = {(m.provider, m.id): m for m in models}
    for mid in ids:
        for p in providers:
            hit = by_id.get((p, mid))
            if hit:
                return hit
        for m in models:
            if m.id == mid and m.provider in providers:
                return m
    return None


_ASSIGN_TTL = 30.0
_ASSIGN_CACHE: tuple[float, str, dict[str, RolePick]] | None = None


def clear_assign_cache() -> None:
    global _ASSIGN_CACHE
    _ASSIGN_CACHE = None


def auto_assign(
    *,
    models: list[CatalogModel] | None = None,
    providers: list[str] | None = None,
    primary: dict[str, Any] | None = None,
) -> dict[str, RolePick]:
    """Pick build / mentor / wrap_up from what we can actually call."""
    global _ASSIGN_CACHE
    primary = primary or {}
    cache_key = ""
    if models is None and providers is None:
        cache_key = f"{primary.get('provider')}|{primary.get('model')}|{upgrade_below_usd()}"
        if _ASSIGN_CACHE and _ASSIGN_CACHE[1] == cache_key and time.time() - _ASSIGN_CACHE[0] < _ASSIGN_TTL:
            return _ASSIGN_CACHE[2]
    keys = set(providers if providers is not None else keyed_providers())
    rows = list(models if models is not None else all_models())
    # No key → nothing we can call; roles fall back to the primary model.
    pool = [m for m in rows if m.provider in keys] if keys else []
    primary_provider = str(primary.get("provider") or "")
    primary_model = str(primary.get("model") or "")

    build = _pick(pool, PREFERRED_BUILD, keys)
    if build is None:
        affordable = [
            m for m in pool
            if m.tools
            and m.prompt_per_m <= BUILD_MAX_PROMPT_PER_M
            and m.completion_per_m <= BUILD_MAX_COMPLETION_PER_M
        ]
        affordable.sort(key=lambda m: (m.prompt_per_m + 0.4 * m.completion_per_m, -m.context))
        build = affordable[0] if affordable else None
    if build is None and primary_model:
        build = CatalogModel(primary_model, primary_provider or "openrouter", primary_model, tools=True)

    build_usd = build.estimate_build_usd() if build else 0.05
    cap = upgrade_below_usd()
    if build is not None and build_usd < cap:
        mentor = _pick(pool, SONNET_IDS, keys) or _pick(pool, HAIKU_IDS, keys)
        sonnet = bool(mentor) and (mentor.id in SONNET_IDS or "sonnet" in mentor.id)
        mentor_why = (
            f"typical build ≈ ${build_usd:.3f} < ${cap:.2f} — "
            + ("Sonnet mentor" if sonnet else "strongest Claude available")
        )
    else:
        mentor = _pick(pool, HAIKU_IDS, keys) or _pick(pool, SONNET_IDS, keys)
        mentor_why = (
            f"typical build ≈ ${build_usd:.3f} ≥ ${cap:.2f} — Haiku mentor"
            if build_usd >= cap else "Haiku mentor (default)"
        )
    if mentor is None:
        mentor = build

    wrap = min(
        (m for m in pool if _usable_wrap(m)),
        key=_wrap_sort_key,
        default=None,
    )
    if wrap is None:
        wrap = build

    def _role(name: str, m: CatalogModel | None, why: str) -> RolePick:
        if m is None:
            return RolePick(name, primary_provider, primary_model, "auto", why or "primary")
        return RolePick(name, m.provider, m.id, "auto", why, m.estimate_build_usd())

    out = {
        "build": _role("build", build, "tool-calling under the price cap"),
        "mentor": _role("mentor", mentor, mentor_why),
        "wrap_up": _role("wrap_up", wrap, "cheapest keyed model for the closing message"),
    }
    if cache_key:
        _ASSIGN_CACHE = (time.time(), cache_key, out)
    return out


def remap_to_keyed(provider: str, model: str, *, providers: list[str] | None = None) -> tuple[str, str]:
    """If the assigned provider has no key, send Claude through OpenRouter."""
    keys = set(providers if providers is not None else keyed_providers())
    p = (provider or "").strip().lower()
    m = (model or "").strip()
    if not p or p in keys:
        return p, m
    if "openrouter" in keys:
        return "openrouter", TO_OPENROUTER.get(m, m if "/" in m else m)
    if keys:
        return next(iter(keys)), m
    return p, m
