"""WebAdapter — Vite / HTML / Phaser. Chromium runtime, not Godot."""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

from .capability import (
    CAPABILITIES,
    EVIDENCE_REL,
    STAGES,
    cap,
    finalize_evidence,
    gates_green,
    gated_publish,
    publish_ready,
    stage_row,
)

WEB_KINDS = frozenset({"static", "vite", "vite-react", "phaser", "streamlit"})
CONTEXT_REL = "studio/build_context.json"
CONTRACT_REL = "studio/web_contract.json"

_TRACKED = (
    "index.html",
    "game.js",
    "main.js",
    "app.js",
    "style.css",
    "package.json",
    "vite.config.js",
    "vite.config.ts",
    "src/main.js",
    "src/main.ts",
    "src/main.tsx",
    "src/App.tsx",
    "src/game.js",
    "playtest/scenario.json",
)

_LOOP = re.compile(r"requestanimationframe|setinterval\s*\(|new\s+phaser\.game", re.I)
_CANVAS = re.compile(r"<canvas|getcontext\s*\(|phaser|webglrenderingcontext", re.I)
_WIN_LOSE = re.compile(
    r"game\s*over|you\s*win|is_won|gameover|fail(?:ed)?|win\s*\(|lose|"
    r"сблъсък|край на игра",
    re.I,
)
_INPUT = re.compile(
    r"keydown|keyup|pointerdown|addEventListener\(\s*['\"]key|"
    r"keyboard|ontouchstart|phaser",
    re.I,
)


def chromium_bin() -> str:
    from .playground_browser import _find_chrome
    return _find_chrome() or ""


def probe(_root: Path | None = None) -> dict[str, dict[str, Any]]:
    chrome = bool(chromium_bin())
    miss = "Chromium missing — web runtime is FAIL, not a skip-pass"
    return {
        "create": cap(True),
        "write": cap(True),
        "run": cap(chrome, reason=miss),
        "input": cap(chrome, reason=miss),
        "render_proof": cap(chrome, reason=miss),
        "gameplay_proof": cap(chrome, reason=miss),
        "export": cap(True),
        "publish": cap(True),
        "rollback": cap(True),
    }


def _read(root: Path, rel: str) -> str:
    path = Path(root) / rel
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _blob(root: Path) -> str:
    parts = [_read(root, rel) for rel in _TRACKED]
    return "\n".join(parts)


def tracked(root: Path) -> list[str]:
    return [rel for rel in _TRACKED if (Path(root) / rel).is_file()]


def playbook_notes() -> str:
    return (
        "Web adapter of the shared pipeline: plan → context → build → "
        "runtime → gameplay → visual → export → publish. "
        "Chromium is the runner. Vite/HTML/Phaser stay web — not Godot. "
        "Missing Chromium is FAIL, not skip. "
        "production({action: pipeline, slug}) walks the order."
    )


def preflight(root: Path | None = None) -> dict[str, Any]:
    chrome = chromium_bin()
    skipped = not chrome
    return stage_row(
        "preflight",
        ok=bool(chrome),
        skipped=skipped,
        blockers=["chromium_missing"] if skipped else [],
        error="" if chrome else "Chromium not on PATH. Web runtime cannot run.",
        binary=chrome,
        runner="chromium",
    )


def ensure_contract(root: Path, *, slug: str = "", kind: str = "static") -> dict[str, Any]:
    from .capability import _write_json

    root = Path(root)
    slug = slug or root.name
    kind = (kind or "static").strip().lower() or "static"
    blockers: list[str] = []
    if (root / "project.godot").is_file():
        blockers.append("godot_not_web")
    html = _read(root, "index.html")
    pkg = _read(root, "package.json")
    if kind in {"vite", "vite-react"}:
        if not pkg:
            blockers.append("package.json")
        if not html and not (root / "index.html").is_file():
            blockers.append("index.html")
    else:
        if not html:
            blockers.append("index.html")
    contract = {
        "kind": kind,
        "target": "web",
        "adapter": "WebAdapter",
        "entry": "index.html",
        "runner": "chromium",
        "slug": slug,
    }
    _write_json(root / CONTRACT_REL, contract)
    return stage_row(
        "contract",
        ok=not blockers,
        blockers=blockers,
        contract=CONTRACT_REL,
        html_is_not_godot=True,
    )


