"""Model Registry — "досиета" на всеки LLM модел за smart worker routing.

Хранилище: Replit DB  `adn:model_registry`  (JSON list на ModelEntry)
Auto-mode:            `adn:worker_auto_mode` (bool)
Named configs:        `adn:worker_named_configs` (dict id→config)

Публичен API:
    get_registry()                         → list[ModelEntry]
    get_model(id)                          → ModelEntry | None
    set_model(id, fields)                  → None
    delete_model(id)                       → bool
    reset_to_defaults()                    → None
    update_stats(id, success, latency_ms)  → None   (called by workers after each call)
    select_for_task(task_desc, budget)     → ModelEntry | None
    get_auto_mode()                        → bool
    set_auto_mode(enabled)                 → None
    list_named_configs()                   → list[NamedConfig]
    save_named_config(name, slots, icon)   → str  (id)
    load_named_config(id)                  → NamedConfig | None
    delete_named_config(id)                → bool
"""
from __future__ import annotations

import logging
import re
import time
import uuid
from typing import TypedDict

log = logging.getLogger(__name__)

_DB_KEY_REGISTRY   = "adn:model_registry"
_DB_KEY_AUTO_MODE  = "adn:worker_auto_mode"
_DB_KEY_NAMED_CFGS = "adn:worker_named_configs"
_DB_KEY_ACTIVE_MIX = "adn:llm_active_mix"

# ── Capability tags ──────────────────────────────────────────────────────────
# Кратко описание на всеки таг за UI.
CAPABILITY_TAGS: dict[str, str] = {
    "code":             "Писане на код",
    "code_review":      "Code review / debugging",
    "planning":         "Планиране и декомпозиция",
    "reasoning":        "Deep reasoning / chain-of-thought",
    "analysis":         "Анализ и синтез",
    "review":           "Финален review / одобрение",
    "writing":          "Писане на текст / документация",
    "extraction":       "Извличане на структурирани данни",
    "classification":   "Класификация / тагване",
    "translation":      "Превод",
    "summarization":    "Резюмиране",
    "math":             "Математика / изчисления",
    "long_context":     "Дълъг контекст (>64K токени)",
    "chinese":          "Китайски езикови задачи",
    "vision":           "Обработка на изображения",
    "fast":             "Много бърз отговор (<3s)",
    "cheap":            "Много евтин (<$0.30/MTok)",
    "tools":            "Function calling / tool use",
    "orchestration":    "Оркестрация на сложни планове",
}

# ── Keyword → capability mapping (за auto task parsing) ─────────────────────
_KEYWORD_MAP: dict[str, list[str]] = {
    "code":          ["код", "code", "function", "class", "скрипт", "script",
                      "програм", "program", "implement", "имплемент", "напиши",
                      "написан", "html", "css", "javascript", "python", "endpoint",
                      "api", "query", "sql", "pandas", "игра", "game"],
    "code_review":   ["review", "debug", "грешк", "bug", "fix", "поправи",
                      "провери код", "check code", "дебъг"],
    "planning":      ["план", "plan", "декомпоз", "decompose", "стъпки", "steps",
                      "разбий", "breakdown", "как да", "how to", "подход",
                      "архитектур", "дизайн", "design"],
    "reasoning":     ["анализирай", "analyze", "reasoning", "reasoning",
                      "математика", "math", "изчисли", "calculate",
                      "логика", "logic", "защо", "why", "обясни защо",
                      "multi-step", "wyckoff", "elliott"],
    "review":        ["провери", "review", "одобри", "approve",
                      "архитект", "architect", "качество", "quality",
                      "финален", "final", "вземи решение", "assess"],
    "analysis":      ["анализ", "analysis", "сравни", "compare",
                      "изследвай", "research", "тенденци", "trend",
                      "оцени", "evaluate", "стратег"],
    "writing":       ["напиши текст", "документация", "docs",
                      "статия", "article", "обяснение", "explanation",
                      "readme", "report", "доклад"],
    "extraction":    ["извлечи", "extract", "парсни", "parse",
                      "json", "таблица", "table", "структурирай"],
    "classification":["класифицирай", "classify", "тагни", "tag",
                      "категоризирай", "categorize", "определи вид"],
    "summarization": ["резюме", "summary", "резюмирай", "summarize",
                      "накратко", "brief", "обобщи"],
    "long_context":  ["дълъг", "long", "документ", "document",
                      "целия файл", "full file", "голям контекст",
                      "много текст", "128k", "large"],
    "math":          ["формул", "formula", "изчисли", "calculat",
                      "математик", "statistic", "процент", "percent"],
    "fast":          ["бързо", "quick", "кратко", "short",
                      "1-2", "едно изречение"],
    "cheap":         ["евтин", "cheap", "бюджет", "budget",
                      "минимален разход", "минимал"],
}

