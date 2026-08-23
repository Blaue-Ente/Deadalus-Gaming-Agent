"""Owner preference memory — durable, always injected into chat.

Understands simple speech ("запомни, че предпочитам Phaser", "always reply in
Bulgarian") plus a small heuristic extractor that runs after every turn.
JSON on disk is the source of truth so Studio works without Chroma.
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any

from .paths import data_root

log = logging.getLogger(__name__)

MAX_PREFS = 80
MAX_PROMPT_CHARS = 1800

_EXPLICIT = re.compile(
    r"(?:"
    r"запомни(?:[,:]?\s*че)?|"
    r"запомни\s+си|"
    r"предпочитам|"
    r"искам\s+винаги|"
    r"винаги\s+да|"
    r"remember(?:\s+that)?|"
    r"i\s+prefer|"
    r"always\s+"
    r")\s*(.+)$",
    re.I | re.M,
)

_KIND_HINTS = (
    ("language", re.compile(r"\b(български|english|на\s+бг|in english|на английски)\b", re.I)),
    ("stack", re.compile(r"\b(phaser|canvas|fastapi|react|unity|expo|vite|pygame)\b", re.I)),
    ("style", re.compile(r"\b(pixel art|minimal|dark|gold|без placeholder|real assets)\b", re.I)),
    ("workflow", re.compile(r"\b(не питай|just do|без уточнения|tdd|autopilot)\b", re.I)),
)


def _path() -> Path:
    p = data_root() / "preferences.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load() -> list[dict[str, Any]]:
    path = _path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data.get("items") if isinstance(data, dict) else data
        return list(items) if isinstance(items, list) else []
    except Exception:
        return []


def _save(items: list[dict[str, Any]]) -> None:
    payload = {"updated_at": time.time(), "items": items[:MAX_PREFS]}
    _path().write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def add(text: str, *, kind: str = "preference", source: str = "chat") -> dict[str, Any]:
    text = re.sub(r"\s+", " ", (text or "").strip()).strip(" .")
    if len(text) < 4:
        raise ValueError("preference too short")
    items = load()
    # de-dupe on normalized text
    key = text.lower()
    for existing in items:
        if str(existing.get("text", "")).lower() == key:
            existing["updated_at"] = time.time()
            existing["hits"] = int(existing.get("hits") or 1) + 1
            _save(items)
            return existing
    row = {
        "id": f"pref_{uuid.uuid4().hex[:10]}",
        "text": text[:400],
        "kind": kind,
        "source": source,
        "created_at": time.time(),
        "updated_at": time.time(),
        "hits": 1,
    }
    items.insert(0, row)
    _save(items)
    try:
        from .memory import DaedalusMemory
        mem = DaedalusMemory()
        mem.lessons_col.add(
            documents=[f"OWNER PREFERENCE [{kind}]: {text}"],
            metadatas=[{"type": "preference", "kind": kind, "source": source}],
            ids=[row["id"]],
        )
    except Exception as exc:
        log.debug("preference chroma skip: %s", exc)
    return row


def remove(pref_id: str) -> bool:
    items = load()
    kept = [i for i in items if i.get("id") != pref_id]
    if len(kept) == len(items):
        return False
    _save(kept)
    return True


def _guess_kind(text: str) -> str:
    for kind, pat in _KIND_HINTS:
        if pat.search(text):
            return kind
    return "preference"


def observe_turn(user_message: str, assistant_reply: str = "") -> list[dict[str, Any]]:
    """Extract explicit remember/prefer statements from a user turn."""
    msg = (user_message or "").strip()
    if not msg:
        return []
    found: list[dict[str, Any]] = []
    for m in _EXPLICIT.finditer(msg):
        body = (m.group(1) or "").strip().strip("\"'")
        body = re.sub(r"^(че|that)\s+", "", body, flags=re.I)
        if len(body) < 4:
            continue
        try:
            found.append(add(body, kind=_guess_kind(body), source="explicit"))
        except ValueError:
            continue
    # Short "call me X" / "казвай ми X"
    m2 = re.search(r"(?:казвай ми|call me|обръщай се към мен като)\s+([A-Za-zА-Яа-я0-9_-]{2,40})", msg, re.I)
    if m2:
        found.append(add(f"Owner name/address: {m2.group(1)}", kind="identity", source="explicit"))
    _ = assistant_reply  # reserved for future reflection-quality filter
    return found


def format_block(limit: int = 12) -> str:
    items = load()[:limit]
    if not items:
        return ""
    lines = [
        "━━━ OWNER PREFERENCES (always honour; fill this from conversation) ━━━",
        "Говори просто, като на човек. Изпълнявай директни команди. "
        "Ако каже „запомни“, запиши предпочитанието завинаги.",
    ]
    used = 0
    for it in items:
        line = f"  • [{it.get('kind','preference')}] {it.get('text','')}"
        if used + len(line) > MAX_PROMPT_CHARS:
            break
        lines.append(line)
        used += len(line)
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)
