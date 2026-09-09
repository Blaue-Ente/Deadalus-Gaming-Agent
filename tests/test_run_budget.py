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
