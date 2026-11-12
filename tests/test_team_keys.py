"""Gap 3c (owner, 2026-11-12, plan approved): each member's own keys; the owner's only when shared, up to a cap."""
from __future__ import annotations

import asyncio
import json
import sys
import types

import pytest
from a0_support import a0_tmp  # noqa: F401
from fastapi.testclient import TestClient

from daedalus import credentials, team

DEE = team.Member("dee123", "Dee", "designer")
BRAIN = {"provider": "openrouter", "model": "owner/pick", "api_key": "OWNER-KEY", "base_url": "http://x",
         "sdk": "openai", "source": "db"}


class _As:
    def __init__(self, member):
        self.member = member

    def __enter__(self):
        self.tok = team.bind(self.member)

    def __exit__(self, *exc):
        team.unbind(self.tok)


@pytest.fixture
def keys(a0_tmp, monkeypatch):  # noqa: F811
    monkeypatch.setenv("OPENROUTER_API_KEY", "OWNER-KEY")
    return a0_tmp


def test_a_member_never_gets_the_owners_key_unless_it_is_shared(keys):
    assert credentials.env("OPENROUTER_API_KEY") == "OWNER-KEY"                # the owner and server tasks
    with _As(DEE):
        assert credentials.env("OPENROUTER_API_KEY") == ""
        credentials.set_sharing(shared=["OPENROUTER_API_KEY"])
        assert credentials.env("OPENROUTER_API_KEY") == "OWNER-KEY"
        credentials.set_key(DEE.id, "OPENROUTER_API_KEY", "DEE-KEY")
        assert credentials.env("OPENROUTER_API_KEY") == "DEE-KEY"                # their own key comes first
        credentials.set_key(DEE.id, "OPENROUTER_API_KEY", "")
        credentials.set_sharing(caps={DEE.id: 0.5})
        credentials.note_shared_spend(DEE.id, 0.6)
        assert credentials.env("OPENROUTER_API_KEY") == ""                       # over the cap
    with pytest.raises(ValueError):
        credentials.set_key(DEE.id, "PATH", "x")


def test_a_vault_is_private_and_values_never_leave_it(keys):
    credentials.set_key(DEE.id, "OPENROUTER_API_KEY", "DEE-KEY")
    vault = credentials._vault(DEE.id)
    assert vault.stat().st_mode & 0o077 == 0
    assert "DEE-KEY" not in json.dumps(credentials.status(DEE.id))
    assert credentials.status(DEE.id)["own"] == ["OPENROUTER_API_KEY"]


def test_an_unreadable_sharing_file_shares_nothing_and_is_not_overwritten(keys):
    credentials.set_sharing(shared=["OPENROUTER_API_KEY"])
    path = credentials._sharing_path()
    path.write_text("{ broken")
    with _As(DEE):
        assert credentials.env("OPENROUTER_API_KEY") == ""
    with pytest.raises(ValueError):
        credentials.set_sharing(shared=[])
    assert path.read_text() == "{ broken"


def test_the_direct_key_readers_follow_the_member(keys):
    from daedalus import cheap_llm

    assert cheap_llm.resolve_cheap_openai() is not None
    with _As(DEE):
        cfg = cheap_llm.resolve_cheap_openai()
        assert cfg is None or "OWNER-KEY" not in json.dumps(cfg)


# ── the gateway ───────────────────────────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def gw(keys, monkeypatch):
    from daedalus.harness import gateway

    monkeypatch.setattr("daedalus.llm_config.get_config", lambda: dict(BRAIN))
    monkeypatch.delenv("DAEDALUS_RUN_BUDGET", raising=False)
    seen: dict = {}

    class Client:
        def __init__(self, **kw):
            seen["key"] = kw.get("api_key")
            self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self.create))

        async def create(self, **kw):
            msg = types.SimpleNamespace(content="ok")
            usage = types.SimpleNamespace(prompt_tokens=1000, completion_tokens=1000)
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)], usage=usage)

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(AsyncOpenAI=Client))
    return gateway, seen


def _ask(gateway):
    return asyncio.run(gateway.call("persona", [{"role": "user", "content": "hi"}]))


def test_a_members_call_without_a_key_says_so_and_calls_no_one(gw):
    gateway, seen = gw
    with _As(DEE):
        out = _ask(gateway)
    assert not out.ok and out.error.type == "no_key" and "Settings → Team" in out.error.message
    assert "key" not in seen


def test_a_members_call_uses_their_own_key(gw):
    gateway, seen = gw
    credentials.set_key(DEE.id, "OPENROUTER_API_KEY", "DEE-KEY")
    with _As(DEE):
        assert _ask(gateway).ok
    assert seen["key"] == "DEE-KEY"
    assert _ask(gateway).ok and seen["key"] == "OWNER-KEY"                    # the owner is unchanged


def test_a_shared_key_is_counted_and_stops_at_the_cap(gw, monkeypatch):
    gateway, seen = gw
    credentials.set_sharing(shared=["OPENROUTER_API_KEY"], caps={DEE.id: 0.01})
    monkeypatch.setattr("daedalus.llm_cost.estimate_cost", lambda *a, **k: 0.02, raising=False)
    with _As(DEE):
        first = _ask(gateway)
    assert first.ok and seen["key"] == "OWNER-KEY"
    if first.cost_usd:                                    # counted when the price is known
        assert credentials.spent_on_shared(DEE.id) == pytest.approx(first.cost_usd)
    credentials.note_shared_spend(DEE.id, 1.0)
    with _As(DEE):
        stopped = _ask(gateway)
    assert not stopped.ok and stopped.error.type == "no_key" and "$0.01" in stopped.error.message


# ── the API ───────────────────────────────────────────────────────────────────────────────────────────────────────
def test_members_manage_their_own_keys_and_only_the_owner_shares(keys, raw_http):
    from app.main import app
    from daedalus.owner_auth import get_or_create_token

    client = TestClient(app)
    owner = {"Authorization": f"Bearer {get_or_create_token()}"}
    made = {}
    for name, role in (("Dee", "designer"), ("Tee", "tester")):
        res = client.post("/api/v1/team/members", json={"name": name, "role": role}, headers=owner).json()
        made[role] = {"Authorization": f"Bearer {res['token']}"}
    saved = client.post("/api/v1/team/keys", json={"name": "OPENROUTER_API_KEY", "value": "DEE-KEY"},
                        headers=made["designer"])
    assert saved.status_code == 200 and saved.json()["own"] == ["OPENROUTER_API_KEY"]
    assert "DEE-KEY" not in saved.text
    assert client.get("/api/v1/team/keys", headers=made["tester"]).status_code == 200
    assert client.post("/api/v1/team/keys", json={"name": "OPENROUTER_API_KEY", "value": "x"},
                       headers=made["tester"]).status_code == 403
    assert client.post("/api/v1/team/sharing", json={"shared": ["OPENROUTER_API_KEY"]},
                       headers=made["designer"]).status_code == 403
    shared = client.post("/api/v1/team/sharing", json={"shared": ["OPENROUTER_API_KEY"]}, headers=owner)
    assert shared.status_code == 200 and shared.json()["shared"] == ["OPENROUTER_API_KEY"]
    assert "OWNER-KEY" not in client.get("/api/v1/team/keys", headers=owner).text