# ── Tier → relative cost weight for scoring ─────────────────────────────────
_TIER_COST_WEIGHT = {
    "fast":      1.0,
    "cheap":     1.5,
    "balanced":  0.7,
    "deep":      0.5,
    "coder":     0.8,
    "reasoning": 0.3,   # expensive but worth it for reasoning tasks
    "review":    0.2,   # most expensive
}

# ── Default registry seed ────────────────────────────────────────────────────
DEFAULT_REGISTRY: list[dict] = [
    {
        "id":             "gemini-flash-openrouter",
        "display_name":   "Gemini 2.5 Flash",
        "provider":       "openrouter",
        "model_id":       "google/gemini-2.5-flash",
        "capabilities":   ["fast", "extraction", "classification", "cheap",
                           "translation", "summarization", "vision"],
        "strengths":      "Най-бърз (~1-3s), vision, добър JSON/класификация, огромен контекст (1M)",
        "weaknesses":     "По-слаб reasoning и код от DeepSeek",
        "cost_input":     0.15,
        "cost_output":    0.60,
        "context_window": 1_000_000,
        "tier":           "fast",
        "supports_tools": True,
        "enabled":        True,
        "fallback_to":    "claude-haiku-openrouter",
        "success_rate":   0.97,
        "avg_latency_ms": 2000,
        "worker_slot":    "fast",
    },
    {
        "id":             "claude-haiku-openrouter",
        "display_name":   "Claude Haiku 3.5",
        "provider":       "openrouter",
        "model_id":       "anthropic/claude-3-5-haiku",
        "capabilities":   ["fast", "extraction", "classification", "cheap",
                           "translation", "summarization", "tools"],
        "strengths":      "Послушен, бърз, стабилен при следване на инструкции, tool calling",
        "weaknesses":     "Ограничен reasoning, кратък изход, по-скъп от Gemini Flash",
        "cost_input":     0.80,
        "cost_output":    4.0,
        "context_window": 200_000,
        "tier":           "fast",
        "supports_tools": True,
        "enabled":        True,
        "fallback_to":    "gemini-flash-openrouter",
        "success_rate":   0.96,
        "avg_latency_ms": 3000,
        "worker_slot":    "fast",
    },
    {
        "id":             "llama-3.3-openrouter",
        "display_name":   "Llama 3.3 70B",
        "provider":       "openrouter",
        "model_id":       "meta-llama/llama-3.3-70b-instruct",
        "capabilities":   ["balanced", "analysis", "research",
                           "writing", "cheap", "tools"],
        "strengths":      "Добър баланс цена/качество, финансов/технически анализ, tool calling",
        "weaknesses":     "Hallucinations при специфични факти, по-слаб код от DeepSeek",
        "cost_input":     0.20,
        "cost_output":    0.60,
        "context_window": 128_000,
        "tier":           "balanced",
        "supports_tools": True,
        "enabled":        True,
        "fallback_to":    "deepseek-v3-novita",
        "success_rate":   0.94,
        "avg_latency_ms": 5000,
        "worker_slot":    "balanced",
    },
    {
        "id":             "deepseek-v3-novita",
        "display_name":   "DeepSeek V3 (Novita)",
        "provider":       "novita",
        "model_id":       "deepseek/deepseek-v3",
        "capabilities":   ["code", "extraction", "cheap", "writing",
                           "analysis", "planning"],
        "strengths":      "Отличен код, Novita по-евтин от OpenRouter, следва инструкции точно",
        "weaknesses":     "Без tool calling, по-слаб при deep reasoning",
        "cost_input":     0.22,
        "cost_output":    0.88,
        "context_window": 64_000,
        "tier":           "deep",
        "supports_tools": False,
        "enabled":        True,
        "fallback_to":    "deepseek-v3-openrouter",
        "success_rate":   0.95,
        "avg_latency_ms": 7000,
        "worker_slot":    "deep",
    },
    {
        "id":             "deepseek-v3-openrouter",
        "display_name":   "DeepSeek V3.2 (OpenRouter)",
        "provider":       "openrouter",
        "model_id":       "deepseek/deepseek-v3.2",
        "capabilities":   ["code", "extraction", "writing", "analysis",
                           "planning", "cheap"],
        "strengths":      "Най-нова версия на V3, отличен код, синтез на сложни задачи",
        "weaknesses":     "Без tool calling, по-бавен от Novita версията",
        "cost_input":     0.27,
        "cost_output":    1.10,
        "context_window": 64_000,
        "tier":           "deep",
        "supports_tools": False,
        "enabled":        True,
        "fallback_to":    "deepseek-v3-novita",
        "success_rate":   0.95,
        "avg_latency_ms": 8000,
        "worker_slot":    "deep",
    },
    {
        "id":             "qwen3-coder-novita",
        "display_name":   "Qwen3 Coder 30B (Novita)",
        "provider":       "novita",
        "model_id":       "qwen/qwen3-coder-30b-a3b-instruct",
        "capabilities":   ["code", "code_review", "cheap"],
        "strengths":      "Специализиран coder, debugging, code review, много евтин",
        "weaknesses":     "Narrower use cases извън код, по-малък контекст",
        "cost_input":     0.15,
        "cost_output":    0.60,
        "context_window": 32_000,
        "tier":           "coder",
        "supports_tools": False,
        "enabled":        True,
        "fallback_to":    "deepseek-v3-novita",
        "success_rate":   0.93,
        "avg_latency_ms": 4000,
        "worker_slot":    "coder",
    },
    {
        "id":             "qwen3-coder-big-novita",
        "display_name":   "Qwen3 Coder 480B (Novita) ⚡",
        "provider":       "novita",
        "model_id":       "qwen/qwen3-coder-480b-a35b-instruct",
        "capabilities":   ["code", "code_review", "planning", "reasoning"],
        "strengths":      "Largest code model, отличен за сложни архитектурни задачи",
        "weaknesses":     "По-бавен и скъп от 30B версията",
        "cost_input":     0.50,
        "cost_output":    1.50,
        "context_window": 64_000,
        "tier":           "coder",
        "supports_tools": False,
        "enabled":        True,
        "fallback_to":    "qwen3-coder-novita",
        "success_rate":   0.94,
        "avg_latency_ms": 12000,
        "worker_slot":    "coder",
    },
    {
        "id":             "kimi-k2-novita",
        "display_name":   "Kimi K2 (Novita)",
        "provider":       "novita",
        "model_id":       "moonshotai/kimi-k2-instruct",
        "capabilities":   ["code", "planning", "analysis", "long_context",
                           "chinese", "tools"],
        "strengths":      "Агентски модел, добър при code+planning комбинации, tool calling, Chinese",
        "weaknesses":     "Нов модел, по-малко тестван в production",
        "cost_input":     0.60,
        "cost_output":    2.50,
        "context_window": 128_000,
        "tier":           "balanced",
        "supports_tools": True,
        "enabled":        True,
        "fallback_to":    "llama-3.3-openrouter",
        "success_rate":   0.90,
        "avg_latency_ms": 8000,
        "worker_slot":    "balanced",
    },
    {
        "id":             "deepseek-r1-openrouter",
        "display_name":   "DeepSeek R1-0528",
        "provider":       "openrouter",
        "model_id":       "deepseek/deepseek-r1-0528",
        "capabilities":   ["reasoning", "planning", "math", "analysis"],
        "strengths":      "Chain-of-thought reasoning, планиране, математика, multi-step анализ",
        "weaknesses":     "Бавен (30-100s), многословен, не за прости задачи",
        "cost_input":     0.55,
        "cost_output":    2.19,
        "context_window": 64_000,
        "tier":           "reasoning",
        "supports_tools": False,
        "enabled":        True,
        "fallback_to":    "deepseek-v3-openrouter",
        "success_rate":   0.90,
        "avg_latency_ms": 45000,
        "worker_slot":    "planner",
    },
    {
        "id":             "deepseek-v4-pro-alltoken",
        "display_name":   "DeepSeek V4 Pro (AllToken) 🔥",
        "provider":       "alltoken",
        "model_id":       "deepseek-v4-pro",
        "capabilities":   ["code", "code_review", "reasoning", "planning",
                           "analysis", "review", "writing", "tools"],
        "strengths":      "На промоция! Флагман AllToken — мощен код + reasoning. Приближава Claude качеството.",
        "weaknesses":     "Без vision, по-скъп от V4 Flash",
        "cost_input":     0.50,
        "cost_output":    2.00,
        "context_window": 128_000,
        "tier":           "deep",
        "supports_tools": True,
        "enabled":        True,
        "fallback_to":    "claude-sonnet-openrouter",
        "success_rate":   0.95,
        "avg_latency_ms": 7000,
        "worker_slot":    "deep",
    },
    {
        "id":             "poolside-laguna-m1",
        "display_name":   "Poolside Laguna M.1",
        "provider":       "poolside",
        "model_id":       "poolside/laguna-m.1",
        "capabilities":   ["code", "code_review", "planning", "tools", "long_context"],
        "strengths":      "Специализиран coding agent модел; 262K контекст; безплатен в момента (beta)",
        "weaknesses":     "Нов/непознат модел — по-малко тестван в production; само text",
        "cost_input":     0.0,
        "cost_output":    0.0,
        "context_window": 262_144,
        "tier":           "coder",
        "supports_tools": True,
        "enabled":        True,
        "fallback_to":    "qwen3-coder-big-novita",
        "success_rate":   0.92,
        "avg_latency_ms": 6000,
        "worker_slot":    "coder",
    },
    {
        "id":             "qwen35-397b-nvidia",
        "display_name":   "Qwen3.5 397B (NVIDIA)",
        "provider":       "nvidia",
        "model_id":       "qwen/qwen3.5-397b-a17b",
        "capabilities":   ["code", "reasoning", "planning", "tools", "long_context"],
        "strengths":      "Голям MoE (A17B активни); силен за код и игри; NVIDIA free tier ($0)",
        "weaknesses":     "Rate-limited (free preview); без vision",
        "cost_input":     0.0,
        "cost_output":    0.0,
        "context_window": 131_072,
        "tier":           "deep",
        "supports_tools": True,
        "enabled":        True,
        "fallback_to":    "deepseek-v4-pro-alltoken",
        "success_rate":   0.93,
        "avg_latency_ms": 7000,
        "worker_slot":    "coder",
    },
    {
        "id":             "llama4-maverick-nvidia",
        "display_name":   "Llama 4 Maverick (NVIDIA)",
        "provider":       "nvidia",
        "model_id":       "meta/llama-4-maverick-17b-128e-instruct",
        "capabilities":   ["vision", "chat", "tools", "long_context"],
        "strengths":      "Multimodal (vision) за asset/скрийншот QA; 1M контекст; NVIDIA free tier ($0)",
        "weaknesses":     "Rate-limited (free preview); не топ за чист код",
        "cost_input":     0.0,
        "cost_output":    0.0,
        "context_window": 1_000_000,
        "tier":           "balanced",
        "supports_tools": True,
        "enabled":        True,
        "fallback_to":    "claude-sonnet-openrouter",
        "success_rate":   0.90,
        "avg_latency_ms": 6000,
        "worker_slot":    "balanced",
    },
    {
        "id":             "claude-sonnet-openrouter",
        "display_name":   "Claude Sonnet 4.5",
        "provider":       "openrouter",
        "model_id":       "anthropic/claude-sonnet-4-5",
        "capabilities":   ["review", "reasoning", "orchestration", "writing",
                           "analysis", "tools"],
        "strengths":      "Нюансирано разбиране, финален review, complex reasoning, tool calling",
        "weaknesses":     "Скъп ($3/MTok вход, $15/MTok изход) — само при реална нужда",
        "cost_input":     3.0,
        "cost_output":    15.0,
        "context_window": 200_000,
        "tier":           "review",
        "supports_tools": True,
        "enabled":        True,
        "fallback_to":    "deepseek-v3-openrouter",
        "success_rate":   0.98,
        "avg_latency_ms": 8000,
        "worker_slot":    "reviewer",
    },
]


