"""Pre-edit baseline snapshot.

Leaf module. Do not import agent.py, native_tools, job_ledger, run_engine,
or scratch_journal. Do not invoke the P0.00 pack recorder or live
run_godot / run_unity. Do not overwrite artifacts/baseline/*.

``current_root()`` is used when a worktree is bound and enabled. Engine
probes are observational unless ``live_engines=True``. skip ≠ ok on every
check and probe row.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from daedalus.contracts.baseline import (
    BaselineCheck,
    BaselineReport,
    DependencySnapshot,
    EngineProbe,
    KnownFailure,
)
from daedalus.contracts.workspace import DirtyBaseline
from daedalus.game_build_contract import skip_is_not_ok, verified
from daedalus.paths import data_root, playground_projects_dir, product_root
from daedalus.run_workspace import RunWorkspaceError, current_root, snapshot_dirty

GIT_TIMEOUT_S = 30
PYTEST_TIMEOUT_S = 60
VERSION_TIMEOUT_S = 12
_GODOT_PIN = "4.3"
_KNOWN_DISCLAIMER = (
    "This file records current behavior. It is not permission to delete, "
    "skip, or weaken tests."
)

_NO_COMPILE = frozenset({"", "static", "streamlit"})
_FILE_COMPILE = frozenset({"vite", "vite-react", "phaser", "expo"})


@dataclass
class BaselineRequest:
    run_id: str = ""
    root: Path | None = None
    slug: str = ""
    kind: str = ""
    files: list[str] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    bench: str = "honesty"
    pack_check: bool = True
    live_engines: bool = False
    persist: bool = False


class BaselineVerifier:
    """Stateless façade. Composes pack / bench / dirty / engine probes."""

    def resolve_root(self, req: BaselineRequest) -> Path:
        if req.root is not None:
            return Path(req.root).expanduser().resolve()
        bound = current_root()
        if bound is not None:
            return bound.resolve()
        return product_root()

    def git_status(self, root: Path) -> DirtyBaseline:
        """``snapshot_dirty`` on a repo; empty row if the path is not git."""
        try:
            return snapshot_dirty(root)
        except RunWorkspaceError as exc:
            text = str(exc).lower()
            if "git cli missing" in text:
                raise
            if "not a git repository" in text:
                return DirtyBaseline(head_sha="", porcelain="", dirty=False)
            raise

    def dependencies(self, root: Path) -> DependencySnapshot:
        pyproject = root / "pyproject.toml"
        version = ""
        requires = ""
        if pyproject.is_file():
            project = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("project") or {}
            version = str(project.get("version") or "")
            requires = str(project.get("requires-python") or "")
        lock = root / "uv.lock"
        if lock.is_file():
            lock_hash = _sha256_file(lock)
            lock_check = _uv_lock_check(root)
        else:
            lock_hash = "missing"
            lock_check = "unavailable"
        return DependencySnapshot(
            python=sys.version.split()[0],
            package_version=version,
            python_requires=requires,
            uv_lock_sha256=lock_hash,
            uv_lock_check=lock_check,
            git_commit=_git_one(root, "rev-parse", "HEAD"),
            git_branch=_git_one(root, "rev-parse", "--abbrev-ref", "HEAD"),
        )

    def probe_engines(self, root: Path, *, live: bool = False) -> list[EngineProbe]:
        del live  # probes never launch editors; live only changes report.ok
        return [
            _probe_godot(root),
            _probe_named("unity", _unity_bin()),
            _probe_named("blender", _blender_bin()),
            _probe_named("chromium", _chromium_bin()),
        ]

    def targeted_tests(
        self,
        req: BaselineRequest,
        root: Path,
    ) -> tuple[BaselineCheck, list[KnownFailure]]:
        requested = bool(req.files or req.tests)
        if not requested:
            return (
                BaselineCheck(id="tests", ok=True, detail="no targeted pytest requested"),
                [],
            )
        paths = _select_tests(root, req.files, req.tests)
        if not paths:
            return (
                BaselineCheck(
                    id="tests",
                    ok=False,
                    skipped=True,
                    detail="no related tests found",
                    blockers=["no_related_tests"],
                ),
                [],
            )
        return _run_targeted_pytest(root, paths)

    def build_baseline(self, req: BaselineRequest, root: Path) -> BaselineCheck:
        deps = self.dependencies(root)
        lock_fail = deps.uv_lock_check == "fail"
        kind = (req.kind or "").strip().lower()
        if not req.slug:
            detail = f"uv_lock_check={deps.uv_lock_check}"
            return BaselineCheck(
                id="build",
                ok=not lock_fail,
                detail=detail,
                blockers=["uv_lock_check_fail"] if lock_fail else [],
            )
        if kind in _NO_COMPILE:
            return BaselineCheck(
                id="build",
                ok=not lock_fail,
                detail="static: no compile gate",
                blockers=["uv_lock_check_fail"] if lock_fail else [],
            )
        if kind == "godot":
            return _engine_compile_check("godot", _godot_bin(), lock_fail)
        if kind == "unity":
            return _engine_compile_check("unity", _unity_bin(), lock_fail)
        if kind == "blender":
            return _engine_compile_check("blender", _blender_bin(), lock_fail)
        if kind in _FILE_COMPILE:
            pdir = playground_projects_dir() / req.slug
            if not pdir.is_dir():
                return BaselineCheck(
                    id="build",
                    ok=False,
                    detail=f"{kind}: slug directory missing",
                    blockers=["no_slug_dir"],
                )
            return BaselineCheck(
                id="build",
                ok=not lock_fail,
                detail=f"{kind}: files present (not a compile run)",
                blockers=["uv_lock_check_fail"] if lock_fail else [],
            )
        return BaselineCheck(
            id="build",
            ok=not lock_fail,
            detail=f"{kind}: no dedicated compile gate",
            blockers=["uv_lock_check_fail"] if lock_fail else [],
        )

    def capture(self, req: BaselineRequest) -> BaselineReport:
        root = self.resolve_root(req)
        run_id = _safe_run_id(req.run_id)
        worktree_bound = current_root() is not None
        known: list[KnownFailure] = []
        checks: list[BaselineCheck] = []
        blockers: list[str] = []

        dirty: DirtyBaseline | None = None
        try:
            dirty = self.git_status(root)
            checks.append(BaselineCheck(
                id="git",
                ok=True,
                detail=f"dirty={int(dirty.dirty)} sha={(dirty.head_sha or '')[:12]}",
            ))
        except RunWorkspaceError as exc:
            text = str(exc)[:240]
            checks.append(BaselineCheck(
                id="git",
                ok=False,
                detail=text,
                blockers=[text],
            ))
            blockers.append(text)

        deps = self.dependencies(root)
        engines = self.probe_engines(root, live=req.live_engines)
        skip_check = _skip_law_check()
        checks.append(skip_check)
        if not skip_check.ok:
            blockers.extend(skip_check.blockers or [skip_check.detail])

        tests_check, test_fails = self.targeted_tests(req, root)
        if req.files or req.tests:
            checks.append(tests_check)
            known.extend(test_fails)
            if not tests_check.ok:
                blockers.extend(tests_check.blockers or [tests_check.detail])

        build_check = self.build_baseline(req, root)
        checks.append(build_check)
        if not build_check.ok:
            blockers.extend(build_check.blockers or [build_check.detail])

        if req.pack_check:
            pack_check, pack_fails = _run_pack_check()
            checks.append(pack_check)
            known.extend(pack_fails)
            if not pack_check.ok:
                blockers.extend(pack_check.blockers or [pack_check.detail])

        suite = (req.bench or "honesty").strip().lower()
        if suite and suite != "off":
            bench_check, bench_fails = _run_bench(suite)
            checks.append(bench_check)
            known.extend(bench_fails)
            if not bench_check.ok:
                blockers.extend(bench_check.blockers or [bench_check.detail])

        engine_check = _engines_check(engines, live=req.live_engines)
        checks.append(engine_check)
        if req.live_engines and not engine_check.ok:
            blockers.extend(engine_check.blockers or [engine_check.detail])

        required_failed = any(
            not row.ok
            for row in checks
            if row.id != "engines" or req.live_engines
        )
        # git dirty is ok=True; git CLI missing is ok=False and already counted.
        ok = not required_failed
        persisted = ""
        report = BaselineReport(
            run_id=run_id,
            root=str(root),
            worktree_bound=worktree_bound,
            dirty=dirty,
            dependencies=deps,
            engines=engines,
            checks=checks,
            known_failures=known,
            ok=ok,
            blockers=_uniq(blockers),
            persisted="",
        )
        if req.persist:
            persisted = _persist(run_id, report)
            report = report.model_copy(update={"persisted": persisted})
        return report


def _godot_bin() -> str:
    from daedalus.godot_runtime_probe import godot_bin

    return godot_bin() or ""


def _unity_bin() -> str:
    from daedalus.engine_runners import unity_bin

    return unity_bin() or ""


def _blender_bin() -> str:
    from daedalus.blender_jobs import blender_bin

    return blender_bin() or ""


def _chromium_bin() -> str:
    from daedalus.web_first import chromium_bin

    return chromium_bin() or ""


def _probe_godot(root: Path) -> EngineProbe:
    from daedalus.godot_first import PINNED_VERSION, preflight

    project = root if (root / "project.godot").is_file() else None
    flight = preflight(project)
    skipped = bool(flight.get("skipped"))
    present = bool(flight.get("binary"))
    ok = bool(flight.get("ok")) and not skipped
    error = str(flight.get("error") or "")
    if not present:
        error = error or "godot_missing"
    return EngineProbe(
        name="godot",
        present=present,
        version=str(flight.get("version") or ""),
        pinned=str(flight.get("pinned") or PINNED_VERSION or _GODOT_PIN),
        skipped=skipped or not present,
        ok=ok,
        error=error,
    )


def _probe_named(name: str, binary: str) -> EngineProbe:
    if not binary:
        return EngineProbe(
            name=name,
            present=False,
            skipped=True,
            ok=False,
            error=f"{name}_missing",
        )
    version = _binary_version(binary)
    return EngineProbe(
        name=name,
        present=True,
        version=version,
        skipped=False,
        ok=True,
    )


def _binary_version(binary: str) -> str:
    try:
        proc = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=VERSION_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    text = ((proc.stdout or "") + " " + (proc.stderr or "")).strip()
    match = re.search(r"\b(\d+\.\d+(?:\.\d+)?)", text)
    return match.group(1) if match else text[:40]


def _engine_compile_check(name: str, binary: str, lock_fail: bool) -> BaselineCheck:
    if not binary:
        return BaselineCheck(
            id="build",
            ok=False,
            skipped=True,
            detail=f"{name} binary missing (not a playtest)",
            blockers=[f"{name}_missing"],
        )
    return BaselineCheck(
        id="build",
        ok=not lock_fail,
        detail=f"{name} binary present (not a playtest)",
        blockers=["uv_lock_check_fail"] if lock_fail else [],
    )


def _skip_law_check() -> BaselineCheck:
    skipped = skip_is_not_ok({"ok": True, "skipped": True})
    passed = (
        skipped.get("ok") is False
        and verified({"ok": True, "skipped": True}) is False
        and verified({"ok": True, "skipped": False}) is True
        and verified({"ok": True, "na": True, "skipped": False}) is True
    )
    return BaselineCheck(
        id="skip_is_not_ok",
        ok=passed,
        detail="skipped is not success" if passed else "skip_is_not_ok drifted",
        blockers=[] if passed else ["skip_is_not_ok drifted"],
    )


def _engines_check(engines: list[EngineProbe], *, live: bool) -> BaselineCheck:
    missing = [row.name for row in engines if row.skipped or not row.ok]
    if live:
        return BaselineCheck(
            id="engines",
            ok=not missing,
            skipped=bool(missing),
            detail="live engines required: " + (",".join(missing) if missing else "all present"),
            blockers=[f"{name}_missing" for name in missing],
        )
    return BaselineCheck(
        id="engines",
        ok=True,
        detail="probes observational; missing=" + (",".join(missing) if missing else "none"),
    )


def _select_tests(root: Path, files: list[str], tests: list[str]) -> list[str]:
    found: list[str] = []
    for raw in tests:
        text = (raw or "").strip()
        if not text:
            continue
        path = Path(text)
        cand = path if path.is_absolute() else root / path
        if cand.is_file():
            found.append(str(cand.resolve()))
    for rel in files:
        text = (rel or "").strip()
        if not text:
            continue
        posix = text.replace("\\", "/")
        if ("tests/" in posix or posix.startswith("test_")) and posix.endswith(".py"):
            cand = Path(posix) if Path(posix).is_absolute() else root / posix
            if cand.is_file():
                found.append(str(cand.resolve()))
            continue
        stem = Path(posix).stem
        parent = Path(posix).parent.name
        for candidate in (
            root / "tests" / f"test_{stem}.py",
            root / "tests" / parent / f"test_{stem}.py",
        ):
            if candidate.is_file():
                found.append(str(candidate.resolve()))
    # Preserve order, drop dupes.
    out: list[str] = []
    seen: set[str] = set()
    for item in found:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _run_targeted_pytest(
    root: Path,
    paths: list[str],
) -> tuple[BaselineCheck, list[KnownFailure]]:
    junit = Path(root) / ".daedalus-baseline-junit.xml"
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "--tb=line",
        f"--junitxml={junit}",
        *paths,
    ]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=PYTEST_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return (
            BaselineCheck(
                id="tests",
                ok=False,
                detail=f"pytest timeout ({PYTEST_TIMEOUT_S}s)",
                blockers=["pytest_timeout"],
            ),
            [],
        )
    fails = _junit_failures(junit)
    if junit.is_file():
        try:
            junit.unlink()
        except OSError:
            pass
    if proc.returncode == 0 and not fails:
        return (
            BaselineCheck(
                id="tests",
                ok=True,
                detail=f"targeted pytest ok ({len(paths)} paths)",
            ),
            [],
        )
    if not fails and proc.returncode != 0:
        fails = [
            KnownFailure(
                source="pytest",
                kind="error",
                id=paths[0],
                message=(proc.stderr or proc.stdout or "pytest failed")[:240],
            )
        ]
    blockers = [row.id for row in fails[:8]]
    return (
        BaselineCheck(
            id="tests",
            ok=False,
            detail=f"targeted pytest failed ({len(fails)})",
            blockers=blockers or ["pytest_failed"],
        ),
        fails,
    )


def _junit_failures(path: Path) -> list[KnownFailure]:
    if not path.is_file():
        return []
    try:
        import xml.etree.ElementTree as ET

        tree = ET.parse(path)
    except (OSError, ET.ParseError):
        return []
    rows: list[KnownFailure] = []
    for case in tree.iter():
        tag = case.tag.rsplit("}", 1)[-1]
        if tag != "testcase":
            continue
        kind = ""
        message = ""
        for child in list(case):
            child_tag = child.tag.rsplit("}", 1)[-1]
            if child_tag in {"failure", "error", "skipped"}:
                kind = "error" if child_tag == "error" else child_tag
                message = (child.get("message") or child.text or "")[:240]
                break
        if not kind:
            continue
        nodeid = case.get("file") or case.get("classname") or ""
        name = case.get("name") or ""
        ident = f"{nodeid}::{name}" if nodeid else name
        rows.append(KnownFailure(source="pytest", kind=kind, id=ident, message=message))
    return rows


def _run_pack_check() -> tuple[BaselineCheck, list[KnownFailure]]:
    errors = _check_pack(product_root())
    if not errors:
        return BaselineCheck(id="pack", ok=True, detail="baseline pack ok"), []
    fails = [
        KnownFailure(source="pack", kind="failure", id="pack", message=err[:240])
        for err in errors[:12]
    ]
    return (
        BaselineCheck(
            id="pack",
            ok=False,
            detail="; ".join(errors)[:240],
            blockers=errors[:8],
        ),
        fails,
    )


def _run_bench(suite: str) -> tuple[BaselineCheck, list[KnownFailure]]:
    from daedalus.game_agent_bench import evaluate

    name = suite if suite in {"honesty", "game", "all"} else "honesty"
    report = evaluate(persist=False, suite=name, live=False)
    failed = [str(x) for x in (report.get("failed") or [])]
    fails = [
        KnownFailure(source="bench", kind="failure", id=cid, message="bench case failed")
        for cid in failed
    ]
    ok = bool(report.get("ok")) and report.get("bake_off") is False
    if "path" in report:
        ok = False
        fails.append(KnownFailure(
            source="bench",
            kind="failure",
            id="persist",
            message="bench persist must stay off",
        ))
    detail = (
        f"{name} {report.get('passed')}/{report.get('total')} "
        f"bake_off={report.get('bake_off')}"
    )
    return (
        BaselineCheck(
            id="bench",
            ok=ok,
            detail=detail,
            blockers=failed,
        ),
        fails,
    )


def _check_pack(root: Path) -> list[str]:
    mod = _load_pack()
    return list(mod.check_pack(root))


def _load_pack() -> Any:
    scripts = product_root() / "scripts"
    inserted = str(scripts)
    if inserted not in sys.path:
        sys.path.insert(0, inserted)
    import record_baseline as baseline

    return baseline


def _persist(run_id: str, report: BaselineReport) -> str:
    dest = data_root() / "baselines" / run_id
    dest.mkdir(parents=True, exist_ok=True)
    payload = dest / "report.json"
    payload.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# Known failures — pre-edit baseline",
        "",
        _KNOWN_DISCLAIMER,
        "",
        f"- Run: `{run_id}`",
        f"- Root: `{report.root}`",
        "",
    ]
    if not report.known_failures:
        lines.append("No failures or skips were recorded.")
    else:
        for row in report.known_failures:
            lines.append(f"- `{row.id}` ({row.source}/{row.kind}) — {row.message or '(no message)'}")
    lines.append("")
    (dest / "known-failures.md").write_text("\n".join(lines), encoding="utf-8")
    return str(payload)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _uv_lock_check(root: Path) -> str:
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


def _git_one(root: Path, *args: str) -> str:
    if shutil.which("git") is None:
        return ""
    try:
        proc = subprocess.run(
            ["git", "--no-optional-locks", *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if proc.returncode != 0:
        return ""
    return (proc.stdout or "").strip()


def _safe_run_id(run_id: str) -> str:
    cleaned = "".join(
        c if c.isalnum() or c in "-_" else "-" for c in (run_id or "")[:80]
    )
    return cleaned or "run"


def _uniq(items: list[str]) -> list[str]:
    out: list[str] = []
    for item in items:
        text = (item or "").strip()
        if text and text not in out:
            out.append(text)
    return out


__all__ = [
    "BaselineRequest",
    "BaselineVerifier",
]
