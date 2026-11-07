"""Daedalus computers — how Play Lab actually runs and is watched.

Backends:
- ``local`` — jailed tree + HTTP on 127.0.0.1 (always works).
- ``docker`` — optional container HTTP for the install tree.
- ``smolvm`` — optional microVM browser (Celesto SmolVM). Live viewer + CDP.
  Not vendored. Missing install / KVM → honest refuse when explicitly requested;
  ``auto`` falls back to local.

Windows guests and Cua drivers are out of scope here.
"""
from __future__ import annotations

import os
from typing import Any

from .paths import playground_projects_dir


def env_flag(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


def env_disabled(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("0", "false", "no", "off")


def requested_backend() -> str:
    raw = (os.environ.get("DAEDALUS_LAB_BACKEND") or "auto").strip().lower()
    aliases = {
        "local-http": "local",
        "http": "local",
        "filesystem": "local",
        "smol": "smolvm",
        "microvm": "smolvm",
        "firecracker": "smolvm",
    }
    raw = aliases.get(raw, raw)
    if raw in {"local", "docker", "smolvm", "auto"}:
        return raw
    return "auto"


def smolvm_allowed() -> bool:
    if env_disabled("DAEDALUS_SMOLVM"):
        return False
    return True


def probe() -> dict[str, Any]:
    from .computer_smolvm import probe as smol_probe

    want = requested_backend()
    smol = smol_probe()
    return {
        "backend_requested": want,
        "smolvm_allowed": smolvm_allowed(),
        "smolvm": smol,
        "note": (
            "local HTTP always serves the specimen on this machine. "
            "SmolVM (optional) is a real guest browser so you can watch. "
            "pip install smolvm && smolvm setup — not vendored. "
            "Windows guests are not started."
        ),
    }


def open_project_computer(slug: str) -> dict[str, Any]:
    """Install a playground project into a lab and boot it so Daedalus can see it."""
    from .play_lab import create_lab, install_into_lab, list_labs, start_vm

    slug = "".join(ch if ch.isalnum() or ch in "-_" else "" for ch in (slug or ""))[:80]
    if not slug:
        raise ValueError("project slug required")
    root = playground_projects_dir() / slug
    if not root.is_dir():
        raise ValueError(f"project not found: {slug}")
    lab_id = ""
    for row in list_labs():
        if row.get("source_kind") == "project" and row.get("source") == slug:
            lab_id = str(row.get("id") or "")
            break
    if not lab_id:
        lab = create_lab(title=slug, source=slug, source_kind="project")
        lab_id = str(lab.get("id") or "")
        install_into_lab(lab_id, root)
    return start_vm(lab_id)


def live_computers() -> list[dict[str, Any]]:
    from .computer_smolvm import list_sessions
    from .play_lab import list_labs

    labs = list_labs()
    smol = {s.get("lab_id"): s for s in list_sessions()}
    out: list[dict[str, Any]] = []
    for row in labs:
        vm = dict(row.get("vm") or {})
        extra = smol.get(row.get("id")) or {}
        if vm.get("status") == "running" or extra:
            out.append({
                "lab_id": row.get("id"),
                "title": row.get("title"),
                "source": row.get("source"),
                "source_kind": row.get("source_kind"),
                "playable": row.get("playable"),
                "url": vm.get("url") or extra.get("url") or "",
                "backend": extra.get("backend") or vm.get("backend") or "local-http",
                "viewer_url": extra.get("viewer_url") or vm.get("viewer_url") or "",
                "cdp_url": extra.get("cdp_url") or vm.get("cdp_url") or "",
                "display_url": extra.get("display_url") or vm.get("display_url") or "",
                "guest_url": extra.get("guest_url") or vm.get("guest_url") or "",
                "warning": extra.get("warning") or vm.get("warning") or "",
            })
    return out
