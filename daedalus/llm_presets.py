"""
LLM Configuration Presets — predefined master + worker combinations.

Each preset applies BOTH the master config (set_config) AND all 3 worker
slot configs (set_worker_slot for fast/balanced/deep) in one click.

Use cases:
  • Eco       — max savings, all open-source via Novita
  • Balanced  — default, smart routing of cost vs quality
  • Premium   — max quality, Claude/GPT-5 across the board
  • Code      — specialist for coding/agent tasks
  • Hybrid    — cheap loop (70%) + premium finalizer via deep worker (30%)
  • Speed     — lowest latency, flash variants everywhere
"""
from __future__ import annotations

from typing import Optional

from .llm_config import set_config
from .workers import set_worker_slot


CONFIG_PRESETS: list[dict] = [
    {
        "id": "balanced",
        "label": "⚖️ Balanced — Стандарт",
        "is_default": True,
        "description": (
            "Препоръчаната конфигурация. DeepSeek V3.2 като master ($0.30/$0.90) "
            "балансира ум и цена. Workers: бърз wrap-up (Llama 8B), отзивчив tool "
            "loop (Llama 3.3 70B), мощен Qwen 235B за дълбоки задачи."
        ),
        "budget": "~$0.30-1.50 / 1M токена",
        "master":   {"provider": "novita", "model": "deepseek/deepseek-v3.2"},
        "fast":     {"provider": "novita", "model": "meta-llama/llama-3.1-8b-instruct"},
        "balanced": {"provider": "novita", "model": "meta-llama/llama-3.3-70b-instruct"},
        "deep":     {"provider": "novita", "model": "qwen/qwen3-235b-a22b-instruct-2507"},
    },
    {
        "id": "nvidia_free",
        "label": "🆓 NVIDIA Free — $0 tier (NIM preview)",
        "is_default": False,
        "description": (
            "Всички слотове през NVIDIA NIM free tier (NVIDIA_API_KEY). "
            "Master = Qwen3-Next 80B (Persona + orchestrator). Workers: OSS 20B fast, "
            "DeepSeek V4 Flash balanced, Mistral Large 3 deep. Включи „Free-only routing“ "
            "за mesh/build fallback също само през NVIDIA."
        ),
        "budget": "$0 (rate-limited preview)",
        "master":   {"provider": "nvidia", "model": "qwen/qwen3-next-80b-a3b-instruct"},
        "fast":     {"provider": "nvidia", "model": "openai/gpt-oss-20b"},
        "balanced": {"provider": "nvidia", "model": "deepseek-ai/deepseek-v4-flash"},
        "deep":     {"provider": "nvidia", "model": "mistralai/mistral-large-3-675b-instruct-2512"},
    },
    {
        "id": "or_free",
        "label": "🆓 OpenRouter Free — $0 (:free pool)",
        "is_default": False,
        "description": (
            "Всички слотове през OpenRouter :free (OPENROUTER_API_KEY). "
            "Master/balanced/deep = Poolside Laguna S 2.1:free (coding agent). "
            "Fast = openrouter/free (автоматичен избор между свободните модели, "
            "ако конкретният id е 429). Rate-limited; някои :free id-та връщат "
            "празен отговор или upstream 429 — не е Claude. Не клонира платени модели."
        ),
        "budget": "$0 (OpenRouter :free, rate-limited)",
        "master":   {"provider": "openrouter", "model": "poolside/laguna-s-2.1:free"},
        "fast":     {"provider": "openrouter", "model": "openrouter/free"},
        "balanced": {"provider": "openrouter", "model": "poolside/laguna-s-2.1:free"},
        "deep":     {"provider": "openrouter", "model": "poolside/laguna-s-2.1:free"},
    },
    {
        "id": "omniroute",
        "label": "🆓 OmniRoute — local sidecar ($0 auto)",
        "is_default": False,
        "description": (
            "Мозъкът сочи към локален OmniRoute на :20128 (OpenAI-compat /v1). "
            "Master = auto (gateway-ът реди free-tier fallback). Fast = auto/fast, "
            "deep/coder = auto/coding. Не вендорираме gateway-а — ако процесът "
            "не слуша, заявките fail closed. Ключ не е нужен за keyless free pool."
        ),
        "budget": "$0 (sidecar; free-tier quotas, rate-limited)",
        "master":   {"provider": "omniroute", "model": "auto"},
        "fast":     {"provider": "omniroute", "model": "auto/fast"},
        "balanced": {"provider": "omniroute", "model": "auto"},
        "deep":     {"provider": "omniroute", "model": "auto/coding"},
    },
    {
        "id": "eco",
        "label": "🌱 Eco — Максимална икономия",
        "is_default": False,
        "description": (
            "За масивни batch задачи и discovery loops. Всичко open-source през "
            "Novita, без премиум модели. Жертва се малко качество за драстично "
            "по-ниска сметка."
        ),
        "budget": "~$0.02-0.90 / 1M токена",
        "master":   {"provider": "novita", "model": "meta-llama/llama-3.3-70b-instruct"},
        "fast":     {"provider": "novita", "model": "meta-llama/llama-3.1-8b-instruct"},
        "balanced": {"provider": "novita", "model": "inclusionai/ling-2.6-flash"},
        "deep":     {"provider": "novita", "model": "deepseek/deepseek-v3.2"},
    },
    {
        "id": "premium",
        "label": "🚀 Premium — Максимално качество",
        "is_default": False,
        "description": (
            "За критични задачи където качеството е по-важно от цената. "
            "Claude Sonnet 5.5 master + Haiku 5.5 fast + Opus 5.5 deep — всичко "
            "през OpenRouter (нужен OPENROUTER_API_KEY)."
        ),
        "budget": "~$0.80-10 / 1M токена (OpenRouter rates)",
        "master":   {"provider": "openrouter", "model": "anthropic/claude-sonnet-5.5"},
        "fast":     {"provider": "openrouter", "model": "anthropic/claude-haiku-5.5"},
        "balanced": {"provider": "openrouter", "model": "deepseek/deepseek-v3.2"},
        "deep":     {"provider": "openrouter", "model": "anthropic/claude-opus-5.5"},
    },
    {
        "id": "code",
        "label": "🤖 Code Specialist — За coding задачи",
        "is_default": False,
        "description": (
            "Оптимизиран за писане на код и агентски workflows. Kimi K2 като "
            "master (специализиран за tool-use), Qwen Coder за code-specific "
            "workers — 30B евтин, 480B мощен."
        ),
        "budget": "~$0.07-1.55 / 1M токена",
        "master":   {"provider": "novita", "model": "moonshotai/kimi-k2-instruct"},
        "fast":     {"provider": "novita", "model": "meta-llama/llama-3.1-8b-instruct"},
        "balanced": {"provider": "novita", "model": "qwen/qwen3-coder-30b-a3b-instruct"},
        "deep":     {"provider": "novita", "model": "qwen/qwen3-coder-480b-a35b-instruct"},
    },
    {
        "id": "hybrid",
        "label": "🔀 Hybrid 70/30 — Евтин loop + премиум финал",
        "is_default": False,
        "description": (
            "Master модел е евтин (DeepSeek V3.2) и върши ~70% от работата "
            "директно. За критични финални синтези Дедал делегира към deep "
            "worker = Claude Sonnet (премиум). Резултат: ~30% от Sonnet цената "
            "за качество близо до 100% Sonnet. ВАЖНО: трябва ANTHROPIC_API_KEY "
            "за deep slot (или ползвай Replit Integration)."
        ),
        "budget": "~$0.30 (loop) + Sonnet (финал на трудни)",
        "master":   {"provider": "novita",   "model": "deepseek/deepseek-v3.2"},
        "fast":     {"provider": "novita",   "model": "meta-llama/llama-3.1-8b-instruct"},
        "balanced": {"provider": "novita",   "model": "meta-llama/llama-3.3-70b-instruct"},
        "deep":     {"provider": "openrouter","model": "anthropic/claude-sonnet-5.5"},
    },
    {
        "id": "speed",
        "label": "⚡ Speed — Минимална латентност",
        "is_default": False,
        "description": (
            "Когато responsiveness е по-важен от качеството. Flash варианти "
            "навсякъде — Ling 2.6 Flash master (~1.3s), DeepSeek V4 Flash, "
            "Llama 3.3 70B (най-бърз tool calling). За интерактивни сесии."
        ),
        "budget": "~$0.02-0.90 / 1M токена",
        "master":   {"provider": "novita", "model": "inclusionai/ling-2.6-flash"},
        "fast":     {"provider": "novita", "model": "meta-llama/llama-3.1-8b-instruct"},
        "balanced": {"provider": "novita", "model": "deepseek/deepseek-v4-flash"},
        "deep":     {"provider": "novita", "model": "meta-llama/llama-3.3-70b-instruct"},
    },
    # ── OpenRouter варианти ────────────────────────────────────────────────
    {
        "id": "or_minimax",
        "label": "🇨🇳 OpenRouter: MiniMax M2.7 — Benchmark Winner",
        "is_default": False,
        "description": (
            "Оптимизиран след live benchmark. Master: MiniMax M2.7 ($0.28/$1.20, "
            "12s, score 2/3 — отличен код + p² opt). Fast: Ling 2.6 Flash "
            "($0.01/$0.02, 3.3s, score 3/3 — най-бърз). Balanced: DeepSeek V3.2 "
            "($0.25/$0.38, 7s, score 3/3). Deep: Kimi K2 Thinking ($0.60/$2.50 — "
            "chain-of-thought за сложен reasoning). Единен OpenRouter ключ."
        ),
        "budget": "~$0.01-2.50 / 1M токена",
        "master":   {"provider": "openrouter", "model": "minimax/minimax-m2.7"},
        "fast":     {"provider": "openrouter", "model": "meta-llama/llama-3.1-8b-instruct"},
        "balanced": {"provider": "openrouter", "model": "deepseek/deepseek-v3.2"},
        "deep":     {"provider": "openrouter", "model": "moonshotai/kimi-k2-thinking"},
    },
    {
        "id": "or_glm",
        "label": "🇨🇳 OpenRouter: GLM 5.1 — Zhipu флагман",
        "is_default": False,
        "description": (
            "GLM-5.1 master ($0.98/$3.08) — последен Zhipu, 202k контекст, "
            "силен на code и reasoning. Fast: GLM-4.7 Flash ($0.06/$0.40). "
            "Balanced: GLM-4.7 ($0.40/$1.75). Deep: Kimi K2 Thinking ($0.60/"
            "$2.50). Всичко в една екосистема — консистентен стил на отговори."
        ),
        "budget": "~$0.06-3.08 / 1M токена",
        "master":   {"provider": "openrouter", "model": "z-ai/glm-5.1"},
        "fast":     {"provider": "openrouter", "model": "z-ai/glm-4.7-flash"},
        "balanced": {"provider": "openrouter", "model": "z-ai/glm-4.7"},
        "deep":     {"provider": "openrouter", "model": "moonshotai/kimi-k2-thinking"},
    },
    {
        "id": "or_kimi",
        "label": "🇨🇳 OpenRouter: Kimi K2.6 — Long-context флагман",
        "is_default": False,
        "description": (
            "Kimi K2.6 master ($0.73/$3.49) — 262k контекст (най-голям в "
            "категорията), специалист за дълги документи и agentic tool-use. "
            "Fast: GLM-4.7 Flash. Balanced: Kimi K2.5 ($0.40/$1.90). Deep: "
            "Kimi K2 Thinking за chain-of-thought. Идеален за големи кодови бази."
        ),
        "budget": "~$0.06-3.49 / 1M токена",
        "master":   {"provider": "openrouter", "model": "moonshotai/kimi-k2.6"},
        "fast":     {"provider": "openrouter", "model": "z-ai/glm-4.7-flash"},
        "balanced": {"provider": "openrouter", "model": "moonshotai/kimi-k2.5"},
        "deep":     {"provider": "openrouter", "model": "moonshotai/kimi-k2-thinking"},
    },
    {
        "id": "or_china_mix",
        "label": "🇨🇳 OpenRouter: China Mix — Best of MiniMax/GLM/Kimi",
        "is_default": False,
        "description": (
            "Топ модел от всяка фамилия комбиниран. Master: MiniMax M2.7 "
            "($0.28/$1.20) — най-добро съотношение цена/качество. Fast: GLM-4.7 "
            "Flash ($0.06/$0.40 — почти безплатен wrap-up). Balanced: GLM-4.6 "
            "($0.43/$1.74). Deep: Kimi K2.6 ($0.73/$3.49) за финални синтези "
            "с 262k контекст. Препоръчан старт за тест на 3-те фамилии."
        ),
        "budget": "~$0.06-3.49 / 1M токена",
        "master":   {"provider": "openrouter", "model": "minimax/minimax-m2.7"},
        "fast":     {"provider": "openrouter", "model": "z-ai/glm-4.7-flash"},
        "balanced": {"provider": "openrouter", "model": "z-ai/glm-4.6"},
        "deep":     {"provider": "openrouter", "model": "moonshotai/kimi-k2.6"},
    },
    {
        "id": "or_hybrid",
        "label": "🌐 OpenRouter: Smart Hybrid — Евтин loop + Sonnet финал",
        "is_default": False,
        "description": (
            "80% от Sonnet качеството за 20% от Sonnet цената. Master: "
            "DeepSeek V3 0324 ($0.27/$1.10) върши целия tool loop и discovery. "
            "Deep worker: Claude Sonnet 4.5 — само за финални синтези, "
            "architecture decisions и сложни code proposals. Fast: Llama 3.1 8B "
            "(wrap-up). Balanced: QwQ-32B (reasoning + код). Единен OpenRouter ключ."
        ),
        "budget": "~$0.27 (loop) + $3/$15 (само deep финали)",
        "master":   {"provider": "openrouter", "model": "deepseek/deepseek-v3.2"},
        "fast":     {"provider": "openrouter", "model": "meta-llama/llama-3.1-8b-instruct"},
        "balanced": {"provider": "openrouter", "model": "qwen/qwq-32b"},
        "deep":     {"provider": "openrouter", "model": "anthropic/claude-sonnet-5.5"},
    },
    {
        "id": "or_balanced",
        "label": "🌐 OpenRouter: Balanced — DeepSeek V3 + Llama",
        "is_default": False,
        "description": (
            "Всичко минава през OpenRouter. Master: DeepSeek V3 (0324) — "
            "отличен reasoning + tool-use на ниска цена. Workers: Llama 3.1 8B "
            "(fast), Llama 3.3 70B (balanced), DeepSeek R1 (deep reasoning). "
            "Единен ключ — само OPENROUTER_API_KEY."
        ),
        "budget": "~$0.07-2.00 / 1M токена",
        "master":   {"provider": "openrouter", "model": "deepseek/deepseek-v3.2"},
        "fast":     {"provider": "openrouter", "model": "meta-llama/llama-3.1-8b-instruct"},
        "balanced": {"provider": "openrouter", "model": "meta-llama/llama-3.3-70b-instruct"},
        "deep":     {"provider": "openrouter", "model": "deepseek/deepseek-r1"},
    },
    {
        "id": "or_claude",
        "label": "🌐 OpenRouter: Claude Sonnet 4.5 Master",
        "is_default": False,
        "description": (
            "Master: Claude Sonnet 4.5 — топ качество за complex reasoning и "
            "tool-use. Fast: Haiku 3.5 (бърз и евтин). Balanced: DeepSeek V3. "
            "Deep: Claude Opus 4.5 за най-трудните задачи. Единен OpenRouter ключ."
        ),
        "budget": "~$0.25-15 / 1M токена",
        "master":   {"provider": "openrouter", "model": "anthropic/claude-sonnet-5.5"},
        "fast":     {"provider": "openrouter", "model": "anthropic/claude-haiku-3-5"},
        "balanced": {"provider": "openrouter", "model": "deepseek/deepseek-v3.2"},
        "deep":     {"provider": "openrouter", "model": "anthropic/claude-opus-5.5"},
    },
    {
        "id": "or_gemini",
        "label": "🌐 OpenRouter: Gemini Pro Master",
        "is_default": False,
        "description": (
            "Master: Gemini 2.5 Pro — изключителен за дълги контексти и "
            "мултимодални задачи. Fast: Gemini 2.0 Flash (латентност <1s). "
            "Balanced: DeepSeek V3. Deep: Gemini 2.5 Pro за дълбок анализ. "
            "Единен OpenRouter ключ."
        ),
        "budget": "~$0.10-1.25 / 1M токена",
        "master":   {"provider": "openrouter", "model": "google/gemini-2.5-pro"},
        "fast":     {"provider": "openrouter", "model": "google/gemini-2.0-flash-001"},
        "balanced": {"provider": "openrouter", "model": "deepseek/deepseek-v3.2"},
        "deep":     {"provider": "openrouter", "model": "google/gemini-2.5-pro"},
    },
    {
        "id": "or_reasoning",
        "label": "🌐 OpenRouter: Deep Reasoning — R1 + QwQ",
        "is_default": False,
        "description": (
            "За задачи изискващи верижно мислене (chain-of-thought). Master: "
            "DeepSeek R1 (0528) — SOTA reasoning. Fast: Llama 3.1 8B. "
            "Balanced: QwQ-32B (reasoning специалист). Deep: R1-0528 отново. "
            "Единен OpenRouter ключ."
        ),
        "budget": "~$0.50-4.00 / 1M токена",
        "master":   {"provider": "openrouter", "model": "deepseek/deepseek-r1-0528"},
        "fast":     {"provider": "openrouter", "model": "meta-llama/llama-3.1-8b-instruct"},
        "balanced": {"provider": "openrouter", "model": "qwen/qwq-32b"},
        "deep":     {"provider": "openrouter", "model": "deepseek/deepseek-r1-0528"},
    },
    {
        "id": "or_eco",
        "label": "🌐 OpenRouter: Eco — Минимална цена",
        "is_default": False,
        "description": (
            "Максимална икономия само с OpenRouter. Master: DeepSeek V4 Flash "
            "($0.07/1M). Fast: Llama 3.1 8B (~free tier). Balanced: Qwen3 30B. "
            "Deep: DeepSeek V3 за по-трудните случаи. Единен ключ."
        ),
        "budget": "~$0.02-0.35 / 1M токена",
        "master":   {"provider": "openrouter", "model": "deepseek/deepseek-v4-flash"},
        "fast":     {"provider": "openrouter", "model": "meta-llama/llama-3.1-8b-instruct"},
        "balanced": {"provider": "openrouter", "model": "qwen/qwen3-30b-a3b"},
        "deep":     {"provider": "openrouter", "model": "deepseek/deepseek-v3.2"},
    },
    # ── AllToken варианти ──────────────────────────────────────────────────────
    {
        "id": "at_nano",
        "label": "🔋 AllToken: Nano Stack — Абсолютна икономия",
        "is_default": False,
        "description": (
            "Най-евтиният AllToken stack. Master: Gemini 3.1 Flash Lite ($0.03/1M, "
            "НУЛЕВ reasoning overhead, 1M context). Fast: GPT-5.4 Nano ($0.05/1M "
            "— GPT-5 поколение на nano цена). Balanced: MiMo V2 Flash ($0.05/1M). "
            "Deep: Qwen3.6-35B-A3B ($0.08/1M — MoE 35B качество на 3B цена). "
            "Единен ALLTOKEN_API_KEY. Идеален за batch loops и discovery."
        ),
        "budget": "~$0.03-0.08 / 1M токена",
        "master":   {"provider": "alltoken", "model": "gemini-3.1-flash-lite-preview"},
        "fast":     {"provider": "alltoken", "model": "gpt-5.4-nano"},
        "balanced": {"provider": "alltoken", "model": "mimo-v2-flash"},
        "deep":     {"provider": "alltoken", "model": "qwen3.6-35b-a3b"},
    },
    {
        "id": "at_gems",
        "label": "💎 AllToken: Hidden Gems — Топ value combo",
        "is_default": False,
        "description": (
            "Скритите съкровища на AllToken. Master: MiMo V2 Flash ($0.05/1M — "
            "173 изходни токена, no reasoning overhead, high quality). Fast: "
            "Gemini 3.1 Flash Lite ($0.03/1M — най-евтин fast). Balanced: "
            "Qwen3.6-35B-A3B MoE ($0.08/1M — very_high quality). Deep: "
            "DeepSeek V4 Pro ($0.50/1M — top reasoning). Единен ALLTOKEN_API_KEY."
        ),
        "budget": "~$0.03-0.50 / 1M токена",
        "master":   {"provider": "alltoken", "model": "mimo-v2-flash"},
        "fast":     {"provider": "alltoken", "model": "gemini-3.1-flash-lite-preview"},
        "balanced": {"provider": "alltoken", "model": "qwen3.6-35b-a3b"},
        "deep":     {"provider": "alltoken", "model": "deepseek-v4-pro"},
    },
    {
        "id": "at_coder",
        "label": "🤖 AllToken: Code Expert — Coding специалисти",
        "is_default": False,
        "description": (
            "Оптимизиран за писане и анализ на код. Master: Qwen3 Coder Next "
            "($0.10/1M — next-gen coding модел, ultra-efficient). Fast: GPT-5.4 "
            "Nano ($0.05/1M — quick code wraps). Balanced: GPT-5.3 Codex ($1.00/1M "
            "— OpenAI Codex специалист, 182 tok output без overhead). Deep: "
            "DeepSeek V4 Pro ($0.50/1M — top code reasoning). Единен ALLTOKEN_API_KEY."
        ),
        "budget": "~$0.05-1.00 / 1M токена",
        "master":   {"provider": "alltoken", "model": "qwen3-coder-next"},
        "fast":     {"provider": "alltoken", "model": "gpt-5.4-nano"},
        "balanced": {"provider": "alltoken", "model": "gpt-5.3-codex"},
        "deep":     {"provider": "alltoken", "model": "deepseek-v4-pro"},
    },
    {
        "id": "at_frontier",
        "label": "🔥 AllToken: Frontier — Claude Opus 4.8 + Sonnet 4.6",
        "is_default": False,
        "description": (
            "Абсолютният максимум на AllToken. Master: Claude Sonnet 4.6 ($3/15 per "
            "1M — текущ Anthropic флагман, по-нов от Sonnet 4). Fast: GPT-5.4 Nano "
            "($0.05/1M — евтини wraps). Balanced: DeepSeek V4 Pro ($0.50/1M — "
            "силен reasoning). Deep: Claude Opus 4.8 ($15/75 per 1M — НАЙ-НОВАТА "
            "версия на Opus, само за критични финали). Единен ALLTOKEN_API_KEY."
        ),
        "budget": "~$0.05-15 / 1M токена (само за критични задачи)",
        "master":   {"provider": "alltoken", "model": "claude-sonnet-4-6"},
        "fast":     {"provider": "alltoken", "model": "gpt-5.4-nano"},
        "balanced": {"provider": "alltoken", "model": "deepseek-v4-pro"},
        "deep":     {"provider": "alltoken", "model": "claude-opus-4-8"},
    },
]


