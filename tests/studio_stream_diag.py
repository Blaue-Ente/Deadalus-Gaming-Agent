"""Failure-only diagnostics for Studio build-stream waits.

This is not a runtime fix. A wait that raises records which checkpoint
facts are false, the status line, and the stubbed request order. The
Playwright timeout is left untouched. Logs drop auth, owner paths, and
anything past a small cap. Artifacts are kept only for the latest few
failures.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit, urlencode

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_DIR = ROOT / "test-results" / "stream-diag"
MAX_EVENTS = 48
TEXT_CAP = 180
FILE_CAP = 6000
KEEP_ARTIFACTS = 3
_SECRET_QUERY = {"token", "key", "authorization", "auth", "api_key"}
_INTERESTING = (
    "/build/guided",
    "/guided/active",
    "/guided/stream",
    "/guided/continue",
    "/guided/stop",
    "/autopilot",
    "/game-studio/api/projects/",
)
_SEQ = 0


def clip(text: str, limit: int = TEXT_CAP) -> str:
    text = str(text).replace("\r", "")
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def safe_url(url: str) -> str:
    raw = str(url)
    parts = urlsplit(raw if "://" in raw else "http://stream.local" + (raw if raw.startswith("/") else "/" + raw))
    kept = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key.lower() in _SECRET_QUERY:
            continue
        kept.append((key, clip(value, 60)))
    query = urlencode(kept)
    return parts.path + (("?" + query) if query else "")


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
            text = safe_url(text)
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
            if str(key).lower() in _SECRET_QUERY or str(key).lower() in {"headers", "cookie", "post_data"}:
                continue
            if isinstance(value, str):
                clean[key] = redact(value)
            else:
                clean[key] = value
        if len(self.events) >= MAX_EVENTS:
            self.dropped += 1
            return
        self.events.append(clean)


def _interesting(url: str) -> bool:
    return any(bit in url for bit in _INTERESTING)


def attach(page) -> Trace:
    """Record stubbed build/stream traffic, console lines, and page errors."""
    trace = Trace()

    def on_request(req) -> None:
        if _interesting(req.url):
            trace.add("request", method=req.method, url=req.url)

    def on_response(res) -> None:
        if _interesting(res.url):
            trace.add("response", status=res.status, url=res.url)

    def on_failed(req) -> None:
        if not _interesting(req.url):
            return
        failure = req.failure
        if isinstance(failure, dict):
            failure = failure.get("errorText") or ""
        trace.add("requestfailed", method=req.method, url=req.url, error=str(failure or ""))

    def on_console(msg) -> None:
        trace.add("console", level=msg.type, text=msg.text)

    def on_pageerror(err) -> None:
        trace.add("pageerror", text=str(err))

    page.on("request", on_request)
    page.on("response", on_response)
    page.on("requestfailed", on_failed)
    page.on("console", on_console)
    page.on("pageerror", on_pageerror)
    return trace


_FACTS = """() => {
  const clip = (value) => {
    const text = value || "";
    return text.length > 180 ? text.slice(-180) : text;
  };
  const build = document.getElementById("build-checkpoint");
  const flightZone = document.getElementById("flight-checkpoint");
  const flight = document.getElementById("pane-flight");
  const status = document.getElementById("build-status");
  const log = document.getElementById("log");
  const out = document.getElementById("build-out");
  const buildHidden = build ? build.hidden : null;
  const flightHidden = flightZone ? flightZone.hidden : null;
  const flightOn = !!(flight && flight.classList.contains("on"));
  return {
    slug: (window.dxStudio && window.dxStudio.getSlug()) || "",
    buildCheckpointHidden: buildHidden,
    flightCheckpointHidden: flightHidden,
    flightOn: flightOn,
    falseFacts: [
      buildHidden === false ? "" : "build-checkpoint visible",
      flightHidden === false ? "" : "flight-checkpoint visible",
      flightOn ? "" : "pane-flight.on",
    ].filter(Boolean),
    statusState: status ? (status.dataset.state || "") : null,
    statusText: status ? clip(status.textContent) : null,
    log: log ? clip(log.textContent) : null,
    buildOut: out ? clip(out.textContent) : null,
  };
}"""


def facts(page) -> dict:
    try:
        data = page.evaluate(_FACTS)
    except Exception as exc:
        return {"facts_error": redact(str(exc)), "falseFacts": ["facts-unavailable"]}
    if isinstance(data, dict):
        return data
    return {"facts_error": "facts were not an object", "falseFacts": ["facts-unavailable"]}


_OWNER_FACTS = """() => {
  const clip = (value) => {
    const text = value || "";
    return text.length > 180 ? text.slice(0, 180) : text;
  };
  const q = (id) => document.getElementById(id);
  const stop = q("build-stop");
  const guided = q("build-guided");
  const params = new URL(location.href).searchParams;
  return {
    slug: (window.dxStudio && window.dxStudio.getSlug()) || "",
    panel: params.get("panel") || "",
    buildStopHidden: stop ? stop.hidden : null,
    buildGuidedDisabled: guided ? guided.disabled : null,
    buildCheckpointHidden: q("build-checkpoint") ? q("build-checkpoint").hidden : null,
    flightCheckpointHidden: q("flight-checkpoint") ? q("flight-checkpoint").hidden : null,
    flightOn: !!(q("pane-flight") && q("pane-flight").classList.contains("on")),
    buildStatus: q("build-status") ? clip(q("build-status").textContent) : null,
  };
}"""


def owner_facts(page) -> dict:
    try:
        data = page.evaluate(_OWNER_FACTS)
    except Exception as exc:
        return {"owner_facts_error": redact(str(exc))}
    if isinstance(data, dict):
        return data
    return {"owner_facts_error": "owner_facts-not-object"}


def owned_state(owned) -> dict:
    """Bounded stub state from guided-resume Playwright tests (no secrets)."""
    if owned is None:
        return {}
    streams = list(getattr(owned, "streams", None) or [])
    return {
        "streams_count": len(streams),
        "streams_paths": [safe_url(u) for u in streams[:24]],
        "active_held_count": len(getattr(owned, "actives", None) or []),
        "active_answered_count": len(getattr(owned, "answered", None) or []),
        "posts_held_count": len(getattr(owned, "held_posts", None) or []),
        "posts_released_count": len(getattr(owned, "posts", None) or []),
        "stops_count": len(getattr(owned, "stops", None) or []),
        "post_mode": str(getattr(owned, "post_mode", "") or ""),
    }


def _utf8_len(text: str) -> int:
    return len(text.encode("utf-8"))


def _serialize_bounded(payload: dict, *, cap: int | None = None) -> str:
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
        elif isinstance(work.get("streams_paths"), list) and work["streams_paths"]:
            work["streams_paths"] = work["streams_paths"][:-1]
            work["truncated"] = True
            trimmed = True
        owned = work.get("owned")
        if not trimmed and isinstance(owned, dict):
            for key in ("streams_paths",):
                if isinstance(owned.get(key), list) and owned[key]:
                    owned = dict(owned)
                    owned[key] = owned[key][:-1]
                    work["owned"] = owned
                    work["truncated"] = True
                    trimmed = True
                    break
        frame = work.get("facts")
        if not trimmed and isinstance(frame, dict) and frame:
            key = max(frame, key=lambda k: len(str(frame[k])))
            work["facts"] = {k: v for k, v in frame.items() if k != key}
            work["truncated"] = True
            trimmed = True
        if not trimmed:
            break
    minimal = {
        "label": work.get("label", "unknown"),
        "truncated": True,
        "reason": "payload_exceeded_file_cap",
        "events_omitted": work.get("events_omitted"),
        "dropped_events": work.get("dropped_events"),
    }
    text = json.dumps(minimal, ensure_ascii=False, indent=2)
    if _utf8_len(text) > cap:
        text = json.dumps(
            {"label": str(work.get("label", "unknown"))[:80], "truncated": True, "reason": "minimal_cap"},
            ensure_ascii=False,
        )
    return text


def _write_artifact(label: str, text: str) -> str:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    global _SEQ
    _SEQ += 1
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in label)[:48]
    path = ARTIFACT_DIR / f"{safe}-{_SEQ}.json"
    path.write_text(text, encoding="utf-8")
    _prune()
    try:
        rel = path.relative_to(ROOT)
    except ValueError:
        rel = path.name
    return f"stream-diag artifact: {rel}"


def _prune() -> None:
    existing = sorted(ARTIFACT_DIR.glob("*.json"), key=lambda path: path.stat().st_mtime)
    while len(existing) > KEEP_ARTIFACTS:
        existing.pop(0).unlink(missing_ok=True)


def dump(page, trace: Trace, label: str) -> str:
    payload = {
        "label": label,
        "note": "diagnostic only; not a product fix",
        "facts": facts(page),
        "dropped_events": trace.dropped,
        "events": trace.events,
    }
    text = _serialize_bounded(payload, cap=FILE_CAP)
    _write_artifact(label, text)
    return text


def record_failure(
    page,
    trace: Trace,
    label: str,
    *,
    owned=None,
) -> str:
    """Failure-only artifact for guided-resume owner flows. Re-raise stays with caller."""
    payload = {
        "label": label,
        "facts": facts(page),
        "owner": owner_facts(page),
        "owned": owned_state(owned),
        "dropped_events": trace.dropped,
        "events": trace.events,
    }
    summary = ""
    try:
        text = _serialize_bounded(payload, cap=FILE_CAP)
        summary = _write_artifact(label, text)
    except Exception as cap_err:
        summary = f"stream-diag capture failed: {type(cap_err).__name__}"
    import sys

    exc = sys.exc_info()[1]
    if exc is not None and hasattr(exc, "add_note"):
        try:
            exc.add_note(summary)
        except Exception:
            pass
    return summary


def wait(page, trace: Trace, expression: str, label: str, arg=None) -> None:
    """Same Playwright default deadline. On failure, attach the compact record."""
    try:
        if arg is None:
            page.wait_for_function(expression)
        else:
            page.wait_for_function(expression, arg=arg)
    except Exception as exc:
        summary = dump(page, trace, label)
        raise AssertionError(label + "\n" + summary) from exc
