"""A4: the KV backend is chosen explicitly and reported; Replit is never used by accident."""
from __future__ import annotations

import sys
import types

import pytest
from a0_support import a0_tmp  # noqa: F401
from fastapi.testclient import TestClient


class FakeReplitDB(dict):
    def prefix(self, p):
        return [k for k in self if str(k).startswith(p)]


@pytest.fixture
def fake_replit(monkeypatch):
    db = FakeReplitDB()
    mod = types.ModuleType("replit")
    mod.db = db
    monkeypatch.setitem(sys.modules, "replit", mod)
    return db


def test_default_is_json_even_when_replit_is_importable(a0_tmp, fake_replit, monkeypatch):
    from daedalus import kv

    monkeypatch.delenv("DAEDALUS_KV", raising=False)
    kv.set("adn:a4", {"x": 1})
    assert fake_replit == {}                      # nothing went to Replit
    assert kv.get("adn:a4") == {"x": 1}
    assert kv.backend() == {"requested": "json", "effective": "json", "explicit": False}


def test_a_non_standalone_host_keeps_replit_first_when_unset(a0_tmp, fake_replit, monkeypatch):
    """A Replit-hosted install must keep seeing the data it already has (no silent switch to empty JSON)."""
    from daedalus import kv

    monkeypatch.delenv("DAEDALUS_KV", raising=False)
    monkeypatch.setenv("DAEDALUS_STANDALONE", "0")
    fake_replit["adn:existing"] = '{"v": 1}'
    assert kv.get("adn:existing") == {"v": 1}
    assert kv.backend()["requested"] == "auto" and kv.backend()["explicit"] is False
    monkeypatch.setenv("DAEDALUS_KV", "json")  # an explicit choice wins
    assert kv.get("adn:existing") is None


def test_replit_is_used_only_when_requested(a0_tmp, fake_replit, monkeypatch):
    from daedalus import kv

    monkeypatch.setenv("DAEDALUS_KV", "replit")
    kv.set("adn:a4", {"x": 2})
    assert "adn:a4" in fake_replit
    assert kv.get("adn:a4") == {"x": 2}
    assert kv.backend()["effective"] == "replit+json"


def test_auto_restores_the_old_behaviour(a0_tmp, fake_replit, monkeypatch):
    from daedalus import kv

    monkeypatch.setenv("DAEDALUS_KV", "auto")
    kv.set("adn:a4", 3)
    assert "adn:a4" in fake_replit and kv.backend()["requested"] == "auto"


def test_requested_replit_without_module_is_honest(a0_tmp, monkeypatch):
    from daedalus import kv

    monkeypatch.setitem(sys.modules, "replit", None)  # import raises
    monkeypatch.setenv("DAEDALUS_KV", "replit")
    b = kv.backend()
    assert b["effective"] == "json" and "not available" in b["note"]
    kv.set("adn:a4", 4)
    assert kv.get("adn:a4") == 4


def test_unknown_value_falls_back_to_json(a0_tmp, fake_replit, monkeypatch):
    from daedalus import kv

    monkeypatch.setenv("DAEDALUS_KV", "banana")
    assert kv.backend()["requested"] == "json"


def test_modules_that_called_replit_directly_follow_the_same_switch(a0_tmp, fake_replit, monkeypatch):
    from daedalus import connectors_config, llm_config, workers

    monkeypatch.delenv("DAEDALUS_KV", raising=False)
    llm_config._write_db({"provider": "openrouter", "model": "m"})
    workers._write_worker_db({"deep": {"model": "x"}})
    connectors_config._write_db({"k": "v"})
    assert fake_replit == {}                      # all three stayed on their JSON files
    assert llm_config._read_db().get("model") == "m"
    monkeypatch.setenv("DAEDALUS_KV", "replit")
    llm_config._write_db({"provider": "openrouter", "model": "m2"})
    assert fake_replit                            # opted in -> they use it


def test_health_reports_the_backend(a0_tmp):
    from app.main import app

    res = TestClient(app).get("/health")
    assert res.status_code == 200
    assert res.json()["kv"]["effective"] == "json"
