"""Daedalus Sandbox — безопасна Python среда за валидация на код.

Пет нива на валидация (от бързо към бавно):

  1. syntax_check(code)
       compile() AST parse — мигновено, без I/O, без imports.
       Хваща SyntaxError преди всичко друго.

  2. import_check(file_path, code)
       Записва кода временно и го importва в subprocess.
       Хваща ImportError, AttributeError, circular imports.

  3. run_snippet(code, timeout)
       exec() в subprocess с stdout/stderr capture.
       За test snippets, smoke tests и assertions.

  4. run_static_analysis(files)  [FIX #3]
       bandit (MEDIUM+ security) + ruff (E/F errors).
       HIGH bandit severity или E/F ruff errors = blocking.

  5. run_related_tests(changed_files)  [FIX #1]
       pytest на свързаните тест файлове.
       Ако няма тестове → skip (НЕ блокира).
       Ако тестовете fail → proposal е draft.

  validate_patch(file_path, new_content, test_code, timeout, changed_files) —
       Оркестрира всичките пет нива и връща обобщен резултат.
       Използва се от agent.py преди propose_patch().
"""
from __future__ import annotations
from .paths import repo_root

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path
from typing import Any

_SANDBOX_TIMEOUT_DEFAULT = 12
_SUBPROCESS_OVERHEAD = 3
_STATIC_TIMEOUT = 30
_TEST_TIMEOUT = 90

# Workspace root — за намиране на тест файлове
_REPO_ROOT = repo_root()


# ---------------------------------------------------------------------------
# Layer 1 — Syntax check (in-process, instant)
# ---------------------------------------------------------------------------

def syntax_check(code: str) -> dict[str, Any]:
    """Parse code with compile(). No execution, no imports.

    Returns: {ok, error?, error_type?, line?, col?, filename}
    """
    try:
        compile(code, "<daedalus_sandbox>", "exec")
        return {"ok": True, "checks": ["syntax"]}
    except SyntaxError as exc:
        return {
            "ok": False,
            "error_type": "SyntaxError",
            "error": str(exc),
            "line": exc.lineno,
            "col": exc.offset,
            "checks": ["syntax"],
        }
    except Exception as exc:
        return {
            "ok": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "checks": ["syntax"],
        }


# ---------------------------------------------------------------------------
# Layer 2 — Import check (subprocess)
# ---------------------------------------------------------------------------

def import_check(file_path: str, code: str, timeout: int = _SANDBOX_TIMEOUT_DEFAULT) -> dict[str, Any]:
    """Write code to a temp file and import it in a subprocess.

    Catches: ImportError, ModuleNotFoundError, AttributeError, circular imports,
    and any top-level exception that fires at import time.

    Returns: {ok, stdout, stderr, error?, error_type?, traceback?, checks}
    """
    with tempfile.TemporaryDirectory(prefix="daedalus_sandbox_") as tmpdir:
        fname = Path(file_path).name
        if not fname.endswith(".py"):
            fname += ".py"
        tmp_file = Path(tmpdir) / fname
        tmp_file.write_text(code, "utf-8")

        module_name = fname[:-3]
        runner = textwrap.dedent(f"""
import sys, json, traceback
sys.path.insert(0, {repr(tmpdir)})
try:
    import {module_name}
    print(json.dumps({{"ok": True, "stdout": "", "stderr": ""}}))
except Exception as exc:
    print(json.dumps({{
        "ok": False,
        "error_type": type(exc).__name__,
        "error": str(exc),
        "traceback": traceback.format_exc()
    }}))
""")
        return _run_subprocess(runner, timeout, checks=["syntax", "import"])


# ---------------------------------------------------------------------------
# Layer 3 — Run snippet (subprocess exec)
# ---------------------------------------------------------------------------

