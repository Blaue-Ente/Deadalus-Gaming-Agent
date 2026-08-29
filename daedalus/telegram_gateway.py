"""Telegram Bot API adapter. Long-poll getUpdates — no public webhook required.

Works while Studio is bound to 127.0.0.1: the PC dials out to api.telegram.org.
A cloud VM is the same: the bot token is enough; you do not open 8787 to the world
just to chat.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from .messaging import (
    Inbound,
    Outbound,
    bot_token,
    channel_id,
    chunk_text,
    enabled,
    handle_inbound,
    restore_sessions,
)

log = logging.getLogger("daedalus.telegram")

API = "https://api.telegram.org"
_task: asyncio.Task[Any] | None = None
_stop: asyncio.Event | None = None
_identity: dict[str, Any] | None = None
_offset = 0


def poller_running() -> bool:
    return _task is not None and not _task.done()


def bot_identity() -> dict[str, Any] | None:
    return dict(_identity) if _identity else None


def api_url(method: str, token: str | None = None) -> str:
    secret = (token or bot_token()).strip()
    return f"{API}/bot{secret}/{method}"


def parse_update(update: dict[str, Any]) -> Inbound | None:
    msg = update.get("message") or update.get("edited_message") or update.get("channel_post")
    if not isinstance(msg, dict):
        return None
    chat = msg.get("chat") or {}
    sender = msg.get("from") or {}
    if sender.get("is_bot"):
        return None
    text = (msg.get("text") or msg.get("caption") or "").strip()
    if not text:
        return None
    chat_type = str(chat.get("type") or "private")
    user_id = str(sender.get("id") or chat.get("id") or "")
    if not user_id:
        return None
    first = str(sender.get("first_name") or "")
    last = str(sender.get("last_name") or "")
    display = (first + " " + last).strip()
    return Inbound(
        platform="telegram",
        user_id=user_id,
        chat_id=str(chat.get("id") or user_id),
        chat_type=chat_type,
        text=text,
        username=str(sender.get("username") or ""),
        display_name=display,
    )


async def get_me(token: str | None = None) -> dict[str, Any]:
    secret = (token or bot_token()).strip()
    if not secret:
        return {"ok": False, "error": "no bot token"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.get(api_url("getMe", secret))
            data = res.json()
    except Exception as extra:
        return {"ok": False, "error": str(extra)[:240]}
    if not data.get("ok"):
        return {"ok": False, "error": str(data.get("description") or "getMe failed")[:240]}
    user = data.get("result") or {}
    ident = {
        "ok": True,
        "id": user.get("id"),
        "username": user.get("username"),
        "name": user.get("first_name"),
    }
    global _identity
    _identity = ident
    return ident


async def send_message(chat_id: str | int, text: str, *, token: str | None = None) -> dict[str, Any]:
    secret = (token or bot_token()).strip()
    if not secret:
        return {"ok": False, "error": "no bot token"}
    chunks = chunk_text(text)
    last: dict[str, Any] = {"ok": False, "error": "empty"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        for part in chunks:
            try:
                res = await client.post(
                    api_url("sendMessage", secret),
                    json={
                        "chat_id": chat_id,
                        "text": part,
                        "disable_web_page_preview": True,
                    },
                )
                last = res.json()
            except Exception as extra:
                return {"ok": False, "error": str(extra)[:240]}
            if not last.get("ok"):
                return {
                    "ok": False,
                    "error": str(last.get("description") or f"HTTP {res.status_code}")[:240],
                }
    return last


async def send_typing(chat_id: str | int, *, token: str | None = None) -> None:
    secret = (token or bot_token()).strip()
    if not secret:
        return
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            await client.post(
                api_url("sendChatAction", secret),
                json={"chat_id": chat_id, "action": "typing"},
            )
    except Exception:
        pass


async def notify_channel(text: str) -> dict[str, Any]:
    target = channel_id()
    if not target:
        return {"ok": False, "error": "DAEDALUS_TELEGRAM_CHANNEL_ID is empty"}
    return await send_message(target, text)


async def _get_updates(offset: int, timeout: int = 25) -> list[dict[str, Any]]:
    secret = bot_token()
    if not secret:
        return []
    async with httpx.AsyncClient(timeout=timeout + 10.0) as client:
        res = await client.get(
            api_url("getUpdates", secret),
            params={
                "offset": offset,
                "timeout": timeout,
                "allowed_updates": json_list(["message", "edited_message"]),
            },
        )
        data = res.json()
    if not data.get("ok"):
        raise RuntimeError(str(data.get("description") or "getUpdates failed")[:240])
    rows = data.get("result") or []
    return [row for row in rows if isinstance(row, dict)]


def json_list(values: list[str]) -> str:
    # Telegram wants a JSON array in the query string.
    inner = ",".join(f'"{v}"' for v in values)
    return f"[{inner}]"


async def deliver(msg: Inbound, out: Outbound) -> None:
    if out.silent or not (out.text or out.chunks):
        return
    parts = out.chunks if len(out.chunks) > 1 else chunk_text(out.text)
    for part in parts:
        sent = await send_message(msg.chat_id, part)
        if not sent.get("ok"):
            log.warning("telegram send failed: %s", sent.get("error"))
            return


async def _poll_loop() -> None:
    global _offset
    assert _stop is not None
    restore_sessions()
    await get_me()
    ident = bot_identity() or {}
    log.info(
        "telegram: polling as @%s (Studio can stay on 127.0.0.1)",
        ident.get("username") or "bot",
    )
    while not _stop.is_set():
        if not enabled():
            try:
                await asyncio.wait_for(_stop.wait(), timeout=15)
            except asyncio.TimeoutError:
                continue
            break
        try:
            updates = await _get_updates(_offset)
        except asyncio.CancelledError:
            raise
        except Exception as extra:
            log.warning("telegram getUpdates: %s", extra)
            try:
                await asyncio.wait_for(_stop.wait(), timeout=4)
            except asyncio.TimeoutError:
                continue
            break
        for update in updates:
            _offset = int(update.get("update_id") or _offset) + 1
            inbound = parse_update(update)
            if inbound is None:
                continue
            try:
                await send_typing(inbound.chat_id)
                out = await handle_inbound(inbound)
                await deliver(inbound, out)
            except asyncio.CancelledError:
                raise
            except Exception as extra:
                log.warning("telegram handle failed: %s", extra)


def start_poller() -> bool:
    global _task, _stop
    if not enabled():
        return False
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False
    if poller_running():
        return True
    _stop = asyncio.Event()
    _task = loop.create_task(_poll_loop(), name="daedalus-telegram")
    return True


async def stop_poller() -> None:
    global _task, _stop
    if _stop is not None:
        _stop.set()
    task = _task
    _task = None
    if task is None:
        return
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass


async def run_forever() -> None:
    """Foreground CLI: poll until Ctrl+C. Does not start Studio HTTP."""
    restore_sessions()
    if not bot_token():
        raise SystemExit("DAEDALUS_TELEGRAM_BOT_TOKEN is empty")
    if not enabled():
        raise SystemExit("gateway disabled (DAEDALUS_GATEWAY=0 or offline kit)")
    stop = asyncio.Event()
    global _stop
    _stop = stop
    try:
        await _poll_loop()
    except KeyboardInterrupt:
        stop.set()
