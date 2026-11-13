"""Paid art and sound, on the user's own key, only when they are wanted (gap 4b).

Owner, 2026-11-12 (plan approved): sound and art are free and procedural by default (game_juice); a paid service
(ElevenLabs for sound effects, an image model for pictures) runs only

- in an epic turn, or
- when the owner's words for this turn ask for it (generated or AI art, images, sound effects, a voice, ElevenLabs),

and always on the key of whoever this turn runs for: the owner's, or a team member's own (credentials.env: a member
never spends the owner's key on these direct calls). Every paid call is shown in the Studio activity with the
service, whose key paid and what is known of its cost.
"""
from __future__ import annotations

import logging
import re
from contextvars import ContextVar
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

ELEVENLABS_SFX_URL = "https://api.elevenlabs.io/v1/sound-generation"
MAX_SECONDS = 10.0
_TURN_WORDS: ContextVar[str] = ContextVar("daedalus_turn_words", default="")
_NAME = re.compile(r"[^a-z0-9_-]+")
# The owner's words that ask for a paid service, English and Bulgarian. Narrow on purpose (review 2026-11-12):
# "procedurally generated music", "real sound physics" or a game about AI are not a request to pay.
PAID_WORDS = re.compile(
    r"eleven\s*labs"
    r"|\bai[\s-]+(?:generated\s+)?(?:art|artwork|images?|pictures?|sprites?|graphics|sounds?|sound\s+effects|sfx|"
    r"voices?|voice[\s-]?over|music)\b"
    r"|\b(?:generated|paid)\s+(?:art|artwork|images?|sprites?|sound\s+effects|sfx|voices?)\s+(?:with|from|by|via)\b"
    r"|\bimage\s+model\b|\bvoice[\s-]?over\b|\bpaid\s+(?:art|assets|sounds?|sound\s+effects)\b"
    r"|\bai\s+графика|\bгенерирани?\s+с\s+(?:ии|ai|изкуствен)|\bозвучи\w*|\bозвучаване",
    re.I)
# A request that says no: "no AI art", "without ElevenLabs", "без AI графика", "не искам озвучаване".
_NEGATION = re.compile(r"\b(?:no|not|without|never|don'?t|dont|avoid|skip|без|не|няма|никакв\w*)\W+(?:\w+\W+){0,2}$",
                       re.I)


def asks_for_paid(text: str) -> bool:
    """Do these words ask for a paid service (and not refuse one)?"""
    for m in PAID_WORDS.finditer(text or ""):
        if not _NEGATION.search(text[max(0, m.start() - 40):m.start()]):
            return True
    return False


def set_turn_words(text: str):
    """The owner's words for this whole turn (mentor_lead.take_request clears its copy after the first read). Set once,
    by the turn's first chat: a nested chat (the planner, a subagent, a self-review) keeps the owner's words, so a
    step that mentions 'AI art' never becomes permission (review 2026-11-12). Returns None when already set."""
    if _TURN_WORDS.get():
        return None
    words = (text or "").strip()
    from .subq_judge import shadow             # SubQ Snap judges the same words in the background (shadow mode)
    shadow("wants_paid_assets", words, asks_for_paid(words))
    return _TURN_WORDS.set(words)


def turn_words() -> str:
    return _TURN_WORDS.get()


def allowed() -> tuple[bool, str]:
    """May this turn spend money on a paid asset? (True, "") or (False, why, said so the writer can tell the owner)."""
    try:
        from .task_effort import current
        dec = current() or {}
        # Epic only when the owner chose it: an epic tier picked automatically from words like 'rpg' is not a yes to
        # spending money (review 2026-11-12).
        if str(dec.get("tier") or "") == "epic" and str(dec.get("source") or "") == "owner":
            return True, ""
    except Exception:
        pass
    if asks_for_paid(turn_words()):
        return True, ""
    return False, ("Paid art and sound run only in an epic turn or when the owner asks for them (for example "
                   "'generated sound effects' or 'AI art'). Keep using the free ones: sfx.js and art drawn in code.")


