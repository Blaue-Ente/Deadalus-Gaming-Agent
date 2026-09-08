"""LLM configuration for Daedalus — runtime-switchable model and API key.

Priority (highest to lowest):
1. Replit DB override  (set via Admin UI — survives restarts)
2. Environment variables  DAEDALUS_API_KEY / DAEDALUS_BASE_URL / DAEDALUS_MODEL
3. Replit AI Integrations  AI_INTEGRATIONS_ANTHROPIC_API_KEY / BASE_URL + default model

Supported providers (all via Anthropic SDK compatible interface):
  anthropic  — api.anthropic.com  (native, with prompt caching)
  openai     — api.openai.com     (via openai SDK)
  groq       — api.groq.com/openai/v1
  gemini     — generativelanguage.googleapis.com/v1beta/openai (OpenAI-compat)
  custom     — any base_url you supply
"""
from __future__ import annotations

import json
import logging
import os

log = logging.getLogger(__name__)

_DB_KEY = "adn:daedalus_llm_config"

# Canonical env var names per provider — single source of truth for key resolution.
PROVIDER_ENV_KEYS: dict[str, str] = {
    "novita":         "NOVITA_API_KEY",
    "openrouter":     "OPENROUTER_API_KEY",
    "groq":           "GROQ_API_KEY",
    "openai":         "OPENAI_API_KEY",
    "gemini":         "GEMINI_API_KEY",
    "alltoken":       "ALLTOKEN_API_KEY",
    "poolside":       "POOLSIDE_API",
    "nvidia":         "NVIDIA_API_KEY",
    "replit_openai":  "AI_INTEGRATIONS_OPENAI_API_KEY",
    "replit_gemini":  "AI_INTEGRATIONS_GEMINI_API_KEY",
    "anthropic":      "ANTHROPIC_API_KEY",
}


def resolve_provider_api_key(provider: str, fallback: str = "") -> str:
    """Read provider API key from the process environment."""
    p = (provider or "").strip().lower()
    if not p:
        return fallback
    env_name = PROVIDER_ENV_KEYS.get(p, "")
    if not env_name:
        return fallback
    return (os.environ.get(env_name) or "").strip() or fallback


def provider_env_hint(provider: str) -> str:
    """Human hint for missing key errors."""
    p = (provider or "").strip().lower()
    env_name = PROVIDER_ENV_KEYS.get(p)
    return env_name or f"API key for {provider}"


def provider_default_base_url(provider: str) -> str:
    """Base URL from PROVIDER_DEFAULTS, or empty."""
    pd = PROVIDER_DEFAULTS.get((provider or "").strip().lower(), {})
    return str(pd.get("base_url") or "")


def openai_compat_headers(provider: str) -> dict[str, str]:
    """Extra headers for OpenAI-compatible clients.

    OpenRouter's :free pool is more reliable when the request names the app.
    """
    if (provider or "").strip().lower() != "openrouter":
        return {}
    referer = (os.environ.get("OPENROUTER_HTTP_REFERER") or "").strip() or (
        "https://gitlab.com/Blaue-Ente/deadalus-agent-0"
    )
    title = (os.environ.get("OPENROUTER_APP_TITLE") or "").strip() or "Daedalus"
    return {"HTTP-Referer": referer, "X-Title": title}


def openai_compat_extra_body(provider: str, *, with_tools: bool = False) -> dict:
    """Extra JSON body for OpenAI-compatible providers.

    OpenRouter otherwise may pick a host that ignores ``tools`` and just
    writes prose — MiniMax/DeepSeek then look like they cannot call tools.
    """
    if (provider or "").strip().lower() != "openrouter":
        return {}
    if with_tools:
        return {"provider": {"require_parameters": True}}
    return {}


