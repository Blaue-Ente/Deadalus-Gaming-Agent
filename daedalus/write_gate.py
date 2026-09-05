"""Write-time quality gate — a file that does not parse is not "written OK".

``write_file`` used to report success for JavaScript with a duplicate ``let``
and the model moved on. Now every Playground write is parsed by language
(``node --check``, Python ``compile``, ``json.loads``, inline ``<script>``
blocks in HTML) and the result says FAILED with the line, so the build loop
keeps the step open until a clean write lands. Web heuristics from
``game_lint`` ride along as warnings.

``DAEDALUS_WRITE_GATE=0`` disables the gate.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

_JS = {".js", ".mjs", ".cjs"}
_HTML = {".html", ".htm"}
_PY = {".py"}
_JSON = {".json"}
_NODE_ERR = re.compile(r":(\d+)\s*$")
_TOP_DECL = re.compile(r"^(?:let|const|var|class|function\*?|async function)\s+([A-Za-z_$][\w$]*)", re.M)


@dataclass
class GateReport:
    ok: bool
    lang: str
    backend: str = ""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    skipped: str = ""

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "lang": self.lang,
            "backend": self.backend,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "skipped": self.skipped,
        }

    def summary(self) -> str:
        if not self.ok:
            return "\n".join(f"syntax: {e}" for e in self.errors)
        head = f"gate: {self.backend} ok" if self.backend else (f"gate: skipped ({self.skipped})" if self.skipped else "")
        lines = [head] if head else []
        lines.extend(f"lint: {w}" for w in self.warnings[:6])
        return "\n".join(lines)


def enabled() -> bool:
    return (os.environ.get("DAEDALUS_WRITE_GATE") or "1").strip().lower() not in ("0", "false", "no", "off")


def lang_for(path: str) -> str:
    suffix = Path(path or "").suffix.lower()
    if suffix in _JS:
        return "js"
    if suffix in _HTML:
        return "html"
    if suffix in _PY:
        return "python"
    if suffix in _JSON:
        return "json"
    return ""


def node_bin() -> str:
    return shutil.which("node") or ""


# ── JavaScript ────────────────────────────────────────────────────────────

def duplicate_top_level_declarations(js: str, *, line_offset: int = 0) -> list[str]:
    """``let x`` twice at column 0 — what killed the snake's ``gameLoopId``.
    Works without node; node --check reports the same as a SyntaxError."""
    seen: dict[str, int] = {}
    out: list[str] = []
    for m in _TOP_DECL.finditer(js):
        name = m.group(1)
        line = js.count("\n", 0, m.start()) + 1 + line_offset
        if name in seen:
            out.append(f"line {line}: '{name}' already declared at line {seen[name]}")
        else:
            seen[name] = line
    return out


def _node_check(js: str, *, line_offset: int = 0) -> tuple[list[str], str]:
    node = node_bin()
    if not node:
        return [], ""
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as fh:
        fh.write(js)
        tmp = fh.name
    try:
        proc = subprocess.run([node, "--check", tmp], capture_output=True, text=True, timeout=15)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return [], f"node --check unavailable: {exc}"
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    if proc.returncode == 0:
        return [], "node --check"
    errors: list[str] = []
    line_no = 0
    message = ""
    for raw in (proc.stderr or "").splitlines():
        if raw.startswith(tmp):
            m = _NODE_ERR.search(raw)
            if m:
                line_no = int(m.group(1)) + line_offset
        elif raw.startswith(("SyntaxError", "ReferenceError", "TypeError")) and not message:
            message = raw.strip()
    errors.append((f"line {line_no}: " if line_no else "") + (message or "node --check failed"))
    return errors, "node --check"


def check_js(js: str, *, line_offset: int = 0) -> GateReport:
    errors, backend = _node_check(js, line_offset=line_offset)
    if not backend:
        dupes = duplicate_top_level_declarations(js, line_offset=line_offset)
        return GateReport(ok=not dupes, lang="js", backend="heuristic", errors=dupes, skipped="node not installed")
    return GateReport(ok=not errors, lang="js", backend=backend, errors=errors)


# ── HTML ──────────────────────────────────────────────────────────────────

class _Scripts(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.inline: list[tuple[int, str]] = []
        self.srcs: list[str] = []
        self._open: int | None = None
        self._buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag != "script":
            return
        a = dict(attrs)
        if a.get("src"):
            self.srcs.append(str(a["src"]))
            return
        if str(a.get("type") or "").lower() in ("module", "text/javascript", "application/javascript", ""):
            self._open = self.getpos()[0]
            self._buf = []

    def handle_data(self, data):
        if self._open is not None:
            self._buf.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self._open is not None:
            self.inline.append((self._open, "".join(self._buf)))
            self._open = None
            self._buf = []


def check_html(html: str, *, project_root: Path | None = None) -> GateReport:
    parser = _Scripts()
    try:
        parser.feed(html)
        parser.close()
    except Exception as exc:  # html.parser is lenient; anything here is real
        return GateReport(ok=False, lang="html", backend="html.parser", errors=[f"html: {exc}"])
    report = GateReport(ok=True, lang="html", backend="html.parser")
    if parser._open is not None:
        report.errors.append(f"line {parser._open}: <script> is never closed")
    for start_line, body in parser.inline:
        if not body.strip():
            continue
        sub = check_js(body, line_offset=start_line - 1)  # body line 1 is the <script> line
        report.errors.extend(sub.errors)
        if sub.backend and sub.backend != "heuristic":
            report.backend = f"html.parser + {sub.backend}"
        elif sub.skipped:
            report.skipped = sub.skipped
    for src in parser.srcs:
        low = src.lower()
        if low.startswith(("http://", "https://", "//", "data:")):
            continue
        if project_root is not None and not (project_root / src.split("?")[0].lstrip("./")).is_file():
            report.warnings.append(f"<script src=\"{src}\"> does not exist yet — write it next")
    report.ok = not report.errors
    return report


# ── entry point ───────────────────────────────────────────────────────────

def check_source(path: str, content: str, *, project_root: Path | None = None) -> GateReport:
    lang = lang_for(path)
    if not enabled() or not lang:
        return GateReport(ok=True, lang=lang or "other", skipped="no checker for this type" if not lang else "gate disabled")
    if lang == "js":
        report = check_js(content)
    elif lang == "html":
        report = check_html(content, project_root=project_root)
    elif lang == "python":
        try:
            compile(content, path, "exec")
            report = GateReport(ok=True, lang="python", backend="compile")
        except SyntaxError as exc:
            report = GateReport(ok=False, lang="python", backend="compile", errors=[f"line {exc.lineno}: {exc.msg}"])
    else:
        try:
            json.loads(content)
            report = GateReport(ok=True, lang="json", backend="json")
        except json.JSONDecodeError as exc:
            report = GateReport(ok=False, lang="json", backend="json", errors=[f"line {exc.lineno}: {exc.msg}"])
    if lang in ("js", "html"):
        from .game_lint import heuristic_web

        report.warnings.extend(i["message"] for i in heuristic_web(content))
    return report
