"""The game quality handbook, given to the writer for every game build (owner, 2026-11-13: "make the handbook
useful and usable").

Before this the handbook (``daedalus/knowledge``) was indexed for the Mentor's RAG only: the writer, who makes the
game, never read it. ``knowledge/game_quality.md`` holds checkable rules: a **Core** section for every game, one
section per genre, and a closing checklist. ``for_request`` picks Core, the genres the request names (by the
``Words:`` line of each section: whole English words, Bulgarian stems marked ``*``; at most two genres) and the checklist, and keeps the block within
``LIMIT`` characters so it never crowds the plan out of the prompt.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

PATH = Path(__file__).resolve().parent / "knowledge" / "game_quality.md"
LIMIT = 5200
MAX_GENRES = 2
_ALWAYS = ("Core", "Checklist before done")


@lru_cache(maxsize=1)
def sections() -> tuple[tuple[str, tuple[str, ...], str], ...]:
    """((title, words, body), ...) in file order. Empty when the file is missing."""
    try:
        text = PATH.read_text(encoding="utf-8")
    except OSError:
        return ()
    out = []
    for chunk in re.split(r"^## ", text, flags=re.M)[1:]:
        title, _, rest = chunk.partition("\n")
        words: tuple[str, ...] = ()
        m = re.match(r"\s*Words:\s*(.+)\n", rest)
        if m:
            words = tuple(w.strip().lower() for w in m.group(1).split(",") if w.strip())
            rest = rest[m.end():]
        out.append((title.strip(), words, rest.strip()))
    return tuple(out)


def _word(w: str) -> str:
    """A whole word; 'стрел*' is a stem (Bulgarian words change their endings: стрелба, стрелят, стрелец)."""
    if w.endswith("*"):
        return rf"(?<![\w-]){re.escape(w[:-1])}\w*"
    return rf"(?<![\w-]){re.escape(w)}(?![\w-])"


def genres_for(text: str) -> list[str]:
    """The genre sections the words name, most mentions first (whole words, so 'card' is not 'discard')."""
    low = (text or "").lower()
    scored = []
    for i, (title, words, _body) in enumerate(sections()):
        if title in _ALWAYS:
            continue
        hits = sum(len(re.findall(_word(w), low)) for w in words)
        if hits:
            scored.append((-hits, i, title))
    return [title for _h, _i, title in sorted(scored)[:MAX_GENRES]]


def for_request(text: str, *, limit: int = LIMIT) -> str:
    """The handbook block for a game request: Core, its genres, the checklist. '' when the file is missing."""
    by_title = {title: body for title, _w, body in sections()}
    if "Core" not in by_title:
        return ""
    picked = ["Core", *genres_for(text), "Checklist before done"]
    while True:
        block = _HEAD + "\n\n".join(f"## {t}\n{by_title[t]}" for t in picked if t in by_title)
        if len(block) <= limit or len(picked) <= len(_ALWAYS):
            return block[:limit]
        picked.pop(-2)                          # the last genre goes first, never Core or the checklist


_HEAD = "━━━ GAME QUALITY HANDBOOK (rules to keep; check them in your code before you say done) ━━━\n"