PROVIDER_DEFAULTS: dict[str, dict] = {
    "anthropic": {
        "base_url": "https://api.anthropic.com",
        "default_model": "claude-sonnet-4-6",
        "sdk": "anthropic",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o",
        "sdk": "openai",
    },
    "replit_openai": {
        "base_url": "",   # resolved at runtime from AI_INTEGRATIONS_OPENAI_BASE_URL
        "default_model": "gpt-5.4",
        "sdk": "openai",
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "default_model": "llama-3.3-70b-versatile",
        "sdk": "openai",
    },
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "default_model": "gemini-2.0-flash",
        "sdk": "openai",
    },
    "replit_gemini": {
        "base_url": "",   # resolved at runtime from AI_INTEGRATIONS_GEMINI_BASE_URL
        "default_model": "gemini-2.5-flash",
        "sdk": "openai",
    },
    # Novita AI — OpenAI-compat, open-source models (DeepSeek, Qwen, Llama)
    # Docs: https://novita.ai/docs/api-reference/llm-api
    "novita": {
        "base_url": "https://api.novita.ai/v3/openai",
        "default_model": "deepseek/deepseek-v3.2",
        "sdk": "openai",
    },
    # OpenRouter — unified gateway to 300+ models (Claude, GPT, Gemini, DeepSeek, Llama…)
    # Docs: https://openrouter.ai/docs  |  Key env: OPENROUTER_API_KEY
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": "deepseek/deepseek-v3.2",
        "sdk": "openai",
    },
    # AllToken — OpenAI-compatible unified proxy (GPT, Gemini, DeepSeek, Claude, Qwen…)
    # Key env: ALLTOKEN_API_KEY  |  Base: https://api.alltoken.ai/v1
    "alltoken": {
        "base_url": "https://api.alltoken.ai/v1",
        "default_model": "gemini-3.1-flash-lite-preview",
        "sdk": "openai",
    },
    # Poolside — coding agent (262K context, $0 beta)
    # Key env: POOLSIDE_API  |  Base: https://inference.poolside.ai/v1
    "poolside": {
        "base_url": "https://inference.poolside.ai/v1",
        "default_model": "poolside/laguna-m.1",
        "sdk": "openai",
    },
    # NVIDIA NIM — OpenAI-compatible gateway to NVIDIA-hosted open models (free preview)
    # Docs: https://build.nvidia.com  |  Key env: NVIDIA_API_KEY  |  Base: integrate.api.nvidia.com/v1
    "nvidia": {
        "base_url": "https://integrate.api.nvidia.com/v1",
        "default_model": "qwen/qwen3-next-80b-a3b-instruct",
        "sdk": "openai",
    },
    # Local OpenAI-compatible servers (no API key on loopback)
    "ollama": {
        "base_url": "http://127.0.0.1:11434/v1",
        "default_model": "qwen2.5-coder:7b",
        "sdk": "openai",
    },
    "lmstudio": {
        "base_url": "http://127.0.0.1:1234/v1",
        "default_model": "local-model",
        "sdk": "openai",
    },
    "llamacpp": {
        "base_url": "http://127.0.0.1:8080/v1",
        "default_model": "local-model",
        "sdk": "openai",
    },
    "vllm": {
        "base_url": "http://127.0.0.1:8000/v1",
        "default_model": "local-model",
        "sdk": "openai",
    },
}