def _whose() -> str:
    from .team import current
    m = current()
    return "owner" if m is None or m.role == "owner" else f"{m.name} ({m.role})"


def ledger(service: str, *, ok: bool, what: str, cost: str, path: str = "", error: str = "") -> None:
    """One activity row in the Studio chat for every paid call, made or refused. Never raises."""
    try:
        from .harness import runs
        rid = runs.current_run_id()
        if rid:
            runs.safe_event(rid, "paid_asset", {"service": service, "ok": ok, "what": what[:160], "cost": cost,
                                                "whose": _whose(), "path": path, "error": error[:200]})
    except Exception:
        log.debug("paid_asset event skipped", exc_info=True)


def clean_name(name: str) -> str:
    base = re.sub(r"\.(mp3|wav|ogg|m4a)$", "", (name or "").strip().lower())
    return (_NAME.sub("-", base).strip("-") or "sound")[:40]


def _free_rel(root: Path, stem: str) -> str:
    """assets/sfx/<stem>.mp3, or <stem>-2.mp3 and so on: a paid sound never replaces an earlier one."""
    rel = f"assets/sfx/{stem}.mp3"
    n = 2
    while (root / rel).exists():
        rel = f"assets/sfx/{stem}-{n}.mp3"
        n += 1
    return rel


async def generate_sound(slug: str, name: str, prompt: str, seconds: float = 2.0) -> dict[str, Any]:
    """An ElevenLabs sound effect saved as assets/sfx/<name>.mp3 in the project. Never raises."""
    ok, why = allowed()
    if not ok:
        ledger("elevenlabs", ok=False, what=prompt, cost="none", error="not requested")
        return {"ok": False, "error": "not_requested", "message": why}
    from .credentials import env

    key = env("ELEVENLABS_API_KEY")
    if not key:
        msg = ("No ElevenLabs key for this turn: add ELEVENLABS_API_KEY (the owner under Connectors, a team member "
               "under Settings → Team → Your keys). The free sfx.js keeps working.")
        ledger("elevenlabs", ok=False, what=prompt, cost="none", error="no key")
        return {"ok": False, "error": "no_key", "message": msg}
    from . import playground as pg

    root = pg.project_dir(slug) if slug else None
    if root is None or not root.is_dir():
        ledger("elevenlabs", ok=False, what=prompt, cost="none", error="no project")
        return {"ok": False, "error": "no_project", "message": f"No project {slug!r}."}
    try:
        seconds = max(0.5, min(float(seconds or 2.0), MAX_SECONDS))
    except (TypeError, ValueError):
        seconds = 2.0
    rel = _free_rel(Path(root), clean_name(name))
    try:
        import httpx

        async with httpx.AsyncClient(timeout=60.0) as client:
            res = await client.post(ELEVENLABS_SFX_URL, headers={"xi-api-key": key},
                                    json={"text": prompt[:450], "duration_seconds": seconds,
                                          "prompt_influence": 0.4})
        if res.status_code != 200 or not res.content:
            detail = res.text[:200] if res.status_code != 200 else "empty reply"
            ledger("elevenlabs", ok=False, what=prompt, cost="unknown", error=f"HTTP {res.status_code}: {detail}")
            return {"ok": False, "error": f"http_{res.status_code}", "message": detail}
    except Exception as exc:
        ledger("elevenlabs", ok=False, what=prompt, cost="unknown", error=str(exc))
        return {"ok": False, "error": "request_failed", "message": str(exc)[:200]}
    cost = f"{seconds:.1f}s of sound on {_whose()}'s ElevenLabs credits"
    try:
        dest = Path(root) / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(res.content)
    except OSError as exc:                       # paid for and not saved: said, never silent
        ledger("elevenlabs", ok=False, what=prompt, cost=cost, error=f"paid but not saved: {exc}")
        return {"ok": False, "error": "not_saved", "message": f"The sound was made ({cost}) but not saved: {exc}"}
    ledger("elevenlabs", ok=True, what=prompt, cost=cost, path=rel)
    return {"ok": True, "path": rel, "seconds": seconds, "bytes": len(res.content), "cost": cost,
            "play": f"sfx.file('{rel}')"}
