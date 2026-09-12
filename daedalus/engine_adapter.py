"""Engine host contract — Godot/Unity discover, preflight, gated run.

Leaf module. Do not import agent.py, native_tools, job_ledger, run_engine,
scratch_journal, engine_session, or godot_bridge. Do not call run_godot /
run_unity. Do not write studio/*.json or overwrite artifacts/baseline/*.

Registry ``AdapterSpec.name`` stays ``"EngineAdapter"`` (Godot-only
capability-pipeline label). This leaf's Protocol is also named
``EngineAdapter``; concretes are ``GodotEngineAdapter`` and
``UnityEngineAdapter``. Do not rename the registry.

Discover and preflight are observational (binary lookup / ``--version``).
``run(live=False)`` is ``live_gated``. Godot ``run(live=True)`` wraps
``godot_first.runtime_verify``. Unity ``run(live=True)`` wraps
``unity_runtime.runtime_verify``. skip ≠ ok. Run schema v1 is unchanged.
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from daedalus.contracts.engine import (
    EngineCapability,
    EngineDiscover,
    EngineKind,
    EnginePreflight,
    EngineRunResult,
)

VERSION_TIMEOUT_S = 12
_UNSUPPORTED = "unsupported_engine"
_LIVE_GATED = "live_gated"
_GODOT_MISSING = "godot_missing"
_UNITY_GAP = "unity_local"
_CAP_NAMES = (
    "create",
    "write",
    "run",
    "input",
    "render_proof",
    "gameplay_proof",
    "export",
    "publish",
    "rollback",
)


@dataclass
class EngineRequest:
    kind: str = ""
    root: Path | None = None
    slug: str = ""
    live: bool = False
    action: str = "discover"


class EngineAdapter(Protocol):
    kind: str

    def detect(self, root: Path) -> bool: ...
    def discover(self, req: EngineRequest) -> EngineDiscover: ...
    def preflight(self, req: EngineRequest) -> EnginePreflight: ...
    def run(self, req: EngineRequest) -> EngineRunResult: ...


class GodotEngineAdapter:
    """Godot host. Live run wraps ``godot_first.runtime_verify``."""

    kind = EngineKind.GODOT.value

    def detect(self, root: Path) -> bool:
        return (Path(root) / "project.godot").is_file()

    def discover(self, req: EngineRequest) -> EngineDiscover:
        root = _root_of(req)
        flight = _godot_preflight(root)
        present = bool(flight.get("binary"))
        skipped = bool(flight.get("skipped")) or not present
        ok = bool(flight.get("ok")) and not skipped
        mcp = bool(_godot_mcp())
        if mcp:
            promise = "mcp"
        elif present:
            promise = "headless"
        else:
            promise = "none"
        error = str(flight.get("error") or "")
        if not present:
            error = error or "godot_missing"
        return EngineDiscover(
            kind=self.kind,
            present=present,
            project=self.detect(root),
            version=str(flight.get("version") or ""),
            pinned=str(flight.get("pinned") or ""),
            mcp=mcp,
            promise=promise,
            skipped=skipped,
            ok=ok,
            error=error,
            capabilities=_godot_capabilities(flight),
        )

    def preflight(self, req: EngineRequest) -> EnginePreflight:
        root = _root_of(req)
        flight = _godot_preflight(root)
        skipped = bool(flight.get("skipped"))
        ok = bool(flight.get("ok")) and not skipped
        return EnginePreflight(
            kind=self.kind,
            ok=ok,
            skipped=skipped,
            blockers=list(flight.get("blockers") or []),
            error=str(flight.get("error") or ""),
            binary=str(flight.get("binary") or ""),
            version=str(flight.get("version") or ""),
            pinned=str(flight.get("pinned") or ""),
            export_templates=str(flight.get("export_templates") or ""),
            mcp_server=_godot_mcp(),
        )

    def run(self, req: EngineRequest) -> EngineRunResult:
        if not req.live:
            return _gated_run(self.kind, live=False)
        from daedalus.godot_first import runtime_verify

        return _godot_run_from_verify(self.kind, runtime_verify(_root_of(req)))


class UnityEngineAdapter:
    """Unity host. Live run wraps ``unity_runtime.runtime_verify`` (batchmode)."""

    kind = EngineKind.UNITY.value

    def detect(self, root: Path) -> bool:
        from daedalus.unity_runtime import is_unity_root

        return is_unity_root(root)

    def discover(self, req: EngineRequest) -> EngineDiscover:
        root = _root_of(req)
        from daedalus.engine_runners import unity_bin
        from daedalus.unity_runtime import mcp_server_name

        binary = unity_bin() or ""
        present = bool(binary)
        mcp = bool(mcp_server_name())
        project = self.detect(root)
        if mcp:
            promise = "mcp"
        elif present:
            promise = "batchmode"
        elif project:
            promise = "local_hub"
        else:
            promise = "none"
        skipped = not present and not mcp
        ok = (present or mcp) and not skipped
        error = _UNITY_GAP if skipped else ""
        return EngineDiscover(
            kind=self.kind,
            present=present,
            project=project,
            version=_binary_version(binary) if binary else "",
            pinned="",
            mcp=mcp,
            promise=promise,
            skipped=skipped,
            ok=ok,
            error=error,
            capabilities=_unity_capabilities(),
        )

    def preflight(self, req: EngineRequest) -> EnginePreflight:
        from daedalus.engine_runners import unity_bin
        from daedalus.unity_runtime import mcp_server_name

        binary = unity_bin() or ""
        mcp = mcp_server_name() or ""
        present = bool(binary) or bool(mcp)
        skipped = not present
        ok = present and not skipped
        return EnginePreflight(
            kind=self.kind,
            ok=ok,
            skipped=skipped,
            blockers=[] if ok else [_UNITY_GAP],
            error=_UNITY_GAP if skipped else "",
            binary=binary,
            version=_binary_version(binary) if binary else "",
            pinned="",
            export_templates="",
            mcp_server=mcp,
        )

    def run(self, req: EngineRequest) -> EngineRunResult:
        if not req.live:
            return _gated_run(self.kind, live=False)
        from daedalus.unity_runtime import runtime_verify

        return _unity_run_from_verify(self.kind, runtime_verify(_root_of(req)))


def adapters() -> dict[str, EngineAdapter]:
    return {
        EngineKind.GODOT.value: GodotEngineAdapter(),
        EngineKind.UNITY.value: UnityEngineAdapter(),
    }


def detect_kind(root: Path, kind: str = "") -> str:
    asked = (kind or "").strip().lower()
    if asked in adapters():
        return asked
    if asked:
        return ""
    base = Path(root)
    for name, adapter in adapters().items():
        if adapter.detect(base):
            return name
    return ""


def discover(req: EngineRequest) -> EngineDiscover:
    adapter = _adapter_of(req)
    if adapter is None:
        return EngineDiscover(
            kind=_asked_kind(req) or "unknown",
            present=False,
            project=False,
            skipped=True,
            ok=False,
            error=_UNSUPPORTED,
        )
    return adapter.discover(req)


def preflight(req: EngineRequest) -> EnginePreflight:
    adapter = _adapter_of(req)
    if adapter is None:
        return EnginePreflight(
            kind=_asked_kind(req) or "unknown",
            ok=False,
            skipped=True,
            blockers=[_UNSUPPORTED],
            error=_UNSUPPORTED,
        )
    return adapter.preflight(req)


def run(req: EngineRequest) -> EngineRunResult:
    adapter = _adapter_of(req)
    if adapter is None:
        return EngineRunResult(
            kind=_asked_kind(req) or "unknown",
            ok=False,
            skipped=True,
            live=req.live,
            blockers=[_UNSUPPORTED],
            error=_UNSUPPORTED,
        )
    return adapter.run(req)


def _adapter_of(req: EngineRequest) -> EngineAdapter | None:
    kind = detect_kind(_root_of(req), req.kind)
    return adapters().get(kind)


def _asked_kind(req: EngineRequest) -> str:
    return (req.kind or "").strip().lower()


def _root_of(req: EngineRequest) -> Path:
    if req.root is not None:
        return Path(req.root).expanduser()
    slug = (req.slug or "").strip()
    if slug:
        from daedalus.paths import playground_projects_dir

        return playground_projects_dir() / slug
    return Path(".")


def _godot_mcp() -> str:
    return (os.environ.get("DAEDALUS_GODOT_MCP") or "").strip()


def _godot_preflight(root: Path) -> dict:
    from daedalus.godot_first import preflight as godot_preflight

    project = root if (root / "project.godot").is_file() else None
    return godot_preflight(project)


def _godot_capabilities(flight: dict) -> list[EngineCapability]:
    run_ok = bool(flight.get("ok")) and not flight.get("skipped")
    export_ok = bool(flight.get("export_capability")) and run_ok
    miss = str(flight.get("error") or "Godot missing")
    reasons = {
        "create": "",
        "write": "",
        "run": "" if run_ok else miss,
        "input": "" if run_ok else "Godot missing — no input probe",
        "render_proof": "" if run_ok else "Godot missing — no render proof",
        "gameplay_proof": "" if run_ok else "Godot missing — no gameplay proof",
        "export": "" if export_ok else "Godot export templates missing",
        "publish": "",
        "rollback": "",
    }
    available = {
        "create": True,
        "write": True,
        "run": run_ok,
        "input": run_ok,
        "render_proof": run_ok,
        "gameplay_proof": run_ok,
        "export": export_ok,
        "publish": True,
        "rollback": True,
    }
    return [
        EngineCapability(name=name, available=available[name], reason=reasons[name])
        for name in _CAP_NAMES
    ]


def _unity_capabilities() -> list[EngineCapability]:
    allowed = {"create", "write", "rollback"}
    return [
        EngineCapability(
            name=name,
            available=name in allowed,
            reason="" if name in allowed else _UNITY_GAP,
        )
        for name in _CAP_NAMES
    ]


def _godot_run_from_verify(kind: str, row: dict) -> EngineRunResult:
    skipped = bool(row.get("skipped"))
    ran = bool(row.get("ran"))
    ok = bool(row.get("ok")) and not skipped
    blockers = _uniq_str(row.get("blockers"))
    error = str(row.get("error") or "")
    pre = row.get("preflight")
    if isinstance(pre, dict):
        for item in pre.get("blockers") or []:
            text = str(item)
            if text and text not in blockers:
                blockers.append(text)
        if skipped and not ran and not pre.get("binary"):
            if _GODOT_MISSING not in blockers:
                blockers.append(_GODOT_MISSING)
            error = error or str(pre.get("error") or "") or _GODOT_MISSING
    if row.get("bridge"):
        via = "bridge"
    elif ran:
        via = "headless"
    else:
        via = "none"
    return EngineRunResult(
        kind=kind,
        ok=ok,
        skipped=skipped,
        ran=ran,
        play=False,
        live=True,
        via=via,
        blockers=blockers,
        error=error,
    )


def _unity_run_from_verify(kind: str, row: dict) -> EngineRunResult:
    skipped = bool(row.get("skipped"))
    ran = bool(row.get("ran"))
    ok = bool(row.get("ok")) and not skipped
    blockers = _uniq_str(row.get("blockers"))
    error = str(row.get("error") or "")
    pre = row.get("preflight")
    if isinstance(pre, dict):
        for item in pre.get("blockers") or []:
            text = str(item)
            if text and text not in blockers:
                blockers.append(text)
        if skipped and not ran and not pre.get("binary"):
            if _UNITY_GAP not in blockers:
                blockers.append(_UNITY_GAP)
            error = error or str(pre.get("error") or "") or _UNITY_GAP
    elif skipped and not ran:
        if _UNITY_GAP not in blockers:
            blockers.append(_UNITY_GAP)
        error = error or _UNITY_GAP
    return EngineRunResult(
        kind=kind,
        ok=ok,
        skipped=skipped,
        ran=ran,
        play=False,
        live=True,
        via="batchmode" if ran else "none",
        blockers=blockers,
        error=error,
    )


def _uniq_str(items: object) -> list[str]:
    if not isinstance(items, (list, tuple)):
        return []
    out: list[str] = []
    for item in items:
        text = str(item)
        if text and text not in out:
            out.append(text)
    return out


def _gated_run(kind: str, *, live: bool) -> EngineRunResult:
    return EngineRunResult(
        kind=kind,
        ok=False,
        skipped=True,
        ran=False,
        play=False,
        live=live,
        via="none",
        blockers=[_LIVE_GATED],
        error=_LIVE_GATED,
    )


def _binary_version(binary: str) -> str:
    if not binary:
        return ""
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


__all__ = [
    "EngineAdapter",
    "EngineRequest",
    "GodotEngineAdapter",
    "UnityEngineAdapter",
    "adapters",
    "detect_kind",
    "discover",
    "preflight",
    "run",
]
