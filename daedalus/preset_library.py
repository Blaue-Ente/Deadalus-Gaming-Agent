"""
Preset Library — curated LLM configurations + honest data-driven scoring.

Surfaces a curated subset of CONFIG_PRESETS (llm_presets.py) into the Model
Registry tab with a 6-axis radar score computed deterministically from per-model
metrics (cost / latency / context / curated quality·reasoning·code factors).

The SAME scoring is reused by the LLM MIX panel so a hand-mixed config shows the
exact same radar math as a preset card (one source of truth).

Axes (0-100):  speed · quality · economy · reasoning · code · context
"""
from __future__ import annotations

import math
from typing import Any

from .llm_presets import CONFIG_PRESETS, get_preset
from .llm_config import get_config


# ── Per-model metrics ────────────────────────────────────────────────────────
# cin/cout = USD per 1M tokens (in/out), lat = typical seconds, ctx = context k,
# q/r/c = curated quality / reasoning / code factors (0-100), benchmark-informed.
_METRICS: dict[str, dict[str, float]] = {
    "deepseek/deepseek-v3.2":                       {"cin": .25, "cout": .90, "lat": 7,   "ctx": 64,  "q": 80, "r": 70, "c": 85},
    "deepseek/deepseek-chat-v3-0324":               {"cin": .27, "cout": 1.10,"lat": 7,   "ctx": 64,  "q": 80, "r": 70, "c": 84},
    "deepseek/deepseek-v4-flash":                   {"cin": .07, "cout": .30, "lat": 3,   "ctx": 64,  "q": 68, "r": 55, "c": 70},
    "deepseek/deepseek-r1-0528":                    {"cin": .55, "cout": 2.19,"lat": 45,  "ctx": 64,  "q": 88, "r": 96, "c": 72},
    "meta-llama/llama-3.1-8b-instruct":             {"cin": .05, "cout": .05, "lat": 2,   "ctx": 16,  "q": 50, "r": 35, "c": 45},
    "meta-llama/llama-3.3-70b-instruct":            {"cin": .20, "cout": .60, "lat": 5,   "ctx": 128, "q": 76, "r": 62, "c": 68},
    "qwen/qwen3-235b-a22b-instruct-2507":           {"cin": .20, "cout": .80, "lat": 9,   "ctx": 256, "q": 85, "r": 82, "c": 82},
    "qwen/qwen3-coder-30b-a3b-instruct":            {"cin": .15, "cout": .60, "lat": 4,   "ctx": 32,  "q": 76, "r": 58, "c": 88},
    "qwen/qwen3-coder-480b-a35b-instruct":          {"cin": .50, "cout": 1.50,"lat": 12,  "ctx": 64,  "q": 86, "r": 78, "c": 95},
    "qwen/qwq-32b":                                 {"cin": .10, "cout": .40, "lat": 10,  "ctx": 32,  "q": 74, "r": 84, "c": 70},
    "inclusionai/ling-2.6-flash":                   {"cin": .01, "cout": .02, "lat": 1.3, "ctx": 64,  "q": 58, "r": 40, "c": 55},
    # Current Anthropic models on OpenRouter (prices from OpenRouter 2026-11-12; quality figures are estimates placed
    # above their 4.x predecessors, so presets moved to 5.5 are not scored as unknown cheap models).
    "anthropic/claude-sonnet-5.5":                  {"cin": 2.0, "cout": 10.0,"lat": 6,   "ctx": 200, "q": 97, "r": 96, "c": 91},
    "anthropic/claude-haiku-5.5":                   {"cin": .10, "cout": .50, "lat": 2.5, "ctx": 200, "q": 82, "r": 66, "c": 75},
    "anthropic/claude-opus-5.5":                    {"cin": 4.0, "cout": 20.0,"lat": 10,  "ctx": 200, "q": 99, "r": 99, "c": 93},
    "deepseek/deepseek-v3.2":                       {"cin": .27, "cout": 1.10,"lat": 8,   "ctx": 64,  "q": 84, "r": 80, "c": 86},
    "google/gemini-3.8-flash":                      {"cin": .75, "cout": 3.75,"lat": 3,   "ctx": 1000,"q": 84, "r": 74, "c": 80},
    "qwen/qwen3.8-flash":                           {"cin": .15, "cout": .47, "lat": 3,   "ctx": 128, "q": 78, "r": 70, "c": 76},
    "anthropic/claude-sonnet-4-5":                  {"cin": 3.0, "cout": 15.0,"lat": 8,   "ctx": 200, "q": 96, "r": 95, "c": 88},
    "anthropic/claude-haiku-4-5":                   {"cin": .80, "cout": 4.0, "lat": 3,   "ctx": 200, "q": 78, "r": 60, "c": 70},
    "anthropic/claude-haiku-3-5":                   {"cin": .80, "cout": 4.0, "lat": 3,   "ctx": 200, "q": 75, "r": 56, "c": 66},
    "anthropic/claude-opus-4-5":                    {"cin": 5.0, "cout": 25.0,"lat": 12,  "ctx": 200, "q": 99, "r": 98, "c": 90},
    "moonshotai/kimi-k2-instruct":                  {"cin": .60, "cout": 2.50,"lat": 8,   "ctx": 128, "q": 85, "r": 75, "c": 86},
    "moonshotai/kimi-k2.5":                         {"cin": .40, "cout": 1.90,"lat": 8,   "ctx": 200, "q": 85, "r": 78, "c": 85},
    "moonshotai/kimi-k2.6":                         {"cin": .73, "cout": 3.49,"lat": 9,   "ctx": 262, "q": 87, "r": 82, "c": 86},
    "moonshotai/kimi-k2-thinking":                  {"cin": .60, "cout": 2.50,"lat": 20,  "ctx": 200, "q": 88, "r": 92, "c": 84},
    "minimax/minimax-m2.7":                         {"cin": .28, "cout": 1.20,"lat": 12,  "ctx": 200, "q": 88, "r": 84, "c": 90},
    "z-ai/glm-4.7-flash":                           {"cin": .06, "cout": .40, "lat": 3,   "ctx": 128, "q": 72, "r": 58, "c": 74},
    "z-ai/glm-4.6":                                 {"cin": .43, "cout": 1.74,"lat": 7,   "ctx": 200, "q": 84, "r": 80, "c": 85},
    "z-ai/glm-4.7":                                 {"cin": .40, "cout": 1.75,"lat": 7,   "ctx": 200, "q": 85, "r": 81, "c": 86},
    "z-ai/glm-5.1":                                 {"cin": .98, "cout": 3.08,"lat": 9,   "ctx": 202, "q": 89, "r": 85, "c": 88},
    "google/gemini-2.5-pro":                        {"cin": 1.25,"cout": 10.0,"lat": 8,   "ctx": 1000,"q": 92, "r": 88, "c": 82},
    "google/gemini-2.5-flash":                      {"cin": .15, "cout": .60, "lat": 2,   "ctx": 1000,"q": 74, "r": 58, "c": 66},
    "google/gemini-2.0-flash-001":                  {"cin": .10, "cout": .40, "lat": 1,   "ctx": 1000,"q": 70, "r": 54, "c": 62},
    "deepseek/deepseek-r1":                         {"cin": .50, "cout": 2.15,"lat": 40,  "ctx": 64,  "q": 87, "r": 95, "c": 72},
    "deepseek/deepseek-v3":                         {"cin": .22, "cout": .88, "lat": 7,   "ctx": 64,  "q": 79, "r": 68, "c": 84},
    "qwen/qwen3-30b-a3b":                           {"cin": .10, "cout": .30, "lat": 4,   "ctx": 32,  "q": 70, "r": 56, "c": 72},
    # ── AllToken модели (без provider prefix) ──────────────────────────────────
    "gemini-3.1-flash-lite-preview":                {"cin": .030,"cout": .120,"lat": 1.5, "ctx": 1000,"q": 78, "r": 40, "c": 72},
    "gpt-5.4-nano":                                 {"cin": .050,"cout": .200,"lat": 2.0, "ctx": 128, "q": 76, "r": 45, "c": 74},
    "mimo-v2-flash":                                {"cin": .050,"cout": .200,"lat": 1.5, "ctx": 128, "q": 76, "r": 40, "c": 74},
    "qwen3.6-35b-a3b":                              {"cin": .080,"cout": .300,"lat": 7.0, "ctx": 128, "q": 83, "r": 80, "c": 84},
    "deepseek-v4-pro":                              {"cin": .500,"cout": 2.000,"lat": 8.0,"ctx": 64,  "q": 88, "r": 82, "c": 88},
    "qwen3-coder-next":                             {"cin": .100,"cout": .400,"lat": 2.0, "ctx": 128, "q": 80, "r": 55, "c": 92},
    "gpt-5.3-codex":                                {"cin": 1.00,"cout": 4.000,"lat": 4.0,"ctx": 128, "q": 85, "r": 65, "c": 95},
    "claude-sonnet-4-6":                            {"cin": 3.00,"cout": 15.0, "lat": 7.0,"ctx": 200, "q": 97, "r": 96, "c": 90},
    "claude-opus-4-7":                              {"cin": 15.0,"cout": 75.0, "lat": 14.0,"ctx":200, "q": 99, "r": 99, "c": 93},
    "claude-opus-4-8":                              {"cin": 15.0,"cout": 75.0, "lat": 14.0,"ctx":200, "q": 99, "r": 98, "c": 92},
    "claude-haiku-4-5":                             {"cin": .080,"cout": .400,"lat": 2.5, "ctx": 200, "q": 78, "r": 58, "c": 70},
    "glm-5-turbo":                                  {"cin": .080,"cout": .300,"lat": 3.0, "ctx": 128, "q": 76, "r": 58, "c": 74},
    "gpt-5.4":                                      {"cin": 5.00,"cout": 20.0, "lat": 5.0, "ctx": 128, "q": 94, "r": 88, "c": 88},
    "poolside/laguna-m.1":                          {"cin": .000,"cout": .000,"lat": 8.0, "ctx": 262, "q": 86, "r": 78, "c": 94},
    "poolside/laguna-s-2.1:free":                   {"cin": .000,"cout": .000,"lat": 5.0, "ctx": 262, "q": 82, "r": 70, "c": 90},
    "poolside/laguna-xs-2.1:free":                  {"cin": .000,"cout": .000,"lat": 3.0, "ctx": 262, "q": 70, "r": 55, "c": 80},
    "openrouter/free":                              {"cin": .000,"cout": .000,"lat": 4.0, "ctx": 200, "q": 68, "r": 50, "c": 70},
    "auto":                                         {"cin": .000,"cout": .000,"lat": 4.0, "ctx": 128, "q": 70, "r": 55, "c": 74},
    "auto/coding":                                  {"cin": .000,"cout": .000,"lat": 5.0, "ctx": 128, "q": 74, "r": 58, "c": 86},
    "auto/fast":                                    {"cin": .000,"cout": .000,"lat": 2.0, "ctx": 64,  "q": 62, "r": 42, "c": 64},
    "auto/cheap":                                   {"cin": .000,"cout": .000,"lat": 3.5, "ctx": 64,  "q": 60, "r": 40, "c": 62},
    "google/gemma-4-31b-it:free":                   {"cin": .000,"cout": .000,"lat": 4.0, "ctx": 262, "q": 74, "r": 58, "c": 72},
    "z-ai/glm-5.2:free":                            {"cin": .000,"cout": .000,"lat": 5.0, "ctx": 256, "q": 80, "r": 72, "c": 82},
    "qwen3.6-flash":                                {"cin": .050,"cout": .200,"lat": 3.0, "ctx": 128, "q": 76, "r": 70, "c": 76},
    "kimi-k2.6":                                    {"cin": .500,"cout": 2.000,"lat": 9.0, "ctx": 262, "q": 87, "r": 82, "c": 86},
    "gemini-3-flash-preview":                       {"cin": .100,"cout": .400,"lat": 2.0, "ctx": 1000,"q": 76, "r": 45, "c": 70},
    "gpt-5.5":                                      {"cin": 10.0,"cout": 40.0, "lat": 6.0,"ctx": 128, "q": 99, "r": 97, "c": 93},
    "o3":                                           {"cin": 10.0,"cout": 40.0, "lat": 10.0,"ctx":128, "q": 98, "r": 99, "c": 90},
    "o3-mini":                                      {"cin": 1.10,"cout": 4.400,"lat": 8.0,"ctx": 128, "q": 88, "r": 95, "c": 82},
    "deepseek-v4-flash":                            {"cin": .150,"cout": .600,"lat": 3.0, "ctx": 64,  "q": 74, "r": 58, "c": 76},
    "minimax-m2.7-highspeed":                       {"cin": .200,"cout": .800,"lat": 4.0, "ctx": 200, "q": 82, "r": 72, "c": 80},
    "qwen3.7-max":                                  {"cin": .300,"cout": 1.200,"lat": 6.0,"ctx": 128, "q": 84, "r": 78, "c": 82},
    # ── NVIDIA NIM free tier (integrate.api.nvidia.com) ───────────────────────
    "mistralai/mistral-large-4-1t-a52b":   {"cin": .000,"cout": .000,"lat": 11.0,"ctx": 262, "q": 90, "r": 88, "c": 92},
    "nvidia/nemotron-3.5-lightning-30b-a3b":                  {"cin": .000,"cout": .000,"lat": 4.0, "ctx": 64,  "q": 74, "r": 58, "c": 76},
    "openai/gpt-oss-120b":                          {"cin": .000,"cout": .000,"lat": 10.0,"ctx": 128, "q": 86, "r": 88, "c": 84},
    "openai/gpt-oss-20b":                           {"cin": .000,"cout": .000,"lat": 3.0, "ctx": 128, "q": 70, "r": 58, "c": 68},
    "nvidia/nemotron-3-ultra-550b-a55b":                  {"cin": .000,"cout": .000,"lat": 9.0, "ctx": 64,  "q": 88, "r": 82, "c": 90},
    "meta/llama-3.2-90b-vision-instruct":      {"cin": .000,"cout": .000,"lat": 6.0, "ctx": 1000,"q": 78, "r": 62, "c": 68},
    "nvidia/nemotron-3.5-bolt-e4b":                   {"cin": .000,"cout": .000,"lat": 2.0, "ctx": 128, "q": 55, "r": 38, "c": 48},
}