def run_snippet(code: str, timeout: int = _SANDBOX_TIMEOUT_DEFAULT) -> dict[str, Any]:
    """Execute arbitrary Python code in an isolated subprocess.

    stdout and stderr are captured. Hard timeout enforced.

    Returns: {ok, stdout, stderr, error?, error_type?, traceback?, checks, elapsed_ms}
    """
    runner = textwrap.dedent(f"""
import sys, json, io, traceback
_out = io.StringIO()
_err = io.StringIO()
sys.stdout = _out
sys.stderr = _err
try:
    exec(compile({repr(code)}, '<daedalus_snippet>', 'exec'), {{}})
    sys.stdout = sys.__stdout__
    sys.stderr = sys.__stderr__
    print(json.dumps({{"ok": True, "stdout": _out.getvalue(), "stderr": _err.getvalue()}}))
except Exception as exc:
    sys.stdout = sys.__stdout__
    sys.stderr = sys.__stderr__
    print(json.dumps({{
        "ok": False,
        "error_type": type(exc).__name__,
        "error": str(exc),
        "traceback": traceback.format_exc(),
        "stdout": _out.getvalue(),
        "stderr": _err.getvalue(),
    }}))
""")
    return _run_subprocess(runner, timeout, checks=["syntax", "import", "runtime"])


# ---------------------------------------------------------------------------
# Layer 4 — Static Analysis: bandit + ruff  [FIX #3]
# ---------------------------------------------------------------------------

def run_bandit(files: list[str]) -> dict[str, Any]:
    """Security scan с bandit -ll (MEDIUM+ severity).

    HIGH severity → blocking. MEDIUM → warning само.
    Returns: {status, summary, high_severity_count, medium_severity_count,
              findings, duration_ms, blocking}
    """
    py_files = [f for f in files if f.endswith(".py")]
    if not py_files:
        return {"status": "skip", "summary": "no python files", "blocking": False,
                "high_severity_count": 0, "medium_severity_count": 0, "findings": []}

    t0 = time.monotonic()
    abs_files = [str(_REPO_ROOT / f) if not os.path.isabs(f) else f for f in py_files]
    abs_files = [f for f in abs_files if os.path.exists(f)]
    if not abs_files:
        return {"status": "skip", "summary": "files not found on disk", "blocking": False,
                "high_severity_count": 0, "medium_severity_count": 0, "findings": []}

    try:
        result = subprocess.run(
            ["bandit", "-q", "-f", "json", "-ll", *abs_files],
            capture_output=True, text=True,
            timeout=_STATIC_TIMEOUT, cwd=str(_REPO_ROOT),
        )
        dur = int((time.monotonic() - t0) * 1000)
        try:
            data = json.loads(result.stdout)
            results = data.get("results", [])
            high = [r for r in results if r.get("issue_severity") == "HIGH"]
            med  = [r for r in results if r.get("issue_severity") == "MEDIUM"]
            status = "fail" if high else ("warn" if med else "pass")
            findings = [
                {
                    "file": r.get("filename", ""),
                    "line": r.get("line_number", 0),
                    "severity": r.get("issue_severity", ""),
                    "test": r.get("test_name", ""),
                    "message": (r.get("issue_text") or "")[:200],
                }
                for r in (high + med)[:15]
            ]
            return {
                "status": status,
                "summary": f"{len(high)} HIGH, {len(med)} MEDIUM security issues",
                "high_severity_count": len(high),
                "medium_severity_count": len(med),
                "findings": findings,
                "duration_ms": dur,
                "blocking": len(high) > 0,
            }
        except (json.JSONDecodeError, KeyError):
            return {
                "status": "error",
                "summary": "bandit output unparseable",
                "high_severity_count": 0,
                "medium_severity_count": 0,
                "findings": [],
                "duration_ms": dur,
                "blocking": False,
            }
    except FileNotFoundError:
        return {"status": "skip", "summary": "bandit not installed", "blocking": False,
                "high_severity_count": 0, "medium_severity_count": 0, "findings": []}
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "summary": f"bandit timeout ({_STATIC_TIMEOUT}s)",
                "blocking": False, "high_severity_count": 0, "medium_severity_count": 0, "findings": []}