PRESET_MODELS = [
    # Anthropic via Replit modelfarm (всички работят без твой ключ)
    {"id": "claude-haiku-4-5",           "label": "Claude Haiku 4.5 (Replit) ⚡", "provider": "anthropic",    "tier": "fast"},
    {"id": "claude-sonnet-4-6",          "label": "Claude Sonnet 4.6 (Replit)",   "provider": "anthropic",    "tier": "fast"},
    {"id": "claude-opus-4-7",            "label": "Claude Opus 4.7 (Replit)",     "provider": "anthropic",    "tier": "power"},
    {"id": "claude-opus-4-5",            "label": "Claude Opus 4.5 (Replit)",     "provider": "anthropic",    "tier": "power"},
    # OpenAI via Replit modelfarm
    {"id": "gpt-5.4",                    "label": "GPT-5.4 (Replit) ⚡",           "provider": "replit_openai","tier": "fast"},
    {"id": "gpt-5-mini",                 "label": "GPT-5 Mini (Replit)",          "provider": "replit_openai","tier": "cheap"},
    {"id": "gpt-5-nano",                 "label": "GPT-5 Nano (Replit)",          "provider": "replit_openai","tier": "cheap"},
    {"id": "o4-mini",                    "label": "o4-mini reasoning (Replit)",   "provider": "replit_openai","tier": "power"},
    # Gemini via Replit modelfarm (native API)
    {"id": "gemini-2.5-flash",           "label": "Gemini 2.5 Flash (Replit)",    "provider": "replit_gemini","tier": "fast"},
    {"id": "gemini-3-flash-preview",     "label": "Gemini 3 Flash (Replit)",      "provider": "replit_gemini","tier": "fast"},
    {"id": "gemini-3.1-pro-preview",     "label": "Gemini 3.1 Pro (Replit)",      "provider": "replit_gemini","tier": "power"},
    # Direct API providers (нужен твой ключ)
    {"id": "gpt-4o",                     "label": "GPT-4o (direct)",              "provider": "openai",       "tier": "fast"},
    {"id": "gpt-4o-mini",                "label": "GPT-4o mini (direct)",         "provider": "openai",       "tier": "cheap"},
    {"id": "llama-3.3-70b-versatile",    "label": "Llama 3.3 70B (Groq)",         "provider": "groq",         "tier": "cheap"},
    # Novita AI — open-source, pay-per-token, OpenAI-compat
    # Base URL: https://api.novita.ai/v3/openai  |  Key env: NOVITA_API_KEY
    {"id": "deepseek/deepseek-v3.2",               "label": "DeepSeek V3.2 (Novita) 💰",          "provider": "novita", "tier": "fast"},
    {"id": "deepseek/deepseek-v3-0324",            "label": "DeepSeek V3 0324 (Novita)",          "provider": "novita", "tier": "fast"},
    {"id": "deepseek/deepseek-r1-0528",            "label": "DeepSeek R1 0528 (Novita) 🧠",       "provider": "novita", "tier": "power"},
    {"id": "deepseek/deepseek-r1-distill-qwen-32b","label": "DeepSeek R1 Distill Qwen 32B (Novita)","provider": "novita","tier": "fast"},
    {"id": "qwen/qwen3-235b-a22b-instruct-2507",   "label": "Qwen3 235B Instruct 2507 (Novita) 💰","provider": "novita", "tier": "fast"},
    {"id": "qwen/qwen3-235b-a22b",                 "label": "Qwen3 235B A22B (Novita)",           "provider": "novita", "tier": "fast"},
    {"id": "qwen/qwen3-coder-480b-a35b-instruct",  "label": "Qwen3 Coder 480B (Novita) ⚡",       "provider": "novita", "tier": "power"},
    {"id": "qwen/qwen3-coder-30b-a3b-instruct",    "label": "Qwen3 Coder 30B (Novita) 💰",        "provider": "novita", "tier": "cheap"},
    {"id": "qwen/qwen3-30b",                       "label": "Qwen3 30B (Novita)",                 "provider": "novita", "tier": "cheap"},
    # Wrap-up / cheap tier — Llama 3.1 8B ($0.02/$0.02), 62x cheaper than Haiku
    {"id": "meta-llama/llama-3.1-8b-instruct",     "label": "Llama 3.1 8B (Novita) 🆓 wrap-up",  "provider": "novita", "tier": "cheap"},
    # ── Тествани 2026-05-22 и потвърдени ────────────────────────────────────────
    # DeepSeek V4 Flash — бърз, евтин, tools ✅
    {"id": "deepseek/deepseek-v4-flash",            "label": "DeepSeek V4 Flash (Novita) ⚡",       "provider": "novita", "tier": "fast"},
    # Llama 3.3 70B — най-бърз tool call (1.3s), доказан, balanced tier
    {"id": "meta-llama/llama-3.3-70b-instruct",     "label": "Llama 3.3 70B (Novita) ⚡ tools",    "provider": "novita", "tier": "fast"},
    # Kimi K2 — специализиран за агентски/coding задачи, кратък и прецизен
    {"id": "moonshotai/kimi-k2-instruct",           "label": "Kimi K2 (Novita) 🤖 code/agents",   "provider": "novita", "tier": "fast"},
    # Ling 2.6 Flash — скрит диамант: 1.3s, tools ✅, cheap
    {"id": "inclusionai/ling-2.6-flash",            "label": "Ling 2.6 Flash (Novita) 💨 cheap",  "provider": "novita", "tier": "cheap"},
    # ── OpenRouter — 300+ модела с един ключ ────────────────────────────────────
    # Base URL: https://openrouter.ai/api/v1  |  Key env: OPENROUTER_API_KEY
    {"id": "deepseek/deepseek-v3.2",                 "label": "DeepSeek V3.2 (OpenRouter) 💰",      "provider": "openrouter", "tier": "fast"},
    {"id": "deepseek/deepseek-v4-flash",             "label": "DeepSeek V4 Flash (OpenRouter) ⚡",   "provider": "openrouter", "tier": "fast"},
    {"id": "deepseek/deepseek-r1-0528",              "label": "DeepSeek R1 0528 (OpenRouter) 🧠",   "provider": "openrouter", "tier": "power"},
    {"id": "google/gemini-2.5-flash",                "label": "Gemini 2.5 Flash (OpenRouter) ⚡",   "provider": "openrouter", "tier": "fast"},
    {"id": "google/gemini-2.5-pro",                  "label": "Gemini 2.5 Pro (OpenRouter)",        "provider": "openrouter", "tier": "power"},
    {"id": "anthropic/claude-sonnet-4-5",            "label": "Claude Sonnet 4.5 (OpenRouter)",     "provider": "openrouter", "tier": "fast"},
    {"id": "anthropic/claude-sonnet-4.6",            "label": "Claude Sonnet 4.6 (OpenRouter)",     "provider": "openrouter", "tier": "fast"},
    {"id": "anthropic/claude-fable-5.1",             "label": "Claude Fable 5.1 (OpenRouter)",      "provider": "openrouter", "tier": "power"},
    {"id": "anthropic/claude-opus-4",                "label": "Claude Opus 4 (OpenRouter)",         "provider": "openrouter", "tier": "power"},
    {"id": "anthropic/claude-opus-4.8",              "label": "Claude Opus 4.8 (OpenRouter)",       "provider": "openrouter", "tier": "power"},
    {"id": "anthropic/claude-opus-5",                "label": "Claude Opus 5 (OpenRouter)",         "provider": "openrouter", "tier": "power"},
    {"id": "openai/gpt-4o",                          "label": "GPT-4o (OpenRouter)",                "provider": "openrouter", "tier": "fast"},
    {"id": "openai/gpt-5.5",                         "label": "GPT-5.5 (OpenRouter)",               "provider": "openrouter", "tier": "power"},
    {"id": "openai/gpt-5.6-terra",                   "label": "GPT-5.6 Terra (OpenRouter)",         "provider": "openrouter", "tier": "fast"},
    {"id": "openai/gpt-5.6-sol",                     "label": "GPT-5.6 Sol (OpenRouter)",           "provider": "openrouter", "tier": "power"},
    {"id": "openai/gpt-6-astra",                     "label": "GPT-6 Astra (OpenRouter)",           "provider": "openrouter", "tier": "power"},
    {"id": "x-ai/grok-4.6",                          "label": "Grok 4.6 (OpenRouter)",              "provider": "openrouter", "tier": "power"},
    {"id": "moonshotai/kimi-k3",                     "label": "Kimi K3 (OpenRouter)",               "provider": "openrouter", "tier": "power"},
    {"id": "z-ai/glm-5.3",                           "label": "GLM 5.3 (OpenRouter)",               "provider": "openrouter", "tier": "power"},
    {"id": "z-ai/glm-5.3-flash",                     "label": "GLM 5.3 Flash (OpenRouter)",         "provider": "openrouter", "tier": "fast"},
    {"id": "qwen/qwen3.8-max-0902",                  "label": "Qwen 3.8 Max (OpenRouter)",          "provider": "openrouter", "tier": "power"},
    {"id": "meta-llama/llama-3.3-70b-instruct",      "label": "Llama 3.3 70B (OpenRouter) 🆓",     "provider": "openrouter", "tier": "cheap"},
    {"id": "openrouter/free",                        "label": "OpenRouter Free Router 🆓",         "provider": "openrouter", "tier": "cheap"},
    {"id": "poolside/laguna-s-2.1:free",             "label": "Poolside Laguna S 2.1 (OR free) 🆓", "provider": "openrouter", "tier": "power"},
    {"id": "poolside/laguna-xs-2.1:free",            "label": "Poolside Laguna XS 2.1 (OR free) 🆓","provider": "openrouter", "tier": "cheap"},
    {"id": "google/gemma-4-31b-it:free",             "label": "Gemma 4 31B (OpenRouter free) 🆓",  "provider": "openrouter", "tier": "fast"},
    {"id": "z-ai/glm-5.2:free",                      "label": "GLM 5.2 (OpenRouter free) 🆓",      "provider": "openrouter", "tier": "fast"},
    {"id": "moonshotai/kimi-k2",                     "label": "Kimi K2 (OpenRouter) 🤖",            "provider": "openrouter", "tier": "fast"},
    {"id": "qwen/qwq-32b",                           "label": "QwQ 32B reasoning (OpenRouter) 🧠",  "provider": "openrouter", "tier": "fast"},
    # ── AllToken — 52 модела, единен ALLTOKEN_API_KEY ──────────────────────────
    # Base URL: https://api.alltoken.ai/v1
    # 💎 Nano / super-cheap tier
    {"id": "claude-haiku-4-5",                  "label": "Claude Haiku 4.5 (AllToken) ⚡",              "provider": "alltoken", "tier": "fast"},
    {"id": "gemini-3.1-flash-lite-preview", "label": "Gemini 3.1 Flash Lite (AllToken) 💎 $0.03",  "provider": "alltoken", "tier": "cheap"},
    {"id": "gpt-5.4-nano",                  "label": "GPT-5.4 Nano (AllToken) ⚡ $0.05",            "provider": "alltoken", "tier": "cheap"},
    {"id": "mimo-v2-flash",                 "label": "Mimo v2 Flash (AllToken) ⚡ $0.05",            "provider": "alltoken", "tier": "cheap"},
    {"id": "glm-4.7-flash",                 "label": "GLM-4.7 Flash (AllToken) 💨",                 "provider": "alltoken", "tier": "cheap"},
    # ── Poolside — coding agent, 262K context, $0 beta ────────────────────────
    # Base URL: https://inference.poolside.ai/v1  |  Key env: POOLSIDE_API
    {"id": "poolside/laguna-m.1",           "label": "Poolside Laguna M.1 💻 262K ($0 beta)",        "provider": "poolside", "tier": "power"},
    # ── NVIDIA NIM — free preview tier (rate-limited) ─────────────────────────
    # Base URL: https://integrate.api.nvidia.com/v1  |  Key env: NVIDIA_API_KEY
    {"id": "mistralai/mistral-large-3-675b-instruct-2512", "label": "Mistral Large 3 675B (NVIDIA) 🆓 code", "provider": "nvidia", "tier": "power"},
    {"id": "deepseek-ai/deepseek-v4-flash",             "label": "DeepSeek V4 Flash (NVIDIA) 🆓 ⚡",       "provider": "nvidia", "tier": "fast"},
    {"id": "openai/gpt-oss-120b",                     "label": "GPT-OSS 120B (NVIDIA) 🆓 reasoning",  "provider": "nvidia", "tier": "power"},
    {"id": "qwen/qwen3-next-80b-a3b-instruct",        "label": "Qwen3 Next 80B (NVIDIA) 🆓 ⚡",       "provider": "nvidia", "tier": "fast"},
    # ── Local (Ollama / LM Studio / llama.cpp / vLLM) ────────────────────────
    {"id": "qwen2.5-coder:7b",     "label": "Qwen2.5 Coder 7B (Ollama) 🖥 local",  "provider": "ollama", "tier": "fast"},
    {"id": "qwen2.5-coder:14b",    "label": "Qwen2.5 Coder 14B (Ollama) 🖥",       "provider": "ollama", "tier": "power"},
    {"id": "llama3.2",             "label": "Llama 3.2 (Ollama) 🖥 local",         "provider": "ollama", "tier": "cheap"},
    {"id": "local-model",          "label": "Loaded model (LM Studio) 🖥",         "provider": "lmstudio", "tier": "fast"},
    {"id": "local-model",          "label": "llama-server (llama.cpp) 🖥",         "provider": "llamacpp", "tier": "fast"},
    {"id": "local-model",          "label": "vLLM local 🖥",                       "provider": "vllm", "tier": "power"},
    {"id": "meta/llama-4-maverick-17b-128e-instruct", "label": "Llama 4 Maverick (NVIDIA) 🆓 vision", "provider": "nvidia", "tier": "fast"},
    {"id": "deepseek-v4-flash",             "label": "DeepSeek V4 Flash (AllToken) ⚡",              "provider": "alltoken", "tier": "cheap"},
    {"id": "gemini-3-flash-preview",        "label": "Gemini 3 Flash (AllToken)",                   "provider": "alltoken", "tier": "cheap"},
    # ⚖️ Fast / balanced tier
    {"id": "qwen3.6-35b-a3b",              "label": "Qwen3.6 35B MoE (AllToken) 💰 $0.08",         "provider": "alltoken", "tier": "fast"},
    {"id": "gpt-5.3-codex",                "label": "GPT-5.3 Codex (AllToken) 💻",                  "provider": "alltoken", "tier": "fast"},
    {"id": "qwen3-coder-next",             "label": "Qwen3 Coder Next (AllToken) 💻",               "provider": "alltoken", "tier": "fast"},
    {"id": "gpt-5.4",                       "label": "GPT-5.4 (AllToken)",                          "provider": "alltoken", "tier": "fast"},
    {"id": "mimo-v2-pro",                   "label": "Mimo v2 Pro (AllToken)",                      "provider": "alltoken", "tier": "fast"},
    {"id": "kimi-k2.6",                     "label": "Kimi K2.6 (AllToken) 🤖",                     "provider": "alltoken", "tier": "fast"},
    {"id": "deepseek-v3.2",                 "label": "DeepSeek V3.2 (AllToken) 💰",                 "provider": "alltoken", "tier": "fast"},
    {"id": "glm-5-turbo",                   "label": "GLM-5 Turbo (AllToken)",                      "provider": "alltoken", "tier": "fast"},
    {"id": "qwen3.6-plus",                  "label": "Qwen3.6 Plus (AllToken)",                     "provider": "alltoken", "tier": "fast"},
    {"id": "minimax-m2.5-highspeed",        "label": "MiniMax M2.5 Highspeed (AllToken) ⚡",        "provider": "alltoken", "tier": "fast"},
    {"id": "gpt-5.4-pro",                   "label": "GPT-5.4 Pro (AllToken)",                      "provider": "alltoken", "tier": "fast"},
    {"id": "gemini-3.1-pro-preview",        "label": "Gemini 3.1 Pro (AllToken)",                   "provider": "alltoken", "tier": "fast"},
    {"id": "gpt-5.3-chat",                  "label": "GPT-5.3 Chat (AllToken)",                     "provider": "alltoken", "tier": "fast"},
    # 🔴 Power / deep tier
    {"id": "deepseek-v4-pro",               "label": "DeepSeek V4 Pro (AllToken) 🧠 $0.50",         "provider": "alltoken", "tier": "power"},
    {"id": "claude-sonnet-4-6",             "label": "Claude Sonnet 4.6 (AllToken)",                "provider": "alltoken", "tier": "power"},
    {"id": "gpt-5.5",                       "label": "GPT-5.5 (AllToken) 🏆",                       "provider": "alltoken", "tier": "power"},
    {"id": "claude-opus-4-8",              "label": "Claude Opus 4.8 (AllToken) 🏆",                "provider": "alltoken", "tier": "power"},
    {"id": "o3-mini",                       "label": "o3-mini reasoning (AllToken) 🧠",              "provider": "alltoken", "tier": "power"},
    {"id": "o3",                            "label": "o3 reasoning (AllToken) 🧠",                   "provider": "alltoken", "tier": "power"},
    {"id": "claude-opus-4-7",              "label": "Claude Opus 4.7 (AllToken)",                   "provider": "alltoken", "tier": "power"},
    {"id": "glm-5.1",                       "label": "GLM-5.1 (AllToken)",                          "provider": "alltoken", "tier": "power"},
    {"id": "qwen3.6-max-preview",           "label": "Qwen3.6 Max Preview (AllToken) 🔬",           "provider": "alltoken", "tier": "power"},
]


