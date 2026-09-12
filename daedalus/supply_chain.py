"""Supply-chain lock and pin inspection.

Leaf module. Do not import agent.py, native_tools, tool_loop, run_finalizer,
mentor, mentor_helm, mentor_graph, otel, independent_reviewer,
verification_controller, or scripts/p0_release_gate.py.
Do not write studio/*.json. skip ≠ ok. Run schema v1 is unchanged.
Do not call pip-audit, Trivy, or secret_scan.

``inspect()`` is an identity no-op when ``DAEDALUS_SUPPLY_CHAIN`` is unset.
Opt-in checks the product (or request) root: uv.lock presence and hash,
``uv lock --check`` (unavailable is skip ≠ ok), pyproject names ⊆ lock,
OpenTelemetry pin table (1.44.0), unpinned pip / uv-pip installs in CI
and Docker files, and presence of the existing audit job names.
Optional persist under ``$DAEDALUS_DATA/supply/{run_id}.json``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from daedalus.contracts.supply import SupplyCheck, SupplyPackage, SupplyReport
from daedalus.paths import data_root, playground_projects_dir, product_root

log = logging.getLogger(__name__)

FLAG_ENV = "DAEDALUS_SUPPLY_CHAIN"
OTEL_PINS = {
    "opentelemetry-api": "1.44.0",
    "opentelemetry-sdk": "1.44.0",
    "opentelemetry-exporter-otlp-proto-grpc": "1.44.0",
}
SCAN_FILES = (
    ".gitlab-ci.yml",
    "Dockerfile",
    ".github/workflows/docker.yml",
)
AUDIT_HOOKS = ("uv-lock", "secret-scan", "pip-audit", "trivy-fs")
_TRUTHY = frozenset({"1", "true", "yes", "on"})
_INSTALL_RE = re.compile(
    r"(?:python3?\s+-m\s+)?(?:uv\s+pip\s+install|pip3?\s+install)\b",
    re.IGNORECASE,
)
_VALUE_FLAGS = frozenset({
    "--python",
    "-r",
    "--requirement",
    "-c",
    "--constraint",
    "--index-url",
    "-i",
    "--extra-index-url",
    "--find-links",
    "-f",
    "--target",
    "-t",
    "--prefix",
    "--root",
    "--src",
})
_EDITABLE_FLAGS = frozenset({"-e", "--editable"})


@dataclass
class SupplyRequest:
    run_id: str = ""
    root: Path | None = None
    persist: bool = False


def _env_on(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in _TRUTHY


def supply_enabled() -> bool:
    return _env_on(FLAG_ENV)


def _safe_run_id(run_id: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", (run_id or "").strip())[:80]


def _uniq(rows: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for row in rows:
        text = str(row or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _req_name(req: str) -> str:
    token = req.split(";")[0].strip()
    for sep in ("[", " ", "<", ">", "=", "!"):
        if sep in token:
            token = token.split(sep, 1)[0]
    return token.strip().lower().replace("_", "-")


def _pkg_key(name: str) -> str:
    return (name or "").strip().lower().replace("_", "-")


def _hash_field(raw: object) -> str:
    text = str(raw or "").strip()
    if text.startswith("sha256:"):
        return text.split(":", 1)[1]
    return text


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_root(req: SupplyRequest) -> Path:
    if req.root is not None:
        return Path(req.root).expanduser().resolve()
    return product_root()


def _persist(run_id: str, report: SupplyReport) -> str:
    safe = _safe_run_id(run_id)
    if not safe:
        return ""
    dest = data_root() / "supply"
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / f"{safe}.json"
    path.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return str(path)


def _with_persist(req: SupplyRequest, report: SupplyReport) -> SupplyReport:
    if not req.persist:
        return report
    try:
        persisted = _persist(req.run_id, report)
    except Exception as exc:
        log.debug("supply persist skipped: %s", exc)
        return report
    if not persisted:
        return report
    return report.model_copy(update={"persisted": persisted})


def _off_report(req: SupplyRequest, root: Path) -> SupplyReport:
    return SupplyReport(
        run_id=req.run_id,
        root=str(root),
        ok=True,
        skipped=False,
    )


def _parse_lock(path: Path) -> tuple[list[SupplyPackage], dict[str, Any] | None, str]:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        return [], None, str(exc)[:200]
    rows: list[SupplyPackage] = []
    for raw in data.get("package") or []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        source = raw.get("source") or {}
        src = "editable" if isinstance(source, dict) and source.get("editable") is not None else "registry"
        sha = ""
        sdist = raw.get("sdist") or {}
        if isinstance(sdist, dict):
            sha = _hash_field(sdist.get("hash"))
        if not sha:
            wheels = raw.get("wheels") or []
            if wheels and isinstance(wheels[0], dict):
                sha = _hash_field(wheels[0].get("hash"))
        rows.append(
            SupplyPackage(
                name=name,
                version=str(raw.get("version") or ""),
                source=src,
                sha256=sha,
            )
        )
    return rows, data, ""


def _uv_cmd() -> list[str] | None:
    found = shutil.which("uv")
    if found:
        return [found]
    candidates = [sys.executable, shutil.which("python3"), shutil.which("python")]
    seen: set[str] = set()
    for exe in candidates:
        if not exe or exe in seen:
            continue
        seen.add(exe)
        try:
            proc = subprocess.run(
                [exe, "-m", "uv", "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if proc.returncode == 0:
            return [exe, "-m", "uv"]
    return None


def _uv_lock_check(root: Path) -> str:
    prefix = _uv_cmd()
    if prefix is None:
        return "unavailable"
    try:
        proc = subprocess.run(
            [*prefix, "lock", "--check"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unavailable"
    if proc.returncode == 0:
        return "pass"
    err = ((proc.stderr or "") + "\n" + (proc.stdout or "")).strip().lower()
    if "no module named uv" in err or "not found" in err:
        return "unavailable"
    return "fail"


def _pyproject_names(root: Path) -> tuple[set[str] | None, str]:
    path = root / "pyproject.toml"
    if not path.is_file():
        return None, "pyproject_missing"
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None, "pyproject_parse"
    project = data.get("project") or {}
    names: set[str] = set()
    for dep in project.get("dependencies") or []:
        name = _req_name(str(dep))
        if name:
            names.add(name)
    extras = project.get("optional-dependencies") or {}
    if isinstance(extras, dict):
        for deps in extras.values():
            for dep in deps or []:
                name = _req_name(str(dep))
                if name:
                    names.add(name)
    return names, ""


def _logical_lines(text: str) -> list[str]:
    out: list[str] = []
    buf = ""
    for line in text.splitlines():
        stripped = line.rstrip()
        if stripped.endswith("\\"):
            buf += stripped[:-1] + " "
            continue
        buf += stripped
        out.append(buf)
        buf = ""
    if buf.strip():
        out.append(buf)
    return out


def _unpinned_reason(line: str) -> str:
    code = line.split("#", 1)[0].strip().rstrip("\\").strip()
    match = _INSTALL_RE.search(code)
    if not match:
        return ""
    if "--frozen" in code or "--require-hashes" in code:
        return ""
    tokens = [tok for tok in code[match.end():].split() if tok != "\\"]
    pkgs: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        low = tok.lower()
        if low in _EDITABLE_FLAGS:
            if i + 1 < len(tokens) and not tokens[i + 1].startswith("-"):
                target = tokens[i + 1].strip("'\"")
                if "==" not in target and "===" not in target:
                    return target or "editable"
                i += 2
                continue
            return "editable"
        if tok.startswith("-"):
            flag = tok.split("=", 1)[0]
            if flag in _VALUE_FLAGS and "=" not in tok and i + 1 < len(tokens) and not tokens[i + 1].startswith("-"):
                i += 2
                continue
            i += 1
            continue
        pkgs.append(tok.strip("'\""))
        i += 1
    for pkg in pkgs:
        if "==" not in pkg and "===" not in pkg:
            return pkg
    return ""


def _check_lock_present(lock: Path) -> SupplyCheck:
    if lock.is_file():
        return SupplyCheck(id="lock_present", ok=True, detail="uv.lock")
    return SupplyCheck(
        id="lock_present",
        ok=False,
        skipped=True,
        detail="uv.lock missing",
        blockers=["lock_missing"],
    )


def _check_lock_hash(lock: Path) -> tuple[SupplyCheck, str]:
    digest = _sha256_file(lock)
    return SupplyCheck(id="lock_hash", ok=True, detail=digest), digest


def _check_lock_check(root: Path, parse_error: str) -> tuple[SupplyCheck, str]:
    if parse_error:
        return (
            SupplyCheck(
                id="lock_check",
                ok=False,
                skipped=True,
                detail=parse_error,
                blockers=["lock_parse"],
            ),
            "unavailable",
        )
    status = _uv_lock_check(root)
    if status == "pass":
        return SupplyCheck(id="lock_check", ok=True, detail="pass"), status
    if status == "unavailable":
        return (
            SupplyCheck(
                id="lock_check",
                ok=False,
                skipped=True,
                detail="uv lock --check unavailable",
                blockers=["uv_unavailable"],
            ),
            status,
        )
    return (
        SupplyCheck(
            id="lock_check",
            ok=False,
            detail="uv lock --check failed",
            blockers=["uv_lock_check_fail"],
        ),
        status,
    )


def _check_pyproject_in_lock(root: Path, packages: list[SupplyPackage]) -> SupplyCheck:
    names, err = _pyproject_names(root)
    if names is None:
        return SupplyCheck(
            id="pyproject_in_lock",
            ok=False,
            skipped=True,
            detail=err,
            blockers=[err],
        )
    lock_names = {_pkg_key(row.name) for row in packages}
    missing = sorted(names - lock_names)
    if missing:
        return SupplyCheck(
            id="pyproject_in_lock",
            ok=False,
            detail="missing from lock: " + ",".join(missing)[:240],
            blockers=["pyproject_not_in_lock"],
        )
    return SupplyCheck(
        id="pyproject_in_lock",
        ok=True,
        detail=f"{len(names)} declared names in lock",
    )


def _check_otel_pin(packages: list[SupplyPackage]) -> SupplyCheck:
    by_name = {_pkg_key(row.name): row.version for row in packages}
    missing: list[str] = []
    mismatched: list[str] = []
    for name, pinned in OTEL_PINS.items():
        have = by_name.get(name)
        if have is None:
            missing.append(name)
        elif have != pinned:
            mismatched.append(f"{name}={have}!={pinned}")
    if missing or mismatched:
        detail = ",".join(missing + mismatched)[:240]
        return SupplyCheck(
            id="otel_pin",
            ok=False,
            skipped=True,
            detail=detail,
            blockers=["otel_unpinned"],
        )
    return SupplyCheck(id="otel_pin", ok=True, detail="1.44.0")


def _check_unpinned_install(root: Path) -> SupplyCheck:
    hits: list[str] = []
    for rel in SCAN_FILES:
        path = root / rel
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, line in enumerate(_logical_lines(text), 1):
            reason = _unpinned_reason(line)
            if reason:
                hits.append(f"{rel}:{lineno}:{reason}")
    if hits:
        return SupplyCheck(
            id="unpinned_install",
            ok=False,
            detail="; ".join(hits)[:400],
            blockers=["unpinned_install"],
        )
    return SupplyCheck(id="unpinned_install", ok=True, detail="no unpinned pip/uv pip install")


def _check_audit_hooks(root: Path) -> SupplyCheck:
    existing = [rel for rel in SCAN_FILES if (root / rel).is_file()]
    if not existing:
        return SupplyCheck(
            id="audit_hooks",
            ok=False,
            skipped=True,
            detail="no ci/docker files",
            blockers=["hooks_unavailable"],
        )
    blob = ""
    for rel in existing:
        try:
            blob += (root / rel).read_text(encoding="utf-8") + "\n"
        except (OSError, UnicodeDecodeError):
            continue
    missing = [name for name in AUDIT_HOOKS if name not in blob]
    if missing:
        return SupplyCheck(
            id="audit_hooks",
            ok=False,
            detail="missing " + ",".join(missing),
            blockers=["hooks_missing"],
        )
    return SupplyCheck(id="audit_hooks", ok=True, detail=",".join(AUDIT_HOOKS))


class SupplyChain:
    """Stateless façade. Hermetic lock inventory and pin checks."""

    def inspect(self, req: SupplyRequest) -> SupplyReport:
        root = _resolve_root(req)
        if not supply_enabled():
            return _with_persist(req, _off_report(req, root))

        checks: list[SupplyCheck] = []
        packages: list[SupplyPackage] = []
        lock_sha256 = ""
        lock_status = ""
        lock = root / "uv.lock"
        present = _check_lock_present(lock)
        checks.append(present)
        if present.ok:
            hash_row, lock_sha256 = _check_lock_hash(lock)
            checks.append(hash_row)
            packages, _data, parse_err = _parse_lock(lock)
            lock_row, lock_status = _check_lock_check(root, parse_err)
            checks.append(lock_row)
            if parse_err:
                checks.append(
                    SupplyCheck(
                        id="pyproject_in_lock",
                        ok=False,
                        skipped=True,
                        detail="lock parse failed",
                        blockers=["lock_parse"],
                    )
                )
                checks.append(
                    SupplyCheck(
                        id="otel_pin",
                        ok=False,
                        skipped=True,
                        detail="lock parse failed",
                        blockers=["lock_parse"],
                    )
                )
            else:
                checks.append(_check_pyproject_in_lock(root, packages))
                checks.append(_check_otel_pin(packages))
        else:
            lock_status = "unavailable"
            checks.append(
                SupplyCheck(
                    id="lock_hash",
                    ok=False,
                    skipped=True,
                    detail="uv.lock missing",
                    blockers=["lock_missing"],
                )
            )
            checks.append(
                SupplyCheck(
                    id="lock_check",
                    ok=False,
                    skipped=True,
                    detail="uv.lock missing",
                    blockers=["lock_missing"],
                )
            )
            checks.append(
                SupplyCheck(
                    id="pyproject_in_lock",
                    ok=False,
                    skipped=True,
                    detail="uv.lock missing",
                    blockers=["lock_missing"],
                )
            )
            checks.append(
                SupplyCheck(
                    id="otel_pin",
                    ok=False,
                    skipped=True,
                    detail="uv.lock missing",
                    blockers=["lock_missing"],
                )
            )

        checks.append(_check_unpinned_install(root))
        checks.append(_check_audit_hooks(root))

        blockers = _uniq([item for row in checks for item in (row.blockers or [])])
        skipped = any(row.skipped for row in checks)
        ok = not any(not row.ok for row in checks)
        report = SupplyReport(
            run_id=req.run_id,
            root=str(root),
            lock_sha256=lock_sha256,
            lock_check=lock_status,
            packages=packages,
            checks=checks,
            ok=ok,
            skipped=skipped,
            blockers=blockers,
        )
        return _with_persist(req, report)


def inspect(req: SupplyRequest) -> SupplyReport:
    return SupplyChain().inspect(req)


def _print_human(report: SupplyReport) -> None:
    mark = "ok" if report.ok else "FAIL"
    print(f"supply_chain {mark}  skipped={str(report.skipped).lower()}")
    for row in report.checks:
        if row.skipped:
            flag = "skip"
        elif row.ok:
            flag = "pass"
        else:
            flag = "FAIL"
        print(f"  {flag}  {row.id}  {row.detail[:160]}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="P1.10 hermetic supply-chain leaf.")
    parser.add_argument("--json", action="store_true", help="Print the full report JSON")
    parser.add_argument("--persist", action="store_true", help="Write $DAEDALUS_DATA/supply/{run_id}.json")
    parser.add_argument("--run-id", default="supply", help="Report run_id")
    parser.add_argument("--root", default="", help="Inspect this root instead of the product root")
    args = parser.parse_args(argv)
    os.environ["DAEDALUS_SUPPLY_CHAIN"] = "1"
    os.environ.setdefault("DAEDALUS_STANDALONE", "1")
    tmp = None
    if not args.persist:
        tmp = tempfile.TemporaryDirectory(prefix="supply-chain-")
        os.environ["DAEDALUS_DATA"] = str(Path(tmp.name) / "data")
        os.environ["DAEDALUS_PLAYGROUND"] = str(Path(tmp.name) / "projects")
        data_root.cache_clear()
        playground_projects_dir.cache_clear()
    try:
        root = Path(args.root).expanduser().resolve() if str(args.root).strip() else None
        report = inspect(SupplyRequest(run_id=args.run_id, root=root, persist=bool(args.persist)))
        if args.json:
            print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
        else:
            _print_human(report)
        return 0 if report.ok else 1
    finally:
        if tmp is not None:
            tmp.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