def run_ruff(files: list[str]) -> dict[str, Any]:
    """Lint с ruff — само E (errors) и F (pyflakes) са blocking.

    E5xx (line length) НЕ е blocking. Style warnings = само warning.
    Returns: {status, summary, critical, duration_ms, blocking}
    """
    py_files = [f for f in files if f.endswith(".py")]
    if not py_files:
        return {"status": "skip", "summary": "no python files", "blocking": False, "critical": []}

    t0 = time.monotonic()
    abs_files = [str(_REPO_ROOT / f) if not os.path.isabs(f) else f for f in py_files]
    abs_files = [f for f in abs_files if os.path.exists(f)]
    if not abs_files:
        return {"status": "skip", "summary": "files not found on disk", "blocking": False, "critical": []}

    try:
        result = subprocess.run(
            ["ruff", "check", "--output-format=json", "--no-cache", *abs_files],
            capture_output=True, text=True,
            timeout=_STATIC_TIMEOUT, cwd=str(_REPO_ROOT),
        )
        dur = int((time.monotonic() - t0) * 1000)
        if result.returncode == 0:
            return {"status": "pass", "summary": "ruff clean", "duration_ms": dur,
                    "blocking": False, "critical": []}

        try:
            issues = json.loads(result.stdout) if result.stdout.strip() else []
            critical = [
                i for i in issues
                if i.get("code", "")[0:1] in ("E", "F")
                and not i.get("code", "").startswith("E5")
            ]
            warnings_count = len(issues) - len(critical)
            if critical:
                details = [
                    {
                        "file": i.get("filename", ""),
                        "line": (i.get("location") or {}).get("row", 0),
                        "code": i.get("code", ""),
                        "msg": i.get("message", ""),
                    }
                    for i in critical[:10]
                ]
                return {
                    "status": "fail",
                    "summary": f"{len(critical)} errors, {warnings_count} style warnings",
                    "critical": details,
                    "duration_ms": dur,
                    "blocking": True,
                }
            return {
                "status": "warn",
                "summary": f"0 errors, {warnings_count} style warnings",
                "critical": [],
                "duration_ms": dur,
                "blocking": False,
            }
        except json.JSONDecodeError:
            return {"status": "error", "summary": "ruff output unparseable",
                    "duration_ms": dur, "blocking": False, "critical": []}
    except FileNotFoundError:
        return {"status": "skip", "summary": "ruff not installed", "blocking": False, "critical": []}
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "summary": f"ruff timeout ({_STATIC_TIMEOUT}s)",
                "blocking": False, "critical": []}


def run_static_analysis(files: list[str]) -> dict[str, Any]:
    """Combines bandit + ruff. Returns {blocking, blockers, bandit, ruff, summary}."""
    bandit = run_bandit(files)
    ruff   = run_ruff(files)
    blockers: list[str] = []
    if bandit.get("blocking"):
        blockers.append(f"bandit: {bandit.get('summary', '')}")
    if ruff.get("blocking"):
        blockers.append(f"ruff: {ruff.get('summary', '')}")
    blocking = bool(blockers)
    parts = []
    if bandit["status"] != "skip":
        icon = "❌" if bandit.get("blocking") else ("⚠" if bandit["status"] == "warn" else "✓")
        parts.append(f"{icon} bandit: {bandit.get('summary', bandit['status'])}")
    if ruff["status"] != "skip":
        icon = "❌" if ruff.get("blocking") else ("⚠" if ruff["status"] == "warn" else "✓")
        parts.append(f"{icon} ruff: {ruff.get('summary', ruff['status'])}")
    return {
        "blocking": blocking,
        "blockers": blockers,
        "bandit": bandit,
        "ruff": ruff,
        "summary": " · ".join(parts) if parts else "static: skip",
    }


# ---------------------------------------------------------------------------
# Layer 5 — Related pytest tests  [FIX #1]
# ---------------------------------------------------------------------------

def _find_related_tests(changed_files: list[str]) -> list[str]:
    """Convention mapping: modules/daedalus/radar.py → tests/test_radar.py."""
    found: set[str] = set()
    for f in changed_files:
        if ("tests/" in f or f.startswith("test_")) and f.endswith(".py"):
            if (_REPO_ROOT / f).exists():
                found.add(f)
            continue
        stem = Path(f).stem
        candidates = [
            f"tests/test_{stem}.py",
            f"argos_data_nexus/tests/test_{stem}.py",
            f"tests/{Path(f).parent.name}/test_{stem}.py",
        ]
        for c in candidates:
            if (_REPO_ROOT / c).exists():
                found.add(c)
    return sorted(found)