def _file_cfg_path():
    from .paths import data_root
    return data_root() / "llm_config.json"


def _read_db() -> dict:
    try:
        from replit import db  # type: ignore
        raw = db.get(_DB_KEY)
        if raw:
            return json.loads(raw) if isinstance(raw, str) else dict(raw)
    except Exception:
        pass
    try:
        path = _file_cfg_path()
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _write_db(cfg: dict) -> None:
    try:
        from replit import db  # type: ignore
        db[_DB_KEY] = json.dumps(cfg)
        return
    except Exception:
        pass
    try:
        path = _file_cfg_path()
        path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        log.warning("llm_config: cannot persist config: %s", exc)


def is_nvidia_free_only() -> bool:
    """True when Admin enabled NVIDIA free-only (all LLM paths should prefer NVIDIA)."""
    try:
        from argos_data_nexus.modules.mesh_nvidia_free import free_only_mode_get
        return free_only_mode_get()
    except Exception:
        return False


def persona_via_master_enabled() -> bool:
    """When True, Daedalus chat persona uses master model (no cheap fast-path bypass)."""
    db_cfg = _read_db()
    if "persona_via_master" in db_cfg:
        return bool(db_cfg.get("persona_via_master"))
    return True


def _apply_nvidia_free_policy(cfg: dict) -> dict:
    """Force NVIDIA master when free-only mode is on."""
    if not is_nvidia_free_only():
        return cfg
    nv_key = resolve_provider_api_key("nvidia")
    pd = PROVIDER_DEFAULTS["nvidia"]
    db_cfg = _read_db()
    out = dict(cfg)
    if db_cfg.get("provider") == "nvidia" and db_cfg.get("model"):
        model = db_cfg["model"]
    else:
        model = (db_cfg.get("nvidia_master_model") or pd["default_model"])
    if not nv_key:
        out["warning"] = (
            (out.get("warning") or "")
            + " NVIDIA free-only е ON, но липсва NVIDIA_API_KEY в Secrets."
        ).strip()
        return out
    out.update({
        "provider": "nvidia",
        "model": model,
        "api_key": nv_key,
        "base_url": db_cfg.get("base_url") or pd["base_url"],
        "sdk": pd["sdk"],
        "use_cache": False,
        "source": "nvidia_free_only",
    })
    return out


