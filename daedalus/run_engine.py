"""Durable run log: JSONL events + a checkpoint file under ``$DAEDALUS_DATA/runs``.

Autopilot already buffers SSE in memory/KV. This store survives a process
restart so Studio can show the last phase and skip completed stages on resume.
It does not re-spawn the asyncio task by itself.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .paths import data_root


def runs_dir() -> Path:
    path = data_root() / "runs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def events_path(run_id: str) -> Path:
    return runs_dir() / f"{_safe(run_id)}.jsonl"


def checkpoint_path(run_id: str) -> Path:
    return runs_dir() / f"{_safe(run_id)}.checkpoint.json"


def slug_pointer_path(slug: str) -> Path:
    return runs_dir() / f"slug-{_safe(slug)}.json"


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in (name or "")[:80]) or "run"


def record(
    run_id: str,
    event: str,
    data: dict[str, Any] | None = None,
    *,
    slug: str = "",
    checkpoint: dict[str, Any] | None = None,
) -> None:
    """Append an event and optionally refresh the checkpoint. No-op if run_id is empty."""
    if not run_id:
        return
    append_event(run_id, event, data)
    if checkpoint is not None:
        save_checkpoint(run_id, checkpoint, slug=slug)


def append_event(run_id: str, event: str, data: dict[str, Any] | None = None) -> None:
    row = {
        "ts": time.time(),
        "event": event,
        "data": data or {},
    }
    path = events_path(run_id)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def save_checkpoint(run_id: str, payload: dict[str, Any], *, slug: str = "") -> Path:
    body = dict(payload)
    body["run_id"] = run_id
    body["updated"] = time.time()
    path = checkpoint_path(run_id)
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    if slug:
        slug_pointer_path(slug).write_text(
            json.dumps({"run_id": run_id, "slug": slug, "updated": body["updated"]}),
            encoding="utf-8",
        )
    return path


def load_checkpoint(run_id: str) -> dict[str, Any] | None:
    path = checkpoint_path(run_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def checkpoint_for_slug(slug: str) -> dict[str, Any] | None:
    pointer = slug_pointer_path(slug)
    if not pointer.is_file():
        return None
    try:
        meta = json.loads(pointer.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    run_id = str(meta.get("run_id") or "")
    if not run_id:
        return None
    ckpt = load_checkpoint(run_id)
    if ckpt:
        return ckpt
    return meta


def load_events(run_id: str, limit: int = 400) -> list[dict[str, Any]]:
    path = events_path(run_id)
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines[-max(1, limit):]:
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows
