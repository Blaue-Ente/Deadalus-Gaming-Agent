"""Match model effort to task complexity.

Whisper stays cheap. Craft keeps the owner's configured model.
Epic may lift to a stronger coder/reasoner for this call only —
it never silently ``ollama pull`` and never persists a wardrobe change.
"""
from __future__ import annotations

import contextvars
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
        note = "Кратко. Евтин/малък модел. Без строеж, освен ако поискаш."
    elif tier == "epic":
        role, budget, min_stages, world = "reason", "quality", 12, True
        apply_model = True
        note = "Епос. По-силен модел за този ход. Свят, глави, персонажи — на парчета."
    else:
        role, budget, min_stages, world = "code", "balanced", 6, False
        apply_model = False
        note = "Занаят. Остава избраният ти модел. Една играема среза, после разширяване."

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
        "label": {"whisper": "Шепот", "craft": "Занаят", "epic": "Епос"}[tier],
    }
    decision.update(resolve_model(decision))
    return decision


def resolve_model(decision: dict[str, Any]) -> dict[str, Any]:
    """Pick a model hint. Empty model = keep get_config()."""
    if not decision.get("apply_model"):
        return {"model": None, "model_source": "owner-default"}
    tier = decision.get("tier")
    role = str(decision.get("role") or "code")
    try:
        from .llm_wardrobe import CATALOG, installed_ollama_models, load, wants_local

        data = load()
        if wants_local():
            installed = installed_ollama_models()
            roles = dict(data.get("roles") or {})
            pinned = roles.get(role)
            if pinned:
                return {"model": pinned, "provider": "ollama", "model_source": "wardrobe-role"}
            picked = None
            for row in CATALOG:
                if row["role"] != role:
                    continue
                name = row["ollama"]
                have = any(n == name or n.startswith(name) or name in n for n in installed)
                if have:
                    picked = name
            if picked:
                return {"model": picked, "provider": "ollama", "model_source": "wardrobe-installed"}
            if data.get("active"):
                return {"model": data["active"], "provider": "ollama", "model_source": "wardrobe-active"}
    except Exception:
        pass
    if tier == "whisper":
        return {"model": None, "model_source": "cheap-path"}
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
                out = {"model": mid, "model_source": "registry"}
                prov = str((entry or {}).get("provider") or "").strip()
                if prov:
                    out["provider"] = prov
                return out
        except Exception:
            pass
    return {"model": None, "model_source": "owner-default"}


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
        "Реални assets/*.png. После разширявай."
    )
