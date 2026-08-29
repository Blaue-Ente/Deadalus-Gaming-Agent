"""Owner messaging gateway — Telegram into the same agent as Studio.

Unknown users never reach tools. Access is an allowlist and/or a pairing
code the owner approves from Studio or ``daedalus gateway pair``.

This is not a dump of another agent's gateway: one inbound shape, one
``agent.chat`` call, Telegram Bot API only. No userbots (logging into a
personal Telegram account) — that violates Telegram's ToS.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("daedalus.messaging")

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LEN = 8
PAIRING_TTL_S = 3600
TG_CHUNK = 3500

_sessions: dict[str, str] = {}
_inflight: dict[str, asyncio.Task[Any]] = {}
_store_lock = asyncio.Lock()


@dataclass
class Inbound:
    platform: str
    user_id: str
    chat_id: str
    chat_type: str
    text: str
    username: str = ""
    display_name: str = ""


@dataclass
class Outbound:
    text: str = ""
    chunks: list[str] = field(default_factory=list)
    silent: bool = False


def bot_token() -> str:
    return (os.environ.get("DAEDALUS_TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()


def allowlist() -> set[str]:
    raw = (os.environ.get("DAEDALUS_TELEGRAM_ALLOWLIST") or os.environ.get("TELEGRAM_ALLOWED_USERS") or "").strip()
    return {part.strip() for part in raw.split(",") if part.strip()}


def channel_id() -> str:
    return (os.environ.get("DAEDALUS_TELEGRAM_CHANNEL_ID") or "").strip()


def groups_allowed() -> bool:
    flag = (os.environ.get("DAEDALUS_TELEGRAM_GROUPS") or "").strip().lower()
    return flag in ("1", "true", "yes", "on")


def pairing_enabled() -> bool:
    flag = (os.environ.get("DAEDALUS_TELEGRAM_PAIRING") or "1").strip().lower()
    return flag not in ("0", "false", "no", "off")


def kill_switched() -> bool:
    flag = (os.environ.get("DAEDALUS_GATEWAY") or "").strip().lower()
    return flag in ("0", "false", "no", "off")


def configured() -> bool:
    return bool(bot_token())


def enabled() -> bool:
    if kill_switched() or not configured():
        return False
    try:
        from .offline import enabled as offline
        if offline():
            return False
    except Exception:
        pass
    return True


def store_path() -> Path:
    from .paths import data_root
    return data_root() / "gateway.json"


def _empty_store() -> dict[str, Any]:
    return {"paired": {}, "pending": {}, "sessions": {}}


def load_store() -> dict[str, Any]:
    path = store_path()
    if not path.is_file():
        return _empty_store()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_store()
    if not isinstance(data, dict):
        return _empty_store()
    data.setdefault("paired", {})
    data.setdefault("pending", {})
    data.setdefault("sessions", {})
    return data


def save_store(data: dict[str, Any]) -> None:
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def origin_key(platform: str, user_id: str) -> str:
    return f"{platform}:{user_id}"


def chat_key(platform: str, chat_id: str) -> str:
    return f"{platform}:{chat_id}"


def new_pairing_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LEN))


def _purge_pending(store: dict[str, Any], *, now: float | None = None) -> None:
    now = now if now is not None else time.time()
    pending = store.get("pending") or {}
    dead = [code for code, row in pending.items() if float(row.get("expires") or 0) < now]
    for code in dead:
        pending.pop(code, None)


def is_paired(platform: str, user_id: str) -> bool:
    store = load_store()
    return origin_key(platform, user_id) in (store.get("paired") or {})


def is_allowed(platform: str, user_id: str) -> bool:
    uid = str(user_id)
    if uid in allowlist():
        return True
    return is_paired(platform, uid)


def request_access(
    platform: str,
    user_id: str,
    *,
    username: str = "",
    chat_id: str = "",
) -> dict[str, Any]:
    """Allowlist hit, existing pair, or a fresh pairing code. Never auto-approves."""
    uid = str(user_id)
    if is_allowed(platform, uid):
        return {"ok": True, "status": "allowed"}
    if not pairing_enabled():
        return {
            "ok": False,
            "status": "denied",
            "message": "Този бот не приема непознати. Собственикът трябва да добави ID-то ти в allowlist.",
        }
    store = load_store()
    _purge_pending(store)
    now = time.time()
    for code, row in (store.get("pending") or {}).items():
        if row.get("platform") == platform and str(row.get("user_id")) == uid:
            return {
                "ok": False,
                "status": "pending",
                "code": code,
                "message": _pairing_message(code),
            }
    code = new_pairing_code()
    store.setdefault("pending", {})[code] = {
        "platform": platform,
        "user_id": uid,
        "username": username,
        "chat_id": str(chat_id or uid),
        "created": now,
        "expires": now + PAIRING_TTL_S,
    }
    save_store(store)
    return {
        "ok": False,
        "status": "pending",
        "code": code,
        "message": _pairing_message(code),
    }


def _pairing_message(code: str) -> str:
    return (
        "Нямам право да говоря с непознати — агентът пише файлове и пуска shell.\n"
        f"Код за pairing: {code}\n"
        f"Собственикът одобрява с: daedalus gateway pair {code}\n"
        "или Studio → Свят → Одобри."
    )


def approve(code: str) -> dict[str, Any]:
    token = (code or "").strip().upper()
    if not token:
        return {"ok": False, "error": "empty code"}
    store = load_store()
    _purge_pending(store)
    row = (store.get("pending") or {}).pop(token, None)
    if not row:
        return {"ok": False, "error": "unknown or expired code"}
    key = origin_key(str(row.get("platform") or "telegram"), str(row.get("user_id")))
    store.setdefault("paired", {})[key] = {
        "platform": row.get("platform"),
        "user_id": str(row.get("user_id")),
        "username": row.get("username") or "",
        "chat_id": str(row.get("chat_id") or row.get("user_id")),
        "approved_at": datetime.now(timezone.utc).isoformat(),
    }
    save_store(store)
    return {"ok": True, "paired": store["paired"][key]}


def revoke(platform: str, user_id: str) -> dict[str, Any]:
    store = load_store()
    key = origin_key(platform, str(user_id))
    gone = (store.get("paired") or {}).pop(key, None)
    save_store(store)
    return {"ok": True, "revoked": bool(gone), "key": key}


def snapshot() -> dict[str, Any]:
    store = load_store()
    _purge_pending(store)
    pending = []
    for code, row in (store.get("pending") or {}).items():
        pending.append({
            "code": code,
            "platform": row.get("platform"),
            "user_id": str(row.get("user_id")),
            "username": row.get("username") or "",
            "expires": row.get("expires"),
        })
    paired = []
    for key, row in (store.get("paired") or {}).items():
        paired.append({
            "key": key,
            "platform": row.get("platform"),
            "user_id": str(row.get("user_id")),
            "username": row.get("username") or "",
        })
    from .telegram_gateway import bot_identity, poller_running
    ident = bot_identity()
    return {
        "ok": True,
        "enabled": enabled(),
        "configured": configured(),
        "polling": poller_running(),
        "kill_switch": kill_switched(),
        "pairing": pairing_enabled(),
        "groups": groups_allowed(),
        "allowlist": sorted(allowlist()),
        "channel": bool(channel_id()),
        "bot": ident,
        "paired": paired,
        "pending": pending,
        "help": (
            "Create a bot with @BotFather. Put DAEDALUS_TELEGRAM_BOT_TOKEN in .env. "
            "daedalus serve polls Telegram even when Studio is only on 127.0.0.1."
        ),
    }


def session_for(platform: str, chat_id: str, *, reset: bool = False) -> str:
    key = chat_key(platform, chat_id)
    if reset or key not in _sessions:
        token = secrets.token_hex(5)
        _sessions[key] = f"{platform}-{token}"
        store = load_store()
        store.setdefault("sessions", {})[key] = _sessions[key]
        save_store(store)
    return _sessions[key]


def restore_sessions() -> None:
    store = load_store()
    for key, sid in (store.get("sessions") or {}).items():
        if isinstance(key, str) and isinstance(sid, str):
            _sessions[key] = sid


def chunk_text(text: str, limit: int = TG_CHUNK) -> list[str]:
    raw = (text or "").strip() or "(празен отговор)"
    if len(raw) <= limit:
        return [raw]
    parts: list[str] = []
    rest = raw
    while rest:
        if len(rest) <= limit:
            parts.append(rest)
            break
        cut = rest.rfind("\n", 0, limit)
        if cut < limit // 3:
            cut = limit
        parts.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    return [p for p in parts if p]


HELP = (
    "Дедал през Telegram — същият агент като Studio.\n"
    "/help — това\n"
    "/status — бот, модел, pairing\n"
    "/new — нова сесия\n"
    "/stop — откажи текущия ход\n"
    "/whoami — твоето Telegram id\n"
    "Пиши нормално: „направи змия“, „какъв е статусът на проекта“."
)


def _is_command(text: str, name: str) -> bool:
    body = (text or "").strip()
    if not body.startswith("/"):
        return False
    cmd = body.split()[0].split("@", 1)[0].lower()
    return cmd == f"/{name}"


async def handle_inbound(msg: Inbound, *, chat_fn: Any | None = None) -> Outbound:
    text = (msg.text or "").strip()
    if not text:
        return Outbound(silent=True)
    if msg.chat_type in ("channel", "supergroup", "group") and not groups_allowed():
        return Outbound(silent=True)
    if msg.chat_type in ("channel",):
        return Outbound(silent=True)

    access = request_access(msg.platform, msg.user_id, username=msg.username, chat_id=msg.chat_id)
    if access.get("status") != "allowed":
        if _is_command(text, "whoami"):
            return Outbound(text=f"id {msg.user_id} · {access.get('status')}")
        if _is_command(text, "start") or _is_command(text, "help"):
            note = access.get("message") or HELP
            return Outbound(text=note)
        return Outbound(text=str(access.get("message") or "denied"))

    if _is_command(text, "help") or _is_command(text, "start"):
        return Outbound(text=HELP)
    if _is_command(text, "whoami"):
        return Outbound(text=f"id {msg.user_id} @{msg.username or '—'} · allowed")
    if _is_command(text, "status"):
        return Outbound(text=_status_text())
    if _is_command(text, "new") or _is_command(text, "reset"):
        sid = session_for(msg.platform, msg.chat_id, reset=True)
        return Outbound(text=f"Нова сесия: {sid}")
    if _is_command(text, "stop"):
        task = _inflight.get(chat_key(msg.platform, msg.chat_id))
        if task and not task.done():
            task.cancel()
            return Outbound(text="Спрях текущия ход.")
        return Outbound(text="Няма активен ход.")

    key = chat_key(msg.platform, msg.chat_id)
    existing = _inflight.get(key)
    if existing and not existing.done():
        return Outbound(text="Още работя. Изчакай или /stop.")

    sid = session_for(msg.platform, msg.chat_id)
    body = _strip_bot_mention(text)

    async def _run() -> dict[str, Any]:
        run = chat_fn
        if run is None:
            from .agent import get_daedalus
            run = get_daedalus().chat
        return await run(message=body, session_id=sid)

    task = asyncio.create_task(_run(), name=f"daedalus-msg-{key}")
    _inflight[key] = task
    try:
        result = await task
    except asyncio.CancelledError:
        return Outbound(text="Спряно.")
    except Exception as extra:
        log.warning("messaging chat failed: %s", extra)
        return Outbound(text=f"грешка: {str(extra)[:240]}")
    finally:
        if _inflight.get(key) is task:
            _inflight.pop(key, None)

    reply = str((result or {}).get("reply") or "").strip()
    chunks = chunk_text(reply)
    return Outbound(text=chunks[0], chunks=chunks)


def _strip_bot_mention(text: str) -> str:
    return re.sub(r"^/\w+@\w+\s*", "", text).strip() or text.strip()


def _status_text() -> str:
    snap = snapshot()
    bot = snap.get("bot") or {}
    uname = bot.get("username") or "(set token, restart)"
    try:
        from .llm_config import get_config
        cfg = get_config()
        model = f"{cfg.get('provider') or '?'} / {cfg.get('model') or '?'}"
    except Exception:
        model = "?"
    lines = [
        f"бот: @{uname}",
        f"poller: {'UP' if snap.get('polling') else 'DOWN'}",
        f"модел: {model}",
        f"allowlist: {len(snap.get('allowlist') or [])}",
        f"paired: {len(snap.get('paired') or [])}",
        f"pending: {len(snap.get('pending') or [])}",
        f"канал: {'да' if snap.get('channel') else 'не'}",
    ]
    return "\n".join(lines)