def is_openrouter_free_id(model: str) -> bool:
    """True for OpenRouter $0 model ids (`:free` suffix or the free router)."""
    m = (model or "").strip()
    return m.endswith(":free") or m == "openrouter/free"


def is_omniroute_auto_id(model: str) -> bool:
    """True for OmniRoute virtual combo ids (`auto` or `auto/…`)."""
    m = (model or "").strip()
    return m == "auto" or m.startswith("auto/")


def get_preset(preset_id: str) -> Optional[dict]:
    for p in CONFIG_PRESETS:
        if p["id"] == preset_id:
            return p
    return None


def list_presets() -> list[dict]:
    """Return preset list without revealing internal-only fields."""
    return [
        {
            "id": p["id"],
            "label": p["label"],
            "description": p["description"],
            "budget": p["budget"],
            "is_default": p["is_default"],
            "master":   p["master"],
            "fast":     p["fast"],
            "balanced": p["balanced"],
            "deep":     p["deep"],
        }
        for p in CONFIG_PRESETS
    ]


def _resolve_provider_key(provider: str, fallback: str = "") -> str:
    """Resolve a provider's API key from the process environment."""
    from .llm_config import provider_env_hint, resolve_provider_api_key

    return resolve_provider_api_key(provider, fallback=fallback)


