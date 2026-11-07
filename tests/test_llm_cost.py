"""OpenRouter usage.cost parse — billed USD, never a catalog guess."""
from types import SimpleNamespace

from daedalus.llm_cost import parse_usage


def test_parse_dict_cost():
    got = parse_usage({
        "prompt_tokens": 10,
        "completion_tokens": 20,
        "cost": 0.00123,
    })
    assert got["prompt_tokens"] == 10
    assert got["completion_tokens"] == 20
    assert got["cost_usd"] == 0.00123
    assert got["cost_known"] is True
    assert got["source"] == "openrouter.usage.cost"


def test_parse_zero_cost_is_known():
    got = parse_usage({"prompt_tokens": 3, "completion_tokens": 1, "cost": 0})
    assert got["cost_usd"] == 0.0
    assert got["cost_known"] is True


def test_parse_missing_cost_is_not_guessed():
    got = parse_usage({"prompt_tokens": 8, "completion_tokens": 2})
    assert got["cost_usd"] is None
    assert got["cost_known"] is False
    assert got["source"] == "missing"


def test_parse_model_extra_and_cost_details():
    usage = SimpleNamespace(
        prompt_tokens=4,
        completion_tokens=5,
        model_extra={"cost": 0.04},
    )
    got = parse_usage(usage)
    assert got["cost_usd"] == 0.04

    details = parse_usage({
        "cost_details": {"upstream_inference_cost": 0.02},
    })
    assert details["cost_usd"] == 0.02
    assert details["source"] == "usage.cost_details"
