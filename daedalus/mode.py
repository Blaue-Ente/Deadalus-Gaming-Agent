"""Daedalus conversation mode — talk / build / auto.

A small persistent switch that controls how Daedalus reacts to messages:

  • ``talk``  — pure conversation. Daedalus NEVER emits NEED_* markers and is
    routed straight to the cheap chat model. He just discusses, advises, and
    can *suggest* switching to build mode — but never starts working.
  • ``build`` — work mode. Daedalus always runs the full agent loop so he can
    act (code, query, propose, Playground) even when a message looks chatty.
  • ``auto``  — the Navigator decides per message (the original behaviour).

State lives under ``adn:daedalus_mode`` (Replit DB, else ``DAEDALUS_DATA/kv``)
so it survives restarts. Default is ``auto``.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

VALID_MODES = ("talk", "build", "auto")
DEFAULT_MODE = "auto"
_DB_KEY = "adn:daedalus_mode"


def _rdb_get(key: str) -> Any:
    from . import kv
    return kv.get(key)


def _rdb_set(key: str, value: str) -> None:
    from . import kv
    kv.set(key, value)


def get_mode() -> str:
    """Return the current mode (``talk`` | ``build`` | ``auto``)."""
    raw = _rdb_get(_DB_KEY)
    mode = str(raw).strip().lower() if raw else DEFAULT_MODE
    return mode if mode in VALID_MODES else DEFAULT_MODE


def set_mode(mode: str) -> str:
    """Persist a new mode. Returns the normalised value actually stored."""
    norm = str(mode or "").strip().lower()
    if norm not in VALID_MODES:
        raise ValueError(f"invalid mode '{mode}' — must be one of {VALID_MODES}")
    _rdb_set(_DB_KEY, norm)
    log.info("Daedalus mode set → %s", norm)
    return norm


_LABELS = {
    "talk":  "💬 Разговор (само обсъждаме — без действия)",
    "build": "🔨 Строеж (изпълнявам задачи и пиша код)",
    "auto":  "🔀 Авто (сам преценявам кога да говоря и кога да действам)",
}


def label(mode: str) -> str:
    return _LABELS.get(mode, mode)


def build_mode_context(mode: str) -> str:
    """A short runtime block injected into the persona so Daedalus knows the
    active mode and behaves accordingly (and can suggest switching)."""
    if mode == "talk":
        return (
            "━━━ АКТИВЕН РЕЖИМ: 💬 РАЗГОВОР ━━━\n"
            "Николай иска САМО да разговаряте сега. Говори естествено, с мнение, "
            "като колега-инженер. НЕ емитирай НИКАКВИ NEED_* маркери, НЕ пиши код, "
            "НЕ търси в базата, НЕ строй нищо. Ако усетиш, че вече има ясна задача "
            "за изпълнение, можеш да предложиш: «Искаш ли да минем в режим Строеж?» "
            "— но изчакай неговото потвърждение."
        )
    if mode == "build":
        return (
            "━━━ АКТИВЕН РЕЖИМ: 🔨 СТРОЕЖ ━━━\n"
            "Николай е в работен режим — действай. Изпълнявай задачите докрай, "
            "емитирай нужните NEED_* маркери, пиши реален код и довършвай. "
            "Ако нещо се счупи — поправи се сам и опитай пак, не спирай на половин път."
        )
    return (
        "━━━ АКТИВЕН РЕЖИМ: 🔀 АВТО ━━━\n"
        "Сам преценявай: при въпрос/мнение/дискусия → говори естествено без маркери; "
        "при ясна задача → действай. Можеш да предложиш ръчно превключване, ако се "
        "колебаеш накъде клони разговорът."
    )