def _resolve_provider_base_url(provider: str) -> str:
    from .llm_config import provider_default_base_url

    return provider_default_base_url(provider)


def apply_preset(preset_id: str, master_api_key: str = "") -> dict:
    """
    Apply a preset: write master config + 3 worker slot configs to Replit DB.

    Each slot gets its OWN provider-specific API key resolved from env,
    so cross-provider presets (e.g. Hybrid: novita master + anthropic deep)
    work without manual config per-slot.

    Returns dict with applied config summary + warnings for missing keys.
    """
    preset = get_preset(preset_id)
    if not preset:
        raise ValueError(f"Unknown preset: {preset_id}")

    master = preset["master"]
    master_provider = master["provider"]
    master_key = (master_api_key or "").strip() or _resolve_provider_key(master_provider)
    master_base_url = _resolve_provider_base_url(master_provider)

    if not master_key:
        raise ValueError(
            f"Preset '{preset_id}' изисква API ключ за master provider "
            f"'{master_provider}' — нито подаден, нито в env."
        )

    # Apply master (include base_url so AI-integrations proxy URL is stored in DB)
    set_config(model=master["model"], api_key=master_key, provider=master_provider,
               base_url=master_base_url)
    try:
        from .llm_wardrobe import apply_provider_hint
        apply_provider_hint(master_provider)
    except Exception:
        pass

    # Apply workers — resolve key per-slot. If worker's provider matches master,
    # we share the master key; else we resolve from that provider's env var.
    # Pipeline roles (planner/coder/reviewer/…) inherit the core three so an
    # eco/free preset cannot silently fall back to paid Claude defaults.
    warnings: list[str] = []
    slot_plan = {
        "fast": "fast",
        "balanced": "balanced",
        "deep": "deep",
        "planner": "balanced",
        "coder": "deep",
        "reviewer": "deep",
        "analysis": "deep",
        "claude": "deep",
    }
    for slot, src in slot_plan.items():
        sc = preset[src]
        slot_provider = sc["provider"]
        if slot_provider == master_provider:
            slot_key = master_key
            slot_base_url = master_base_url
        else:
            slot_key = _resolve_provider_key(slot_provider)
            slot_base_url = _resolve_provider_base_url(slot_provider)
            if not slot_key:
                warnings.append(
                    f"Worker '{slot}' ({slot_provider}/{sc['model']}) няма "
                    f"API ключ в env — slot-ът ще се счупи при ползване. "
                    f"Задай ръчно от Worker Agents панела."
                )
        set_worker_slot(slot=slot, model=sc["model"], provider=slot_provider,
                        api_key=slot_key, base_url=slot_base_url, enabled=True)

    from . import model_registry as mr
    mr.set_active_mix(preset_id, preset["label"], kind="preset")

    return {
        "ok": True,
        "preset_id": preset_id,
        "label": preset["label"],
        "master":   master,
        "fast":     preset["fast"],
        "balanced": preset["balanced"],
        "deep":     preset["deep"],
        "warnings": warnings,
    }


