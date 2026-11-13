"""SubQ Snap as a second judge for Daedalus's yes/no decisions, in shadow mode (owner, 2026-11-13).

Daedalus decides some questions with word lists: is this request a game (the game gates, sfx.js and the handbook
follow from it), and does the owner ask for paid art or sound. Word lists miss Bulgarian refusals ("без AI
графика"), games about AI, and follow-ups that never say "game". SubQ Snap (``POST /v1/systemone``) answers such
questions as a probability of yes in about 0.7 s and bills only the input tokens.

**Shadow mode:** the current decision stays in charge. ``shadow`` asks SubQ the same question in the background
(a daemon thread: never a delay for the turn) and appends both answers to ``data/subq_shadow.jsonl``; ``summary``
says how often they agree and lists the disagreements, so the owner can decide whether SubQ takes over. Nothing
happens without a key (``SUBQ_API_KEY``: the owner's in the environment, a team member's own), with
``DAEDALUS_SUBQ_SHADOW=0``, or for a text already judged in this process. Never raises.

Setting the key is the consent: the request text (at most 4,000 characters) then goes to api.subq.ai.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

URL = "https://api.subq.ai/v1/systemone"
MODEL = "subq-snap-1.2"
TIMEOUT = 8.0
YES = 0.5
MAX_STATE = 4000
_SEEN_MAX = 512
MAX_ROWS = 5000            # the shadow log keeps the latest rows: summary reads it whole
_seen: OrderedDict[tuple[str, str], None] = OrderedDict()
_lock = threading.Lock()

# The questions, worded once. Each: (instructions, criteria for true, criteria for false).
QUESTIONS: dict[str, tuple[str, str, str]] = {
    "is_game": (
        "Is this a request to build, continue or change a playable game?",
        "A game, or a change to a game (its controls, levels, art, sound, difficulty, bugs)",
        "An app, a website, a tool, a question or chat that is not about a game"),
    "wants_paid_assets": (
        "Does the owner ask Daedalus to spend money on AI-generated art, images, sound effects, music or voice?",
        "Asks for generated or AI art, images, sound effects, music, voice-over or ElevenLabs",
        "Does not ask, or refuses paid or AI-generated assets, or the game is only about AI"),
}


def key() -> str:
    from .credentials import env
    return env("SUBQ_API_KEY")


_OFF = {"0", "false", "off", "no"}


def switch_on() -> bool:
    """The owner's switch (Settings and the onboarding): DAEDALUS_SUBQ_SHADOW pins it, else the saved choice, else on."""
    env = (os.environ.get("DAEDALUS_SUBQ_SHADOW") or "").strip().lower()
    if env:
        return env not in _OFF
    try:
        from . import settings_file
        saved = settings_file.load().get("subq_shadow")
        return True if saved is None else bool(saved)
    except Exception:
        return True


def set_switch(on: bool) -> None:
    from . import settings_file
    settings_file.update({"subq_shadow": bool(on)})


def enabled() -> bool:
    return switch_on() and bool(key())


def ask(state: str, names: list[str], *, api_key: str = "") -> dict[str, float] | None:
    """{question: probability of yes} from SubQ Snap, or None (no key, an error, a timeout). Never raises."""
    api_key = api_key or key()
    names = [n for n in names if n in QUESTIONS]
    if not api_key or not names or not (state or "").strip():
        return None
    body = {"model": MODEL, "state": state[:MAX_STATE],
            "questions": {n: {"type": "noul", "instructions": QUESTIONS[n][0],
                              "criteria": {"true": QUESTIONS[n][1], "false": QUESTIONS[n][2]}} for n in names}}
    try:
        import httpx

        res = httpx.post(URL, json=body, timeout=TIMEOUT,
                         headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
        if res.status_code != 200:
            log.info("subq: HTTP %s", res.status_code)
            return None
        answers = res.json().get("answers") or {}
        return {n: float(answers[n]["noul"]) for n in names if isinstance(answers.get(n), dict)}
    except Exception:
        log.info("subq: request failed", exc_info=True)
        return None


def _path() -> Path:
    from .paths import data_root
    return data_root() / "subq_shadow.jsonl"


def _record(row: dict[str, Any]) -> None:
    try:
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            if path.stat().st_size > MAX_ROWS * 400:
                lines = path.read_text(encoding="utf-8").splitlines()
                if len(lines) > MAX_ROWS:
                    path.write_text("\n".join(lines[-MAX_ROWS:]) + "\n", encoding="utf-8")
    except OSError:
        log.info("subq: shadow row not written", exc_info=True)


def _first_time(name: str, text: str) -> bool:
    k = (name, text)
    with _lock:
        if k in _seen:
            return False
        _seen[k] = None
        while len(_seen) > _SEEN_MAX:
            _seen.popitem(last=False)
        return True


def shadow(name: str, text: str, current: bool, *, wait: bool = False) -> threading.Thread | None:
    """Ask SubQ ``name`` about ``text`` beside the current decision and record both. Returns the thread (tests
    join it), or None when nothing is asked. ``wait`` runs it in this thread."""
    text = (text or "").strip()
    if name not in QUESTIONS or not text or not enabled() or not _first_time(name, text):
        return None
    api_key = key()          # read here: the turn's member (a ContextVar) is not visible in the new thread

    def run() -> None:
        t0 = time.monotonic()
        got = ask(text, [name], api_key=api_key)
        p = (got or {}).get(name)
        _record({"at": int(time.time()), "question": name, "text": text[:300], "current": bool(current),
                 "subq": None if p is None else round(p, 4),
                 "agree": None if p is None else (p >= YES) == bool(current),
                 "ms": int((time.monotonic() - t0) * 1000)})

    if wait:
        run()
        return None
    th = threading.Thread(target=run, name=f"subq-shadow-{name}", daemon=True)
    th.start()
    return th


def summary(limit: int = 20) -> dict[str, Any]:
    """Per question: how many judged, how often SubQ agreed, its failures, and the latest disagreements."""
    out: dict[str, Any] = {"enabled": enabled(), "questions": {}}
    try:
        lines = _path().read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        q = out["questions"].setdefault(row.get("question", "?"),
                                        {"judged": 0, "agree": 0, "failed": 0, "disagreements": []})
        if row.get("agree") is None:
            q["failed"] += 1
            continue
        q["judged"] += 1
        if row["agree"]:
            q["agree"] += 1
        else:
            q["disagreements"].append({k: row.get(k) for k in ("at", "text", "current", "subq")})
    for q in out["questions"].values():
        q["agreement"] = round(q["agree"] / q["judged"], 3) if q["judged"] else None
        q["disagreements"] = q["disagreements"][-limit:]
    return out