def get_config() -> dict:
    """Return resolved LLM config: {provider, model, api_key, base_url, sdk, use_cache}."""
    cfg = _resolve_llm_config()
    try:
        from .task_effort import applied_model
        extra = applied_model()
    except Exception:
        extra = None
    if extra and isinstance(cfg, dict):
        from .model_router import overlay_effort
        cfg = overlay_effort(cfg, extra)
    return cfg


def _resolve_llm_config() -> dict:
    """Return resolved LLM config: {provider, model, api_key, base_url, sdk, use_cache}."""
    from .local_llm import (
        is_local_provider,
        local_requested,
        normalize_provider,
        resolve_local_config,
    )

    # 0. Explicit local LLM — wardrobe / DAEDALUS_PROVIDER=ollama / DAEDALUS_LOCAL_LLM=1.
    # Always pin to the local backend even if the probe fails, so a running
    # OpenRouter key cannot silently steal workers, autopilot, or Command Center.
    if local_requested():
        backend = ""
        try:
            from .llm_wardrobe import load as _wardrobe_load
            data = _wardrobe_load()
            backend = normalize_provider(str(data.get("backend") or ""))
        except Exception:
            backend = ""
        if not backend or backend in {"auto", "cloud"}:
            backend = (os.environ.get("DAEDALUS_PROVIDER") or "ollama").strip() or "ollama"
        local = resolve_local_config(backend)
        if local:
            return local

    db_cfg = _read_db()

    # 1. Replit DB override — model+provider from Admin UI; key from DB or env
    if db_cfg.get("model"):
        provider = db_cfg.get("provider", "anthropic")
        if is_local_provider(provider):
            local = resolve_local_config(provider)
            if local:
                if db_cfg.get("model"):
                    local["model"] = db_cfg["model"]
                if db_cfg.get("base_url"):
                    local["base_url"] = db_cfg["base_url"]
                local["source"] = "db_local"
                return local
        pd = PROVIDER_DEFAULTS.get(provider, PROVIDER_DEFAULTS["openai"])
        api_key = db_cfg.get("api_key") or resolve_provider_api_key(provider)
        source = "db"
        if api_key and not db_cfg.get("api_key"):
            source = "db+env_key"
        if not api_key:
            source = "db_incomplete"
            log.warning(
                "llm_config: DB has model %s/%s but no API key in DB or env",
                provider,
                db_cfg["model"],
            )
            incomplete = {
                "provider": provider,
                "model": db_cfg["model"],
                "api_key": "",
                "base_url": db_cfg.get("base_url") or pd["base_url"],
                "sdk": pd["sdk"],
                "use_cache": db_cfg.get("use_cache", provider == "anthropic"),
                "source": source,
                "warning": "Липсва API ключ за избрания provider — добави в Secrets или Admin LLM.",
            }
            return _apply_nvidia_free_policy(incomplete)
        resolved = {
            "provider": provider,
            "model": db_cfg["model"],
            "api_key": api_key,
            "base_url": db_cfg.get("base_url") or pd["base_url"],
            "sdk": pd["sdk"],
            "use_cache": db_cfg.get("use_cache", provider == "anthropic"),
            "source": source,
        }
        return _apply_nvidia_free_policy(resolved)

    # 2. Env var overrides (DAEDALUS_*)
    env_key  = os.environ.get("DAEDALUS_API_KEY")
    env_url  = os.environ.get("DAEDALUS_BASE_URL")
    env_model = os.environ.get("DAEDALUS_MODEL")
    env_provider = os.environ.get("DAEDALUS_PROVIDER", "")
    if env_model and (env_key or is_local_provider(env_provider) or env_url):
        provider = env_provider or ("custom" if env_url else "anthropic")
        if is_local_provider(provider) or (env_url and not env_key):
            local = resolve_local_config(provider if is_local_provider(provider) else "ollama")
            if local:
                local["model"] = env_model
                if env_url:
                    from .local_llm import _ensure_v1
                    local["base_url"] = _ensure_v1(env_url)
                if env_key:
                    local["api_key"] = env_key
                local["source"] = "env_local"
                return local
        pd = PROVIDER_DEFAULTS.get(provider, PROVIDER_DEFAULTS["openai"])
        return _apply_nvidia_free_policy({
            "provider":  provider,
            "model":     env_model,
            "api_key":   env_key,
            "base_url":  env_url or pd["base_url"],
            "sdk":       pd["sdk"],
            "use_cache": provider == "anthropic",
            "source":    "env",
        })

    # 2b. NVIDIA free-only without explicit DB model — use NVIDIA default master
    if is_nvidia_free_only():
        nv_key = resolve_provider_api_key("nvidia")
        if nv_key:
            pd = PROVIDER_DEFAULTS["nvidia"]
            return {
                "provider": "nvidia",
                "model": pd["default_model"],
                "api_key": nv_key,
                "base_url": pd["base_url"],
                "sdk": pd["sdk"],
                "use_cache": False,
                "source": "nvidia_free_only",
            }

    # 3. External providers fallback (OpenRouter → Novita) — no Replit AI integrations
    or_key = os.environ.get("OPENROUTER_API_KEY", "")
    if or_key:
        return _apply_nvidia_free_policy({
            "provider":  "openrouter",
            "model":     "anthropic/claude-sonnet-4-5",
            "api_key":   or_key,
            "base_url":  "https://openrouter.ai/api/v1",
            "sdk":       "openai",
            "use_cache": False,
            "source":    "openrouter_default",
        })
    alltoken_key = os.environ.get("ALLTOKEN_API_KEY", "")
    if alltoken_key:
        pd = PROVIDER_DEFAULTS["alltoken"]
        return _apply_nvidia_free_policy({
            "provider":  "alltoken",
            "model":     os.environ.get("DAEDALUS_MODEL") or pd["default_model"],
            "api_key":   alltoken_key,
            "base_url":  pd["base_url"],
            "sdk":       pd["sdk"],
            "use_cache": False,
            "source":    "alltoken_default",
        })
    novita_key = os.environ.get("NOVITA_API_KEY", "")
    if novita_key:
        return _apply_nvidia_free_policy({
            "provider":  "novita",
            "model":     "deepseek/deepseek-v3.2",
            "api_key":   novita_key,
            "base_url":  "https://api.novita.ai/v3/openai",
            "sdk":       "openai",
            "use_cache": False,
            "source":    "novita_default",
        })
    nvidia_key = os.environ.get("NVIDIA_API_KEY", "")
    if nvidia_key:
        pd = PROVIDER_DEFAULTS["nvidia"]
        return _apply_nvidia_free_policy({
            "provider":  "nvidia",
            "model":     pd["default_model"],
            "api_key":   nvidia_key,
            "base_url":  pd["base_url"],
            "sdk":       pd["sdk"],
            "use_cache": False,
            "source":    "nvidia_default",
        })
    local = resolve_local_config(None)
    if local and local.get("reachable"):
        return local

    raise RuntimeError(
        "Няма конфигуриран LLM provider. "
        "Задай ALLTOKEN_API_KEY, OPENROUTER_API_KEY, NOVITA_API_KEY или NVIDIA_API_KEY, "
        "или DAEDALUS_API_KEY + DAEDALUS_MODEL, "
        "или пусни локален Ollama/LM Studio и задай DAEDALUS_PROVIDER=ollama "
        "(или DAEDALUS_LOCAL_LLM=1)."
    )