_DEFAULT_METRIC = {"cin": .30, "cout": 1.0, "lat": 6, "ctx": 64, "q": 70, "r": 60, "c": 65}

# Curated subset shown in the library + "what it's best for" (BG).
CHOSEN_IDS = [
    "balanced", "eco", "or_free", "omniroute", "nvidia_free", "premium", "code", "speed",
    "hybrid", "or_china_mix",
    "at_nano", "at_gems", "at_coder", "at_frontier",
]

BEST_FOR: dict[str, str] = {
    "balanced":     "Ежедневна работа — баланс ум/цена. Добрият избор по подразбиране.",
    "eco":          "Масови batch задачи и discovery loops — максимална икономия.",
    "or_free":      "OpenRouter :free — $0 live тест / собственик без кредит. Rate-limited.",
    "omniroute":    "Локален OmniRoute sidecar (:20128) — $0 auto / auto/coding. Gateway-ът не е в репото.",
    "nvidia_free":  "NVIDIA NIM free preview — $0, отделен NVIDIA_API_KEY.",
    "premium":      "Критични задачи където качеството е над всичко (Claude).",
    "code":         "Кодиране и игри в Playground — специализирани coder модели.",
    "speed":        "Интерактивни сесии — минимална латентност (flash модели).",
    "hybrid":       "Евтин loop (~70%) + Claude Sonnet само за финалните синтези.",
    "or_china_mix": "Тест на китайските флагмани — MiniMax + GLM + Kimi заедно.",
    "at_nano":      "AllToken: абсолютна икономия — $0.03-0.08/1M, Gemini 3.1 Lite + GPT-5 Nano.",
    "at_gems":      "AllToken: скрити съкровища — MiMo Flash + MoE 35B + DeepSeek Pro.",
    "at_coder":     "AllToken: coding специалисти — Qwen Coder Next + Codex + DeepSeek Pro.",
    "at_frontier":  "AllToken: абсолютен max — Claude Sonnet 4.6 master + Opus 4.8 deep.",
}