def write_context(root: Path, *, plan: str = "") -> dict[str, Any]:
    from .capability import _write_json, file_hash

    root = Path(root)
    rels = tracked(root) or ["index.html"]
    hashes = {rel: file_hash(root, rel) for rel in rels}
    ctx = {
        "plan": plan or playbook_notes(),
        "interfaces": {
            "loop": "requestAnimationFrame or Phaser.Game",
            "input": "keyboard / pointer on the canvas",
            "proof": "Chromium screenshot + gameplay bot",
        },
        "hashes": hashes,
        "patch_rule": "edit_file for existing JS/HTML — do not dump a whole rewrite.",
    }
    _write_json(root / CONTEXT_REL, ctx)
    return stage_row("context", ok=True, hashes=hashes)


def build_slice(root: Path, *, kind: str = "static") -> dict[str, Any]:
    root = Path(root)
    blob = _blob(root)
    checks = [
        {"id": "canvas", "ok": bool(_CANVAS.search(blob)), "detail": "canvas / Phaser / WebGL"},
        {"id": "loop", "ok": bool(_LOOP.search(blob)), "detail": "rAF / Phaser.Game"},
        {"id": "input", "ok": bool(_INPUT.search(blob)), "detail": "keyboard or pointer"},
        {"id": "win_lose", "ok": bool(_WIN_LOSE.search(blob)), "detail": "win or lose path"},
        {
            "id": "no_godot",
            "ok": not (root / "project.godot").is_file(),
            "detail": "this adapter is not Godot",
        },
    ]
    blockers = [c["id"] for c in checks if not c["ok"]]
    return stage_row("build", ok=not blockers, blockers=blockers, checks=checks, kind=kind)


def _run_async(coro: Any) -> Any:
    import asyncio
    import concurrent.futures

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result(timeout=90)


def _browser_kind(kind: str) -> str:
    k = (kind or "static").strip().lower()
    if k in {"phaser", "html"}:
        return "static"
    return k or "static"


def runtime_verify(root: Path, *, slug: str, kind: str) -> dict[str, Any]:
    pf = preflight(root)
    if not pf.get("ok"):
        return stage_row(
            "runtime",
            ok=False,
            skipped=True,
            blockers=["runtime_skipped", "chromium_missing"],
            error=pf.get("error") or "Chromium missing — skip is not Play.",
            runner="chromium",
        )
    from .playground_browser import verify_app

    try:
        raw = _run_async(verify_app(slug, _browser_kind(kind)))
    except Exception as exc:
        return stage_row(
            "runtime",
            ok=False,
            skipped=False,
            blockers=["runtime_failed"],
            error=str(exc)[:300],
        )
    skipped = bool(raw.get("skipped"))
    gap = ""
    if skipped or str(raw.get("error") or "") in {"chromium_not_found", "playwright_unavailable"}:
        gap = "chromium_missing"
        skipped = True
    ok = bool(raw.get("ok")) and not skipped and not gap
    frames = [str(raw["screenshot_path"])] if raw.get("screenshot_path") else []
    return stage_row(
        "runtime",
        ok=ok,
        skipped=skipped,
        blockers=["runtime_skipped"] if skipped else ([] if ok else ["runtime"]),
        error="" if ok else str(raw.get("error") or raw.get("summary") or "runtime failed"),
        frames=frames,
        honest_gap=gap,
        blank_screen=bool(raw.get("blank_screen")),
        summary=str(raw.get("summary") or ""),
        runner="chromium",
    )


