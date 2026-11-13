"""NVIDIA models, checked live 2026-11-13 with the owner's key: several ids Daedalus named were gone (the NVIDIA
default among them), so the NVIDIA brain did not work. Nemotron 3 Ultra answered in 2.7 s, Nemotron 3.5 Lightning in
2.6 s, Bolt in 0.5 s, Mistral Large 4 correctly; they replace the retired ids."""
from __future__ import annotations

import pathlib

RETIRED = ("qwen/qwen3-next-80b-a3b-instruct", "mistralai/mistral-large-3-675b-instruct-2512",
           "deepseek-ai/deepseek-v4-flash", "deepseek-ai/deepseek-v4-pro",
           "meta/llama-4-maverick-17b-128e-instruct", "meta/llama-3.2-3b-instruct", "moonshotai/kimi-k2.6")


def test_the_nvidia_default_is_nemotron_3_ultra():
    from daedalus.llm_config import PROVIDER_DEFAULTS
    assert PROVIDER_DEFAULTS["nvidia"]["default_model"] == "nvidia/nemotron-3-ultra-550b-a55b"


def test_no_retired_nvidia_model_is_named_in_daedalus():
    """Outside the one map that replaces them (kimi-k2.6 stays allowed on AllToken and OpenRouter rows)."""
    from daedalus import llm_config

    assert set(llm_config.NVIDIA_RETIRED) == set(RETIRED)
    root = pathlib.Path(__file__).resolve().parents[1] / "daedalus"
    hits = []
    for p in root.rglob("*.py"):
        text = p.read_text()
        if p.name == "llm_config.py":
            text = text.replace(text[text.index("NVIDIA_RETIRED: dict"):text.index("def current_nvidia_model")], "")
        for model in RETIRED:
            for line in text.splitlines():
                if model in line and not (model == "moonshotai/kimi-k2.6" and "nvidia" not in line.lower()):
                    hits.append(f"{p.name}: {line.strip()[:80]}")
    assert hits == []


def test_a_saved_retired_nvidia_brain_keeps_working(monkeypatch):
    from daedalus import llm_config

    monkeypatch.setattr(llm_config, "_resolve_llm_config", lambda: {
        "provider": "nvidia", "model": "qwen/qwen3-next-80b-a3b-instruct", "api_key": "k", "source": "db"})
    cfg = llm_config.get_brain_config()
    assert cfg["model"] == "nvidia/nemotron-3-ultra-550b-a55b"
    assert cfg["model_migrated_from"] == "qwen/qwen3-next-80b-a3b-instruct"
    assert llm_config.current_nvidia_model("openrouter", "moonshotai/kimi-k2.6") == "moonshotai/kimi-k2.6"


def test_the_free_nvidia_mix_names_live_models():
    from daedalus.llm_presets import nvidia_free_mix_slots

    models = {slot.get("model") for slot in nvidia_free_mix_slots().values() if isinstance(slot, dict)}
    assert "nvidia/nemotron-3-ultra-550b-a55b" in models
    assert not models & set(RETIRED)
