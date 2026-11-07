"""A4: ConfigView shape, env locks (409, nothing saved), no secrets."""
from __future__ import annotations

import pytest
from a0_support import a0_tmp  # noqa: F401
from fastapi.testclient import TestClient

REQUIRED = {"key", "title_key", "group", "kind", "env", "env_locked", "env_value", "writable",
            "effective", "persisted", "default", "source"}


def _client():
    from app.main import app
    return TestClient(app)


def _row(body, key):
    return next(r for r in body["settings"] if r["key"] == key)


def test_every_row_has_the_full_shape(a0_tmp):
    body = _client().get("/api/v1/harness/config").json()
    assert body["ok"] is True and body["settings"]
    for row in body["settings"]:
        assert REQUIRED <= set(row), row["key"]
        assert row["source"] in {"env", "saved", "default", "unavailable"}
    keys = {r["key"] for r in body["settings"]}
    assert {"mentor.enabled", "run_budget.usd", "writer_assist.mode", "evolution.enabled",
            "auto_apply.enabled", "kv.backend", "brain.model"} <= keys


def test_default_persisted_and_effective_are_distinct(a0_tmp):
    c = _client()
    row = _row(c.get("/api/v1/harness/config").json(), "run_budget.usd")
    assert row["source"] == "default" and row["persisted"] is None and row["effective"] == row["default"]
    res = c.post("/api/v1/harness/config", json={"key": "run_budget.usd", "value": 5})
    assert res.status_code == 200, res.text
    row = _row(c.get("/api/v1/harness/config").json(), "run_budget.usd")
    assert row["source"] == "saved" and row["effective"] == 5 and row["persisted"] == 5


@pytest.mark.parametrize("key,env,pin,value", [
    ("mentor.enabled", "DAEDALUS_MENTOR", "off", True),
    ("run_budget.usd", "DAEDALUS_RUN_BUDGET", "20", 5),
    ("writer_assist.mode", "DAEDALUS_WRITER_ASSIST", "off", "kernel-scaffold"),
    ("evolution.enabled", "DAEDALUS_EVOLUTION", "off", True),
    ("mentor.day_token_cap", "DAEDALUS_MENTOR_DAY_CAP", "1000", 5),
])
def test_locked_setting_reports_lock_and_refuses_write(a0_tmp, monkeypatch, key, env, pin, value):
    from daedalus import settings_file

    c = _client()
    before = (c.get("/api/v1/harness/config").json(), settings_file.load())
    monkeypatch.setenv(env, pin)
    row = _row(c.get("/api/v1/harness/config").json(), key)
    assert row["env_locked"] is True and row["env_value"] == pin
    assert row["writable"] is False and row["source"] == "env"
    res = c.post("/api/v1/harness/config", json={"key": key, "value": value})
    assert res.status_code == 409, res.text
    assert res.json() == {"ok": False, "error": "env_locked", "field": key, "env": env}
    monkeypatch.delenv(env)
    after = (c.get("/api/v1/harness/config").json(), settings_file.load())
    assert _row(after[0], key)["persisted"] == _row(before[0], key)["persisted"]  # nothing was stored
    assert after[1] == before[1]


def test_mentor_cap_is_default_until_saved(a0_tmp):
    c = _client()
    row = _row(c.get("/api/v1/harness/config").json(), "mentor.day_token_cap")
    assert row["persisted"] is None and row["source"] == "default"
    assert c.post("/api/v1/harness/config", json={"key": "mentor.day_token_cap", "value": 1234}).status_code == 200
    row = _row(c.get("/api/v1/harness/config").json(), "mentor.day_token_cap")
    assert row["persisted"] == 1234 and row["effective"] == 1234 and row["source"] == "saved"


def test_play_max_sessions_is_bounded_one_to_three(a0_tmp):
    c = _client()
    row = _row(c.get("/api/v1/harness/config").json(), "play.max_sessions")
    assert row["effective"] == 1 and row["default"] == 1 and row["persisted"] is None
    assert (row["min"], row["max"]) == (1, 3)                      # the UI can constrain its input
    for bad in (0, 4, -1, "x"):
        assert c.post("/api/v1/harness/config", json={"key": "play.max_sessions", "value": bad}).status_code == 400
    assert c.post("/api/v1/harness/config", json={"key": "play.max_sessions", "value": 3}).status_code == 200
    row = _row(c.get("/api/v1/harness/config").json(), "play.max_sessions")
    assert row["effective"] == 3 and row["persisted"] == 3 and row["source"] == "saved"


def test_unparsable_pin_does_not_lock(a0_tmp, monkeypatch):
    monkeypatch.setenv("DAEDALUS_RUN_BUDGET", "lots")
    row = _row(_client().get("/api/v1/harness/config").json(), "run_budget.usd")
    assert row["env_locked"] is False


def test_unknown_and_read_only_keys(a0_tmp):
    c = _client()
    assert c.post("/api/v1/harness/config", json={"key": "nope", "value": 1}).status_code == 404
    res = c.post("/api/v1/harness/config", json={"key": "kv.backend", "value": "replit"})
    assert res.status_code == 405 and res.json()["error"] == "read_only"


@pytest.mark.parametrize("key,value", [
    ("run_budget.usd", "abc"), ("run_budget.usd", -5), ("run_budget.usd", True),
    ("writer_assist.mode", "banana"), ("mentor.enabled", "maybe"), ("mentor.day_token_cap", "x"),
])
def test_bad_value_is_400_and_nothing_is_saved(a0_tmp, key, value):
    c = _client()
    before = _row(c.get("/api/v1/harness/config").json(), key)["persisted"]
    res = c.post("/api/v1/harness/config", json={"key": key, "value": value})
    assert res.status_code == 400, res.text
    assert res.json()["error"] == "invalid_value"
    assert _row(c.get("/api/v1/harness/config").json(), key)["persisted"] == before


def test_flags_in_settings_json_round_trip(a0_tmp):
    c = _client()
    c.post("/api/v1/harness/config", json={"key": "auto_apply.enabled", "value": True})
    body = c.get("/api/v1/harness/config").json()
    assert _row(body, "auto_apply.enabled")["effective"] is True
    assert _row(body, "auto_apply.enabled")["persisted"] is True


def test_no_secret_is_returned(a0_tmp, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-SECRET-should-not-appear")
    monkeypatch.setenv("DAEDALUS_TOKEN", "tok-SECRET-should-not-appear")
    text = _client().get("/api/v1/harness/config").text
    assert "SECRET-should-not-appear" not in text


def test_route_requires_owner_auth(a0_tmp, monkeypatch):
    from app.main import app

    monkeypatch.setenv("DAEDALUS_TOKEN", "owner-token-x")
    anon = TestClient(app)
    anon.headers.pop("Authorization", None)
    res = anon.get("/api/v1/harness/config", headers={"Authorization": ""})
    assert res.status_code in (401, 403)
