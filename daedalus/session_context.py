"""Session pins for Studio chat — skills and MCP servers the owner chose.

Pins live under ``$DAEDALUS_DATA/session_context/{session_id}.json``.
Turn extras (one prompt) merge on top and are not written back.

Progressive disclosure still applies: pins win SKILL.md body slots.
Empty ``mcp_ids`` means every enabled server is allowed; a non-empty
tuple restricts ``mcp_call`` to that list for the turn.
"""
from __future__ import annotations

import json
import logging
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .paths import data_root

log = logging.getLogger(__name__)

MAX_PINS = 16
_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,79}$")

_bound: ContextVar["TurnPack | None"] = ContextVar("daedalus_turn_pack", default=None)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean_id(raw: Any) -> str:
    s = str(raw or "").strip()
    if not s or "/" in s or "\\" in s or ".." in s:
        return ""
    if not _ID_RE.match(s):
        slug = re.sub(r"[^a-zA-Z0-9._:-]+", "-", s).strip("-")[:80]
        return slug if slug and _ID_RE.match(slug) else ""
    return s[:80]


def clean_ids(raw: Any, *, limit: int = MAX_PINS) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    if not isinstance(raw, (list, tuple)):
        return out
    for item in raw:
        sid = clean_id(item)
        if not sid or sid in seen:
            continue
        seen.add(sid)
        out.append(sid)
        if len(out) >= limit:
            break
    return out


def _safe_session(session_id: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9._-]+", "-", (session_id or "").strip())[:80]
    return s or "default"


def context_dir() -> Path:
    p = data_root() / "session_context"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _path(session_id: str) -> Path:
    return context_dir() / f"{_safe_session(session_id)}.json"


def load(session_id: str) -> dict[str, Any]:
    path = _path(session_id)
    if not path.is_file():
        return {"session_id": _safe_session(session_id), "skill_ids": [], "mcp_ids": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"session_id": _safe_session(session_id), "skill_ids": [], "mcp_ids": []}
    if not isinstance(data, dict):
        return {"session_id": _safe_session(session_id), "skill_ids": [], "mcp_ids": []}
    return {
        "session_id": _safe_session(session_id),
        "skill_ids": clean_ids(data.get("skill_ids")),
        "mcp_ids": clean_ids(data.get("mcp_ids")),
        "updated_at": data.get("updated_at") or "",
    }


def save(session_id: str, *, skill_ids: list[str] | None = None, mcp_ids: list[str] | None = None) -> dict[str, Any]:
    current = load(session_id)
    if skill_ids is not None:
        current["skill_ids"] = clean_ids(skill_ids)
    if mcp_ids is not None:
        current["mcp_ids"] = clean_ids(mcp_ids)
    current["updated_at"] = _now()
    path = _path(session_id)
    path.write_text(json.dumps(current, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return current


@dataclass(frozen=True)
class TurnPack:
    session_id: str
    pin_skill_ids: tuple[str, ...] = ()
    turn_skill_ids: tuple[str, ...] = ()
    mcp_ids: tuple[str, ...] = ()
    attachment_text: str = ""
    vision_blocks: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    @property
    def skill_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys([*self.pin_skill_ids, *self.turn_skill_ids]))


def resolve_turn(
    session_id: str,
    *,
    skill_ids: list[str] | None = None,
    mcp_ids: list[str] | None = None,
    attachment_ids: list[str] | None = None,
) -> TurnPack:
    saved = load(session_id)
    pins = tuple(saved.get("skill_ids") or [])
    saved_mcp = tuple(saved.get("mcp_ids") or [])
    turn = tuple(clean_ids(skill_ids))
    turn_mcp = tuple(clean_ids(mcp_ids))
    merged_mcp = tuple(dict.fromkeys([*saved_mcp, *turn_mcp]))
    text = ""
    vision: tuple[dict[str, Any], ...] = ()
    if attachment_ids:
        try:
            from .chat_attachments import load_many, prompt_block, vision_blocks

            rows = load_many(session_id, attachment_ids)
            text = prompt_block(rows)
            vision = tuple(vision_blocks(rows))
        except Exception as exc:
            log.debug("attachment load skipped: %s", exc)
    return TurnPack(
        session_id=_safe_session(session_id),
        pin_skill_ids=pins,
        turn_skill_ids=turn,
        mcp_ids=merged_mcp,
        attachment_text=text,
        vision_blocks=vision,
    )


@contextmanager
def bind(pack: TurnPack | None) -> Iterator[TurnPack | None]:
    token = _bound.set(pack)
    try:
        yield pack
    finally:
        _bound.reset(token)


def current() -> TurnPack | None:
    return _bound.get()


def user_llm_payload(text: str) -> Any:
    """String or multimodal content list for the first user turn."""
    pack = current()
    blocks = list(pack.vision_blocks) if pack else []
    if not blocks:
        return text
    return [{"type": "text", "text": text}, *blocks]


def mcp_prompt_block(mcp_ids: tuple[str, ...] | list[str]) -> str:
    ids = [str(x) for x in mcp_ids if str(x).strip()]
    if not ids:
        return ""
    try:
        from .mcp_client import load_servers

        servers = load_servers()
    except Exception:
        servers = {}
    lines = [
        "━━━ SESSION MCP ━━━",
        "Owner pinned these MCP servers for this turn. Prefer mcp_call on them.",
        "Do not call other MCP servers unless the owner asks.",
    ]
    for name in ids:
        spec = servers.get(name) if isinstance(servers, dict) else None
        if not spec:
            lines.append(f"- {name} (not enabled — owner must enable the addon first)")
            continue
        kind = "http" if spec.get("url") else "stdio"
        lines.append(f"- {name} ({kind})")
    lines.append("mcp_call({server, list_tools: true}) or mcp_call({server, tool, arguments}).")
    return "\n".join(lines)


def mcp_allowed(server: str) -> tuple[bool, str]:
    """If the turn pinned MCP servers, only those names may be called."""
    pack = current()
    if pack is None or not pack.mcp_ids:
        return True, ""
    name = (server or "").strip()
    if not name:
        return True, ""
    if name in pack.mcp_ids:
        return True, ""
    allowed = ", ".join(pack.mcp_ids)
    return False, f"MCP server {name!r} is not in the owner's session list ({allowed})."