# ── DB helpers ───────────────────────────────────────────────────────────────

def _db_read(key: str, default):
    from . import kv
    raw = kv.get(key)
    return default if raw is None else raw


def _db_write(key: str, value) -> None:
    from . import kv
    try:
        kv.set(key, value)
    except Exception as exc:
        log.warning("model_registry: DB write failed [%s]: %s", key, exc)


# ── Registry CRUD ────────────────────────────────────────────────────────────

def get_registry() -> list[dict]:
    """Return full model registry (seed + any DB overrides/additions)."""
    stored = _db_read(_DB_KEY_REGISTRY, None)
    if stored is None:
        return [dict(e) for e in DEFAULT_REGISTRY]
    return stored


def get_model(model_id: str) -> dict | None:
    for entry in get_registry():
        if entry.get("id") == model_id:
            return entry
    return None


def set_model(model_id: str, fields: dict) -> None:
    """Upsert a model entry. `fields` merged into existing or created fresh."""
    registry = get_registry()
    for i, entry in enumerate(registry):
        if entry.get("id") == model_id:
            registry[i] = {**entry, **fields, "id": model_id}
            _db_write(_DB_KEY_REGISTRY, registry)
            return
    # New entry
    registry.append({**fields, "id": model_id})
    _db_write(_DB_KEY_REGISTRY, registry)


