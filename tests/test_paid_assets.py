"""Gap 4b (owner, 2026-11-12, plan approved): paid art and sound only in an epic turn or when asked, on the user's
own key, and always shown."""
from __future__ import annotations

import asyncio
import json
import types

import pytest
from a0_support import a0_tmp  # noqa: F401

from daedalus import credentials, paid_assets, team


@pytest.fixture
def game(a0_tmp, monkeypatch):  # noqa: F811
    from daedalus import playground as pg

    monkeypatch.setenv("ELEVENLABS_API_KEY", "OWNER-11")
    return asyncio.run(pg.create_project("Roar", kind="static"))["slug"]


class _Turn:
    def __init__(self, words="", tier="", member=None):
        self.words, self.tier, self.member = words, tier, member

    def __enter__(self):
        from daedalus import task_effort
        self.tok = paid_assets.set_turn_words(self.words)
        self.bind = task_effort.bind({"tier": self.tier}) if self.tier else None
        if self.bind:
            self.bind.__enter__()
        self.mtok = team.bind(self.member) if self.member else None

    def __exit__(self, *exc):
        if self.mtok:
            team.unbind(self.mtok)
        if self.bind:
            self.bind.__exit__(*exc)
        paid_assets._TURN_WORDS.reset(self.tok)


@pytest.mark.parametrize("words,tier,ok", [
    ("make a frog game", "", False),
    ("make a frog game", "craft", False),
    ("make a frog game", "epic", True),
    ("make a frog game with generated sound effects", "", True),
    ("use ElevenLabs for the boss roar", "", True),
    ("AI art for the sprites please", "", True),
    ("направи игра с генериран звук", "", True),
    ("озвучи героя", "", True),
    ("a game about a sound wave", "", False),
])
def test_paid_only_in_epic_or_when_asked(a0_tmp, words, tier, ok):  # noqa: F811
    with _Turn(words, tier):
        assert paid_assets.allowed()[0] is ok


class _Client:
    calls: list = []
    status = 200
    body = b"ID3-fake-mp3-bytes"

    def __init__(self, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None):
        _Client.calls.append({"url": url, "key": headers.get("xi-api-key"), "json": json})
        return types.SimpleNamespace(status_code=_Client.status, content=_Client.body if _Client.status == 200 else b"",
                                     text="quota exceeded")


@pytest.fixture
def http(monkeypatch):
    import httpx

    _Client.calls, _Client.status = [], 200
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    return _Client


def _events(monkeypatch):
    seen = []
    monkeypatch.setattr("daedalus.harness.runs.current_run_id", lambda: "run-1")
    monkeypatch.setattr("daedalus.harness.runs.safe_event", lambda rid, kind, payload: seen.append((kind, payload)))
    return seen


def test_a_requested_sound_is_saved_on_the_owners_key_and_shown(game, http, monkeypatch):
    seen = _events(monkeypatch)
    from daedalus import playground as pg

    with _Turn("add generated sound effects for the boss"):
        r = asyncio.run(paid_assets.generate_sound(game, "Boss Roar!", "deep monster roar", 30))
    assert r["ok"] and r["path"] == "assets/sfx/boss-roar.mp3" and r["seconds"] == paid_assets.MAX_SECONDS
    assert (pg.project_dir(game) / r["path"]).read_bytes() == _Client.body
    assert http.calls[0]["key"] == "OWNER-11" and http.calls[0]["json"]["duration_seconds"] == 10.0
    kind, row = seen[-1]
    assert kind == "paid_asset" and row["ok"] and row["whose"] == "owner" and "ElevenLabs credits" in row["cost"]
    assert r["play"] == "sfx.file('assets/sfx/boss-roar.mp3')"


def test_not_requested_means_not_called(game, http, monkeypatch):
    seen = _events(monkeypatch)
    with _Turn("make a frog game"):
        r = asyncio.run(paid_assets.generate_sound(game, "x", "y"))
    assert r["error"] == "not_requested" and "sfx.js" in r["message"] and http.calls == []
    assert seen[-1][1]["ok"] is False


def test_a_member_never_spends_the_owners_elevenlabs_key(game, http, monkeypatch):
    dee = team.Member("dee123", "Dee", "designer")
    credentials.set_sharing(shared=["ELEVENLABS_API_KEY"])          # even shared: a direct call takes own keys only
    with _Turn("generated sound effects please", member=dee):
        r = asyncio.run(paid_assets.generate_sound(game, "x", "y"))
    assert r["error"] == "no_key" and http.calls == []
    credentials.set_key(dee.id, "ELEVENLABS_API_KEY", "DEE-11")
    with _Turn("generated sound effects please", member=dee):
        assert asyncio.run(paid_assets.generate_sound(game, "x", "y"))["ok"]
    assert http.calls[-1]["key"] == "DEE-11"


def test_a_refused_service_is_said_not_saved(game, http, monkeypatch):
    seen = _events(monkeypatch)
    http.status = 401
    with _Turn("use elevenlabs"):
        r = asyncio.run(paid_assets.generate_sound(game, "x", "y"))
    assert r["error"] == "http_401" and "quota" in r["message"] and seen[-1][1]["ok"] is False


def test_the_image_tool_is_paid_too(game, monkeypatch):
    from daedalus import native_tools

    async def boom(**kw):
        raise AssertionError("no image call when not requested")

    monkeypatch.setattr("daedalus.image_client.generate_image", boom)
    with _Turn("make a frog game"):
        out = asyncio.run(native_tools.execute_native_tool("generate_image", {"prompt": "frog", "slug": game},
                                                           memory=None, session_id="s", read_file_fn=None))
    assert "SKIPPED (paid, not requested)" in out


def test_the_studio_row_shows_who_paid_and_hides_secrets():
    from daedalus import activity

    row = activity.compact({"seq": 3, "ts": 1.0, "type": "paid_asset", "payload": {
        "service": "elevenlabs", "ok": True, "what": "roar token=abc123", "cost": "2.0s of sound",
        "whose": "Dee (designer)", "path": "assets/sfx/roar.mp3"}})
    assert row["whose"] == "Dee (designer)" and "abc123" not in row["what"] and json.dumps(row)


def test_the_member_tool_list_and_the_specs_agree():
    from daedalus.native_tools import NATIVE_TOOL_SPECS

    names = {s["name"] for s in NATIVE_TOOL_SPECS}
    assert "paid_sound" in names and "paid_sound" in team.MEMBER_TOOLS
    assert "ELEVENLABS_API_KEY" in credentials.member_key_names()