def nvidia_free_mix_slots() -> dict[str, dict]:
    """Master + core workers for NVIDIA free tier."""
    preset = get_preset("nvidia_free")
    if not preset:
        return {
            "master": {"provider": "nvidia", "model": "qwen/qwen3-next-80b-a3b-instruct"},
            "fast": {"provider": "nvidia", "model": "openai/gpt-oss-20b"},
            "balanced": {"provider": "nvidia", "model": "deepseek-ai/deepseek-v4-flash"},
            "deep": {"provider": "nvidia", "model": "mistralai/mistral-large-3-675b-instruct-2512"},
        }
    return {
        "master": preset["master"],
        "fast": preset["fast"],
        "balanced": preset["balanced"],
        "deep": preset["deep"],
    }


def apply_nvidia_free_profile(*, persona_via_master: bool = True) -> dict:
    """Apply NVIDIA free MIX + enable persona-via-master. Router write context required."""
    return apply_mix_config(
        nvidia_free_mix_slots(),
        source_id="nvidia_free",
        source_label="🆓 NVIDIA Free",
        source_kind="nvidia_free",
        persona_via_master=persona_via_master,
    )


def apply_mix_config(
    slots: dict[str, dict],
    *,
    source_id: str = "",
    source_label: str = "",
    source_kind: str = "mix",
    master_api_key: str = "",
    persona_via_master: bool | None = None,
) -> dict:
    """Apply master + worker slots from a MIX / named-config dict.

    Unlike apply_named_config (workers only), this always writes master via
    set_config() when slots.master is present — so Дедал chat + Playground build
    match the MIX panel.
    """
    from .llm_config import set_config, provider_env_hint, resolve_provider_api_key, provider_default_base_url
    from .workers import WORKER_SLOTS, set_worker_slot
    from . import model_registry as mr

    warnings: list[str] = []
    applied: list[str] = []

    master = slots.get("master") or {}
    master_provider = (master.get("provider") or "").strip()
    master_model = (master.get("model") or "").strip()
    if master_model:
        if not master_provider:
            raise ValueError("master slot requires provider")
        master_key = (master_api_key or "").strip() or resolve_provider_api_key(master_provider)
        master_base_url = provider_default_base_url(master_provider)
        if not master_key:
            raise ValueError(
                f"Няма API ключ за master provider '{master_provider}' — "
                f"задай {provider_env_hint(master_provider)} в Studio → Connectors или `.env`."
            )
        set_config(
            model=master_model,
            api_key=master_key,
            provider=master_provider,
            base_url=master_base_url,
            persona_via_master=persona_via_master,
        )
        try:
            from .llm_wardrobe import apply_provider_hint
            apply_provider_hint(master_provider)
        except Exception:
            pass
        applied.append("master")

    for slot, sc in (slots or {}).items():
        if slot == "master" or slot not in WORKER_SLOTS:
            continue
        if not isinstance(sc, dict):
            continue
        model = (sc.get("model") or "").strip()
        if not model:
            continue
        slot_provider = (sc.get("provider") or "openrouter").strip()
        if master_model and slot_provider == master_provider and master_model:
            slot_key = (master_api_key or "").strip() or resolve_provider_api_key(master_provider)
            slot_base_url = provider_default_base_url(master_provider)
        else:
            slot_key = (sc.get("api_key") or "").strip() or resolve_provider_api_key(slot_provider)
            slot_base_url = (sc.get("base_url") or "").strip() or provider_default_base_url(slot_provider)
            if not slot_key:
                warnings.append(
                    f"Worker '{slot}' ({slot_provider}/{model}) няма API ключ — "
                    f"задай {provider_env_hint(slot_provider)} в Secrets."
                )
        set_worker_slot(
            slot=slot,
            model=model,
            provider=slot_provider,
            api_key=slot_key or "",
            base_url=slot_base_url,
            enabled=sc.get("enabled", True),
        )
        applied.append(slot)

    if not applied:
        raise ValueError("Няма валидни слотове за прилагане (master/fast/balanced/deep)")

    label = source_label or source_id or "custom mix"
    mr.set_active_mix(source_id or label, label, kind=source_kind)

    if persona_via_master is not None:
        from .llm_config import set_persona_via_master
        set_persona_via_master(persona_via_master)

    return {
        "ok": True,
        "applied_slots": applied,
        "source_id": source_id,
        "label": label,
        "master": master if master_model else None,
        "warnings": warnings,
    }
