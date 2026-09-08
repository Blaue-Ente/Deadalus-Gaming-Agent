"""Kernel visual proof — judge the running game, not a clean compile.

After the model writes a visual Playground slice and goes quiet, the build
loop calls ``run_visual_proof``. Web kinds get Chromium (blank screen /
console / a PNG). Godot gets the headless bridge (or an honest gap when
``GODOT_BIN`` is missing). Frames are copied to ``studio/proof/`` and can
be injected as vision so the next turn *sees* the defect.

Skip is not success. Missing Chromium or Godot is an honest gap, not a pass.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

WEB_KINDS = frozenset({"static", "vite", "vite-react", "streamlit", "expo", "phaser"})
ENGINE_KINDS = frozenset({"godot"})
PROOF_TOOLS = frozenset({"browser", "engine", "visual_proof"})
MAX_VISION_FRAMES = 3
_HEAD = 240


def enabled() -> bool:
    raw = (os.environ.get("DAEDALUS_VISUAL_PROOF") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def is_visual_kind(kind: str) -> bool:
    return (kind or "").strip().lower() in WEB_KINDS | ENGINE_KINDS


def proof_dir(root: Path) -> Path:
    d = Path(root) / "studio" / "proof"
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass
class ProofResult:
    ok: bool
    skipped: bool
    kind: str
    slug: str
    tool: str
    summary: str
    frames: list[str] = field(default_factory=list)
    error: str = ""
    honest_gap: str = ""
    blank_screen: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "skipped": self.skipped,
            "kind": self.kind,
            "slug": self.slug,
            "tool": self.tool,
            "summary": self.summary,
            "frames": list(self.frames),
            "error": self.error,
            "honest_gap": self.honest_gap,
            "blank_screen": self.blank_screen,
        }

    def as_tool_text(self) -> str:
        status = "OK" if self.ok and not self.skipped else "FAILED"
        gap = f" gap={self.honest_gap}" if self.honest_gap else ""
        frames = f" frames={','.join(self.frames)}" if self.frames else ""
        err = f"\n{self.error}" if self.error else ""
        return (
            f"── visual_proof {status} tool={self.tool} slug={self.slug} "
            f"kind={self.kind}{gap}{frames} ──\n{self.summary}{err}"
            f"\nPlayground files live under playground/projects/{self.slug or '{slug}'}/. "
            "Do not find /workspace."
            + (
                f"\nNEXT: call write_file with slug={self.slug or '<project>'} "
                "to fix what the frame shows."
                if status == "FAILED"
                else "\nThe kernel saw the running page."
            )
        )[:4000]

    def nudge_block(self) -> str:
        lines = [
            "━━━ VISUAL PROOF (kernel — not the model) ━━━",
            self.summary,
        ]
        if self.honest_gap == "chromium_missing":
            lines.append(
                "Chromium is not on PATH. This is not a pass. "
                "Files may exist; the running page was not seen."
            )
        elif self.honest_gap == "godot_missing":
            lines.append(
                "Godot is not on PATH (set GODOT_BIN). A .tscn on disk is not Play."
            )
        elif self.blank_screen:
            lines.append("The page painted nothing. Fix the render (write_file), then stop — the kernel will open it again.")
        elif not self.ok:
            lines.append("The running game failed proof. Fix the defect; do not claim готово.")
        else:
            lines.append("The running game was seen. Watch the frame(s) before you summarise.")
        if self.frames:
            lines.append("Frames: " + ", ".join(self.frames))
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        return "\n".join(lines)

    def vision_blocks(self, *, root: Path | None = None, limit: int = MAX_VISION_FRAMES) -> list[dict[str, Any]]:
        if not self.slug or not self.frames:
            return []
        try:
            from . import playground as pg
            base = root or pg.project_dir(self.slug)
        except Exception:
            return []
        return vision_blocks_for_paths(base, self.frames[: max(1, limit)])


def vision_blocks_for_paths(root: Path, rels: list[str]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    base = Path(root)
    for rel in rels:
        path = Path(rel)
        if not path.is_absolute():
            path = base / rel
        if not path.is_file():
            continue
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        if len(raw) < 32 or len(raw) > 4_000_000:
            continue
        mime = "image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
        b64 = base64.b64encode(raw).decode("ascii")
        blocks.append({
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{b64}"},
        })
    return blocks


def _stash_frames(root: Path, sources: list[Path]) -> list[str]:
    dest_dir = proof_dir(root)
    out: list[str] = []
    for i, src in enumerate(sources):
        if not src.is_file():
            continue
        suffix = src.suffix.lower() if src.suffix else ".png"
        dest = dest_dir / f"frame_{i:03d}{suffix}"
        try:
            shutil.copy2(src, dest)
        except OSError:
            continue
        out.append(f"studio/proof/{dest.name}")
    return out


def _write_manifest(root: Path, result: ProofResult) -> None:
    dest = proof_dir(root) / "latest.json"
    payload = {
        **result.as_dict(),
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        from .project_status import record_proof
        record_proof(root, result.summary, frames=result.frames)
    except Exception:
        log.debug("project_status.record_proof skipped", exc_info=True)


def _gap(slug: str, kind: str, tool: str, gap: str, summary: str) -> ProofResult:
    return ProofResult(
        ok=False,
        skipped=True,
        kind=kind,
        slug=slug,
        tool=tool,
        summary=summary,
        honest_gap=gap,
        error=gap,
    )


async def run_visual_proof(slug: str, kind: str = "static") -> ProofResult:
    """Open the running game. Never raises — infra failures become honest gaps."""
    slug = (slug or "").strip()
    kind = (kind or "static").strip().lower() or "static"
    if not slug:
        return ProofResult(
            ok=False, skipped=True, kind=kind, slug="", tool="browser",
            summary="No Playground slug — cannot open the game.",
            honest_gap="no_slug", error="no_slug",
        )
    if kind == "unity":
        return _gap(
            slug, kind, "engine", "unity_local",
            "Unity Editor is not in this process. Scaffold is not Play.",
        )
    if not is_visual_kind(kind):
        return ProofResult(
            ok=True, skipped=True, kind=kind, slug=slug, tool="browser",
            summary=f"Kind {kind} is not a visual playtest (no pixel proof required).",
        )
    try:
        from . import playground as pg
        root = pg.project_dir(slug)
    except Exception as exc:
        return ProofResult(
            ok=False, skipped=True, kind=kind, slug=slug, tool="browser",
            summary=f"Playground path failed: {exc}",
            error=str(exc)[:200],
        )
    if kind in ENGINE_KINDS:
        result = await _prove_godot(slug, root)
    else:
        result = await _prove_web(slug, kind, root)
    _write_manifest(root, result)
    return result


async def _prove_web(slug: str, kind: str, root: Path) -> ProofResult:
    try:
        from .playground_browser import verify_app
        raw = await verify_app(slug, kind)
    except Exception as exc:
        log.warning("visual_proof web failed for %s: %s", slug, exc)
        return ProofResult(
            ok=False, skipped=True, kind=kind, slug=slug, tool="browser",
            summary=f"Browser proof crashed: {exc}"[:_HEAD],
            error=str(exc)[:200],
        )
    skipped = bool(raw.get("skipped"))
    error = str(raw.get("error") or "")
    gap = ""
    if skipped or error in {"chromium_not_found", "playwright_unavailable"}:
        gap = "chromium_missing"
        skipped = True
    frames: list[str] = []
    shot = raw.get("screenshot_path")
    if shot:
        frames = _stash_frames(root, [root / str(shot)])
    ok = bool(raw.get("ok")) and not skipped and not gap
    summary = str(raw.get("summary") or ("visual proof skipped" if skipped else "visual proof"))
    return ProofResult(
        ok=ok,
        skipped=skipped,
        kind=kind,
        slug=slug,
        tool="browser",
        summary=summary,
        frames=frames,
        error=error if not ok else "",
        honest_gap=gap,
        blank_screen=bool(raw.get("blank_screen")),
        details={
            "reachable": raw.get("reachable"),
            "console_errors": (raw.get("console_errors") or [])[:5],
            "page_errors": (raw.get("page_errors") or [])[:5],
        },
    )


async def _prove_godot(slug: str, root: Path) -> ProofResult:
    from .godot_runtime_probe import godot_bin, run_runtime

    if not godot_bin():
        return _gap(
            slug, "godot", "engine", "godot_missing",
            "Godot not on PATH; static DoD only. Set GODOT_BIN. Files are not Play.",
        )
    try:
        info = run_runtime(root, frames=24)
    except Exception as exc:
        return ProofResult(
            ok=False, skipped=False, kind="godot", slug=slug, tool="engine",
            summary=f"Godot runtime probe crashed: {exc}"[:_HEAD],
            error=str(exc)[:200],
        )
    frames: list[str] = []
    try:
        from .engine_session import run_action
        shot = await run_action("screenshot", slug)
        rel = (shot or {}).get("path") or ""
        if rel:
            frames = _stash_frames(root, [root / str(rel)])
        elif (root / "studio" / "bridge_shot.png").is_file():
            frames = _stash_frames(root, [root / "studio" / "bridge_shot.png"])
    except Exception:
        log.debug("godot screenshot during proof skipped", exc_info=True)
    ok = bool(info.get("ok"))
    skipped = bool(info.get("skipped"))
    blocking = (info.get("dod") or {}).get("blocking") or []
    visual = {}
    review = {}
    try:
        from .godot_first import (
            ensure_contract, review_gate, slice_static, visual_quality, write_context,
        )
        pf = {
            "stage": "preflight",
            "ok": not skipped,
            "skipped": skipped,
            "blockers": ["godot_missing"] if skipped else [],
        }
        contract = ensure_contract(root, slug=slug)
        ctx = write_context(root)
        slice_row = slice_static(root)
        runtime_row = {
            "stage": "runtime",
            "ok": bool(info.get("ok")) and not skipped,
            "skipped": skipped,
            "blockers": list(blocking),
        }
        visual = visual_quality(root, slug=slug)
        review = review_gate(
            {
                "preflight": pf,
                "contract": contract,
                "context": ctx,
                "slice": slice_row,
                "runtime": runtime_row,
                "visual": visual,
            },
            root=root,
        )
        pipe_path = root / "studio" / "godot_pipeline.json"
        pipe_path.parent.mkdir(parents=True, exist_ok=True)
        pipe_path.write_text(
            json.dumps(
                {
                    "preflight": pf,
                    "contract": contract,
                    "context": ctx,
                    "slice": slice_row,
                    "runtime": runtime_row,
                    "visual": visual,
                    "review": review,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    except Exception:
        log.debug("godot-first pipeline during proof skipped", exc_info=True)
    summary = (
        f"Godot runtime {'ok' if ok else 'failed'}"
        + (f" blocking={blocking}" if blocking else "")
        + (f" skipped={info.get('skipped')}" if info.get("skipped") else "")
        + (f" review={(review or {}).get('verdict')}" if review else "")
    )
    gap = "godot_missing" if skipped else ""
    visual_ok = bool((visual or {}).get("ok", True))
    review_ok = bool((review or {}).get("ok", True))
    return ProofResult(
        ok=ok and not skipped and visual_ok and review_ok,
        skipped=skipped,
        kind="godot",
        slug=slug,
        tool="engine",
        summary=summary[:_HEAD],
        frames=frames,
        error=(info.get("error") or info.get("bridge_error") or "")[:200],
        honest_gap=gap,
        details={
            "dod": info.get("dod"),
            "bridge": info.get("bridge"),
            "visual": visual,
            "review": review,
        },
    )