def delete_model(model_id: str) -> bool:
    registry = get_registry()
    new = [e for e in registry if e.get("id") != model_id]
    if len(new) == len(registry):
        return False
    _db_write(_DB_KEY_REGISTRY, new)
    return True


def reset_to_defaults() -> None:
    _db_write(_DB_KEY_REGISTRY, [dict(e) for e in DEFAULT_REGISTRY])


def update_stats(model_id: str, *, success: bool, latency_ms: int) -> None:
    """EMA update of success_rate and avg_latency after each worker call."""
    entry = get_model(model_id)
    if not entry:
        return
    alpha = 0.1  # smoothing factor
    old_sr = entry.get("success_rate", 0.95)
    old_lat = entry.get("avg_latency_ms", 5000)
    entry["success_rate"]   = round(old_sr * (1 - alpha) + (1.0 if success else 0.0) * alpha, 4)
    entry["avg_latency_ms"] = int(old_lat * (1 - alpha) + latency_ms * alpha)
    entry["last_used"]      = int(time.time())
    set_model(model_id, entry)


# ── Auto-mode ────────────────────────────────────────────────────────────────

def get_auto_mode() -> bool:
    return bool(_db_read(_DB_KEY_AUTO_MODE, False))


def set_auto_mode(enabled: bool) -> None:
    _db_write(_DB_KEY_AUTO_MODE, enabled)


