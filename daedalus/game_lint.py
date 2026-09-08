"""Architectural lint for game code — ruff, clang-tidy/format, Roslyn/dotnet, heuristics.

Does not claim a memory-leak proof. clang-tidy / ``dotnet format`` run when the
binaries exist; otherwise heuristics still fire. Format returns text — it does
not write files.

Native tool: ``game_lint``. CLI: ``python -m daedalus.game_lint``.
"""
from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

_MAX_REPORT = 24
_MAX_FILE = 400_000

_CPP = {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".inl"}
_CS = {".cs"}
_PY = {".py", ".pyw"}
_GD = {".gd"}
_JS = {".js", ".mjs", ".cjs"}
_HTML = {".html", ".htm"}
_IDENT = re.compile(r"[A-Za-z_$][\w$]*")
_SCRIPT = re.compile(
    r"<script\b(?![^>]*\bsrc\s*=)[^>]*>(.*?)</script>",
    re.I | re.S,
)

_CLANG_TIDY_CHECKS = (
    "clang-analyzer-cplusplus.NewDeleteLeaks,"
    "clang-analyzer-unix.Malloc,"
    "bugprone-use-after-move,"
    "bugprone-dangling-handle,"
    "cppcoreguidelines-owning-memory,"
    "cppcoreguidelines-no-malloc"
)


def _which(name: str) -> str:
    return shutil.which(name) or ""


def _lang_for(path: str) -> str:
    suffix = Path(path or "").suffix.lower()
    if suffix in _CPP:
        return "cpp"
    if suffix in _CS:
        return "csharp"
    if suffix in _PY:
        return "python"
    if suffix in _GD:
        return "gdscript"
    if suffix in _JS:
        return "javascript"
    if suffix in _HTML:
        return "html"
    return "unknown"


def resolve_path(path: str, *, slug: str = "") -> Path:
    from .paths import data_root, playground_projects_dir, product_root
    from .shell_center import jail_roots

    raw = (path or "").strip()
    if not raw:
        raise ValueError("path required")
    cand = Path(raw).expanduser()
    if slug:
        from . import playground as pg
        root = pg.project_dir(slug)
        if not cand.is_absolute():
            cand = (root / cand).resolve()
        else:
            cand = cand.resolve()
            cand.relative_to(root.resolve())
        return cand
    if not cand.is_absolute():
        cand = (product_root() / cand).resolve()
    else:
        cand = cand.resolve()
    for root in jail_roots() + [data_root().resolve(), playground_projects_dir().resolve()]:
        try:
            cand.relative_to(root)
            return cand
        except ValueError:
            continue
    raise ValueError(f"path outside jail: {path}")


