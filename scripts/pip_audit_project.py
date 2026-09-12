#!/usr/bin/env python3
"""Audit this project's installed dependency graph. No docker.

Uses ``pip-audit --path`` on this interpreter's site-packages so CI does not
need ``ensurepip``. Editable ``daedalus`` is skipped. Only distributions
reachable from ``pyproject.toml`` runtime deps are blocking.

Vulns with no published fix are reported and do not fail the job (chromadb
1.5.9 currently has none). Vulns with a fix version fail closed.
"""
from __future__ import annotations

import importlib.metadata as md
import json
import subprocess
import sys
import sysconfig
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _req_name(req: str) -> str:
    token = req.split(";")[0].strip()
    for sep in ("[", " ", "<", ">", "=", "!"):
        if sep in token:
            token = token.split(sep, 1)[0]
    return token.strip().lower().replace("_", "-")


def _project_names() -> set[str]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    queue = [_req_name(d) for d in (data.get("project") or {}).get("dependencies") or []]
    seen: set[str] = set()
    while queue:
        name = queue.pop()
        if not name or name in seen:
            continue
        seen.add(name)
        try:
            dist = md.distribution(name)
        except md.PackageNotFoundError:
            continue
        for req in dist.requires or []:
            dep = _req_name(req)
            if dep and dep not in seen and "extra" not in req.split(";")[-1]:
                queue.append(dep)
    return seen


def _site_paths() -> list[str]:
    seen: list[str] = []
    for key in ("purelib", "platlib"):
        path = sysconfig.get_path(key)
        if path and path not in seen:
            seen.append(path)
    return seen


def main() -> int:
    names = _project_names()
    if not names:
        print("pip-audit: no project dependency names resolved", file=sys.stderr)
        return 1
    cmd = [
        sys.executable, "-m", "pip_audit",
        "--skip-editable", "--progress-spinner", "off", "--format", "json",
    ]
    for path in _site_paths():
        cmd.extend(["--path", path])
    proc = subprocess.run(cmd, capture_output=True, text=True)
    Path("pip-audit.json").write_text(proc.stdout or "", encoding="utf-8")
    if not (proc.stdout or "").strip():
        print(proc.stderr or "pip-audit produced no output", file=sys.stderr)
        return 1
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        print(proc.stderr, file=sys.stderr)
        print("pip-audit JSON parse failed", file=sys.stderr)
        return 1
    deps = payload if isinstance(payload, list) else payload.get("dependencies") or []
    blocking: list[str] = []
    noted: list[str] = []
    for dep in deps:
        name = str(dep.get("name") or "").lower().replace("_", "-")
        if name not in names:
            continue
        version = str(dep.get("version") or "")
        for vuln in dep.get("vulns") or []:
            vid = str(vuln.get("id") or "?")
            fixes = [str(x) for x in (vuln.get("fix_versions") or []) if x]
            line = f"{name} {version} {vid} fix={fixes or '-'}"
            if fixes:
                blocking.append(line)
            else:
                noted.append(line)
    if noted:
        print("pip-audit unfixed (not blocking):")
        print("\n".join(noted))
    if blocking:
        print("pip-audit blocking:", file=sys.stderr)
        print("\n".join(blocking), file=sys.stderr)
        return 1
    print("pip-audit ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