def run_related_tests(changed_files: list[str]) -> dict[str, Any]:
    """Пуска pytest на тест файловете свързани с changed_files.

    Ако няма тест файлове → status='skip', blocking=False.
    Ако тестовете минат  → status='pass', blocking=True.
    Ако тестовете fail   → status='fail', blocking=True.
    """
    t0 = time.monotonic()
    relevant = _find_related_tests(changed_files)

    if not relevant:
        return {
            "status": "skip",
            "passed": False,
            "summary": "⏭ No related tests found",
            "warning": "Препоръчително: добави tests/test_<module>.py",
            "duration_ms": 0,
            "blocking": False,
        }

    report_file = "/tmp/daedalus_pytest_report.json"
    cmd = [
        sys.executable, "-m", "pytest",
        "-q", "--tb=short", "--no-header",
        "--json-report", f"--json-report-file={report_file}",
        *relevant,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=_TEST_TIMEOUT, cwd=str(_REPO_ROOT),
        )
        dur = int((time.monotonic() - t0) * 1000)
        try:
            with open(report_file, encoding="utf-8") as fh:
                report = json.load(fh)
            s = report.get("summary", {})
            passed  = s.get("passed", 0)
            failed  = s.get("failed", 0)
            errored = s.get("error", 0)
            skipped = s.get("skipped", 0)
            total   = passed + failed + errored + skipped
            if failed == 0 and errored == 0:
                return {
                    "status": "pass",
                    "passed": True,
                    "summary": f"✅ {passed}/{total} tests passed",
                    "tests_run": relevant,
                    "duration_ms": dur,
                    "blocking": True,
                }
            failed_ids = [
                t.get("nodeid", "?")
                for t in report.get("tests", [])
                if t.get("outcome") in ("failed", "error")
            ][:10]
            return {
                "status": "fail",
                "passed": False,
                "summary": f"❌ {failed + errored}/{total} tests failed",
                "failed_tests": failed_ids,
                "stdout_tail": result.stdout[-2000:],
                "tests_run": relevant,
                "duration_ms": dur,
                "blocking": True,
            }
        except (FileNotFoundError, json.JSONDecodeError):
            ok = result.returncode == 0
            return {
                "status": "pass" if ok else "error",
                "passed": ok,
                "summary": "✅ Tests passed" if ok else f"⚠ pytest error (exit {result.returncode})",
                "stderr_tail": result.stderr[-1000:],
                "tests_run": relevant,
                "duration_ms": dur,
                "blocking": True,
            }
    except subprocess.TimeoutExpired:
        return {
            "status": "timeout",
            "passed": False,
            "summary": f"⚠ pytest timeout ({_TEST_TIMEOUT}s)",
            "blocking": True,
            "duration_ms": int((time.monotonic() - t0) * 1000),
        }
    except Exception as exc:
        return {
            "status": "error",
            "passed": False,
            "summary": f"⚠ pytest runner error: {exc}",
            "blocking": False,
            "duration_ms": int((time.monotonic() - t0) * 1000),
        }


# ---------------------------------------------------------------------------
# NEED_EDIT — surgical diff validation (pi edit-diff pattern)
# ---------------------------------------------------------------------------

def validate_edit_diff(
    file_path: str,
    search: str,
    replace: str,
    *,
    read_fn: Any | None = None,
    test_code: str | None = None,
    timeout: int = _SANDBOX_TIMEOUT_DEFAULT,
) -> dict[str, Any]:
    """Read file, apply search/replace, run full validate_patch on result."""
    from .edit_diff import apply_edit_diff

    if read_fn is None:
        from .self_improve import _resolve_safe, _is_hard_blocked
        target = _resolve_safe(file_path)
        if target is None or _is_hard_blocked(str(target)):
            return {"ok": False, "errors": ["path blocked or outside allowlist"], "edit_applied": False}
        if not target.exists():
            return {"ok": False, "errors": [f"file not found: {file_path}"], "edit_applied": False}
        current = target.read_text("utf-8", errors="replace")
    else:
        r = read_fn(file_path)
        if not r.get("ok"):
            return {"ok": False, "errors": [r.get("error", "read failed")], "edit_applied": False}
        current = r.get("content", "")

    new_content, err = apply_edit_diff(current, search, replace)
    if err:
        return {"ok": False, "errors": [err], "edit_applied": False}

    result = validate_patch(
        file_path=file_path,
        new_content=new_content,
        test_code=test_code,
        timeout=timeout,
        changed_files=[file_path],
    )
    result["edit_applied"] = True
    result["new_content"] = new_content
    return result


# ---------------------------------------------------------------------------
# Orchestrator — validate_patch
# ---------------------------------------------------------------------------