def set_config(
    model: str,
    api_key: str,
    provider: str = "anthropic",
    base_url: str = "",
    use_cache: bool = True,
    *,
    persona_via_master: bool | None = None,
) -> dict:
    """Persist LLM config to Replit DB. Returns the saved config.

    api_key may be empty — runtime resolves from env via get_config().
    Live writes go through Connectors / wardrobe (see llm_router_config).
    """
    from .llm_router_config import require_llm_write_permission

    require_llm_write_permission()
    pd = PROVIDER_DEFAULTS.get(provider, PROVIDER_DEFAULTS["openai"])
    prev = _read_db()
    cfg: dict = {
        "model": model,
        "provider": provider,
        "base_url": base_url or pd["base_url"],
        "use_cache": use_cache,
    }
    if (api_key or "").strip():
        cfg["api_key"] = api_key.strip()
    if persona_via_master is not None:
        cfg["persona_via_master"] = bool(persona_via_master)
    elif "persona_via_master" in prev:
        cfg["persona_via_master"] = prev["persona_via_master"]
    _write_db(cfg)
    return cfg


def set_persona_via_master(enabled: bool) -> dict:
    """Toggle whether Daedalus chat uses master model as persona (router-only write)."""
    from .llm_router_config import require_llm_write_permission

    require_llm_write_permission()
    cfg = _read_db()
    cfg["persona_via_master"] = bool(enabled)
    _write_db(cfg)
    return {"persona_via_master": bool(enabled)}


