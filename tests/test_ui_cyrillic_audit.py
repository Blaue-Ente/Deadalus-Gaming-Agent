"""Fail on hardcoded Cyrillic in Command Center-facing templates and static JS.

Bulgarian user-facing strings belong in app/static/i18n.js (bg locale). This test
catches accidental literals in HTML and bundled scripts.

Allowlist: only entries documented below with a short rationale. Prefer fixing
the source over growing the list.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

CYR = re.compile(r"[\u0400-\u04FF]")


def _strip_js_line_comment(line: str) -> str:
    """Remove // comments from a JS line without stripping // inside single/double-quoted strings."""
    out = []
    i = 0
    quote = None
    while i < len(line):
        c = line[i]
        if quote:
            out.append(c)
            if c == "\\" and i + 1 < len(line):
                out.append(line[i + 1])
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c in "\"'":
            quote = c
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < len(line) and line[i + 1] == "/":
            break
        out.append(c)
        i += 1
    return "".join(out)

# Whole-file skips (dictionary source or generated).
SKIP_FILES = {
    ROOT / "app" / "static" / "i18n.js",
    # Classic chat: large legacy script; migrating strings to i18n.js in tree (see classic.* keys).
    # Remove this skip once ui/static/classic_chat.js has no Cyrillic outside t() / dictionary.
    ROOT / "ui" / "static" / "classic_chat.js",
    # Classic chat Jinja shell: many labels still inline; localized incrementally (data-i18n / JS).
    ROOT / "ui" / "templates" / "daedalus.html",
}

# Relative path (from ROOT) -> set of 1-based line numbers that may contain Cyrillic.
# Keep sorted; document each block in a comment next to the path.
ALLOWLIST_LINES: dict[str, set[int]] = {
}


def _iter_scan_files() -> list[Path]:
    out: list[Path] = []
    for base in (ROOT / "ui" / "templates", ROOT / "ui" / "static", ROOT / "app" / "static"):
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            if not p.is_file():
                continue
            if p.suffix.lower() not in {".html", ".js"}:
                continue
            if p in SKIP_FILES:
                continue
            out.append(p)
    return sorted(out)


@pytest.mark.parametrize("path", _iter_scan_files(), ids=lambda p: str(p.relative_to(ROOT)))
def test_no_hardcoded_cyrillic_outside_allowlist(path: Path) -> None:
    rel = str(path.relative_to(ROOT))
    allowed = ALLOWLIST_LINES.get(rel, set())
    text = path.read_text(encoding="utf-8")
    bad: list[str] = []
    for i, line in enumerate(text.splitlines(), start=1):
        if i in allowed:
            continue
        stripped = line.strip()
        if stripped.startswith("//"):
            continue
        if path.suffix.lower() == ".js":
            line = _strip_js_line_comment(line)
        if CYR.search(line):
            bad.append(f"{rel}:{i}:{stripped[:120]}")
    assert not bad, "Hardcoded Cyrillic — move to i18n.js or add a documented allowlist line:\n" + "\n".join(
        bad[:80]
    )
