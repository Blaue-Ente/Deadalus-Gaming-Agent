"""Write-time quality gate (S4) — syntax and arcade lint on ``write_file``.

The Snake live eval wrote ``game.js`` with a duplicate ``let gameLoopId`` and
the model spent the rest of the budget hunting files. The gate keeps the
bytes on disk (so a later ``read_file`` can see them) but returns FAILED so
the build loop's NEXT stays ``write_file``, not ``list_dir``.

Kill switch: ``DAEDALUS_WRITE_GATE=0``.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_GATED = {
    ".js", ".mjs", ".cjs",
    ".html", ".htm",
    ".py", ".pyw",
    ".gd",
    ".cs",
    ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx",
}

_WEB_KINDS = frozenset({"static", "vite", "vite-react", "phaser", "expo"})
_MAIN_HTML = frozenset({"index.html", "index.htm", "game.html", "main.html"})
_MAIN_JS = frozenset({"game.js", "main.js", "app.js", "index.js", "main.mjs"})


def enabled() -> bool:
    raw = (os.environ.get("DAEDALUS_WRITE_GATE") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _issue(severity: str, code: str, message: str, *, row: int = 0) -> dict[str, Any]:
    return {"severity": severity, "code": code, "message": message, "row": row}


def _has_loop(text: str) -> bool:
    """Canvas rAF *or* an engine ticker. Phaser.Game is the loop for Vite slices."""
    low = (text or "").lower()
    if "requestanimationframe" in low or bool(re.search(r"\bsetinterval\s*\(", low)):
        return True
    if re.search(r"new\s+phaser\.game\b", low):
        return True
    if "this.scene.start" in low or re.search(r"\bscene\.start\s*\(", low):
        return True
    if re.search(r"\bticker\.(?:add|start)\s*\(", low):
        return True
    if "new pixi.application" in low:
        return True
    return False


def arcade_blockers(path: str, content: str, kind: str) -> list[dict[str, Any]]:
    """Canvas + animation loop on the files a Playground web slice actually runs."""
    if (kind or "").strip().lower() not in _WEB_KINDS:
        return []
    name = Path(path or "").name.lower()
    suffix = Path(path or "").suffix.lower()
    low = (content or "").lower()
    has_canvas = "<canvas" in low or "getcontext(" in low or "webglrenderingcontext" in low
    has_ext_script = bool(re.search(r"<script[^>]+src\s*=", content or "", re.I))
    issues: list[dict[str, Any]] = []
    if name in _MAIN_HTML or suffix in {".html", ".htm"} and name in _MAIN_HTML:
        if not has_canvas:
            issues.append(_issue(
                "error", "arcade-missing-canvas",
                "index.html has no <canvas> (or WebGL context). The kernel proof will be a blank page.",
            ))
        if not _has_loop(content) and not has_ext_script:
            issues.append(_issue(
                "error", "arcade-missing-loop",
                "no requestAnimationFrame / setInterval / Phaser.Game — the canvas will not animate. "
                "Put the loop here or in a <script src=…> file.",
            ))
    elif name in _MAIN_JS:
        if not _has_loop(content):
            issues.append(_issue(
                "error", "arcade-missing-loop",
                f"{name} has no requestAnimationFrame / setInterval / Phaser.Game.",
            ))
    return issues


_HTML_IN_GDSCRIPT = re.compile(
    r"\bimport\s+Phaser\b|from ['\"]phaser['\"]|require\(['\"]phaser['\"]\)|"
    r"document\.getelementbyid|<canvas[\s>]|new\s+phaser\.game",
    re.I,
)
_GODOT_PATHS = (".gd", ".tscn", "project.godot")


def godot_write_issues(
    path: str, content: str, *, kind: str = "", slug: str = "",
) -> list[dict[str, Any]]:
    """Phaser/HTML gameplay and blind whole-file rewrites are not Godot patches."""
    rel = (path or "").replace("\\", "/")
    kind_l = (kind or "").strip().lower()
    is_godot_file = rel.endswith(_GODOT_PATHS) or rel.endswith("project.godot")
    if kind_l != "godot" and not is_godot_file:
        return []
    issues: list[dict[str, Any]] = []
    if is_godot_file and _HTML_IN_GDSCRIPT.search(content or ""):
        issues.append(_issue(
            "error", "godot-html-gameplay",
            "Phaser/HTML gameplay does not belong in a Godot file. Patch GDScript/.tscn.",
        ))
    if slug and is_godot_file:
        try:
            from .godot_first import rewrite_is_blind
            from . import playground as pg
            root = pg.project_dir(slug)
            if rewrite_is_blind(root, rel, content or ""):
                issues.append(_issue(
                    "error", "godot-blind-rewrite",
                    "Do not rewrite a whole existing .gd/.tscn. Use edit_file for a small patch.",
                ))
        except Exception:
            pass
    return issues


@dataclass
class GateResult:
    ok: bool
    blockers: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    skipped: bool = False

    def summary(self) -> str:
        if self.skipped:
            return "write gate skipped"
        if self.ok and not self.warnings:
            return "write gate ok"
        lines: list[str] = []
        for row in self.blockers:
            loc = f" line {row['row']}" if row.get("row") else ""
            lines.append(f"BLOCKER {row.get('code')}{loc}: {row.get('message')}")
        for row in self.warnings[:8]:
            loc = f" line {row['row']}" if row.get("row") else ""
            lines.append(f"warning {row.get('code')}{loc}: {row.get('message')}")
        return "\n".join(lines) or "write gate ok"


def check_write(path: str, content: str, *, kind: str = "", slug: str = "") -> GateResult:
    if not enabled():
        return GateResult(ok=True, skipped=True)
    godot_issues = godot_write_issues(path, content, kind=kind, slug=slug)
    suffix = Path(path or "").suffix.lower()
    if suffix not in _GATED:
        blockers = [i for i in godot_issues if i.get("severity") == "error"]
        warnings = [i for i in godot_issues if i.get("severity") != "error"]
        if not blockers and not warnings:
            return GateResult(ok=True, skipped=True)
        return GateResult(ok=len(blockers) == 0, blockers=blockers, warnings=warnings)
    from .game_lint import check as lint_check

    lint = lint_check(path, content=content, slug=slug)
    blockers = [dict(x) for x in (lint.get("blockers") or [])]
    warnings = [dict(x) for x in (lint.get("warnings") or [])]
    blockers.extend(arcade_blockers(path, content, kind))
    blockers.extend(i for i in godot_issues if i.get("severity") == "error")
    warnings.extend(i for i in godot_issues if i.get("severity") != "error")
    return GateResult(ok=len(blockers) == 0, blockers=blockers, warnings=warnings)