def describe_effective_config() -> dict:
    """Admin diagnostics: raw DB row vs resolved get_config() + drift warnings."""
    db_cfg = _read_db()
    warnings: list[str] = []
    try:
        effective = get_config()
    except RuntimeError as exc:
        effective = {"error": str(exc)}
    else:
        if db_cfg.get("model"):
            if effective.get("model") != db_cfg.get("model"):
                warnings.append(
                    f"DB model {db_cfg.get('model')!r} ≠ effective {effective.get('model')!r} "
                    f"(source={effective.get('source')})"
                )
            if effective.get("provider") != db_cfg.get("provider"):
                warnings.append(
                    f"DB provider {db_cfg.get('provider')!r} ≠ effective {effective.get('provider')!r}"
                )
        if effective.get("source") in ("openrouter_default", "novita_default"):
            if db_cfg.get("model"):
                warnings.append(
                    "Има запис в DB, но effective идва от env fallback — провери API ключ в Secrets."
                )
        if effective.get("source") == "openrouter_default":
            warnings.append(
                "OpenRouter без избран модел ползва платения Claude Sonnet 4.5. "
                "За $0 приложи preset or_free (модели с :free) или задай DAEDALUS_MODEL."
            )
        if effective.get("warning"):
            warnings.append(str(effective["warning"]))
        if effective.get("source") == "db_incomplete":
            warnings.append("Моделът е запазен в DB, но липсва API ключ.")

    safe_eff = {k: v for k, v in (effective or {}).items() if k != "api_key"}
    safe_eff["api_key_set"] = bool((effective or {}).get("api_key"))
    safe_db = {k: v for k, v in db_cfg.items() if k != "api_key"}
    safe_db["api_key_set"] = bool(db_cfg.get("api_key"))
    return {
        "db_raw": safe_db,
        "effective": safe_eff,
        "warnings": warnings,
        "aligned": len(warnings) == 0 and not safe_eff.get("error"),
    }