# ── Task → Model selector ────────────────────────────────────────────────────

def _extract_caps_from_task(task_desc: str) -> list[str]:
    """Extract capability tags from a task description via keyword matching."""
    text = task_desc.lower()
    found: set[str] = set()
    for cap, keywords in _KEYWORD_MAP.items():
        for kw in keywords:
            if kw in text:
                found.add(cap)
                break
    return list(found)


def _score_model(entry: dict, required_caps: list[str], budget_hint: str) -> float:
    """Score a model for a task. Higher = better match."""
    if not entry.get("enabled", True):
        return -1.0
    caps = entry.get("capabilities", [])
    score = 0.0

    # Capability match (main signal)
    matched = sum(1 for c in required_caps if c in caps)
    score += matched * 12.0

    # Success rate bonus
    score += entry.get("success_rate", 0.90) * 5.0

    # Cost factor (cheaper = small bonus, unless task needs expensive tier)
    cost = entry.get("cost_input", 1.0)
    if budget_hint == "cheap":
        score += max(0, (2.0 - cost) * 3.0)
    elif budget_hint == "quality":
        # For quality, slight bias toward higher-tier models
        tier_bonus = {"review": 3, "reasoning": 2, "deep": 1, "coder": 1}.get(
            entry.get("tier", ""), 0)
        score += tier_bonus

    # Latency penalty (slight — only breaks ties)
    lat = entry.get("avg_latency_ms", 5000)
    score -= lat / 20000.0  # max ~0.5 penalty

    # Curated benchmark bonus (vs Claude Opus 4.7 + task fit + value)
    try:
        from .llm_benchmark import auto_mode_benchmark_bonus
        score += auto_mode_benchmark_bonus(
            entry.get("model_id") or "",
            required_caps,
            budget_hint,
        )
    except Exception:
        pass

    return score