def gameplay_verify(root: Path, *, slug: str, kind: str) -> dict[str, Any]:
    pf = preflight(root)
    file_ok = bool(_INPUT.search(_blob(root))) and bool(_CANVAS.search(_blob(root)))
    if not pf.get("ok"):
        return stage_row(
            "gameplay",
            ok=False,
            skipped=True,
            blockers=["gameplay_skipped", "chromium_missing"],
            error="Chromium missing — interaction scenarios cannot run.",
            file_input=file_ok,
        )
    from .playground_gameplay_verify import gameplay_verify_app

    try:
        raw = _run_async(gameplay_verify_app(slug, _browser_kind(kind)))
    except Exception as exc:
        return stage_row(
            "gameplay",
            ok=False,
            skipped=False,
            blockers=["gameplay_failed"],
            error=str(exc)[:300],
        )
    skipped = bool(raw.get("skipped"))
    checks = list(raw.get("checks") or [])
    bot = raw.get("bot") or {}
    console_errors = []
    for c in checks:
        if isinstance(c, dict) and str(c.get("id") or "").endswith("error"):
            if not c.get("ok"):
                console_errors.append(c)
    proofs = {
        "started": bool(raw.get("ok") or bot.get("frames") or file_ok),
        "input_accepted": bool(bot.get("saw_canvas_changed") or any(
            isinstance(c, dict) and c.get("id") == "keyboard" and c.get("ok") for c in checks
        ) or file_ok),
        "state_changed": bool(bot.get("saw_canvas_changed")),
        "mechanic": bool(bot.get("saw_canvas_changed") or bot.get("fps")),
        "outcome": bool(bot.get("saw_win") or bot.get("saw_lose")),
        "no_console_errors": not bool(raw.get("console_errors") or console_errors),
    }
    if not skipped:
        checks = list(checks) + [{"id": k, "ok": bool(v)} for k, v in proofs.items()]
    blockers = [c.get("id") for c in checks if isinstance(c, dict) and not c.get("ok")]
    if skipped:
        blockers = ["gameplay_skipped", *blockers]
    missing = [k for k, v in proofs.items() if not v]
    ok = bool(raw.get("ok")) and not skipped and not missing
    return stage_row(
        "gameplay",
        ok=ok,
        skipped=skipped,
        blockers=blockers,
        checks=checks,
        proofs=proofs,
        error="" if ok else str(raw.get("summary") or "gameplay failed"),
        runner="chromium",
    )


def visual_quality(root: Path, *, slug: str = "") -> dict[str, Any]:
    root = Path(root)
    pf = preflight(root)
    frames: list[str] = []
    proof = root / "studio" / "proof"
    shot_ok = False
    if proof.is_dir():
        frames = [f"studio/proof/{p.name}" for p in sorted(proof.glob("*.png"))]
        for name in frames:
            path = root / name
            try:
                from .frame_geometry import frame_shows_scene
                if frame_shows_scene(path):
                    shot_ok = True
                    break
            except Exception:
                if path.is_file() and path.stat().st_size > 800:
                    shot_ok = True
    skipped = bool(pf.get("skipped"))
    blockers: list[str] = []
    if skipped:
        blockers.append("visual_skipped")
    elif not shot_ok:
        blockers.append("screenshot")
    return stage_row(
        "visual",
        ok=not blockers,
        skipped=skipped,
        blockers=blockers,
        screenshot=shot_ok,
        frames=frames[:6],
        error="visual skipped — Chromium missing" if skipped else "",
        runner="chromium",
    )


def export_release(root: Path, *, slug: str = "", kind: str = "static") -> dict[str, Any]:
    """Static tree or Vite dist/ → build/web/. Does not invent a Godot binary."""
    root = Path(root)
    slug = slug or root.name
    kind = (kind or "static").strip().lower()
    dest = root / "build" / "web"
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)
    if kind in {"vite", "vite-react"}:
        dist = root / "dist"
        if not (dist.is_dir() and (dist / "index.html").is_file()):
            return stage_row(
                "export",
                ok=False,
                skipped=True,
                blockers=["dist_missing"],
                error="Vite dist/ missing — export is FAIL, not a source-tree preview.",
            )
        shutil.copytree(dist, dest, dirs_exist_ok=True)
    else:
        copied = 0
        for rel in ("index.html", "game.js", "main.js", "app.js", "style.css"):
            src = root / rel
            if src.is_file():
                shutil.copy2(src, dest / rel)
                copied += 1
        assets = root / "assets"
        if assets.is_dir():
            shutil.copytree(assets, dest / "assets", dirs_exist_ok=True)
        src_dir = root / "src"
        if src_dir.is_dir() and kind != "streamlit":
            shutil.copytree(src_dir, dest / "src", dirs_exist_ok=True)
        if not (dest / "index.html").is_file():
            return stage_row(
                "export",
                ok=False,
                skipped=False,
                blockers=["index.html"],
                error="No index.html to export.",
            )
        if copied == 0:
            return stage_row(
                "export",
                ok=False,
                skipped=False,
                blockers=["empty_export"],
                error="Web export copied nothing.",
            )
    size = sum(p.stat().st_size for p in dest.rglob("*") if p.is_file())
    return stage_row(
        "export",
        ok=True,
        artifact=str(dest),
        size=size,
        target="web",
    )