def clear_config() -> None:
    """Remove DB override — fall back to env/OpenRouter/Novita defaults."""
    from .llm_router_config import require_llm_write_permission

    require_llm_write_permission()
    try:
        from replit import db  # type: ignore
        del db[_DB_KEY]
    except Exception:
        pass
    try:
        _file_cfg_path().unlink(missing_ok=True)
    except Exception:
        pass


# Replit AI Integrations — скрити по подразбиране (по-скъпи от AllToken/Novita/OR).
_REPLIT_PROVIDERS = frozenset({"replit_openai", "replit_gemini"})
_REPLIT_ANTHROPIC_IDS = frozenset({
    "claude-haiku-4-5", "claude-sonnet-4-6", "claude-opus-4-7", "claude-opus-4-5",
})


def is_replit_preset(entry: dict) -> bool:
    """True за Replit modelfarm записи (не за AllToken/OpenRouter алтернативи)."""
    provider = (entry.get("provider") or "").strip()
    if provider in _REPLIT_PROVIDERS:
        return True
    if provider == "anthropic" and (entry.get("id") or "") in _REPLIT_ANTHROPIC_IDS:
        return True
    return False


def list_preset_models(*, include_replit: bool | None = None) -> list[dict]:
    """MIX/worker dropdown models. Replit entries hidden unless DAEDALUS_SHOW_REPLIT_LLM=1."""
    if include_replit is None:
        include_replit = os.environ.get("DAEDALUS_SHOW_REPLIT_LLM", "0").strip().lower() in (
            "1", "true", "yes", "on",
        )
    if include_replit:
        return list(PRESET_MODELS)
    return [m for m in PRESET_MODELS if not is_replit_preset(m)]
