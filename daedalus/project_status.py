"""Durable game-repo status — README the build can survive compaction with.

The owner-facing continuity ledger is separate. This file lives *inside* the
Playground project so a later turn (or a weaker model) can see what is built,
what is left, and how big each asset should be in-game.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

STATUS_MARK = "## Status (Daedalus)"
_ASSET_TABLE_HEAD = (
    "| Name | Description | Size | Path | Notes |\n"
    "|------|-------------|------|------|-------|\n"
)


def status_block(
    *,
    kind: str = "static",
    built: str = "scaffold",
    left: str = "playable slice",
    proof: str = "none yet",
) -> str:
    k = (kind or "static").strip() or "static"
    return (
        f"{STATUS_MARK}\n"
        f"- Kind: `{k}`\n"
        f"- Built: {built}\n"
        f"- Left: {left}\n"
        f"- Proof: {proof}\n"
        "\n"
        "## Assets\n"
        "Size is **in-game** (meters for 3D, display pixels for sprites). "
        "Without it, assets get scaled wrong.\n"
        "\n"
        f"{_ASSET_TABLE_HEAD}"
        "| (none yet) |  |  |  | greybox / generated |\n"
    )


def ensure_status_readme(
    root: Path,
    *,
    name: str = "",
    kind: str = "static",
    description: str = "",
) -> bool:
    """Create README.md or append a Status section. Returns True if it wrote."""
    path = Path(root) / "README.md"
    title = (name or Path(root).name or "Playground").strip()
    block = status_block(kind=kind)
    if not path.exists():
        desc = (description or "").strip()
        parts = [f"# {title}\n"]
        if desc:
            parts.append(f"\n{desc}\n")
        parts.append(f"\n{block}\n")
        path.write_text("".join(parts), encoding="utf-8")
        return True
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    if STATUS_MARK in text:
        return False
    path.write_text(text.rstrip() + "\n\n" + block + "\n", encoding="utf-8")
    return True


def record_proof(root: Path, summary: str, *, frames: list[str] | None = None) -> None:
    """Patch the Proof line in the Status section. No-op if README is missing."""
    path = Path(root) / "README.md"
    if not path.is_file():
        return
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    if STATUS_MARK not in text:
        return
    extra = ""
    rels = [f for f in (frames or []) if f]
    if rels:
        extra = f" (`{rels[0]}`" + (f" +{len(rels) - 1}" if len(rels) > 1 else "") + ")"
    line = f"- Proof: {((summary or 'taken').strip()[:180])}{extra}"
    out: list[str] = []
    patched = False
    for raw in text.splitlines():
        if raw.startswith("- Proof:") and not patched:
            out.append(line)
            patched = True
        else:
            out.append(raw)
    if patched:
        path.write_text("\n".join(out) + "\n", encoding="utf-8")


def record_built(root: Path, built: str) -> None:
    path = Path(root) / "README.md"
    if not path.is_file():
        return
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    line = f"- Built: {(built or '').strip()[:180]}"
    out: list[str] = []
    patched = False
    for raw in text.splitlines():
        if raw.startswith("- Built:") and not patched:
            out.append(line)
            patched = True
        else:
            out.append(raw)
    if patched:
        path.write_text("\n".join(out) + "\n", encoding="utf-8")


def as_dict(root: Path) -> dict[str, Any]:
    path = Path(root) / "README.md"
    return {
        "readme": path.is_file(),
        "has_status": STATUS_MARK in path.read_text(encoding="utf-8") if path.is_file() else False,
    }