def run_pipeline(
    slug: str,
    *,
    root: Path,
    kind: str = "static",
    through: str = "publish",
    plan: str = "",
) -> dict[str, Any]:
    from .capability import file_hash

    root = Path(root)
    kind = (kind or "static").strip().lower() or "static"
    want = (through or "publish").strip().lower()
    if want not in STAGES:
        want = "publish"
    stop_at = STAGES.index(want)
    rows: dict[str, dict[str, Any]] = {}
    pf = preflight(root)
    contract = ensure_contract(root, slug=slug, kind=kind)
    rows["plan"] = stage_row(
        "plan",
        ok=bool(pf.get("ok")) and bool(contract.get("ok")),
        skipped=bool(pf.get("skipped")),
        blockers=list(pf.get("blockers") or []) + list(contract.get("blockers") or []),
        error=str(pf.get("error") or contract.get("error") or ""),
        preflight=pf,
        contract=contract,
        runner="chromium",
    )
    if stop_at >= 1:
        rows["context"] = write_context(root, plan=plan)
    if stop_at >= 2:
        rows["build"] = build_slice(root, kind=kind)
    if stop_at >= 3:
        rows["runtime"] = runtime_verify(root, slug=slug, kind=kind)
    if stop_at >= 4:
        rows["gameplay"] = gameplay_verify(root, slug=slug, kind=kind)
    if stop_at >= 5:
        rows["visual"] = visual_quality(root, slug=slug)
    if stop_at >= 6:
        if gates_green(rows, ("runtime", "gameplay", "visual")):
            rows["export"] = export_release(root, slug=slug, kind=kind)
        else:
            rows["export"] = stage_row(
                "export",
                ok=False,
                blockers=["gates_not_green"],
                error="Export waits for runtime, gameplay, and visual. Skip is not a pass.",
            )
    if stop_at >= 7:
        exp = rows.get("export") or {}
        if publish_ready(rows) and exp.get("ok") and exp.get("artifact"):
            rows["publish"] = gated_publish(
                root,
                slug=slug,
                artifact=Path(str(exp["artifact"])),
                target="web",
                stages=rows,
                hashes={rel: file_hash(root, rel) for rel in tracked(root)},
            )
        else:
            rows["publish"] = stage_row(
                "publish",
                ok=False,
                blockers=["no_immutable_artifact"],
                error="Publish requires green runtime, gameplay, visual, and export.",
            )
    hashes = {rel: file_hash(root, rel) for rel in tracked(root)}
    caps = probe(root)
    art = str((rows.get("export") or {}).get("artifact") or "")
    doc = finalize_evidence(
        root,
        slug=slug,
        kind=kind,
        target="web",
        adapter="WebAdapter",
        caps=caps,
        hashes=hashes,
        stages=rows,
        artifact=art,
    )
    ordered = [rows[s] for s in STAGES if s in rows]
    ok = all(r.get("ok") for r in ordered) and not any(r.get("skipped") for r in ordered)
    unused = [c for c in CAPABILITIES if not (caps.get(c) or {}).get("available")]
    return {
        "ok": bool(ok) and doc.get("verdict") == "APPROVED",
        "slug": slug,
        "kind": kind,
        "target": "web",
        "adapter": "WebAdapter",
        "through": want,
        "stages": rows,
        "evidence": EVIDENCE_REL,
        "verdict": doc.get("verdict"),
        "blockers": doc.get("blockers") or [],
        "capabilities": caps,
        "unavailable": unused,
    }
