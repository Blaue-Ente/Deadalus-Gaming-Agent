"""Run-budget envelopes — $5 / $20 / $50 pick writer + mentor."""
from __future__ import annotations

from daedalus.run_budget import bind, applied_writer, normalize_usd, plan, status


def test_normalize_aliases():
    assert normalize_usd("off") == 0
    assert normalize_usd("$5") == 5
    assert normalize_usd("20") == 20
    assert normalize_usd(50) == 50
    assert normalize_usd("99") == 50


def test_off_does_not_steal_brain():
    p = plan(0)
    assert p["active"] is False
    assert p["apply_writer"] is False
    assert p["apply_mentor"] is False
    with bind(p):
        assert applied_writer() is None


def test_five_uses_free_writer_and_flash_mentor(monkeypatch):
    monkeypatch.delenv("DAEDALUS_MODEL", raising=False)
    monkeypatch.delenv("DAEDALUS_LOCAL_LLM", raising=False)
    p = plan(5)
    assert p["fits"] is True
    assert p["estimate_usd"] <= 5
    assert p["writer"]["model"].endswith(":free") or p["writer"]["usd"] == 0
    assert p["mentor"]["choice_id"] == "or-glm-53-flash"
    with bind(p):
        aw = applied_writer()
        assert aw is not None
        assert aw["model"] == p["writer"]["model"]


def test_twenty_and_fifty_scale_up():
    twenty = plan(20)
    fifty = plan(50)
    assert twenty["estimate_usd"] <= 20
    assert fifty["estimate_usd"] <= 50
    assert fifty["mentor"]["choice_id"] == "or-fable-51"
    assert fifty["estimate_usd"] > twenty["estimate_usd"]


def test_env_lock(tmp_path, monkeypatch):
    from daedalus.paths import data_root
    from daedalus import run_budget as rb

    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path))
    monkeypatch.setenv("DAEDALUS_RUN_BUDGET", "20")
    data_root.cache_clear()
    assert rb.usd() == 20
    st = rb.status()
    assert st["env_locked"] is True
    assert st["plan"]["usd"] == 20


def test_http_toggle(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from daedalus.paths import data_root

    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path))
    monkeypatch.delenv("DAEDALUS_RUN_BUDGET", raising=False)
    data_root.cache_clear()
    client = TestClient(app)
    got = client.get("/api/v1/daedalus/run-budget")
    assert got.status_code == 200
    assert got.json()["usd"] == 0
    post = client.post("/api/v1/daedalus/run-budget", json={"usd": 20})
    assert post.status_code == 200
    assert post.json()["usd"] == 20
    assert post.json()["plan"]["active"] is True
    spend = post.json()["spend"]
    assert spend["spent_usd"] == 0
    assert spend["exhausted"] is False
    assert spend["cap_usd"] == 20


def test_openrouter_ledger_stops_at_cap(tmp_path, monkeypatch):
    from daedalus.paths import data_root
    from daedalus import run_budget as rb

    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path))
    monkeypatch.delenv("DAEDALUS_RUN_BUDGET", raising=False)
    data_root.cache_clear()
    rb.set_usd(5)
    rb.note_usage(
        {"cost_usd": 4.2, "cost_known": True, "prompt_tokens": 10, "completion_tokens": 2},
        model="openai/gpt-oss-120b",
        role="writer",
    )
    led = rb.ledger()
    assert led["spent_usd"] == 4.2
    assert led["exhausted"] is False
    assert rb.would_block("openrouter") is False
    rb.note_usage(
        {"cost_usd": 1.0, "cost_known": True, "prompt_tokens": 1, "completion_tokens": 1},
        role="mentor",
    )
    assert rb.exhausted() is True
    assert rb.would_block("openrouter") is True
    assert rb.would_block("ollama") is False
    assert "envelope" in rb.stop_text()


def test_off_does_not_accumulate_spend(tmp_path, monkeypatch):
    from daedalus.paths import data_root
    from daedalus import run_budget as rb

    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path))
    monkeypatch.delenv("DAEDALUS_RUN_BUDGET", raising=False)
    data_root.cache_clear()
    rb.set_usd(0)
    rb.note_usage({"cost_usd": 9.9, "cost_known": True, "prompt_tokens": 1, "completion_tokens": 1})
    assert rb.ledger()["spent_usd"] == 0
    assert rb.would_block("openrouter") is False


def test_call_with_tools_stops_before_network(monkeypatch):
    import asyncio
    from daedalus import llm_client

    monkeypatch.setattr(
        "daedalus.llm_config.get_config",
        lambda: {
            "api_key": "sk-test",
            "provider": "openrouter",
            "model": "openai/gpt-oss-120b",
            "sdk": "openai",
            "base_url": "https://openrouter.ai/api/v1",
        },
    )
    monkeypatch.setattr("daedalus.run_budget.would_block", lambda provider=None: True)
    monkeypatch.setattr("daedalus.run_budget.stop_text", lambda: "envelope blocked")
    out = asyncio.run(llm_client.call_with_tools("sys", [{"role": "user", "content": "hi"}], []))
    assert out["budget_stopped"] is True
    assert "envelope blocked" in out["content"][0]["text"]


def test_reset_spend_keeps_cap(tmp_path, monkeypatch):
    from daedalus.paths import data_root
    from daedalus import run_budget as rb

    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path))
    monkeypatch.delenv("DAEDALUS_RUN_BUDGET", raising=False)
    data_root.cache_clear()
    rb.set_usd(20)
    rb.note_usage({"cost_usd": 3.0, "cost_known": True})
    st = rb.apply_update({"reset_spend": True})
    assert st["usd"] == 20
    assert st["spend"]["spent_usd"] == 0
    assert st["spend"]["exhausted"] is False
