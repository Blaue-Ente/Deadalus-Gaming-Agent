"""Telegram gateway: pairing, allowlist, slash commands, update parse. No live Bot API."""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def gw_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path / "projects"))
    monkeypatch.delenv("DAEDALUS_USE_ADN_DB", raising=False)
    monkeypatch.delenv("DAEDALUS_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("DAEDALUS_TELEGRAM_ALLOWLIST", raising=False)
    monkeypatch.delenv("DAEDALUS_GATEWAY", raising=False)
    monkeypatch.delenv("DAEDALUS_OFFLINE", raising=False)
    monkeypatch.delenv("DAEDALUS_TELEGRAM_GROUPS", raising=False)
    monkeypatch.delenv("DAEDALUS_TELEGRAM_PAIRING", raising=False)
    monkeypatch.setenv("DAEDALUS_GATEWAY", "0")
    from daedalus.paths import data_root, playground_projects_dir, product_root, repo_root
    data_root.cache_clear()
    playground_projects_dir.cache_clear()
    product_root.cache_clear()
    repo_root.cache_clear()
    from daedalus.store import reset_sqlite_pool
    reset_sqlite_pool()
    yield tmp_path
    reset_sqlite_pool()


def test_chunk_and_parse_update():
    from daedalus.messaging import chunk_text
    from daedalus.telegram_gateway import parse_update

    parts = chunk_text("a" * 8000, limit=3500)
    assert len(parts) >= 3
    assert all(len(p) <= 3500 for p in parts)

    bot = parse_update({"update_id": 1, "message": {"from": {"id": 1, "is_bot": True}, "chat": {"id": 1, "type": "private"}, "text": "hi"}})
    assert bot is None
    empty = parse_update({"update_id": 2, "message": {"from": {"id": 9}, "chat": {"id": 9, "type": "private"}}})
    assert empty is None
    hit = parse_update({
        "update_id": 3,
        "message": {
            "from": {"id": 42, "username": "owner", "first_name": "N"},
            "chat": {"id": 42, "type": "private"},
            "text": "/status",
        },
    })
    assert hit is not None
    assert hit.user_id == "42"
    assert hit.text == "/status"
    cap = parse_update({
        "update_id": 4,
        "message": {
            "from": {"id": 7},
            "chat": {"id": 7, "type": "private"},
            "caption": "направи змия",
        },
    })
    assert cap is not None and cap.text.startswith("направи")


def test_unknown_user_gets_pairing_code(gw_tmp, monkeypatch):
    from daedalus.messaging import Inbound, approve, handle_inbound, is_allowed, snapshot

    monkeypatch.setenv("DAEDALUS_TELEGRAM_BOT_TOKEN", "111:fake")

    async def _go():
        first = await handle_inbound(Inbound(
            platform="telegram", user_id="99", chat_id="99",
            chat_type="private", text="hello", username="stranger",
        ))
        assert "pairing" in first.text.lower() or "Код" in first.text
        snap = snapshot()
        assert len(snap["pending"]) == 1
        code = snap["pending"][0]["code"]
        again = await handle_inbound(Inbound(
            platform="telegram", user_id="99", chat_id="99",
            chat_type="private", text="hello again", username="stranger",
        ))
        assert code in again.text
        assert not is_allowed("telegram", "99")
        ok = approve(code)
        assert ok["ok"] is True
        assert is_allowed("telegram", "99")

    asyncio.run(_go())


def test_allowlist_skips_pairing(gw_tmp, monkeypatch):
    from daedalus.messaging import Inbound, handle_inbound

    monkeypatch.setenv("DAEDALUS_TELEGRAM_BOT_TOKEN", "111:fake")
    monkeypatch.setenv("DAEDALUS_TELEGRAM_ALLOWLIST", "42")

    async def fake_chat(*, message, session_id=None, effort="auto"):
        return {"reply": "pong:" + message, "session_id": session_id}

    async def _go():
        help_out = await handle_inbound(Inbound(
            platform="telegram", user_id="42", chat_id="42",
            chat_type="private", text="/help",
        ))
        assert "/status" in help_out.text
        chat = await handle_inbound(
            Inbound(
                platform="telegram", user_id="42", chat_id="42",
                chat_type="private", text="направи змия",
            ),
            chat_fn=fake_chat,
        )
        assert chat.text == "pong:направи змия"
        who = await handle_inbound(Inbound(
            platform="telegram", user_id="42", chat_id="42",
            chat_type="private", text="/whoami",
        ))
        assert "42" in who.text and "allowed" in who.text

    asyncio.run(_go())


def test_group_messages_ignored_by_default(gw_tmp, monkeypatch):
    from daedalus.messaging import Inbound, handle_inbound

    monkeypatch.setenv("DAEDALUS_TELEGRAM_ALLOWLIST", "42")

    async def _go():
        out = await handle_inbound(Inbound(
            platform="telegram", user_id="42", chat_id="-1001",
            chat_type="group", text="hi",
        ))
        assert out.silent is True

    asyncio.run(_go())


def test_gateway_http_pair(gw_tmp, monkeypatch):
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_TELEGRAM_BOT_TOKEN", "111:fake")
    from daedalus.messaging import request_access
    pending = request_access("telegram", "77", username="bob", chat_id="77")
    assert pending["status"] == "pending"
    from app.main import app
    from daedalus.owner_auth import get_or_create_token
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {get_or_create_token()}"}
    st = client.get("/api/v1/daedalus/gateway", headers=headers)
    assert st.status_code == 200
    assert st.json()["configured"] is True
    assert "messaging-gateway" in client.get("/capabilities").json()["capabilities"]
    bad = client.post("/api/v1/daedalus/gateway/pair", json={"code": "NOPECODE"}, headers=headers)
    assert bad.status_code == 400
    ok = client.post(
        "/api/v1/daedalus/gateway/pair",
        json={"code": pending["code"]},
        headers=headers,
    )
    assert ok.status_code == 200
    home = client.get("/")
    assert "gwpair" in home.text
    assert "TELEGRAM" in home.text or "Telegram" in home.text


def test_cli_gateway_status(gw_tmp, monkeypatch, capsys):
    monkeypatch.setenv("DAEDALUS_TELEGRAM_BOT_TOKEN", "111:fake")
    from app.cli import main
    main(["gateway", "status"])
    out = capsys.readouterr().out
    assert "configured=True" in out
    assert "BotFather" in out or "pairing" in out
