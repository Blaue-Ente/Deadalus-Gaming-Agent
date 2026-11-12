"""What real players do with a published game: anonymous session counts, for the owner and the next build.

Owner, 2026-11-12 (gap 5): simulated players judge a game before it ships, but nobody measured the people who then
played it. A game served from ``/apps/<slug>/`` now reports one small record per visit, sent once when the page is
left:

- how long the visit lasted, how soon the player first pressed a key or clicked, how many inputs;
- the first JavaScript errors (message only);
- events the game names itself with ``PlaygroundAnalytics.track("score")`` (counts only).

A visit sends its record again, cumulatively, each time the page is hidden; a random per-visit id (new on every page
load, never stored in the browser) lets the server replace the earlier copy. No IP address, user agent, cookie or
lasting identifier is stored; a browser that sends Do Not Track sends nothing. Records stay in this Daedalus's KV
store (the last ``MAX_SESSIONS`` per game). Games published elsewhere (Netlify, GitLab Pages) are not measured: that
needs a public collector, an owner decision.

Anyone who can load the page can send records, so the numbers are signals, not proof: a sender who spreads across
many addresses can, within the per-game limit (``RATE_PER_MINUTE``), replace the stored visits with invented ones.
The owner's summary and the builder's prompt say so. Their text is untrusted too: the builder's prompt gets the
numbers and event names restricted to ``[A-Za-z0-9_.:-]``, never error messages (review 2026-11-12: a forged beacon
would otherwise put words in the agent's prompt). The owner sees the messages in the summary.
"""
from __future__ import annotations

import json
import re
import statistics
import time
from collections import Counter, deque
from typing import Any

MAX_SESSIONS = 500
MAX_BODY_BYTES = 4096
MAX_EVENTS = 20
RATE_PER_MINUTE = 120            # records per game per minute; more is dropped (a loop or a flood, not players)
CLIENT_PER_MINUTE = 12           # per client address (held in memory for a minute, never stored)
MIN_FOR_PROMPT = 3
_MAX_MS = 6 * 3600 * 1000
_recent: dict[str, deque[float]] = {}
_EVENT_NAME = re.compile(r"^[A-Za-z0-9_.:-]{1,40}$")
_VISIT_ID = re.compile(r"^[a-z0-9]{8,16}$")

SNIPPET = """<script>
/* Daedalus play analytics: anonymous, one record per visit (no cookies, no lasting ids) */
(function(){
  if (navigator.doNotTrack === '1' || window.doNotTrack === '1') {
    window.PlaygroundAnalytics = {track: function(){}}; return;
  }
  var slug = %(slug)s, t0 = Date.now(), first = null, keys = 0, clicks = 0, errs = [], ev = {}, n = 0;
  var v = Math.random().toString(36).slice(2, 14), last = '';
  function mark(){ if (first === null) first = Date.now() - t0; }
  addEventListener('keydown', function(){ keys++; mark(); }, true);
  addEventListener('pointerdown', function(){ clicks++; mark(); }, true);
  addEventListener('error', function(e){ if (errs.length < 3) errs.push(String(e.message || 'error').slice(0, 160)); });
  window.PlaygroundAnalytics = {track: function(name){
    name = String(name).slice(0, 40);
    if (ev[name] !== undefined || n < %(max_events)d) { if (ev[name] === undefined) n++; ev[name] = (ev[name] || 0) + 1; }
  }};
  function send(){            /* cumulative: the server keeps the latest copy of this visit */
    var body = JSON.stringify({v: v, ms: Date.now() - t0, first_input_ms: first, keys: keys, clicks: clicks,
                               errors: errs, events: ev});
    var same = body.replace(/"ms":\\d+,/, '');
    if (same === last) return; last = same;
    try { navigator.sendBeacon('/apps/' + encodeURIComponent(slug) + '/analytics',
                               new Blob([body], {type: 'application/json'})); } catch (e) {}
  }
  addEventListener('pagehide', send);
  document.addEventListener('visibilitychange', function(){ if (document.visibilityState === 'hidden') send(); });
})();
</script>"""


def snippet(slug: str) -> str:
    return SNIPPET % {"slug": json.dumps(slug), "max_events": MAX_EVENTS}


def _key(slug: str) -> str:
    from . import playground as pg
    return f"playground:analytics:{pg.slugify(slug)}"


def _store():
    from .kv import db
    return db


def _int(val: Any, hi: int) -> int | None:
    try:
        num = int(val)
    except (TypeError, ValueError, OverflowError):        # JSON allows Infinity and 1e400
        return None
    return max(0, min(num, hi))


def clean(body: Any) -> dict[str, Any] | None:
    """One visit record from untrusted JSON: numbers clamped, strings capped, nothing else kept."""
    if not isinstance(body, dict):
        return None
    ms = _int(body.get("ms"), _MAX_MS)
    if ms is None:
        return None
    first = _int(body.get("first_input_ms"), _MAX_MS) if body.get("first_input_ms") is not None else None
    errors = [str(e)[:160] for e in (body.get("errors") or [])[:3]] if isinstance(body.get("errors"), list) else []
    events: dict[str, int] = {}
    if isinstance(body.get("events"), dict):
        for name, count in list(body["events"].items())[:MAX_EVENTS]:
            num = _int(count, 1_000_000)
            if num is not None and _EVENT_NAME.match(str(name)):
                events[str(name)] = num
    visit = str(body.get("v") or "")
    return {"at": int(time.time()), "v": visit if _VISIT_ID.match(visit) else "", "ms": ms, "first_input_ms": first,
            "keys": _int(body.get("keys"), 1_000_000) or 0, "clicks": _int(body.get("clicks"), 1_000_000) or 0,
            "errors": errors, "events": events}