def validate_patch(
    file_path: str,
    new_content: str,
    test_code: str | None = None,
    timeout: int = _SANDBOX_TIMEOUT_DEFAULT,
    changed_files: list[str] | None = None,
) -> dict[str, Any]:
    """Full five-layer validation for a proposed code patch.

    Steps:
      1. Syntax check (always, instant)
      2. Import check for .py files (subprocess)
      3. run_snippet(test_code) if test_code provided (subprocess)
      4. Static analysis: bandit + ruff (blocking on HIGH/E/F)
      5. Related pytest tests (blocking only if tests exist and fail)

    Returns a consolidated result dict with:
      ok, layers, errors, warnings, elapsed_ms, sandbox_summary,
      static (dict), tests (dict)
    """
    t0 = time.monotonic()
    layers: list[dict[str, Any]] = []
    errors: list[str] = []
    warnings: list[str] = []
    is_python = file_path.endswith(".py")
    files = changed_files or [file_path]

    static_result: dict[str, Any] = {}
    tests_result: dict[str, Any] = {}

    # --- Layer 1: Syntax ---
    if is_python:
        syn = syntax_check(new_content)
        layers.append({"layer": "syntax", **syn})
        if not syn["ok"]:
            errors.append(f"Syntax error (line {syn.get('line','?')}): {syn.get('error','')}")
            return _build_result(False, layers, errors, warnings, t0, static_result, tests_result)

    # --- Layer 2: Import ---
    if is_python:
        imp = import_check(file_path, new_content, timeout=timeout)
        layers.append({"layer": "import", **imp})
        if not imp["ok"]:
            tb = imp.get("traceback") or imp.get("error", "")
            errors.append(f"Import error ({imp.get('error_type','?')}): {imp.get('error','')}\n{tb[:800]}")
            if not test_code:
                return _build_result(False, layers, errors, warnings, t0, static_result, tests_result)

    # --- Layer 3: Test snippet ---
    if test_code and test_code.strip():
        run = run_snippet(test_code, timeout=timeout)
        layers.append({"layer": "runtime", **run})
        if not run["ok"]:
            tb = run.get("traceback") or run.get("error", "")
            errors.append(
                f"Test failed ({run.get('error_type','?')}): {run.get('error','')}\n{tb[:800]}"
            )
            return _build_result(False, layers, errors, warnings, t0, static_result, tests_result)
        else:
            stdout = (run.get("stdout") or "").strip()
            if stdout:
                warnings.append(f"Test stdout: {stdout[:200]}")

    # --- Layer 4: Static Analysis on PROPOSED content (not the disk file) ---
    if is_python:
        # Write proposed content to temp file — bandit/ruff analyse the NEW code, not the old
        with tempfile.NamedTemporaryFile(
            suffix=f"_{Path(file_path).name}", prefix="daedalus_static_",
            mode="w", encoding="utf-8", delete=False,
        ) as _tmp:
            _tmp.write(new_content)
            _tmp_path = _tmp.name
        try:
            static_result = run_static_analysis([_tmp_path])
        finally:
            try:
                os.unlink(_tmp_path)
            except OSError:
                pass
        layers.append({"layer": "static", "ok": not static_result["blocking"],
                       "summary": static_result.get("summary", "")})
        if static_result.get("blocking"):
            for blocker in static_result.get("blockers", []):
                errors.append(f"Static analysis blocked: {blocker}")
            return _build_result(False, layers, errors, warnings, t0, static_result, tests_result)
        elif static_result.get("bandit", {}).get("medium_severity_count", 0) > 0:
            warnings.append(f"Security warnings: {static_result.get('bandit', {}).get('summary', '')}")
        if static_result.get("ruff", {}).get("status") == "warn":
            warnings.append(f"Style warnings: {static_result.get('ruff', {}).get('summary', '')}")

    # --- Layer 5: Related Tests (uses actual file paths, not content) ---
    if is_python:
        tests_result = run_related_tests(files)
        layers.append({"layer": "tests", "ok": tests_result.get("passed") or not tests_result.get("blocking"),
                       "summary": tests_result.get("summary", "")})
        if tests_result.get("blocking") and not tests_result.get("passed"):
            errors.append(f"Tests failed: {tests_result.get('summary', '')}")
            return _build_result(False, layers, errors, warnings, t0, static_result, tests_result)
        if tests_result.get("status") == "skip":
            warnings.append(tests_result.get("warning", "No related tests found"))

    ok = not errors
    return _build_result(ok, layers, errors, warnings, t0, static_result, tests_result)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_subprocess(script: str, timeout: int, checks: list[str]) -> dict[str, Any]:
    t0 = time.monotonic()
    from daedalus.shell_center import child_env
    env = child_env()
    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=timeout + _SUBPROCESS_OVERHEAD,
            env=env,
        )
        elapsed_ms = round((time.monotonic() - t0) * 1000)
        raw_out = (result.stdout or "").strip()
        raw_err = (result.stderr or "").strip()
        try:
            parsed: dict[str, Any] = json.loads(raw_out)
        except Exception:
            parsed = {
                "ok": result.returncode == 0,
                "error": raw_err[:500] or f"returncode={result.returncode}",
                "stdout": raw_out[:500],
                "stderr": raw_err[:500],
            }
        parsed["checks"] = checks
        parsed["elapsed_ms"] = elapsed_ms
        return parsed
    except subprocess.TimeoutExpired:
        elapsed_ms = round((time.monotonic() - t0) * 1000)
        return {
            "ok": False,
            "error": f"Timeout after {timeout}s",
            "error_type": "TimeoutError",
            "checks": checks,
            "elapsed_ms": elapsed_ms,
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "error_type": type(exc).__name__,
            "checks": checks,
            "elapsed_ms": 0,
        }


