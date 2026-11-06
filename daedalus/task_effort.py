"""Match model effort to task complexity.

Whisper stays cheap. Craft keeps the owner's configured model.
Epic may lift to a stronger coder/reasoner for this call only —
it never silently ``ollama pull`` and never persists a wardrobe change.

When the wardrobe is local, epic uses a cloud provider for this turn if a
cloud key is set and offline mode is off. Whisper stays on the local model.
The default wardrobe model id is not rewritten to "fix" a small context
window; see ``local_prompt`` for the slim Ollama path.
"""
from __future__ import annotations

import contextvars
import os
import re
from contextlib import contextmanager
from typing import Any, Iterator

TIERS = ("whisper", "craft", "epic")
_REQUESTED = {"auto", *TIERS}

_WHISPER = (
    r"\b(здравей|здрасти|hello|hi|hey|благодаря|thanks|мерси|"
    r"как си|какво правиш|ping|pong|статус|status)\b",
    r"^\s*запомни[, ]",
    r"^\s*remember\b",
    r"^\s*\?\s*$",
)
_EPIC = (
    r"\b(необят\w*|простор\w*|open[\s-]?world|кампан\w*|campaign\w*|"
    r"сюжет\w*|персонаж\w*|характер\w*|фракц\w*|faction\w*|"
    r"метроид\w*|metroid\w*|rpg|лорн\w*|lore|епос\w*|epic|"
    r"сага\w*|saga\w*|глава|глави|chapter\w*|quest\w*|квест\w*|"
    r"biome\w*|биом\w*|continent\w*|континент\w*)\b",
    r"\b(сложен свят|vast world|living world|необятен свят|"
    r"multiple (levels|acts)|различни нива|много нива|level graph)\b",
    r"\b(свят|worlds?)\b.{0,48}\b(кампан\w*|персонаж\w*|квест\w*|фракц\w*|сюжет\w*|нива|region)",
)
_CRAFT = (
    r"\b(направи|построй|напиши|оправи|поправи|игра|game|tetris|змия|"
    r"snake|phaser|build|fix|implement|autopilot)\b",
)

_bound: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "daedalus_effort_bind", default=None
)


def _match_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(p, text, re.I) for p in patterns)


def classify(text: str, *, requested: str = "auto") -> dict[str, Any]:
    """Return a public effort decision. Does not touch wardrobe or .env."""
    req = (requested or "auto").strip().lower()
    if req not in _REQUESTED:
        req = "auto"
    blob = (text or "").strip()
    if req in TIERS:
        tier = req
        source = "owner"
    elif _match_any(blob, _EPIC):
        tier = "epic"
        source = "auto"
    elif len(blob) < 48 and _match_any(blob, _WHISPER) and not _match_any(blob, _CRAFT):
        tier = "whisper"
        source = "auto"
    elif not blob:
        tier = "whisper"
        source = "auto"
    elif len(blob) < 28 and _match_any(blob, _WHISPER) and not _match_any(blob, _CRAFT):
        tier = "whisper"
        source = "auto"
    else:
        tier = "craft"
        source = "auto"

    if tier == "whisper":
        role, budget, min_stages, world = "talk", "cheap", 0, False
        apply_model = True
        note = "Short. Cheap/small model. No build unless you ask."
    elif tier == "epic":
        role, budget, min_stages, world = "reason", "quality", 12, True
        apply_model = True
        note = "Epic. Stronger model for this turn. World, chapters, characters — in slices."
    else:
        role, budget, min_stages, world = "code", "balanced", 6, False
        apply_model = False
        note = "Craft. Keeps your chosen model. One playable slice, then expand."

    decision = {
        "ok": True,
        "tier": tier,
        "source": source,
        "role": role,
        "budget": budget,
        "min_stages": min_stages,
        "world_kit": world,
        "apply_model": apply_model,
        "note": note,
        "label": {"whisper": "Whisper", "craft": "Craft", "epic": "Epic"}[tier],
    }
    decision.update(resolve_model(decision))
    return decision


_EPIC_CLOUD_KEYS = (
    "OPENROUTER_API_KEY",
    "ALLTOKEN_API_KEY",
    "NOVITA_API_KEY",
    "NVIDIA_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
)

EPIC_LOCAL_WARNING = (
    "Epic is on the local brain for this turn. "
    "A small Ollama window uses the slim build prompt. "
    "Set a cloud key for epic, or raise OLLAMA_CONTEXT_LENGTH and restart Ollama. "
    "The default wardrobe model is unchanged."
)


def _offline_pinned() -> bool:
    flag = (os.environ.get("DAEDALUS_OFFLINE") or "").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        return True
    try:
        from .llm_wardrobe import load
        return bool(load().get("offline"))
    except Exception:
        return False


def _cloud_key_present() -> bool:
    return any((os.environ.get(key) or "").strip() for key in _EPIC_CLOUD_KEYS)


