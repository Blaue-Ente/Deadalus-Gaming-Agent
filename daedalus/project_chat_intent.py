"""Project chat: does the owner's message ask for a change, or only for an answer?

Found live (2026-11-11): every project-chat message ran the build loop, whose review gate wants a completed slice,
so a question ("what does the game do?") still rewrote index.html. Owner decision (2026-11-11): when the message
does not ask for a change, the turn only reads and answers. ``answering(slug)`` marks such a turn; while it is set,
every playground write raises ``ReadOnlyTurn`` and only read tools run.

The classifier reads the owner's own words (never the injected file context). When in doubt it builds: a change
verb anywhere wins, unless the message is plainly a question ("how do I add sound?").
"""
from __future__ import annotations

import contextlib
import re
from contextvars import ContextVar
from typing import Any, Iterator

_ANSWERING: ContextVar[str] = ContextVar("daedalus_project_chat_answering", default="")

# Tools that never change a project. Brain tools with both kinds of action are limited to their read actions.
READ_TOOLS = frozenset({"read_file", "list_dir", "game_lint", "milestones", "runs", "preview"})
READ_ACTIONS: dict[str, frozenset[str]] = {
    "milestones": frozenset({"state", "gates"}),
    "runs": frozenset({"list", "get", "events"}),
    "preview": frozenset({"status", "logs"}),
}

_EN_CHANGE = (
    "make", "add", "change", "fix", "create", "build", "write", "implement", "remove", "delete", "update",
    "replace", "rename", "refactor", "improve", "adjust", "tweak", "increase", "decrease", "move", "finish",
    "continue", "convert", "translate", "polish", "redesign", "rewrite", "port", "set up", "speed up", "slow down",
    "turn", "put", "use", "insert", "rework", "redo", "undo", "revert", "generate", "draw", "animate", "colour",
    "color", "resize", "swap", "enable", "disable",
)
_BG_CHANGE = (
    "направ", "добав", "промен", "оправ", "поправ", "създа", "напиш", "напис", "махн", "премахн", "смен", "сложи",
    "сложете", "реализ", "имплемент", "подобр", "преправ", "пренапиш", "изтри", "преименув", "увелич", "намал",
    "ускор", "забав", "нагласи", "настрой", "обнов", "рефактор", "построй", "довърш", "завърш", "продълж",
    "преведи", "оцвет", "анимир", "генерир", "нарисув", "върни", "размени", "включи", "изключи", "използвай",
    "ползвай", "вкарай", "оцвети", "преобраз", "изгради",
)
_CHANGE_RE = re.compile(
    r"(?<!\w)(?:" + "|".join(re.escape(w) for w in _EN_CHANGE) + r")(?:s|es|ed|ing)?(?!\w)"
    r"|(?<!\w)(?:" + "|".join(re.escape(w) for w in _BG_CHANGE) + r")\w*",
    re.I,
)
_QUESTION_START = re.compile(
    r"^\s*(?:what|why|how|which|where|when|who|is|are|does|do|did|can i|should i|"
    r"какв\w*|защо|как|кой|коя|кое|кои|къде|кога|дали|има\s+ли|трябва\s+ли|мога\s+ли|бива\s+ли)(?!\w)",
    re.I,
)
# A question that is really a request: "can you add sound?", "можеш ли да добавиш звук?"
_POLITE_REQUEST = re.compile(
    r"(?<!\w)(?:can\s+you|could\s+you|would\s+you|will\s+you|please|pls|"
    r"можеш\s+ли|може\s+ли|би\s+ли|ще\s+(?:можеш|може)|моля)(?!\w)",
    re.I,
)


def wants_change(message: str) -> bool:
    """True when the owner asks Daedalus to change the project; False for a question or small talk."""
    text = (message or "").strip()
    if not text or not _CHANGE_RE.search(text):
        return False
    if _POLITE_REQUEST.search(text):
        return True
    if _QUESTION_START.search(text) and text.rstrip().endswith("?"):
        return False
    return True


class ReadOnlyTurn(PermissionError):
    """A write was attempted during a turn that only answers the owner's question."""


def current() -> str:
    """The project being answered about in this turn, else ''."""
    return _ANSWERING.get()


def build_allowed() -> bool:
    return not _ANSWERING.get()


@contextlib.contextmanager
def answering(slug: str) -> Iterator[None]:
    token = _ANSWERING.set((slug or "").strip() or "?")
    try:
        yield
    finally:
        _ANSWERING.reset(token)


def guard_write(slug: str, rel: str = "") -> None:
    """Called by every playground write. Raises while a question is being answered."""
    bound = _ANSWERING.get()
    if bound:
        raise ReadOnlyTurn(
            f"write refused ({slug}/{rel}): the owner asked a question in this chat, not for a change. "
            "Answer it without changing files."
        )


def tool_refusal(tool: str, args: dict[str, Any] | None) -> str | None:
    """None when the tool may run in this turn; else the refusal text the model sees."""
    if not _ANSWERING.get():
        return None
    if tool in READ_TOOLS:
        allowed = READ_ACTIONS.get(tool)
        action = str((args or {}).get("action") or "").strip().lower()
        if allowed is None or action in allowed:
            return None
    return (f"── {tool} REFUSED: the owner asked a question in this chat, not for a change ──\n"
            "Answer from what you can read (read_file, list_dir). Do not change files, run, play or publish. "
            "If a change would help, describe it and let the owner ask for it.")


def read_only_specs() -> list[dict[str, Any]]:
    from .native_tools import NATIVE_TOOL_SPECS

    return [s for s in NATIVE_TOOL_SPECS if s["name"] in READ_TOOLS]


ANSWER_NOTE = (
    "━━━ ANSWER ONLY ━━━\n"
    "The owner asked a question about this project. Read what you need and answer it. Do not change, create or "
    "delete files, and do not start, play or publish anything. If a change would help, describe it briefly and "
    "let the owner ask for it."
)

ANSWER_SYSTEM = (
    "You are Daedalus answering the owner's question about one Playground project. Use read_file and list_dir to "
    "look at the project's files. Never change files. When you know enough, stop calling tools."
)