def _read(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(str(path))
    data = path.read_bytes()
    if len(data) > _MAX_FILE:
        raise ValueError(f"file too large ({len(data)} bytes)")
    return data.decode("utf-8", errors="replace")


def _run(cmd: list[str], *, timeout: int = 25, cwd: str | None = None) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd,
        )
        return {
            "ok": proc.returncode == 0,
            "code": proc.returncode,
            "stdout": (proc.stdout or "")[:8000],
            "stderr": (proc.stderr or "")[:4000],
        }
    except FileNotFoundError:
        return {"ok": False, "error": f"missing binary: {cmd[0]}"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timeout after {timeout}s", "timeout": True}


def _issue(severity: str, code: str, message: str, *, row: int = 0) -> dict[str, Any]:
    return {"severity": severity, "code": code, "message": message, "row": row}


def heuristic_cpp(text: str) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    news = len(re.findall(r"\bnew\s+", text))
    deletes = len(re.findall(r"\bdelete(\s*\[)?\s*", text))
    mallocs = len(re.findall(r"\b(malloc|calloc|realloc)\s*\(", text))
    frees = len(re.findall(r"\bfree\s*\(", text))
    smart = len(re.findall(r"\b(std::)?(unique_ptr|shared_ptr|make_unique|make_shared)\b", text))
    if news and deletes + smart < news:
        issues.append(_issue(
            "warning", "leak-heuristic-new",
            f"raw new ({news}) outnumbers delete ({deletes}) + smart pointers ({smart}). "
            "Prefer unique_ptr / make_unique, or run clang-tidy.",
        ))
    if mallocs and frees < mallocs:
        issues.append(_issue(
            "warning", "leak-heuristic-malloc",
            f"malloc/calloc/realloc ({mallocs}) vs free ({frees}). Pair them or use containers.",
        ))
    if re.search(r"\busing namespace std\s*;", text):
        issues.append(_issue("warning", "using-namespace-std", "avoid using namespace std in headers/game code"))
    return issues


def heuristic_csharp(text: str) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if re.search(r"void\s+Update\s*\(", text):
        if re.search(r"FindObjectOfType\s*<", text) or re.search(r"GameObject\.Find\s*\(", text):
            issues.append(_issue(
                "warning", "unity-find-in-update",
                "FindObjectOfType / GameObject.Find inside a file that has Update — cache in Awake/Start.",
            ))
        if len(re.findall(r"GetComponent\s*<", text)) >= 3:
            issues.append(_issue(
                "warning", "unity-getcomponent-hot",
                "several GetComponent<> calls; cache on the hot path (Update/FixedUpdate).",
            ))
    if "IDisposable" in text and not re.search(r"\b(Dispose|using\s*\()", text):
        issues.append(_issue("warning", "idisposable", "IDisposable type without Dispose/using"))
    if re.search(r"new\s+(Texture2D|RenderTexture|Material|Mesh)\s*\(", text) and "Destroy" not in text:
        issues.append(_issue(
            "warning", "unity-alloc-destroy",
            "UnityEngine.Object allocation without Destroy — possible native leak.",
        ))
    return issues


def heuristic_gdscript(text: str) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if re.search(r'(\.connect|\bconnect)\s*\(\s*"', text):
        issues.append(_issue(
            "warning", "godot3-connect",
            'string-named .connect("signal") is Godot 3; use typed .connect(callable) on 4.3.',
        ))
    if re.search(r"\bKinematicBody(2D|3D)?\b", text):
        issues.append(_issue("warning", "godot3-kinematic", "KinematicBody* is Godot 3; use CharacterBody2D/3D"))
    return issues


def heuristic_javascript(text: str) -> list[dict[str, Any]]:
    """Duplicate ``let``/``const`` in the same brace scope, plus unbalanced delimiters.

    The Snake ``game.js`` had two top-level ``let gameLoopId`` — a SyntaxError the
    model never saw because nothing parsed the file at write time.
    """
    issues: list[dict[str, Any]] = []
    n = len(text or "")
    i = 0
    line = 1
    depth = 0
    paren = 0
    bracket = 0
    binds: list[set[str]] = [set()]

    while i < n:
        c = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if c == "\n":
            line += 1
            i += 1
            continue
        if c == "/" and nxt == "/":
            i += 2
            while i < n and text[i] != "\n":
                i += 1
            continue
        if c == "/" and nxt == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                if text[i] == "\n":
                    line += 1
                i += 1
            i = min(n, i + 2)
            continue
        if c in "'\"":
            quote = c
            i += 1
            while i < n:
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == "\n":
                    line += 1
                if text[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        if c == "`":
            i += 1
            while i < n and text[i] != "`":
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == "\n":
                    line += 1
                i += 1
            i = min(n, i + 1)
            continue
        if c == "{":
            depth += 1
            while len(binds) <= depth:
                binds.append(set())
            binds[depth] = set()
            i += 1
            continue
        if c == "}":
            if depth > 0:
                binds[depth] = set()
                depth -= 1
            else:
                issues.append(_issue("error", "js-unbalanced", "extra closing brace", row=line))
            i += 1
            continue
        if c == "(":
            paren += 1
            i += 1
            continue
        if c == ")":
            paren -= 1
            i += 1
            continue
        if c == "[":
            bracket += 1
            i += 1
            continue
        if c == "]":
            bracket -= 1
            i += 1
            continue
        if c.isalpha() or c in "_$":
            m = _IDENT.match(text, i)
            word = m.group(0) if m else c
            if word in ("let", "const"):
                j = i + len(word)
                while j < n and text[j] in " \t":
                    j += 1
                m2 = _IDENT.match(text, j)
                if m2:
                    name = m2.group(0)
                    d = max(0, min(depth, len(binds) - 1))
                    if name in binds[d]:
                        issues.append(_issue(
                            "error",
                            "js-duplicate-binding",
                            f"duplicate {word} {name} (same scope) — SyntaxError in JS",
                            row=line,
                        ))
                    else:
                        binds[d].add(name)
                    i = m2.end()
                    continue
                i = j
                continue
            i += len(word)
            continue
        i += 1

    if depth != 0:
        issues.append(_issue("error", "js-unbalanced", f"unbalanced braces (depth {depth})"))
    if paren != 0:
        issues.append(_issue("error", "js-unbalanced", "unbalanced parentheses"))
    if bracket != 0:
        issues.append(_issue("error", "js-unbalanced", "unbalanced brackets"))
    # WebGL 1: the transpose argument to uniformMatrix*fv must be false.
    # Claude's 3D slice used transpose=true for row-major mats — INVALID_VALUE,
    # the canvas clears but never draws.
    if re.search(r"uniformMatrix[234]fv\s*\([^)]*\btrue\b", text):
        issues.append(_issue(
            "error",
            "webgl-transpose",
            "WebGL 1 requires uniformMatrix*fv transpose=false. "
            "transpose=true is INVALID_VALUE — the scene will not draw.",
        ))
    return issues


def heuristic_html(text: str) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    blob = text or ""
    for m in _SCRIPT.finditer(blob):
        chunk = m.group(1) or ""
        row0 = blob[: m.start(1)].count("\n")
        for issue in heuristic_javascript(chunk):
            row = dict(issue)
            if row.get("row"):
                row["row"] = int(row["row"]) + row0
            issues.append(row)
    return issues


def _ruff(path: str, content: str | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    from .lint_gate import lint_content, lint_file

    if content is not None:
        result = lint_content(path or "snippet.py", content)
    else:
        result = lint_file(path)
    blockers = [{"severity": "error", "code": i.code, "message": i.message, "row": i.row} for i in result.blockers]
    warnings = [{"severity": "warning", "code": i.code, "message": i.message, "row": i.row} for i in result.warnings]
    return blockers, warnings, result.error or ""


def _clang_tidy(path: str) -> tuple[list[dict[str, Any]], str]:
    bin_path = _which("clang-tidy")
    if not bin_path:
        return [], "clang-tidy not on PATH"
    ran = _run([bin_path, path, f"--checks={_CLANG_TIDY_CHECKS}", "--quiet", "--"], timeout=30)
    blob = (ran.get("stdout") or "") + "\n" + (ran.get("stderr") or "")
    issues: list[dict[str, Any]] = []
    for line in blob.splitlines():
        if "warning:" in line or "error:" in line:
            severity = "error" if "error:" in line else "warning"
            issues.append(_issue(severity, "clang-tidy", line.strip()[:240]))
            if len(issues) >= _MAX_REPORT:
                break
    return issues, ""


def _clang_format_diff(path: str, content: str | None) -> dict[str, Any]:
    bin_path = _which("clang-format")
    if not bin_path:
        return {"ran": False, "need": "clang-format"}
    src = content if content is not None else Path(path).read_text(encoding="utf-8", errors="replace")
    try:
        proc = subprocess.run(
            [bin_path, f"--assume-filename={path or 'snippet.cpp'}"],
            input=src, capture_output=True, text=True, timeout=15,
        )
    except Exception as exc:
        return {"ran": False, "error": str(exc)[:200]}
    formatted = proc.stdout or ""
    changed = formatted != src
    return {
        "ran": True,
        "changed": changed,
        "formatted": formatted if changed else "",
        "code": proc.returncode,
    }


def _dotnet_format(path: str) -> dict[str, Any]:
    bin_path = _which("dotnet")
    if not bin_path:
        return {"ran": False, "need": "dotnet"}
    target = str(Path(path).parent)
    ran = _run(
        [bin_path, "format", target, "--verify-no-changes", "--severity", "warn"],
        timeout=40,
    )
    return {
        "ran": True,
        "ok": ran.get("ok"),
        "need": "",
        "stdout": (ran.get("stdout") or ran.get("stderr") or "")[:2000],
        "error": ran.get("error") or "",
    }


def _ruff_available() -> bool:
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "ruff", "--version"],
            capture_output=True, timeout=8,
        )
        return proc.returncode == 0
    except Exception:
        return False


def status() -> dict[str, Any]:
    return {
        "ok": True,
        "tools": {
            "ruff": _ruff_available(),
            "clang-format": bool(_which("clang-format")),
            "clang-tidy": bool(_which("clang-tidy")),
            "dotnet": bool(_which("dotnet")),
        },
        "note": (
            "Heuristics always run. clang-tidy/dotnet format run when installed. "
            "Missing tools are not a pass on leaks — they are an honest skip of that backend."
        ),
    }


def check(
    path: str = "",
    *,
    slug: str = "",
    content: str | None = None,
    format_diff: bool = False,
) -> dict[str, Any]:
    rel = (path or "").strip() or ("snippet" + (".cpp" if (content or "").lstrip().startswith(("#include", "//")) else ".py"))
    lang = _lang_for(rel)
    disk_path = ""
    text = content
    if content is None:
        resolved = resolve_path(rel, slug=slug)
        disk_path = str(resolved)
        text = _read(resolved)
        lang = _lang_for(disk_path)
    assert text is not None
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    backends: dict[str, Any] = {}

    if lang == "python":
        try:
            ast.parse(text)
        except SyntaxError as exc:
            blockers.append(_issue(
                "error", "python-syntax", str(exc).split("\n", 1)[0][:240],
                row=int(exc.lineno or 0),
            ))
        b, w, err = _ruff(disk_path or rel, content)
        blockers.extend(b)
        warnings.extend(w)
        backends["ruff"] = {"ran": True, "error": err}
    elif lang == "javascript":
        js = heuristic_javascript(text)
        blockers.extend(i for i in js if i.get("severity") == "error")
        warnings.extend(i for i in js if i.get("severity") != "error")
        backends["javascript"] = {"ran": True, "backend": "heuristic"}
    elif lang == "html":
        js = heuristic_html(text)
        blockers.extend(i for i in js if i.get("severity") == "error")
        warnings.extend(i for i in js if i.get("severity") != "error")
        backends["html"] = {"ran": True, "backend": "script-heuristic"}
    elif lang == "cpp":
        warnings.extend(heuristic_cpp(text))
        tidy, tidy_need = _clang_tidy(disk_path or "")
        if disk_path and not tidy_need:
            for issue in tidy:
                if issue["severity"] == "error":
                    blockers.append(issue)
                else:
                    warnings.append(issue)
            backends["clang-tidy"] = {"ran": True, "count": len(tidy)}
        else:
            backends["clang-tidy"] = {"ran": False, "need": tidy_need or "clang-tidy (needs a file on disk)"}
        if format_diff:
            backends["clang-format"] = _clang_format_diff(disk_path or rel, text if content is not None else None)
    elif lang == "csharp":
        warnings.extend(heuristic_csharp(text))
        if disk_path:
            backends["dotnet-format"] = _dotnet_format(disk_path)
        else:
            backends["dotnet-format"] = {"ran": False, "need": "file on disk"}
    elif lang == "gdscript":
        warnings.extend(heuristic_gdscript(text))
        backends["gdscript"] = {"ran": True, "backend": "heuristic"}
    else:
        backends["unknown"] = {"ran": False, "note": f"no linter for {lang}"}

    ok = len(blockers) == 0
    return {
        "ok": ok,
        "lang": lang,
        "path": disk_path or rel,
        "blocker_count": len(blockers),
        "warning_count": len(warnings),
        "blockers": blockers[:_MAX_REPORT],
        "warnings": warnings[:_MAX_REPORT],
        "backends": backends,
        "note": "Heuristics are not a leak proof. Install clang-tidy / dotnet for compiler-grade checks.",
    }


def format_source(path: str = "", *, slug: str = "", content: str | None = None) -> dict[str, Any]:
    rel = (path or "").strip()
    lang = _lang_for(rel)
    text = content
    disk_path = ""
    if text is None:
        resolved = resolve_path(rel, slug=slug)
        disk_path = str(resolved)
        text = _read(resolved)
        lang = _lang_for(disk_path)
    if lang == "cpp":
        fmt = _clang_format_diff(disk_path or rel, text)
        if not fmt.get("ran"):
            return {"ok": False, **fmt, "wrote": False}
        return {
            "ok": True,
            "wrote": False,
            "changed": bool(fmt.get("changed")),
            "formatted": (fmt.get("formatted") or "")[:12000],
            "note": "format does not write; use write_file if you accept the result",
        }
    if lang == "python":
        bin_path = [sys.executable, "-m", "ruff", "format", "--stdin-filename", rel or "snippet.py"]
        try:
            proc = subprocess.run(
                bin_path, input=text, capture_output=True, text=True, timeout=15,
            )
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:200], "wrote": False}
        formatted = proc.stdout or ""
        return {
            "ok": proc.returncode == 0,
            "wrote": False,
            "changed": formatted != text,
            "formatted": formatted[:12000],
            "stderr": (proc.stderr or "")[:500],
            "note": "format does not write; use write_file if you accept the result",
        }
    return {"ok": False, "error": f"no formatter for {lang}", "wrote": False}


def run_action(action: str = "status", **fields: Any) -> dict[str, Any]:
    act = (action or "status").strip().lower()
    path = str(fields.get("path") or "")
    slug = str(fields.get("slug") or "")
    content = fields.get("content")
    if content is not None:
        content = str(content)
    if act in ("status", "tools"):
        return status()
    if act in ("check", "lint"):
        try:
            return check(path, slug=slug, content=content, format_diff=bool(fields.get("format_diff")))
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:300]}
    if act in ("format", "fmt"):
        try:
            return format_source(path, slug=slug, content=content)
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:300], "wrote": False}
    return {"ok": False, "error": f"unknown action {act}"}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        print(json.dumps(status(), indent=2))
        return 0
    if args[0].startswith("{"):
        payload = json.loads(args[0])
        result = run_action(str(payload.pop("action", "check")), **payload)
    else:
        action = args[0]
        payload = {"path": args[1]} if len(args) > 1 else {}
        result = run_action(action, **payload)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