def _metric(model_id: str) -> dict[str, float]:
    return _METRICS.get((model_id or "").strip(), _DEFAULT_METRIC)


def _clamp(v: float, lo: float = 0, hi: float = 100) -> int:
    return int(round(max(lo, min(hi, v))))


def _speed_score(lat: float) -> float:
    # 1.3s → ~97, 3s → ~84, 7s → ~52, 12s → ~12, 45s → 0
    return 100 - (lat - 1) * 8


def _economy_score(blended_cost: float) -> float:
    # output-weighted blended USD/1M → score; .05→~97, .9→~65, 3→~36, 11→~13
    return 100.0 / (1.0 + 0.6 * blended_cost)


def _context_score(ctx_k: float) -> float:
    # log scale: 8k → 0, 262k → 100
    if ctx_k <= 8:
        return 0.0
    return (math.log2(ctx_k / 8.0) / math.log2(262.0 / 8.0)) * 100.0


def _slot_models(cfg: dict) -> dict[str, str]:
    """Extract {slot: model_id} from a preset/mix config (slots use 'model')."""
    out = {}
    for slot in ("master", "fast", "balanced", "deep"):
        sc = cfg.get(slot) or {}
        out[slot] = sc.get("model", "")
    return out


def score_config(cfg: dict) -> dict[str, int]:
    """Compute the 6-axis radar (0-100) for a master+fast+balanced+deep config."""
    m = {slot: _metric(mid) for slot, mid in _slot_models(cfg).items()}
    master, fast, balanced, deep = m["master"], m["fast"], m["balanced"], m["deep"]

    # Speed — weighted by how often each slot is on the hot path.
    speed = (_speed_score(master["lat"]) * 0.40
             + _speed_score(fast["lat"]) * 0.35
             + _speed_score(balanced["lat"]) * 0.25)

    # Quality — master leads, deep for hard finals, balanced fills.
    quality = master["q"] * 0.50 + deep["q"] * 0.30 + balanced["q"] * 0.20

    # Economy — output-weighted blended cost across slots.
    def blended(x: dict) -> float:
        return x["cin"] * 0.3 + x["cout"] * 0.7
    cost = (blended(master) * 0.40 + blended(balanced) * 0.30
            + blended(fast) * 0.20 + blended(deep) * 0.10)
    economy = _economy_score(cost)

    # Reasoning — best of master/deep.
    reasoning = master["r"] * 0.5 + deep["r"] * 0.5

    # Code — master + the code-heavy slots.
    code = master["c"] * 0.4 + balanced["c"] * 0.3 + deep["c"] * 0.3

    # Context — the largest window available in the mix.
    context = max(_context_score(x["ctx"]) for x in m.values())

    return {
        "speed":     _clamp(speed),
        "quality":   _clamp(quality),
        "economy":   _clamp(economy),
        "reasoning": _clamp(reasoning),
        "code":      _clamp(code),
        "context":   _clamp(context),
    }


