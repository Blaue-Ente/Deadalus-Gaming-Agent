"""Failure-only diagnostics for Studio Review radar + journal Playwright flow.

Test-only. On assertion or wait timeout, records a bounded, redacted snapshot of
stub call order, held-route depth, discovery fetches, and status DOM. Does not
change product code, timeouts, or assertions. Artifacts: test-results/radar-journal-diag/.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_DIR = ROOT / "test-results" / "radar-journal-diag"
MAX_EVENTS = 64
TEXT_CAP = 200
FILE_CAP = 8000
KEEP_ARTIFACTS = 3
_SECRET_QUERY = frozenset({"token", "key", "authorization", "auth", "api_key"})
_RADAR_JOURNAL = ("/admin/daedalus/radar/", "/admin/daedalus/journal/", "/admin/daedalus/discoveries")
_SEQ = 0


def clip(text: str, limit: int = TEXT_CAP) -> str:
    text = str(text).replace("\r", "")
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def safe_path(url: str) -> str:
    raw = str(url)
    parts = urlsplit(raw if "://" in raw else "http://127.0.0.1" + (raw if raw.startswith("/") else "/" + raw))
    return parts.path


def redact(value: str) -> str:
    text = re.sub(r"(?i)bearer\s+\S+", "Bearer [redacted]", str(value))
    text = re.sub(
        r"(?i)(token|api_key|authorization|auth|key)=([^&\s]+)",
        r"\1=[redacted]",
        text,
    )
    if "owner.token" in text or "DAEDALUS_DATA" in text:
        return "[redacted]"
    if "://" in text or text.startswith("/"):
        try:
            return safe_path(text)
        except ValueError:
            return "[redacted]"
    return clip(text)


class Trace:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self.dropped = 0

    def add(self, kind: str, **fields) -> None:
        clean: dict = {"n": len(self.events), "kind": kind}
        for key, value in fields.items():
            lk = str(key).lower()
            if lk in _SECRET_QUERY or lk in {"headers", "cookie", "cookies", "post_data", "body"}:
                continue
            if key == "url":
                clean[key] = safe_path(str(value))
            elif key == "method":
                clean[key] = str(value).upper()[:12]
            elif isinstance(value, str):
                clean[key] = redact(value)
            else:
                clean[key] = value
        if len(self.events) >= MAX_EVENTS:
            self.dropped += 1
            return
        self.events.append(clean)


def attach(page) -> Trace:
    trace = Trace()

    def on_failed(req) -> None:
        path = safe_path(req.url)
        if not any(bit in path for bit in _RADAR_JOURNAL):
            return
        failure = req.failure
        if isinstance(failure, dict):
            failure = failure.get("errorText") or ""
        trace.add("requestfailed", method=req.method, url=req.url, error=clip(str(failure or ""), 120))

    def on_console(msg) -> None:
        if msg.type not in ("error", "warning"):
            return
        trace.add("console", level=msg.type, text=msg.text)

    def on_pageerror(err) -> None:
        trace.add("pageerror", text=str(err))

    page.on("requestfailed", on_failed)
    page.on("console", on_console)
    page.on("pageerror", on_pageerror)
    return trace


_FRAME = """() => {
  const clip = (value) => {
    const text = value || "";
    return text.length > 200 ? text.slice(0, 200) : text;
  };
  const panel = document.querySelector('.workspace-panel[data-panel="review"]');
  const q = (id) => document.getElementById(id);
  const radarScan = q('radar-scan');
  const journalLoad = q('journal-load');
  return {
    panelReviewHidden: panel ? !!panel.hidden : null,
    queryPanel: new URL(location.href).searchParams.get('panel'),
    radarScanDisabled: radarScan ? radarScan.disabled : null,
    journalLoadDisabled: journalLoad ? journalLoad.disabled : null,
    radarStatus: clip((q('radar-status') && q('radar-status').textContent) || ''),
    journalStatus: clip((q('journal-status') && q('journal-status').textContent) || ''),
    radarScanView: clip((q('radar-scan-view') && q('radar-scan-view').textContent) || ''),
    journalList: clip((q('journal-list') && q('journal-list').textContent) || ''),
    discoveriesStatus: clip((q('discovery-status') && q('discovery-status').textContent) || ''),
  };
}"""


def frame_facts(page) -> dict:
    try:
        data = page.evaluate(_FRAME)
    except Exception as exc:
        return {"frame_error": redact(str(exc))}
    if isinstance(data, dict):
        return data
    return {"frame_error": "frame_facts-not-object"}


def call_sequence(calls: list) -> list[dict]:
    out: list[dict] = []
    for row in (calls or [])[:MAX_EVENTS]:
        if not isinstance(row, dict):
            continue
        out.append({
            "kind": row.get("kind"),
            "method": row.get("method"),
            "has_body": row.get("raw") is not None,
        })
    return out


def discovery_paths(discoveries: list) -> list[str]:
    return [safe_path(str(u)) for u in (discoveries or [])[:24]]


def _utf8_len(text: str) -> int:
    return len(text.encode("utf-8"))


def _serialize_bounded(payload: dict, *, cap: int | None = None) -> str:
    """JSON that always parses; size bounded by UTF-8 byte length (not char slice)."""
    cap = FILE_CAP if cap is None else cap
    work: dict = dict(payload)
    work.pop("truncated", None)
    work.pop("events_omitted", None)
    for _ in range(256):
        text = json.dumps(work, ensure_ascii=False, indent=2)
        if _utf8_len(text) <= cap:
            return text
        trimmed = False
        events = work.get("events")
        if isinstance(events, list) and events:
            work["events"] = events[:-1]
            work["truncated"] = True
            work["events_omitted"] = int(work.get("events_omitted") or 0) + 1
            trimmed = True
        elif isinstance(work.get("leaked_sample"), list) and work["leaked_sample"]:
            work["leaked_sample"] = work["leaked_sample"][:-1]
            work["truncated"] = True
            trimmed = True
        elif isinstance(work.get("discoveries_paths"), list) and work["discoveries_paths"]:
            work["discoveries_paths"] = work["discoveries_paths"][:-1]
            work["truncated"] = True
            trimmed = True
        elif isinstance(work.get("call_sequence"), list) and work["call_sequence"]:
            work["call_sequence"] = work["call_sequence"][:-1]
            work["truncated"] = True
            trimmed = True
        frame = work.get("frame")
        if not trimmed and isinstance(frame, dict) and frame:
            key = max(frame, key=lambda k: len(str(frame[k])))
            work["frame"] = {k: v for k, v in frame.items() if k != key}
            work["truncated"] = True
            trimmed = True
        if not trimmed:
            break
    minimal = {
        "label": work.get("label", "unknown"),
        "truncated": True,
        "reason": "payload_exceeded_file_cap",
        "held_len": work.get("held_len"),
        "discoveries_count": work.get("discoveries_count"),
        "dropped_events": work.get("dropped_events"),
        "events_omitted": work.get("events_omitted"),
    }
    text = json.dumps(minimal, ensure_ascii=False, indent=2)
    if _utf8_len(text) > cap:
        text = json.dumps(
            {"label": str(work.get("label", "unknown"))[:80], "truncated": True, "reason": "minimal_cap"},
            ensure_ascii=False,
        )
    return text


def _prune() -> None:
    existing = sorted(ARTIFACT_DIR.glob("*.json"), key=lambda path: path.stat().st_mtime)
    while len(existing) > KEEP_ARTIFACTS:
        existing.pop(0).unlink(missing_ok=True)


def record_failure(
    page,
    trace: Trace,
    label: str,
    *,
    calls: list | None = None,
    held_len: int = 0,
    discoveries: list | None = None,
    mode: dict | None = None,
    dialogs_count: int = 0,
    leaked: list | None = None,
) -> str:
    """Write one artifact and attach a short note to the active exception (if any)."""
    payload = {
        "label": label,
        "held_len": held_len,
        "discoveries_count": len(discoveries or []),
        "discoveries_paths": discovery_paths(discoveries or []),
        "dialogs_count": dialogs_count,
        "leaked_count": len(leaked or []),
        "leaked_sample": [redact(x) for x in (leaked or [])[:8]],
        "mode_keys": sorted((mode or {}).keys()),
        "call_sequence": call_sequence(calls or []),
        "frame": frame_facts(page),
        "dropped_events": trace.dropped,
        "events": trace.events,
    }
    text = _serialize_bounded(payload, cap=FILE_CAP)
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    global _SEQ
    _SEQ += 1
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in label)[:48]
    path = ARTIFACT_DIR / f"{safe}-{_SEQ}.json"
    summary: str
    try:
        path.write_text(text, encoding="utf-8")
        _prune()
        try:
            rel = path.relative_to(ROOT)
        except ValueError:
            rel = path.name
        summary = f"radar-journal diag artifact: {rel}"
    except OSError as io_err:
        summary = f"radar-journal diag artifact write failed: {type(io_err).__name__}"
    import sys

    exc = sys.exc_info()[1]
    if exc is not None and hasattr(exc, "add_note"):
        exc.add_note(summary)
    return summary
