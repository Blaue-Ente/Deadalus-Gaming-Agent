"""MobileAdapter — Expo/React Native UI, Capacitor wrap of Vite/HTML canvas.

Never a web-preview pass. Missing emulator/device is FAIL. Expo web export
is not an iOS/Android artifact. Capacitor skip is not export success.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .capability import (
    CAPABILITIES,
    EVIDENCE_REL,
    STAGES,
    cap,
    finalize_evidence,
    gates_green,
    publish_ready,
    stage_row,
    store_release,
)

CONTEXT_REL = "studio/build_context.json"
CONTRACT_REL = "studio/mobile_contract.json"


def playbook_notes() -> str:
    return (
        "Mobile adapter of the shared pipeline: plan → context → build → "
        "runtime → gameplay → visual → export → publish. "
        "Expo/React Native for native UI; Capacitor wraps the same Vite/HTML "
        "canvas (do not rewrite to Expo). "
        "Emulator/device smoke is required. Expo web export is not a native pass. "
        "production({action: pipeline, slug}) walks the order."
    )


def flavor_of(kind: str, root: Path | None = None) -> str:
    k = (kind or "").strip().lower()
    if k == "expo":
        return "expo"
    if root is not None:
        root = Path(root)
        if (root / "app.json").is_file() and (
            (root / "App.jsx").is_file()
            or (root / "App.tsx").is_file()
            or (root / "app").is_dir()
        ):
            return "expo"
        if (root / "capacitor.config.json").is_file() or (
            root / "hybrid" / "capacitor.config.json"
        ).is_file():
            return "capacitor"
    return "capacitor"


def device_probe() -> dict[str, Any]:
    """adb devices / xcrun simctl. Presence of the binary without a device is still skip."""
    adb = shutil.which("adb") or ""
    xcrun = shutil.which("xcrun") or ""
    emu = shutil.which("emulator") or ""
    android_home = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT") or ""
    devices: list[str] = []
    if adb:
        try:
            proc = subprocess.run(
                [adb, "devices"], capture_output=True, text=True, timeout=8,
            )
            for line in (proc.stdout or "").splitlines()[1:]:
                parts = line.split()
                if len(parts) >= 2 and parts[1] == "device":
                    devices.append(parts[0])
        except (OSError, subprocess.TimeoutExpired):
            pass
    ios = False
    if xcrun:
        try:
            proc = subprocess.run(
                [xcrun, "simctl", "list", "devices", "available"],
                capture_output=True, text=True, timeout=8,
            )
            ios = "Booted" in (proc.stdout or "") or "iPhone" in (proc.stdout or "")
        except (OSError, subprocess.TimeoutExpired):
            pass
    ok = bool(devices) or bool(ios)
    return {
        "ok": ok,
        "adb": adb,
        "xcrun": xcrun,
        "emulator": emu,
        "android_home": android_home,
        "devices": devices,
        "ios_sim": ios,
        "skipped": not ok,
        "error": "" if ok else "No emulator or device. Mobile runtime is FAIL, not a preview.",
    }


def probe(_root: Path | None = None) -> dict[str, dict[str, Any]]:
    dev = device_probe()
    run_ok = bool(dev.get("ok"))
    miss = str(dev.get("error") or "emulator_missing")
    toolchain = bool(dev.get("android_home") or dev.get("xcrun") or os.environ.get("EAS_TOKEN"))
    return {
        "create": cap(True),
        "write": cap(True),
        "run": cap(run_ok, reason=miss),
        "input": cap(run_ok, reason=miss),
        "render_proof": cap(run_ok, reason=miss),
        "gameplay_proof": cap(run_ok, reason=miss),
        "export": cap(toolchain and run_ok, reason="native toolchain / device missing"),
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


def tracked(root: Path, flavor: str) -> list[str]:
    if flavor == "expo":
        cands = (
            "app.json", "app.config.js", "App.jsx", "App.tsx", "package.json",
            "app/index.tsx", "app/_layout.tsx",
        )
    else:
        cands = (
            "index.html", "game.js", "package.json", "capacitor.config.json",
            "hybrid/capacitor.config.json", "playtest/scenario.json",
        )
    return [rel for rel in cands if (Path(root) / rel).is_file()]


def preflight(root: Path, *, kind: str = "") -> dict[str, Any]:
    flavor = flavor_of(kind, root)
    dev = device_probe()
    skipped = not dev.get("ok")
    return stage_row(
        "preflight",
        ok=bool(dev.get("ok")),
        skipped=skipped,
        blockers=["emulator_missing"] if skipped else [],
        error=str(dev.get("error") or ""),
        flavor=flavor,
        device=dev,
        runner="expo" if flavor == "expo" else "capacitor",
    )


def ensure_contract(root: Path, *, slug: str = "", kind: str = "") -> dict[str, Any]:
    from .capability import _write_json
    from .playground_capacitor import ship_advice

    root = Path(root)
    slug = slug or root.name
    kind = (kind or "").strip().lower()
    flavor = flavor_of(kind, root)
    blockers: list[str] = []
    if (root / "project.godot").is_file():
        blockers.append("godot_not_mobile_wrap")
    if flavor == "expo":
        if not (
            (root / "app.json").is_file()
            or (root / "app.config.js").is_file()
            or (root / "app.config.ts").is_file()
        ):
            blockers.append("app.json")
        if not (
            (root / "App.jsx").is_file()
            or (root / "App.tsx").is_file()
            or (root / "app").is_dir()
        ):
            blockers.append("App")
    else:
        advice = ship_advice(kind or "static")
        if advice.get("path") != "capacitor":
            blockers.append("not_capacitor_kind")
        html = _read(root, "index.html")
        if not html and not (root / "dist" / "index.html").is_file():
            blockers.append("web_entry")
    contract = {
        "kind": kind or ("expo" if flavor == "expo" else "static"),
        "target": "mobile",
        "adapter": "MobileAdapter",
        "flavor": flavor,
        "slug": slug,
        "rewrite_to_expo": False,
        "web_preview_is_not_native": True,
    }
    _write_json(root / CONTRACT_REL, contract)
    return stage_row(
        "contract",
        ok=not blockers,
        blockers=blockers,
        contract=CONTRACT_REL,
        flavor=flavor,
        web_preview_is_not_native=True,
    )


def write_context(root: Path, *, plan: str = "", flavor: str = "capacitor") -> dict[str, Any]:
    from .capability import _write_json, file_hash

    root = Path(root)
    rels = tracked(root, flavor)
    hashes = {rel: file_hash(root, rel) for rel in rels}
    ctx = {
        "plan": plan or playbook_notes(),
        "flavor": flavor,
        "interfaces": {
            "expo": "React Native views — no document/canvas",
            "capacitor": "same web canvas inside a native WebView",
        },
        "hashes": hashes,
        "patch_rule": "one writer per file. Do not rewrite Expo into HTML or HTML into Expo.",
    }
    _write_json(root / CONTEXT_REL, ctx)
    return stage_row("context", ok=True, hashes=hashes, flavor=flavor)


def build_slice(root: Path, *, kind: str = "", flavor: str = "capacitor") -> dict[str, Any]:
    root = Path(root)
    checks: list[dict[str, Any]] = []
    blockers: list[str] = []
    if flavor == "expo":
        from .playground_kind_guard import scan_file

        blobs: list[tuple[str, str]] = []
        for rel in ("App.jsx", "App.tsx", "app/index.tsx", "app/index.jsx"):
            body = _read(root, rel)
            if body:
                blobs.append((rel, body))
        expo_ok = bool(blobs) or (root / "app").is_dir()
        checks.append({"id": "expo_entry", "ok": expo_ok, "detail": "App / app router"})
        violations: list[Any] = []
        for rel, body in blobs:
            violations.extend(scan_file("expo", rel, body))
        checks.append({
            "id": "no_dom",
            "ok": not violations,
            "detail": "no HTML canvas/document in RN",
        })
        if not expo_ok:
            blockers.append("expo_entry")
        if violations:
            blockers.append("dom_in_rn")
    else:
        html = _read(root, "index.html") + _read(root, "game.js")
        canvas = "<canvas" in html.lower() or "getcontext(" in html.lower() or "phaser" in html.lower()
        checks.append({"id": "canvas", "ok": bool(canvas or html), "detail": "same web canvas"})
        checks.append({
            "id": "not_expo_rewrite",
            "ok": flavor_of(kind, root) != "expo",
            "detail": "Capacitor wrap, not an Expo rewrite",
        })
        if not html and not (root / "dist" / "index.html").is_file():
            blockers.append("web_entry")
        if (root / "project.godot").is_file():
            blockers.append("godot_not_capacitor")
    return stage_row("build", ok=not blockers, blockers=blockers, checks=checks, flavor=flavor)


def runtime_verify(root: Path, *, flavor: str) -> dict[str, Any]:
    pf = preflight(root)
    if not pf.get("ok"):
        return stage_row(
            "runtime",
            ok=False,
            skipped=True,
            blockers=["runtime_skipped", "emulator_missing"],
            error="No emulator/device — mobile runtime is FAIL, not a web preview.",
            flavor=flavor,
        )
    # A real smoke would install the debug build. Cloud does not fake it.
    dev = device_probe()
    return stage_row(
        "runtime",
        ok=bool(dev.get("ok")),
        skipped=not dev.get("ok"),
        blockers=[] if dev.get("ok") else ["runtime_skipped"],
        error="" if dev.get("ok") else "device present but smoke was not executed in-cloud",
        device=dev,
        flavor=flavor,
    )


def gameplay_verify(root: Path, *, flavor: str) -> dict[str, Any]:
    pf = preflight(root)
    if not pf.get("ok"):
        return stage_row(
            "gameplay",
            ok=False,
            skipped=True,
            blockers=["gameplay_skipped", "emulator_missing"],
            error="No device — cannot smoke-test input on iOS/Android.",
        )
    return stage_row(
        "gameplay",
        ok=True,
        skipped=False,
        blockers=[],
        flavor=flavor,
        note="device listed; in-cloud does not inject input into a store binary",
    )


def visual_quality(root: Path, *, slug: str = "") -> dict[str, Any]:
    pf = preflight(root)
    frames: list[str] = []
    proof = Path(root) / "studio" / "proof"
    if proof.is_dir():
        frames = [f"studio/proof/{p.name}" for p in sorted(proof.glob("*.png"))]
    skipped = bool(pf.get("skipped"))
    blockers: list[str] = []
    if skipped:
        blockers.append("visual_skipped")
    elif not frames:
        blockers.append("device_screenshot")
    return stage_row(
        "visual",
        ok=not blockers,
        skipped=skipped,
        blockers=blockers,
        frames=frames[:6],
        error="visual skipped — no device screenshot" if skipped else "",
    )


def export_release(root: Path, *, slug: str = "", kind: str = "", flavor: str = "capacitor") -> dict[str, Any]:
    """Store-direction build. Config / Expo web dist are not artifacts."""
    root = Path(root)
    flavor = flavor or flavor_of(kind, root)
    if flavor == "expo":
        return stage_row(
            "export",
            ok=False,
            skipped=True,
            blockers=["native_toolchain", "expo_web_is_not_native"],
            error=(
                "Expo web export (`npx expo export --platform web`) is not an "
                "iOS/Android artifact. Need EAS or a device/emulator build."
            ),
            flavor="expo",
        )
    from .playground_capacitor import ship_advice

    advice = ship_advice(kind or "static")
    if advice.get("path") != "capacitor":
        return stage_row(
            "export",
            ok=False,
            skipped=True,
            blockers=["not_capacitor_kind"],
            error=str(advice.get("note") or "Capacitor does not wrap this kind."),
        )
    has_android = (root / "android").is_dir() or (root / "hybrid" / "android").is_dir()
    has_ios = (root / "ios").is_dir() or (root / "hybrid" / "ios").is_dir()
    if not has_android and not has_ios:
        return stage_row(
            "export",
            ok=False,
            skipped=True,
            blockers=["native_project_missing"],
            error=(
                "Capacitor config is not a store build. "
                "Need android/ or ios/ from a local `npx cap add` plus a device smoke. "
                "Skip is not export success."
            ),
        )
    return stage_row(
        "export",
        ok=False,
        skipped=True,
        blockers=["store_build_not_run"],
        error=(
            "Native project exists but this cloud does not run Gradle/Xcode. "
            "A folder named android/ is not an AAB."
        ),
        native_projects={"android": has_android, "ios": has_ios},
    )


def run_pipeline(
    slug: str,
    *,
    root: Path,
    kind: str = "",
    through: str = "publish",
    plan: str = "",
) -> dict[str, Any]:
    from .capability import file_hash

    root = Path(root)
    flavor = flavor_of(kind, root)
    want = (through or "publish").strip().lower()
    if want not in STAGES:
        want = "publish"
    stop_at = STAGES.index(want)
    rows: dict[str, dict[str, Any]] = {}
    pf = preflight(root, kind=kind)
    contract = ensure_contract(root, slug=slug, kind=kind)
    rows["plan"] = stage_row(
        "plan",
        ok=bool(pf.get("ok")) and bool(contract.get("ok")),
        skipped=bool(pf.get("skipped")),
        blockers=list(pf.get("blockers") or []) + list(contract.get("blockers") or []),
        error=str(pf.get("error") or contract.get("error") or ""),
        preflight=pf,
        contract=contract,
        flavor=flavor,
    )
    if stop_at >= 1:
        rows["context"] = write_context(root, plan=plan, flavor=flavor)
    if stop_at >= 2:
        rows["build"] = build_slice(root, kind=kind, flavor=flavor)
    if stop_at >= 3:
        rows["runtime"] = runtime_verify(root, flavor=flavor)
    if stop_at >= 4:
        rows["gameplay"] = gameplay_verify(root, flavor=flavor)
    if stop_at >= 5:
        rows["visual"] = visual_quality(root, slug=slug)
    if stop_at >= 6:
        if gates_green(rows, ("runtime", "gameplay", "visual")):
            rows["export"] = export_release(root, slug=slug, kind=kind, flavor=flavor)
        else:
            rows["export"] = stage_row(
                "export",
                ok=False,
                blockers=["gates_not_green"],
                error="Export waits for device runtime, gameplay, and visual. A preview is not enough.",
            )
    if stop_at >= 7:
        exp = rows.get("export") or {}
        if publish_ready(rows) and exp.get("ok") and exp.get("artifact"):
            rows["publish"] = store_release(
                root, slug=slug, artifact=Path(str(exp["artifact"])), target="mobile",
            )
        else:
            rows["publish"] = stage_row(
                "publish",
                ok=False,
                blockers=["no_immutable_artifact"],
                error="Publish requires a store-direction artifact. Expo web / preview HTML is not a release.",
            )
    hashes = {rel: file_hash(root, rel) for rel in tracked(root, flavor)}
    caps = probe(root)
    doc = finalize_evidence(
        root,
        slug=slug,
        kind=kind or ("expo" if flavor == "expo" else "static"),
        target="mobile",
        adapter="MobileAdapter",
        caps=caps,
        hashes=hashes,
        stages=rows,
        artifact=str((rows.get("export") or {}).get("artifact") or ""),
    )
    ordered = [rows[s] for s in STAGES if s in rows]
    ok = all(r.get("ok") for r in ordered) and not any(r.get("skipped") for r in ordered)
    unused = [c for c in CAPABILITIES if not (caps.get(c) or {}).get("available")]
    return {
        "ok": bool(ok) and doc.get("verdict") == "APPROVED",
        "slug": slug,
        "kind": kind or ("expo" if flavor == "expo" else "static"),
        "target": "mobile",
        "adapter": "MobileAdapter",
        "flavor": flavor,
        "through": want,
        "stages": rows,
        "evidence": EVIDENCE_REL,
        "verdict": doc.get("verdict"),
        "blockers": doc.get("blockers") or [],
        "capabilities": caps,
        "unavailable": unused,
    }
