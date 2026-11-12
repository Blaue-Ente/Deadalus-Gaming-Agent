"""DAEDALUS_MODEL is the owner's pick for every keyed provider. Found in a live OpenRouter test (2026-11-11): with only
OPENROUTER_API_KEY set, the pick was ignored and Claude Sonnet 4.5 was used (about 30x the price of the model asked
for), while AllToken honoured it."""
from __future__ import annotations

import pytest
from a0_support import a0_tmp  # noqa: F401

KEYS = ("DAEDALUS_API_KEY", "DAEDALUS_BASE_URL", "DAEDALUS_PROVIDER", "DAEDALUS_MODEL", "ALLTOKEN_API_KEY",
        "OPENROUTER_API_KEY", "NOVITA_API_KEY", "NVIDIA_API_KEY", "ANTHROPIC_API_KEY", "DAEDALUS_LOCAL_LLM")


@pytest.fixture
def clean_env(a0_tmp, monkeypatch):  # noqa: F811
    for k in KEYS:
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


@pytest.mark.parametrize("key_env, provider", [("OPENROUTER_API_KEY", "openrouter"), ("NOVITA_API_KEY", "novita"),
                                               ("NVIDIA_API_KEY", "nvidia"), ("ALLTOKEN_API_KEY", "alltoken")])
def test_the_owners_model_pick_wins_for_a_provider_key(clean_env, key_env, provider):
    from daedalus.llm_config import get_config

    clean_env.setenv(key_env, "k-test")
    clean_env.setenv("DAEDALUS_MODEL", "vendor/cheap-model-1")
    cfg = get_config()
    assert cfg["provider"] == provider and cfg["model"] == "vendor/cheap-model-1"


def test_without_a_pick_each_provider_keeps_its_default(clean_env):
    from daedalus.llm_config import get_config

    clean_env.setenv("OPENROUTER_API_KEY", "k-test")
    from daedalus.llm_config import OPENROUTER_DEFAULT_MODEL
    assert get_config()["model"] == OPENROUTER_DEFAULT_MODEL


def test_an_answer_with_no_text_is_not_a_success_and_says_when_tokens_ran_out():
    from daedalus.llm_client import mark_empty_reply

    cut = mark_empty_reply({"ok": True, "reply": "", "usage": {"completion_tokens": 20}}, max_tokens=20)
    assert cut["ok"] is False and cut["error"] == "empty_reply" and cut["truncated"] is True
    blank = mark_empty_reply({"ok": True, "reply": "  ", "usage": {"completion_tokens": 3}}, max_tokens=400)
    assert blank["ok"] is False and blank["truncated"] is False
    fine = mark_empty_reply({"ok": True, "reply": "PONG-7", "usage": {"completion_tokens": 3}}, max_tokens=20)
    assert fine == {"ok": True, "reply": "PONG-7", "usage": {"completion_tokens": 3}}


def test_a_timeout_error_is_never_an_empty_message():
    from daedalus.harness import gateway as gw

    err = gw.classify_error(TimeoutError())
    assert err.type == "timeout" and str(gw.GatewayCallError(err)) == "timeout"