def client_key(headers: Any, peer: str) -> str:
    """Whom the per-client limit counts. Behind a hosting proxy every player arrives from the proxy's address, so the
    first forwarded address is used when there is one (review 2026-11-12). It can be forged, which only spreads one
    sender over more client buckets: the per-game limit still holds. Held in memory for a minute, never stored."""
    forwarded = str(headers.get("fly-client-ip") or headers.get("x-forwarded-for") or "").split(",")[0].strip()
    return forwarded[:64] or peer


def _within(bucket: str, limit: int, now: float) -> bool:
    window = _recent.setdefault(bucket, deque())
    while window and now - window[0] > 60:
        window.popleft()
    return len(window) < limit


def _allowed(key: str, client: str, now: float) -> bool:
    """One budget per game and a smaller one per client, so one sender cannot fill a game's buffer alone."""
    if len(_recent) > 5000:                                  # forget idle buckets (addresses are never stored)
        for bucket in [b for b, w in _recent.items() if not w or now - w[-1] > 60]:
            _recent.pop(bucket, None)
    buckets = [(key, RATE_PER_MINUTE)] + ([(f"{key}|{client}", CLIENT_PER_MINUTE)] if client else [])
    if not all(_within(b, limit, now) for b, limit in buckets):
        return False
    for b, _ in buckets:
        _recent[b].append(now)
    return True


def record(slug: str, raw: bytes, *, client: str = "") -> tuple[bool, str]:
    """Store one visit, or replace the earlier copy of the same visit. (stored, reason). Blocking: call off the loop."""
    if len(raw or b"") > MAX_BODY_BYTES:
        return False, "too_large"
    try:
        row = clean(json.loads(raw or b"null"))
    except (ValueError, RecursionError):
        row = None
    if row is None:
        return False, "bad_record"
    key = _key(slug)
    if not _allowed(key, client, time.monotonic()):
        return False, "rate_limited"
    db = _store()
    rows = db.get(key) or []
    rows = list(rows) if isinstance(rows, list) else []
    if row["v"]:
        for i in range(len(rows) - 1, max(-1, len(rows) - 51), -1):      # the same visit, sent again: replace it
            if isinstance(rows[i], dict) and rows[i].get("v") == row["v"]:
                del rows[i]
                break
    db[key] = (rows + [row])[-MAX_SESSIONS:]
    return True, ""


def sessions(slug: str) -> list[dict[str, Any]]:
    rows = _store().get(_key(slug)) or []
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _pct(part: int, whole: int) -> int:
    return round(100 * part / whole) if whole else 0


def summary(slug: str) -> dict[str, Any]:
    """What the players did, in numbers the owner and the builder can act on."""
    rows = sessions(slug)
    n = len(rows)
    if not n:
        return {"sessions": 0}
    durations = [r.get("ms") or 0 for r in rows]
    firsts = [r["first_input_ms"] for r in rows if r.get("first_input_ms") is not None]
    no_input = sum(1 for r in rows if r.get("first_input_ms") is None)
    errors = Counter(e for r in rows for e in (r.get("errors") or []))
    events: Counter[str] = Counter()
    for r in rows:
        events.update(r.get("events") or {})
    return {
        "sessions": n,
        "note": "anonymous records: anyone who can load the game can send one, so treat these as signals",
        "since": min(int(r.get("at") or 0) for r in rows),
        "median_s": round(statistics.median(durations) / 1000, 1),
        "left_within_10s_pct": _pct(sum(1 for d in durations if d < 10_000), n),
        "no_input_pct": _pct(no_input, n),
        "median_first_input_s": round(statistics.median(firsts) / 1000, 1) if firsts else None,
        "error_sessions_pct": _pct(sum(1 for r in rows if r.get("errors")), n),
        "top_errors": [{"message": m, "sessions": c} for m, c in errors.most_common(3)],
        "events_per_session": {k: round(v / n, 2) for k, v in events.most_common(8)},
    }


def format_for_prompt(slug: str) -> str:
    """For the next build of this game: what its real players did. Empty below ``MIN_FOR_PROMPT`` visits."""
    s = summary(slug)
    if s.get("sessions", 0) < MIN_FOR_PROMPT:
        return ""
    lines = [f"━━━ REAL PLAYERS (/apps, last {s['sessions']} visits) ━━━",
             f"- median visit {s['median_s']}s; {s['left_within_10s_pct']}% left within 10s; "
             f"{s['no_input_pct']}% never pressed a key or clicked"
             + (f"; first input after {s['median_first_input_s']}s (median)" if s.get("median_first_input_s") else "")]
    if s["error_sessions_pct"]:          # counts only: the messages come from anyone and stay with the owner
        lines.append(f"- {s['error_sessions_pct']}% of visits hit a JavaScript error (the owner can see the messages "
                     "in the analytics summary; check the game's error handling)")
    if s["events_per_session"]:
        lines.append("- game events per visit: " + ", ".join(f"{k} {v}" for k, v in s["events_per_session"].items()))
    lines.append("These are anonymous signals (anyone can send a record), not proof. Use them when they bear on the "
                 "request (a game players leave in 10s needs a clearer start); do not invent reasons for them.")
    return "\n".join(lines)
