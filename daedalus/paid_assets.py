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
# The owner's words that ask for a paid service: English and Bulgarian.
PAID_WORDS = re.compile(
    r"eleven\s*labs|\b(?:ai|generated?|real)\s+(?:art|image|images|picture|pictures|sprites?|sound|sounds|sfx|"
    r"audio|voice|music)\b|\bimage\s+model\b|\bvoice[\s-]?over\b|\bsound\s+effects?\s+(?:from|with|by)\b"
    r"|генерира\w*\s+(?:звук|изображени|картин|спрайт|глас|музик)|\bai\s+графика|\bозвуч\w*|\bизкуствен\w*\s+интелект",
    re.I)


def set_turn_words(text: str):
    """The owner's words for this whole turn (mentor_lead.take_request clears its copy after the first read)."""
    return _TURN_WORDS.set((text or "").strip())


def turn_words() -> str:
    return _TURN_WORDS.get()


def allowed() -> tuple[bool, str]:
    """May this turn spend money on a paid asset? (True, "") or (False, why, said so the writer can tell the owner)."""
    try:
        from .task_effort import current
        if str((current() or {}).get("tier") or "") == "epic":
            return True, ""
    except Exception:
        pass
    if PAID_WORDS.search(turn_words()):
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
    return (_NAME.sub("-", (name or "").strip().lower()).strip("-") or "sound")[:40]


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
        return {"ok": False, "error": "no_project", "message": f"No project {slug!r}."}
    seconds = max(0.5, min(float(seconds or 2.0), MAX_SECONDS))
    rel = f"assets/sfx/{clean_name(name)}.mp3"
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
    dest = Path(root) / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(res.content)
    cost = f"{seconds:.1f}s of sound on {_whose()}'s ElevenLabs credits"
    ledger("elevenlabs", ok=True, what=prompt, cost=cost, path=rel)
    return {"ok": True, "path": rel, "seconds": seconds, "bytes": len(res.content), "cost": cost,
            "play": f"sfx.file('{rel}')"}