def _epic_cloud_pick() -> dict[str, Any] | None:
    """Cloud model for this epic turn. None when no key is configured."""
    if not _cloud_key_present():
        return None
    try:
        from .model_registry import select_for_task
        entry = select_for_task(
            "complex game world plan code",
            budget_hint="quality",
            required_caps=["code", "planning", "reasoning"],
        )
    except Exception:
        return None
    mid = (entry or {}).get("model_id")
    if not mid:
        return None
    out: dict[str, Any] = {
        "model": mid,
        "model_source": "epic-cloud",
        "provider_path": "epic-cloud",
    }
    prov = str((entry or {}).get("provider") or "").strip()
    if prov:
        out["provider"] = prov
    return out


def _tag_local(out: dict[str, Any], tier: str | None) -> dict[str, Any]:
    row = dict(out)
    if tier == "whisper":
        row["provider_path"] = "whisper-local"
    elif tier == "epic":
        row["provider_path"] = "local"
        row["warning"] = EPIC_LOCAL_WARNING
    else:
        row["provider_path"] = "local"
    return row


def resolve_model(decision: dict[str, Any]) -> dict[str, Any]:
    """Pick a model hint. Empty model = keep get_config()."""
    if not decision.get("apply_model"):
        return {"model": None, "model_source": "owner-default", "provider_path": "owner"}
    tier = decision.get("tier")
    role = str(decision.get("role") or "code")
    try:
        from .llm_wardrobe import CATALOG, installed_ollama_models, load, wants_local

        # Epic on a local wardrobe lifts to cloud for this call only.
        # Offline stays local. The catalog model id is not rewritten.
        if tier == "epic" and wants_local() and not _offline_pinned():
            cloud = _epic_cloud_pick()
            if cloud:
                return cloud

        data = load()
        if wants_local():
            installed = installed_ollama_models()
            roles = dict(data.get("roles") or {})
            pinned = roles.get(role)
            if pinned:
                return _tag_local(
                    {"model": pinned, "provider": "ollama", "model_source": "wardrobe-role"},
                    str(tier) if tier else None,
                )
            picked = None
            for row in CATALOG:
                if row["role"] != role:
                    continue
                name = row["ollama"]
                have = any(n == name or n.startswith(name) or name in n for n in installed)
                if have:
                    picked = name
            if picked:
                return _tag_local(
                    {"model": picked, "provider": "ollama", "model_source": "wardrobe-installed"},
                    str(tier) if tier else None,
                )
            if data.get("active"):
                return _tag_local(
                    {"model": data["active"], "provider": "ollama", "model_source": "wardrobe-active"},
                    str(tier) if tier else None,
                )
    except Exception:
        pass
    if tier == "whisper":
        return {"model": None, "model_source": "cheap-path", "provider_path": "whisper-local"}
    if tier == "epic":
        try:
            from .model_registry import select_for_task
            entry = select_for_task(
                "complex game world plan code",
                budget_hint="quality",
                required_caps=["code", "planning", "reasoning"],
            )
            mid = (entry or {}).get("model_id")
            if mid:
                out: dict[str, Any] = {
                    "model": mid,
                    "model_source": "registry",
                    "provider_path": "epic-cloud",
                }
                prov = str((entry or {}).get("provider") or "").strip()
                if prov:
                    out["provider"] = prov
                return out
        except Exception:
            pass
    return {"model": None, "model_source": "owner-default", "provider_path": "owner"}


def current() -> dict[str, Any] | None:
    return _bound.get()


def applied_model() -> dict[str, Any] | None:
    dec = _bound.get()
    if not dec or not dec.get("apply_model"):
        return None
    if not dec.get("model"):
        return None
    return dec


@contextmanager
def bind(decision: dict[str, Any] | None) -> Iterator[dict[str, Any] | None]:
    token = _bound.set(decision)
    try:
        yield decision
    finally:
        _bound.reset(token)


def briefing(decision: dict[str, Any]) -> str:
    """Short block injected into the user turn so the model matches effort."""
    tier = decision.get("tier")
    if tier == "whisper":
        return (
            "[EFFORT: whisper] Отговори в 2–6 изречения. Без инструменти, "
            "освен remember / едно status четене. Не започвай игра."
        )
    if tier == "epic":
        return (
            "[EFFORT: epic] Усилие = сложност. Не стъпквай целия свят в един файл. "
            "Пиши/обнови world/bible.md, world/map.json, world/quests.json, characters/, levels/. "
            "Една играема среза на ход. Продължавай през journal / resume. "
            "world({action: prove}) трябва да мине — празен JSON не е кампания. "
            "Definition of Done: loop + регион + персонаж + квест/диалог, не placeholder."
        )
    return (
        "[EFFORT: craft] Една завършена среза: input → update → render → win/lose. "
        "Реални assets/*.png или assets({action:atlas|sfx|font}). После разширявай."
    )
