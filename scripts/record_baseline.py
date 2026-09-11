#!/usr/bin/env python3
"""Record or validate the P0.00 golden baseline pack.

Generates (or checks) six artifacts under ``artifacts/baseline/``.
Does not change product code, public API, or CI. Honesty-suite persist is off.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tomllib
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "artifacts" / "baseline"
ARTIFACTS = (
    "test-results.xml",
    "coverage.xml",
    "dependency-snapshot.txt",
    "architecture-inventory.json",
    "benchmark-results.json",
    "known-failures.md",
)
HOTSPOTS = (
    "daedalus/agent.py",
    "app/admin_compat.py",
    "daedalus/native_tools.py",
    "daedalus/identity.py",
    "daedalus/tools.py",
    "daedalus/build_loop.py",
    "daedalus/tdd_loop.py",
    "daedalus/kernel/full_autopilot.py",
)
ENGINE_PATHS = {
    "godot": [
        "daedalus/godot_first.py",
        "daedalus/godot_bridge.py",
        "daedalus/godot_tools.py",
        "daedalus/godot_runtime_probe.py",
        "daedalus/godot_scene_index.py",
        "daedalus/godot_platform_guide.py",
        "daedalus/godot_dod.py",
        "daedalus/playground_godot_scaffold.py",
        "daedalus/playground_godot3d_scaffold.py",
    ],
    "unity": [
        "daedalus/unity_runtime.py",
        "daedalus/unity_platform_guide.py",
        "daedalus/playground_unity_scaffold.py",
        "daedalus/playground_unity_init.py",
        "daedalus/playground_unity_build.py",
        "daedalus/playground_unity_bridge.py",
    ],
    "blender": [
        "daedalus/blender_jobs.py",
        "daedalus/blender_mcp.py",
    ],
    "web": [
        "daedalus/web_first.py",
        "daedalus/arcade_craft.py",
        "daedalus/visual_proof.py",
    ],
}
LOOPS = {
    "agent": "daedalus/agent.py",
    "native_tools": "daedalus/native_tools.py",
    "build_loop": "daedalus/build_loop.py",
    "tdd_loop": "daedalus/tdd_loop.py",
    "autopilot": "daedalus/kernel/full_autopilot.py",
    "builder_runtime": "daedalus/builder_runtime.py",
}
INVENTORY_KEYS = (
    "generated_at",
    "package_version",
    "git_commit",
    "python_requires",
    "entrypoints",
    "hotspots",
    "kernel",
    "loops",
    "native_tools",
    "engines",
    "tests",
    "modules",
)
MIN_COLLECTED_TESTS = 580
HONESTY_CASE_COUNT = 15
PACK_TEST_NAME = "test_baseline_pack.py"


def wc_lines(path: Path) -> int:
    data = path.read_bytes()
    return 0 if not data else data.count(b"\n")


def count_broad_excepts(source: str) -> int:
    tree = ast.parse(source)
    n = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if node.type is None:
            n += 1
            continue
        names: list[str] = []
        if isinstance(node.type, ast.Name):
            names = [node.type.id]
        elif isinstance(node.type, ast.Tuple):
            names = [elt.id for elt in node.type.elts if isinstance(elt, ast.Name)]
        if "Exception" in names:
            n += 1
    return n


def _const_str(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _assigned_value(tree: ast.AST, name: str) -> ast.AST | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
                return node.value
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == name:
                return node.value
    return None


def native_tool_names(root: Path | None = None) -> list[str]:
    path = (root or ROOT) / "daedalus" / "native_tools.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    value = _assigned_value(tree, "NATIVE_TOOL_SPECS")
    names: list[str] = []
    if not isinstance(value, ast.List):
        return names
    for elt in value.elts:
        if not isinstance(elt, ast.Dict):
            continue
        for key, val in zip(elt.keys, elt.values):
            if _const_str(key) == "name":
                name = _const_str(val)
                if name:
                    names.append(name)
                break
    return names


def honesty_case_ids(root: Path | None = None) -> list[str]:
    path = (root or ROOT) / "daedalus" / "game_agent_bench.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    value = _assigned_value(tree, "CASES")
    if not isinstance(value, ast.Dict):
        return []
    ids: list[str] = []
    for key in value.keys:
        name = _const_str(key)
        if name:
            ids.append(name)
    return ids


def list_py_modules(package_dir: Path) -> list[str]:
    files = [
        p.relative_to(package_dir.parent).as_posix()
        for p in sorted(package_dir.rglob("*.py"))
        if "__pycache__" not in p.parts
    ]
    return files


def kernel_filenames(root: Path) -> list[str]:
    kernel = root / "daedalus" / "kernel"
    return sorted(
        p.name for p in kernel.glob("*.py") if p.name != "__init__.py" and p.is_file()
    )


def product_test_files(root: Path) -> list[Path]:
    tests_dir = root / "tests"
    return sorted(
        p for p in tests_dir.glob("test_*.py") if p.name != PACK_TEST_NAME and p.is_file()
    )


def count_product_tests(root: Path) -> tuple[int, int]:
    files = product_test_files(root)
    functions = 0
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                functions += 1
            elif isinstance(node, ast.AsyncFunctionDef) and node.name.startswith("test_"):
                functions += 1
    return len(files), functions


def load_pyproject(root: Path) -> dict[str, Any]:
    return tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))


def package_version(root: Path) -> str:
    project = load_pyproject(root).get("project") or {}
    return str(project.get("version") or "")


def python_requires(root: Path) -> str:
    project = load_pyproject(root).get("project") or {}
    return str(project.get("requires-python") or "")


def git_commit(root: Path) -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            text=True,
            stderr=subprocess.DEVNULL,
        )
        return out.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def git_branch(root: Path) -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=root,
            text=True,
            stderr=subprocess.DEVNULL,
        )
        return out.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_uv_lock_check(root: Path) -> str:
    uv = shutil.which("uv")
    cmd = [uv, "lock", "--check"] if uv else [sys.executable, "-m", "uv", "lock", "--check"]
    try:
        proc = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return "unavailable"
    if proc.returncode == 0:
        return "pass"
    err = (proc.stderr or proc.stdout or "").strip()
    if "No module named uv" in err or "not found" in err.lower():
        return "unavailable"
    return "fail"


def pip_freeze() -> str:
    proc = subprocess.run(
        [sys.executable, "-m", "pip", "freeze"],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout.strip()


def baseline_env(root: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["DAEDALUS_STANDALONE"] = "1"
    env["DAEDALUS_DATA"] = str(root / "data" / "daedalus-baseline")
    env["DAEDALUS_PLAYGROUND"] = str(root / "playground" / "projects")
    env["COVERAGE_FILE"] = str(OUT_DIR / ".coverage")
    return env


def run_pytest(root: Path) -> tuple[int, str]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "tests",
        "--ignore=tests/test_baseline_pack.py",
        "-q",
        f"--junitxml={OUT_DIR / 'test-results.xml'}",
        "--cov=daedalus",
        f"--cov-report=xml:{OUT_DIR / 'coverage.xml'}",
        "--cov-report=term-missing",
        "--cov-fail-under=0",
    ]
    proc = subprocess.run(cmd, cwd=root, env=baseline_env(root), capture_output=True, text=True)
    log = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    return proc.returncode, log


def run_honesty_suite(root: Path) -> dict[str, Any]:
    env = baseline_env(root)
    proc = subprocess.run(
        [sys.executable, "-m", "daedalus.game_agent_bench", "--no-persist", "--json"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    text = (proc.stdout or "").strip()
    if not text:
        raise RuntimeError(
            "game_agent_bench produced no JSON\n" + (proc.stderr or "")
        )
    report = json.loads(text)
    if report.get("path"):
        raise RuntimeError("honesty suite persisted a bench file; --no-persist was required")
    return report


def write_dependency_snapshot(root: Path) -> str:
    project = load_pyproject(root).get("project") or {}
    runtime = [str(d) for d in (project.get("dependencies") or [])]
    extras = project.get("optional-dependencies") or {}
    extra_lines = []
    for name in ("dev", "playwright", "computer", "gui"):
        deps = extras.get(name) or []
        extra_lines.append(f"{name}: {', '.join(str(d) for d in deps)}")
    lock = root / "uv.lock"
    lock_hash = sha256_file(lock) if lock.is_file() else "missing"
    lines = [
        "# Daedalus dependency snapshot",
        f"python: {sys.version.split()[0]}",
        f"platform: {platform.platform()}",
        f"git_commit: {git_commit(root)}",
        f"git_branch: {git_branch(root)}",
        f"package_version: {package_version(root)}",
        f"requires-python: {python_requires(root)}",
        "",
        "## pyproject runtime",
        *runtime,
        "",
        "## pyproject extras",
        *extra_lines,
        "",
        "## uv.lock",
        f"sha256: {lock_hash}",
        f"uv_lock_check: {run_uv_lock_check(root)}",
        "",
        "## pip freeze",
        pip_freeze(),
        "",
    ]
    return "\n".join(lines)


def build_architecture_inventory(root: Path) -> dict[str, Any]:
    hotspots = []
    for rel in HOTSPOTS:
        path = root / rel
        src = path.read_text(encoding="utf-8")
        hotspots.append(
            {
                "path": rel,
                "lines": wc_lines(path),
                "broad_excepts": count_broad_excepts(src),
            }
        )
    modules_n, functions_n = count_product_tests(root)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "package_version": package_version(root),
        "git_commit": git_commit(root),
        "python_requires": python_requires(root),
        "entrypoints": {"cli": "app.cli:main"},
        "hotspots": hotspots,
        "kernel": kernel_filenames(root),
        "loops": dict(LOOPS),
        "native_tools": native_tool_names(root),
        "engines": {name: list(paths) for name, paths in ENGINE_PATHS.items()},
        "tests": {"modules": modules_n, "functions": functions_n},
        "modules": {
            "daedalus": list_py_modules(root / "daedalus"),
            "app": list_py_modules(root / "app"),
        },
    }


def _local_tag(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def iter_elements(root: ET.Element, name: str) -> Iterable[ET.Element]:
    for el in root.iter():
        if _local_tag(el.tag) == name:
            yield el


def testcase_nodeid(el: ET.Element) -> str:
    file_attr = el.get("file") or ""
    classname = el.get("classname") or ""
    name = el.get("name") or ""
    if file_attr:
        return f"{file_attr}::{name}"
    if classname.startswith("tests."):
        return f"{classname.replace('.', '/')}.py::{name}"
    if classname:
        return f"{classname}::{name}"
    return name


def junit_cases(xml_path: Path) -> list[dict[str, str]]:
    tree = ET.parse(xml_path)
    rows: list[dict[str, str]] = []
    for case in iter_elements(tree.getroot(), "testcase"):
        kind = "passed"
        message = ""
        for child in list(case):
            tag = _local_tag(child.tag)
            if tag in {"failure", "error", "skipped"}:
                kind = "error" if tag == "error" else tag
                message = (child.get("message") or "").strip() or (child.text or "").strip()
                break
        rows.append(
            {
                "nodeid": testcase_nodeid(case),
                "kind": kind,
                "message": message[:500],
            }
        )
    return rows


def junit_summary(xml_path: Path) -> dict[str, Any]:
    tree = ET.parse(xml_path)
    root = tree.getroot()
    tests = failures = errors = skipped = 0
    time_s = 0.0
    suites = [root] if _local_tag(root.tag) == "testsuite" else list(iter_elements(root, "testsuite"))
    if not suites and _local_tag(root.tag) == "testsuites":
        suites = list(root)
    for suite in suites:
        tests += int(suite.get("tests") or 0)
        failures += int(suite.get("failures") or 0)
        errors += int(suite.get("errors") or 0)
        skipped += int(suite.get("skipped") or 0)
        try:
            time_s += float(suite.get("time") or 0)
        except ValueError:
            pass
    if tests == 0:
        cases = junit_cases(xml_path)
        tests = len(cases)
        failures = sum(1 for c in cases if c["kind"] == "failure")
        errors = sum(1 for c in cases if c["kind"] == "error")
        skipped = sum(1 for c in cases if c["kind"] == "skipped")
    return {
        "tests": tests,
        "failures": failures,
        "errors": errors,
        "skipped": skipped,
        "passed": max(tests - failures - errors - skipped, 0),
        "time": time_s,
    }


def coverage_line_rate(xml_path: Path) -> float | None:
    root = ET.parse(xml_path).getroot()
    raw = root.get("line-rate")
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def coverage_has_daedalus(xml_path: Path) -> bool:
    root = ET.parse(xml_path).getroot()
    for src in iter_elements(root, "source"):
        text = (src.text or "").strip().rstrip("/\\")
        if text.endswith("daedalus"):
            return True
    for pkg in iter_elements(root, "package"):
        name = pkg.get("name") or ""
        if name == "daedalus" or name.startswith("daedalus."):
            return True
    for cls in iter_elements(root, "class"):
        filename = cls.get("filename") or ""
        if filename.startswith("daedalus/") or "/daedalus/" in filename:
            return True
    return False


def pytest_summary_line(log: str) -> str:
    for line in reversed(log.splitlines()):
        stripped = line.strip()
        if re.search(r"\b(passed|failed|skipped|error)s?\b", stripped) and (
            "passed" in stripped or "failed" in stripped or "skipped" in stripped
        ):
            if re.search(r"\d+", stripped):
                return stripped
    return ""


def write_known_failures(
    *,
    root: Path,
    junit_path: Path,
    coverage_path: Path,
    bench: dict[str, Any],
    pytest_log: str,
    pytest_code: int,
) -> str:
    summary = junit_summary(junit_path)
    cases = junit_cases(junit_path)
    failed = [c for c in cases if c["kind"] in {"failure", "error"}]
    skipped = [c for c in cases if c["kind"] == "skipped"]
    rate = coverage_line_rate(coverage_path)
    cov_pct = f"{rate * 100:.2f}%" if rate is not None else "unknown"
    bench_failed = [str(x) for x in (bench.get("failed") or [])]
    lines = [
        "# Known failures — P0.00 golden baseline",
        "",
        "This file records current behavior. It is not permission to delete, skip, or weaken tests.",
        "",
        "## Environment",
        "",
        f"- Generated at: {datetime.now(timezone.utc).isoformat()}",
        f"- Git commit: `{git_commit(root)}`",
        f"- Git branch: `{git_branch(root)}`",
        f"- Python: {sys.version.split()[0]}",
        f"- Extras installed: `dev` only (no playwright / computer / gui)",
        f"- `DAEDALUS_STANDALONE=1`",
        f"- Honesty suite: `python -m daedalus.game_agent_bench --no-persist --json`",
        "",
        "## Pytest summary",
        "",
        f"- Collected / reported tests: {summary['tests']}",
        f"- Passed: {summary['passed']}",
        f"- Failed: {summary['failures']}",
        f"- Errors: {summary['errors']}",
        f"- Skipped: {summary['skipped']}",
        f"- Duration (s): {summary['time']:.3f}",
        f"- Pytest exit code: {pytest_code}",
        f"- Coverage line-rate (`daedalus`, observational): {cov_pct}",
    ]
    summary_line = pytest_summary_line(pytest_log)
    if summary_line:
        lines += ["", f"- Pytest last line: `{summary_line}`"]
    lines += ["", "## Failed or errored tests", ""]
    if not failed:
        lines.append("No product test failures or errors were recorded.")
    else:
        for row in failed:
            lines += [
                f"### `{row['nodeid']}` ({row['kind']})",
                "",
                "```",
                row["message"] or "(no message)",
                "```",
                "",
            ]
    lines += ["## Skipped tests", ""]
    if not skipped:
        lines.append("No skipped tests were recorded.")
    else:
        for row in skipped:
            reason = row["message"] or "(no reason)"
            lines.append(f"- `{row['nodeid']}` — {reason}")
    lines += [
        "",
        "## Honesty suite (`game_agent_bench`)",
        "",
        f"- ok: {bench.get('ok')}",
        f"- passed/total: {bench.get('passed')}/{bench.get('total')}",
        f"- bake_off: {bench.get('bake_off')}",
        f"- ms: {bench.get('ms')}",
    ]
    if bench_failed:
        lines += ["", "Failed case ids:"]
        for cid in bench_failed:
            lines.append(f"- `{cid}`")
    else:
        lines += ["", "No honesty-suite case failures."]
    lines.append("")
    return "\n".join(lines)


def cleanup_coverage_files() -> None:
    for path in (ROOT / ".coverage", OUT_DIR / ".coverage"):
        if path.is_file():
            path.unlink()
    leftover_dir = ROOT / "data" / "daedalus-baseline" / "bench"
    if leftover_dir.is_dir():
        raise RuntimeError(f"honesty suite wrote {leftover_dir}; persist must stay off")


def record(root: Path) -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    data_root = root / "data" / "daedalus-baseline"
    data_root.mkdir(parents=True, exist_ok=True)

    pytest_code, pytest_log = run_pytest(root)

    if not (OUT_DIR / "test-results.xml").is_file():
        sys.stderr.write(pytest_log)
        raise RuntimeError("pytest did not write test-results.xml")
    if not (OUT_DIR / "coverage.xml").is_file():
        sys.stderr.write(pytest_log)
        raise RuntimeError("pytest did not write coverage.xml")

    bench = run_honesty_suite(root)
    (OUT_DIR / "benchmark-results.json").write_text(
        json.dumps(bench, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (OUT_DIR / "dependency-snapshot.txt").write_text(
        write_dependency_snapshot(root),
        encoding="utf-8",
    )
    (OUT_DIR / "architecture-inventory.json").write_text(
        json.dumps(build_architecture_inventory(root), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (OUT_DIR / "known-failures.md").write_text(
        write_known_failures(
            root=root,
            junit_path=OUT_DIR / "test-results.xml",
            coverage_path=OUT_DIR / "coverage.xml",
            bench=bench,
            pytest_log=pytest_log,
            pytest_code=pytest_code,
        ),
        encoding="utf-8",
    )
    cleanup_coverage_files()
    sys.stdout.write(pytest_log)
    if not pytest_summary_line(pytest_log):
        sys.stdout.write(f"\npytest exit {pytest_code}\n")
    sys.stdout.write(f"\nwrote {OUT_DIR}\n")
    return 0


def _require(cond: bool, errors: list[str], message: str) -> None:
    if not cond:
        errors.append(message)


def check_pack(root: Path) -> list[str]:
    errors: list[str] = []
    out = root / "artifacts" / "baseline"
    for name in ARTIFACTS:
        path = out / name
        _require(path.is_file() and path.stat().st_size > 0, errors, f"missing or empty {path.relative_to(root)}")
    if errors:
        return errors

    junit = out / "test-results.xml"
    try:
        summary = junit_summary(junit)
        cases = junit_cases(junit)
    except ET.ParseError as exc:
        return [f"test-results.xml is not well-formed XML: {exc}"]
    _require(summary["tests"] >= MIN_COLLECTED_TESTS, errors, f"junit tests={summary['tests']} < {MIN_COLLECTED_TESTS}")

    cov = out / "coverage.xml"
    try:
        ET.parse(cov)
    except ET.ParseError as exc:
        errors.append(f"coverage.xml is not well-formed XML: {exc}")
        return errors
    _require(coverage_has_daedalus(cov), errors, "coverage.xml has no daedalus package/classes")

    snap = (out / "dependency-snapshot.txt").read_text(encoding="utf-8")
    for needle in ("fastapi", "chromadb", "pytest", f"package_version: {package_version(root)}"):
        _require(needle in snap, errors, f"dependency-snapshot.txt missing {needle!r}")
    lock = root / "uv.lock"
    if lock.is_file():
        expected = sha256_file(lock)
        _require(
            f"sha256: {expected}" in snap,
            errors,
            "dependency-snapshot.txt uv.lock sha256 does not match uv.lock",
        )

    try:
        inventory = json.loads((out / "architecture-inventory.json").read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        errors.append(f"architecture-inventory.json is not JSON: {exc}")
        return errors
    for key in INVENTORY_KEYS:
        _require(key in inventory, errors, f"architecture-inventory.json missing key {key}")

    if isinstance(inventory.get("hotspots"), list):
        by_path = {row.get("path"): row for row in inventory["hotspots"] if isinstance(row, dict)}
        for rel in HOTSPOTS:
            path = root / rel
            _require(path.is_file(), errors, f"hotspot missing on disk: {rel}")
            row = by_path.get(rel)
            if not isinstance(row, dict):
                errors.append(f"architecture-inventory.json missing hotspot {rel}")
                continue
            _require(
                int(row.get("lines") or -1) == wc_lines(path),
                errors,
                f"hotspot {rel} lines {row.get('lines')} != wc -l {wc_lines(path)}",
            )
    else:
        errors.append("architecture-inventory.json hotspots must be a list")

    expected_tools = native_tool_names(root)
    _require(
        list(inventory.get("native_tools") or []) == expected_tools,
        errors,
        "architecture-inventory.json native_tools does not match NATIVE_TOOL_SPECS",
    )
    engines = inventory.get("engines") or {}
    if not isinstance(engines, dict):
        errors.append("architecture-inventory.json engines must be an object")
    else:
        for group, paths in ENGINE_PATHS.items():
            got = list(engines.get(group) or [])
            _require(got == paths, errors, f"architecture-inventory.json engines.{group} mismatch")
            for rel in paths:
                _require((root / rel).is_file(), errors, f"engine path missing: {rel}")

    modules_n, functions_n = count_product_tests(root)
    tests_block = inventory.get("tests") or {}
    _require(
        int((tests_block or {}).get("modules") or -1) == modules_n,
        errors,
        f"inventory tests.modules {(tests_block or {}).get('modules')} != {modules_n}",
    )
    _require(
        int((tests_block or {}).get("functions") or -1) == functions_n,
        errors,
        f"inventory tests.functions {(tests_block or {}).get('functions')} != {functions_n}",
    )

    try:
        bench = json.loads((out / "benchmark-results.json").read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        errors.append(f"benchmark-results.json is not JSON: {exc}")
        return errors
    _require(bench.get("bake_off") is False, errors, "benchmark-results.json bake_off must be false")
    _require(
        int(bench.get("total") or 0) == HONESTY_CASE_COUNT,
        errors,
        f"benchmark-results.json total {bench.get('total')} != {HONESTY_CASE_COUNT}",
    )
    expected_ids = honesty_case_ids(root)
    got_ids = [row.get("id") for row in (bench.get("cases") or []) if isinstance(row, dict)]
    _require(got_ids == expected_ids, errors, "benchmark-results.json case ids do not match CASES")
    _require("path" not in bench, errors, "benchmark-results.json must not persist a bench path")

    failures_md = (out / "known-failures.md").read_text(encoding="utf-8")
    _require(
        "not permission to delete, skip, or weaken tests" in failures_md,
        errors,
        "known-failures.md missing observational disclaimer",
    )
    for row in cases:
        if row["kind"] in {"failure", "error", "skipped"}:
            _require(
                row["nodeid"] in failures_md,
                errors,
                f"known-failures.md missing {row['kind']} nodeid {row['nodeid']}",
            )
    for cid in bench.get("failed") or []:
        _require(f"`{cid}`" in failures_md, errors, f"known-failures.md missing honesty fail {cid}")

    leftover = root / ".coverage"
    _require(not leftover.is_file(), errors, "leftover .coverage at repo root")
    _require(not (out / ".coverage").is_file(), errors, "leftover artifacts/baseline/.coverage")
    _require(
        not (root / "data" / "daedalus-baseline" / "bench").exists(),
        errors,
        "honesty suite persisted under $DAEDALUS_DATA/bench",
    )
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record or check the P0.00 golden baseline pack.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate existing artifacts/baseline files (does not re-run the suite).",
    )
    args = parser.parse_args(argv)
    if args.check:
        errors = check_pack(ROOT)
        if errors:
            sys.stderr.write("baseline pack check failed:\n")
            for err in errors:
                sys.stderr.write(f"  - {err}\n")
            return 1
        print("baseline pack ok")
        return 0
    return record(ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