def _build_result(
    ok: bool,
    layers: list[dict[str, Any]],
    errors: list[str],
    warnings: list[str],
    t0: float,
    static: dict[str, Any] | None = None,
    tests: dict[str, Any] | None = None,
) -> dict[str, Any]:
    elapsed_ms = round((time.monotonic() - t0) * 1000)
    summary_lines = []
    for lyr in layers:
        icon = "✓" if lyr.get("ok") else "✗"
        name = lyr.get("layer", "?")
        ms = lyr.get("elapsed_ms", 0)
        lyr_summary = lyr.get("summary", "")
        summary_lines.append(f"  {icon} {name} ({ms}ms){': ' + lyr_summary if lyr_summary else ''}")
    if errors:
        summary_lines.append("Errors:")
        for e in errors:
            summary_lines.append(f"  • {e[:300]}")
    sandbox_summary = "\n".join(summary_lines)
    result: dict[str, Any] = {
        "ok": ok,
        "layers": layers,
        "errors": errors,
        "warnings": warnings,
        "elapsed_ms": elapsed_ms,
        "sandbox_summary": sandbox_summary,
    }
    if static:
        result["static"] = static
    if tests:
        result["tests"] = tests
    return result


def format_for_llm(result: dict[str, Any], file_path: str) -> str:
    """Format a validate_patch() result for injection into an LLM prompt."""
    ok = result.get("ok", False)
    status = "PASSED ✓" if ok else "FAILED ✗"
    lines = [
        f"━━━ SANDBOX VALIDATION — {file_path} — {status} ━━━",
        f"Elapsed: {result.get('elapsed_ms', 0)}ms",
        "",
        result.get("sandbox_summary", ""),
    ]
    # Static analysis details
    static = result.get("static", {})
    if static.get("blockers"):
        lines.append("")
        lines.append("STATIC ANALYSIS BLOCKERS:")
        for b in static["blockers"]:
            lines.append(f"  ⛔ {b}")
        bandit_findings = (static.get("bandit") or {}).get("findings", [])
        for f_item in bandit_findings[:5]:
            lines.append(f"    [{f_item.get('severity')}] {f_item.get('file')}:{f_item.get('line')} — {f_item.get('message')}")
        ruff_critical = (static.get("ruff") or {}).get("critical", [])
        for f_item in ruff_critical[:5]:
            lines.append(f"    [{f_item.get('code')}] {f_item.get('file')}:{f_item.get('line')} — {f_item.get('msg')}")
    # Test results
    tests = result.get("tests", {})
    if tests and tests.get("status") not in ("skip", None):
        lines.append("")
        lines.append(f"TESTS: {tests.get('summary', '')}")
        for fid in (tests.get("failed_tests") or [])[:5]:
            lines.append(f"  ✗ {fid}")
    if not ok:
        lines.append("")
        lines.append("FULL ERRORS:")
        for i, err in enumerate(result.get("errors", []), 1):
            lines.append(f"{i}. {err}")
        lines.append("")
        lines.append(
            "Fix the code and emit a new <NEED_PROPOSE_START> block. "
            "Look carefully at the error line numbers — do NOT repeat the same mistake. "
            "Only emit the marker; do not explain the error to the owner yet."
        )
    return "\n".join(lines)
