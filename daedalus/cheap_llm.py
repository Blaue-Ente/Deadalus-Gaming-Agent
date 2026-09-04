"""Cheap wrap-up / Navigator LLM — never Claude, never a dead Ling id.

Order: NVIDIA NIM lightning → OpenRouter :free lightning → Novita DeepSeek.
"""
from __future__ import annotations

import os
from typing import Any


def resolve_cheap_openai() -> dict[str, Any] | None:
    """Return {api_key, base_url, model} or None. Callers must not log the key."""
    nv = (os.environ.get("NVIDIA_API_KEY") or "").strip()
    if nv:
        return {
            "api_key": nv,
            "base_url": "https://integrate.api.nvidia.com/v1",
            "model": "nvidia/nemotron-3.5-lightning-30b-a3b",
        }
    or_key = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
    if or_key:
        return {
            "api_key": or_key,
            "base_url": "https://openrouter.ai/api/v1",
            "model": "nvidia/nemotron-3.5-lightning:free",
        }
    nov = (os.environ.get("NOVITA_API_KEY") or "").strip()
    if nov:
        return {
            "api_key": nov,
            "base_url": "https://api.novita.ai/v3/openai",
            "model": "deepseek/deepseek-v3.2",
        }
    return None
