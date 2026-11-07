"""A0-f: a setting pinned by an environment variable refuses writes (409) and stores nothing."""
from __future__ import annotations

import pytest
from a0_support import a0_tmp  # noqa: F401
from fastapi.testclient import TestClient

# (route, env var, locked body, free body, GET field holding the persisted value)
CASES = [
    ("/api/v1/daedalus/mentor", "DAEDALUS_MENTOR", "off", {"enabled": True}, {"enabled": False}),
    ("/api/v1/daedalus/run-budget", "DAEDALUS_RUN_BUDGET", "20", {"usd": 5}, {"usd": 5}),
    ("/api/v1/daedalus/writer-assist", "DAEDALUS_WRITER_ASSIST", "off", {"mode": "kernel-scaffold"}, {"mode": "kernel-scaffold"}),
]
ADMIN = {
    "/api/v1/daedalus/mentor": "/admin/daedalus/mentor",
    "/api/v1/daedalus/run-budget": "/admin/daedalus/run-budget",
    "/api/v1/daedalus/writer-assist": "/admin/daedalus/writer-assist",
}


def _persisted(path):
    from daedalus import kv
    key = {"/api/v1/daedalus/mentor": "adn:daedalus_mentor",
           "/api/v1/daedalus/run-budget": "adn:daedalus_run_budget",
           "/api/v1/daedalus/writer-assist": "adn:daedalus_writer_assist"}[path]
    return kv.get(key)


@pytest.mark.parametrize("path,env,value,locked_body,free_body", CASES)
@pytest.mark.parametrize("prefix", ["harness", "admin"])
def test_locked_write_is_409_and_not_persisted(a0_tmp, monkeypatch, path, env, value, locked_body, free_body, prefix):
    from app.main import app

    url = path if prefix == "harness" else ADMIN[path]
    client = TestClient(app)
    before = _persisted(path)
    monkeypatch.setenv(env, value)
    res = client.post(url, json=locked_body)
    assert res.status_code == 409, res.text
    body = res.json()
    assert body["ok"] is False and body["error"] == "env_locked" and body["env"] == env
    assert _persisted(path) == before
    st = client.get(url).json()
    assert st["env_locked"] is True

    monkeypatch.delenv(env)
    ok = client.post(url, json=free_body)
    assert ok.status_code == 200 and ok.json()["ok"] is True
    assert _persisted(path) is not None and _persisted(path) != before


def test_budget_reset_spend_allowed_while_locked(a0_tmp, monkeypatch):
    from app.main import app

    monkeypatch.setenv("DAEDALUS_RUN_BUDGET", "20")
    res = TestClient(app).post("/api/v1/daedalus/run-budget", json={"reset_spend": True})
    assert res.status_code == 200


def test_unrelated_fields_do_not_trip_the_lock(a0_tmp, monkeypatch):
    """The Studio sends only the field it changes; defaults must not count as a write."""
    from app.main import app

    monkeypatch.setenv("DAEDALUS_MENTOR", "off")
    client = TestClient(app)
    res = client.post("/api/v1/daedalus/mentor", json={"day_token_cap": 12345})
    assert res.status_code == 200


def test_mentor_choice_and_cap_env_locks(a0_tmp, monkeypatch):
    from app.main import app
    from daedalus import kv

    client = TestClient(app)
    monkeypatch.setenv("DAEDALUS_MENTOR_MODEL", "some/model")
    res = client.post("/api/v1/daedalus/mentor", json={"provider": "openrouter", "model": "x/y"})
    assert res.status_code == 409 and res.json()["field"] == "choice"
    monkeypatch.delenv("DAEDALUS_MENTOR_MODEL")
    monkeypatch.setenv("DAEDALUS_MENTOR_DAY_CAP", "500")
    res = client.post("/api/v1/daedalus/mentor", json={"day_token_cap": 900})
    assert res.status_code == 409 and res.json()["field"] == "day_token_cap"
    assert kv.get("adn:daedalus_mentor_day_cap") is None
    monkeypatch.setenv("DAEDALUS_MENTOR_DAY_CAP", "not-a-number")  # unparsable = not locked
    assert client.post("/api/v1/daedalus/mentor", json={"day_token_cap": 900}).status_code == 200


def test_mixed_body_writes_nothing_when_one_field_is_locked(a0_tmp, monkeypatch):
    from app.main import app
    from daedalus import kv

    monkeypatch.setenv("DAEDALUS_MENTOR_DAY_CAP", "500")
    res = TestClient(app).post("/api/v1/daedalus/mentor", json={"enabled": False, "day_token_cap": 900})
    assert res.status_code == 409
    assert kv.get("adn:daedalus_mentor") is None  # 'enabled' was not written either
