"""Owner files/images attached to a Studio chat turn.

Stored under ``$DAEDALUS_DATA/chat_attachments/{session_id}/``.
Text is injected into the user message. Images become vision blocks
(OpenAI-compatible ``image_url`` data URLs) on the first LLM call.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any

from .paths import data_root
from .session_context import _safe_session, clean_ids

log = logging.getLogger(__name__)

MAX_BYTES = 6 * 1024 * 1024
MAX_TEXT_CHARS = 60_000
MAX_ATTACH = 8

_TEXT_EXT = {
    "txt", "md", "markdown", "rst", "csv", "tsv", "json", "jsonl", "yaml", "yml",
    "xml", "html", "htm", "css", "js", "jsx", "ts", "tsx", "py", "rb", "go",
    "rs", "java", "kt", "cs", "c", "cc", "cpp", "h", "hpp", "gd", "tscn",
    "sql", "sh", "toml", "ini", "cfg", "log", "env.example", "svg",
}
_IMAGE_EXT = {"png", "jpg", "jpeg", "gif", "webp"}
_IMAGE_MIME = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
}


def _root(session_id: str) -> Path:
    p = data_root() / "chat_attachments" / _safe_session(session_id)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _ext(name: str) -> str:
    if "." not in (name or ""):
        return ""
    return name.rsplit(".", 1)[-1].lower()


def _looks_binary(raw: bytes) -> bool:
    if not raw:
        return False
    sample = raw[:4096]
    if b"\x00" in sample:
        return True
    # PDF / ZIP / OLE
    if sample.startswith(b"%PDF") or sample.startswith(b"PK") or sample.startswith(b"\xd0\xcf"):
        return True
    return False


def _decode_text(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace")
    text = text.replace("\x00", "")
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS] + f"\n\n[… truncated to {MAX_TEXT_CHARS:,} characters]"
    return text


def save_bytes(
    session_id: str,
    filename: str,
    raw: bytes,
    *,
    content_type: str = "",
) -> dict[str, Any]:
    if not raw:
        return {"ok": False, "error": "empty file"}
    if len(raw) > MAX_BYTES:
        return {"ok": False, "error": f"file too large (max {MAX_BYTES // (1024 * 1024)} MB)"}
    name = re.sub(r"[^\w.\- ]+", "_", (filename or "file").strip())[:120] or "file"
    ext = _ext(name)
    mime = (content_type or "").split(";")[0].strip().lower()
    att_id = uuid.uuid4().hex[:12]
    kind = "file"
    text = ""
    if ext in _IMAGE_EXT or mime.startswith("image/"):
        kind = "image"
        if ext not in _IMAGE_EXT:
            ext = "png"
            name = name + ".png"
        mime = _IMAGE_MIME.get(ext, mime or "image/png")
    elif ext in _TEXT_EXT or (mime.startswith("text/") or mime in ("application/json", "application/xml")):
        kind = "file"
        text = _decode_text(raw)
    elif not _looks_binary(raw):
        kind = "file"
        text = _decode_text(raw)
    else:
        kind = "file"
        text = (
            f"[binary document: {name}]\n"
            "Daedalus could not extract text from this format. "
            "Paste the relevant passage or convert to .md / .txt."
        )
    rec = {
        "id": att_id,
        "name": name,
        "kind": kind,
        "mime": mime or ("text/plain" if kind == "file" else "application/octet-stream"),
        "chars": len(text),
        "bytes": len(raw),
        "session_id": _safe_session(session_id),
    }
    if text:
        rec["text"] = text
    root = _root(session_id)
    (root / f"{att_id}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if kind == "image":
        (root / f"{att_id}.bin").write_bytes(raw)
    return {"ok": True, **{k: v for k, v in rec.items() if k != "text"}, "preview": (text or "")[:240]}


def ingest_project_file(session_id: str, slug: str, rel: str) -> dict[str, Any]:
    from . import playground as pg

    path = (rel or "").strip().lstrip("/")
    if not path or ".." in path.split("/"):
        return {"ok": False, "error": "invalid path"}
    try:
        text = pg.read_file(slug, path, max_bytes=200_000)
    except FileNotFoundError:
        return {"ok": False, "error": f"missing {path}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}
    raw = text.encode("utf-8", errors="replace")
    name = path.replace("/", "_")
    return save_bytes(session_id, name, raw, content_type="text/plain")


def load_one(session_id: str, att_id: str) -> dict[str, Any] | None:
    sid = clean_ids([att_id], limit=1)
    if not sid:
        return None
    path = _root(session_id) / f"{sid[0]}.json"
    if not path.is_file():
        return None
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(rec, dict):
        return None
    rec["_bin"] = _root(session_id) / f"{sid[0]}.bin"
    return rec


def load_many(session_id: str, ids: list[str] | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for att_id in clean_ids(ids or [], limit=MAX_ATTACH):
        rec = load_one(session_id, att_id)
        if rec:
            rows.append(rec)
    return rows


def prompt_block(rows: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for rec in rows:
        name = rec.get("name") or rec.get("id") or "file"
        kind = rec.get("kind") or "file"
        if kind == "image":
            parts.append(f"━━━ ATTACHED IMAGE: {name} ━━━\n(image delivered as vision on this turn)")
            continue
        text = (rec.get("text") or "").strip()
        if not text:
            continue
        parts.append(f"━━━ ATTACHED FILE: {name} ━━━\n{text}")
    return "\n\n".join(parts)


def vision_blocks(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    import base64

    for rec in rows:
        if rec.get("kind") != "image":
            continue
        bin_path = rec.get("_bin")
        if not isinstance(bin_path, Path) or not bin_path.is_file():
            continue
        try:
            raw = bin_path.read_bytes()
        except OSError:
            continue
        mime = rec.get("mime") or "image/png"
        b64 = base64.b64encode(raw).decode("ascii")
        blocks.append({
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{b64}"},
        })
    return blocks