def select_for_task(
    task_desc: str,
    budget_hint: str = "balanced",
    required_caps: list[str] | None = None,
    preferred_tier: str | None = None,
) -> dict | None:
    """Pick the best model for a task description.

    Args:
        task_desc:     Natural language task (used for keyword extraction).
        budget_hint:   "cheap" | "balanced" | "quality" — cost preference.
        required_caps: Override auto-extracted capabilities.
        preferred_tier: If set, only consider models of this tier.

    Returns:
        Best matching ModelEntry or None if registry is empty / all disabled.
    """
    caps = required_caps if required_caps is not None else _extract_caps_from_task(task_desc)
    registry = get_registry()
    candidates = [e for e in registry if e.get("enabled", True)]
    if preferred_tier:
        tier_candidates = [e for e in candidates if e.get("tier") == preferred_tier]
        if tier_candidates:
            candidates = tier_candidates

    if not candidates:
        return None

    scored = [(e, _score_model(e, caps, budget_hint)) for e in candidates]
    scored.sort(key=lambda x: x[1], reverse=True)
    best = scored[0][0]
    log.debug(
        "model_registry: selected %s (score=%.1f) for caps=%s",
        best.get("display_name"), scored[0][1], caps,
    )
    return best


def iter_candidates_for_task(
    task_desc: str,
    budget_hint: str = "balanced",
    required_caps: list[str] | None = None,
    preferred_tier: str | None = None,
) -> list[dict]:
    """Return registry entries scored for this task, best first (for filtered selection)."""
    caps = required_caps if required_caps is not None else _extract_caps_from_task(task_desc)
    registry = get_registry()
    candidates = [e for e in registry if e.get("enabled", True)]
    if preferred_tier:
        tier_candidates = [e for e in candidates if e.get("tier") == preferred_tier]
        if tier_candidates:
            candidates = tier_candidates
    if not candidates:
        return []
    scored = [(e, _score_model(e, caps, budget_hint)) for e in candidates]
    scored.sort(key=lambda x: x[1], reverse=True)
    return [e for e, _ in scored]


def explain_selection(task_desc: str, budget_hint: str = "balanced") -> dict:
    """Return selection result + reasoning for admin test UI."""
    caps = _extract_caps_from_task(task_desc)
    registry = get_registry()
    candidates = [e for e in registry if e.get("enabled", True)]
    scored = sorted(
        [(e, _score_model(e, caps, budget_hint)) for e in candidates],
        key=lambda x: x[1], reverse=True,
    )
    try:
        from .llm_benchmark import opus47_pct, profile_for_model
        def _top_row(e: dict, s: float) -> dict:
            mid = e.get("model_id") or ""
            prof = profile_for_model(mid)
            return {
                "id":           e.get("id"),
                "display_name": e.get("display_name"),
                "model_id":     mid,
                "score":        round(s, 2),
                "tier":         e.get("tier"),
                "opus47_pct":   prof["opus47_pct"],
                "best_for_bg":  prof["best_for_bg"],
                "value_index":  prof["value_index"],
            }
        top3 = [_top_row(e, s) for e, s in scored[:3]]
    except Exception:
        top3 = [{"id": e.get("id"), "display_name": e.get("display_name"),
                  "model_id": e.get("model_id"), "score": round(s, 2),
                  "tier": e.get("tier")} for e, s in scored[:3]]
    winner = scored[0][0] if scored else None
    return {
        "extracted_caps": caps,
        "budget_hint":    budget_hint,
        "winner":         winner,
        "top3":           top3,
    }


# ── Named Configurations ─────────────────────────────────────────────────────

