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
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if len(text) > FILE_CAP:
        text = text[: FILE_CAP - 1] + "…"
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    global _SEQ
    _SEQ += 1
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in label)[:48]
    (ARTIFACT_DIR / f"{safe}-{_SEQ}.json").write_text(text, encoding="utf-8")
    _prune()
    return text


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