def _effective_slots() -> dict[str, tuple[str, str]]:
    """Current live config as {slot: (provider, model)} for master+fast+balanced+deep."""
    out: dict[str, tuple[str, str]] = {}
    try:
        m = get_config()
        out["master"] = (m.get("provider", ""), m.get("model", ""))
    except Exception:
        out["master"] = ("", "")
    try:
        from .workers import get_worker_config
        wc = get_worker_config()
        for slot in ("fast", "balanced", "deep"):
            sc = wc.get(slot) or {}
            out[slot] = (sc.get("provider", ""), sc.get("model", ""))
    except Exception:
        for slot in ("fast", "balanced", "deep"):
            out.setdefault(slot, ("", ""))
    return out


def active_preset_id() -> str | None:
    """Which curated preset matches the current config across ALL 4 slots.

    Master-only matching is ambiguous (e.g. `balanced` and `hybrid` share the
    same master), so we require every slot's (provider, model) to match.
    """
    eff = _effective_slots()
    if not eff.get("master", ("", ""))[1]:
        return None
    for pid in CHOSEN_IDS:
        p = get_preset(pid)
        if not p:
            continue
        ok = True
        for slot in ("master", "fast", "balanced", "deep"):
            sc = p.get(slot) or {}
            if (sc.get("provider", ""), sc.get("model", "")) != eff.get(slot, ("", "")):
                ok = False
                break
        if ok:
            return pid
    return None


def library() -> list[dict[str, Any]]:
    """Return the curated presets with scores + best_for, ready for the UI."""
    out: list[dict[str, Any]] = []
    active = active_preset_id()
    for pid in CHOSEN_IDS:
        p = get_preset(pid)
        if not p:
            continue
        out.append({
            "id":       p["id"],
            "label":    p["label"],
            "budget":   p["budget"],
            "best_for": BEST_FOR.get(pid, ""),
            "master":   p["master"],
            "fast":     p["fast"],
            "balanced": p["balanced"],
            "deep":     p["deep"],
            "scores":   score_config(p),
            "active":   p["id"] == active,
        })
    return out