def list_named_configs() -> list[dict]:
    store = _db_read(_DB_KEY_NAMED_CFGS, {})
    return list(store.values())


def set_active_mix(source_id: str, label: str, *, kind: str = "named") -> None:
    """Remember which preset/named mix is live (for UI + Live Screener)."""
    from .llm_router_config import require_llm_write_permission

    require_llm_write_permission()
    _db_write(_DB_KEY_ACTIVE_MIX, {
        "source_id": source_id,
        "label": label,
        "kind": kind,
        "applied_at": int(time.time()),
    })


def get_active_mix() -> dict | None:
    raw = _db_read(_DB_KEY_ACTIVE_MIX, None)
    return raw if isinstance(raw, dict) and raw.get("label") else None


def clear_active_mix() -> None:
    from .llm_router_config import require_llm_write_permission

    require_llm_write_permission()
    from . import kv
    kv.delete(_DB_KEY_ACTIVE_MIX)


def save_named_config(name: str, slots: dict, icon: str = "⚙️") -> str:
    """Save current worker slot config as a named configuration. Returns id."""
    store = _db_read(_DB_KEY_NAMED_CFGS, {})
    cfg_id = re.sub(r"[^a-z0-9_-]", "-", name.lower())[:32]
    store[cfg_id] = {
        "id":         cfg_id,
        "name":       name,
        "icon":       icon,
        "slots":      slots,
        "saved_at":   int(time.time()),
    }
    _db_write(_DB_KEY_NAMED_CFGS, store)
    return cfg_id


def load_named_config(cfg_id: str) -> dict | None:
    store = _db_read(_DB_KEY_NAMED_CFGS, {})
    return store.get(cfg_id)


def delete_named_config(cfg_id: str) -> bool:
    store = _db_read(_DB_KEY_NAMED_CFGS, {})
    if cfg_id not in store:
        return False
    del store[cfg_id]
    _db_write(_DB_KEY_NAMED_CFGS, store)
    return True


# ── Default Named Configs (seed при първо стартиране) ────────────────────────

_DEFAULT_NAMED_CONFIGS: list[dict] = [
    {
        "id":    "v4-pro-premium",
        "name":  "V4 Pro Premium 🔥",
        "icon":  "🔥",
        "slots": {
            # Бърз — V4 Flash за скорост
            "fast":     {"model": "deepseek-v4-flash",   "provider": "alltoken"},
            # Балансиран — V4 Pro за качество
            "balanced": {"model": "deepseek-v4-pro",     "provider": "alltoken"},
            # Дълбок — V4 Pro за синтез и анализ
            "deep":     {"model": "deepseek-v4-pro",     "provider": "alltoken"},
            # Coder — Qwen3 Coder Next за специализиран код
            "coder":    {"model": "qwen3-coder-next",    "provider": "alltoken"},
            # Planner — V4 Pro за reasoning/декомпозиция
            "planner":  {"model": "deepseek-v4-pro",     "provider": "alltoken"},
            # Reviewer — V4 Pro за финален review
            "reviewer": {"model": "deepseek-v4-pro",     "provider": "alltoken"},
            # Claude слот — заместен от V4 Pro
            "claude":   {"model": "deepseek-v4-pro",     "provider": "alltoken"},
            # Analysis — V4 Pro за deep analysis
            "analysis": {"model": "deepseek-v4-pro",     "provider": "alltoken"},
        },
    },
]


def seed_named_configs_if_empty() -> int:
    """Seed DEFAULT_NAMED_CONFIGS в Replit DB ако няма нито един запис.

    Извиква се при стартиране на приложението (lifespan в main.py).
    Returns: брой новодобавени конфигурации.
    """
    store = _db_read(_DB_KEY_NAMED_CFGS, {})
    if store:
        return 0  # вече има user-дефинирани configs — не презаписвай
    added = 0
    for cfg in _DEFAULT_NAMED_CONFIGS:
        cfg_id = cfg["id"]
        store[cfg_id] = {**cfg, "saved_at": int(time.time())}
        added += 1
    _db_write(_DB_KEY_NAMED_CFGS, store)
    log.info("model_registry: seeded %d default named config(s)", added)
    return added
