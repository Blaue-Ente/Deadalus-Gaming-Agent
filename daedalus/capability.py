"""Capability-first production kernel — Godot-strong, not Godot-only.

The kernel walks one order for every target:

    plan → context → build → runtime → gameplay → visual → export → publish

It does not know HTML, Godot, or iOS. Adapters declare capabilities
(create, write, run, input, render_proof, gameplay_proof, export, publish,
rollback). A missing runner is FAIL, not a skip-pass, and never a crosswalk
(Godot ↛ HTML, Expo ↛ static preview, mobile ↛ screenshot-only).
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STAGES = (
    "plan",
    "context",
    "build",
    "runtime",
    "gameplay",
    "visual",
    "export",
    "publish",
)

CAPABILITIES = (
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

WEB_KINDS = frozenset({"static", "vite", "vite-react", "phaser", "streamlit"})
ENGINE_KINDS = frozenset({"godot"})
MOBILE_KINDS = frozenset({"expo"})
EVIDENCE_REL = "studio/capability_evidence.json"
CONTRACT_REL = "studio/capability_contract.json"
TOOL_VERSION = "1"

PUBLISH_GATES = ("runtime", "gameplay", "visual", "export")

WRITER_ROLES = frozenset({"coder", "builder", "specialist", "polish"})
READ_ROLES = frozenset({"explorer", "planner", "verify", "verifier"})
ROLE_ALIASES = {
    "planner": "explorer",
    "builder": "coder",
    "specialist": "coder",
    "verifier": "verify",
    "polish": "coder",
}

_MOBILE_ASK = (
    r"\b(expo|react[- ]native|capacitor|eas(?:\s+build)?|"
    r"ios|iphone|ipad|android|apk|aab|ipa|"
    r"app store|play store|google play)\b"
)

# slug -> {rel_or_*: holder_id}
_WRITE_LOCKS: dict[str, dict[str, str]] = {}
_LOCK = threading.Lock()


def wants_mobile(text: str) -> bool:
    return bool(re.search(_MOBILE_ASK, text or "", re.I))


def resolve_target(kind: str, text: str = "", path: str = "") -> str:
    """Pick web | engine | mobile | unity. kind=godot never becomes web."""
    from .godot_first import coerce_kind

    k = coerce_kind(kind or "", description=text, path=path)
    if k == "godot":
        return "engine"
    if k == "unity":
        return "unity"
    if k == "expo":
        return "mobile"
    if k in WEB_KINDS:
        if wants_mobile(text):
            return "mobile"
        return "web"
    if wants_mobile(text):
        return "mobile"
    return k or "web"


def refuse_crosswalk(kind: str, target: str) -> dict[str, Any] | None:
    """Honest block instead of turning Godot into HTML or Expo into a preview."""
    k = (kind or "").strip().lower()
    t = (target or "").strip().lower()
    if k == "godot" and t in {"web", "mobile"}:
        return {
            "ok": False,
            "skipped": False,
            "blockers": ["no_crosswalk"],
            "error": (
                "kind=godot stays on EngineAdapter. "
                "Godot is not HTML and Capacitor does not wrap .tscn."
            ),
        }
    if k == "expo" and t == "web":
        return {
            "ok": False,
            "skipped": False,
            "blockers": ["no_crosswalk"],
            "error": (
                "Expo web export is not an iOS/Android pass. "
                "MobileAdapter only — never a static preview success."
            ),
        }
    if k == "unity" and t in {"web", "engine", "mobile"}:
        return {
            "ok": False,
            "skipped": True,
            "honest_gap": "unity_local",
            "blockers": ["unity_local"],
            "error": "Unity Editor is not in this process. Scaffold is not Play.",
        }
    return None


def stage_row(
    stage: str,
    *,
    ok: bool,
    skipped: bool = False,
    blockers: list[str] | None = None,
    error: str = "",
    **extra: Any,
) -> dict[str, Any]:
    row = {
        "stage": stage,
        "ok": bool(ok) and not skipped,
        "skipped": bool(skipped),
        "blockers": list(blockers or []),
        "error": error,
    }
    row.update(extra)
    if row["skipped"]:
        row["ok"] = False
        if "skipped" not in row["blockers"] and not any(
            str(b).endswith("_skipped") or str(b).endswith("_missing") for b in row["blockers"]
        ):
            row["blockers"] = [f"{stage}_skipped", *row["blockers"]]
    return row


def file_hash(root: Path, rel: str) -> str:
    path = Path(root) / rel
    data = path.read_bytes() if path.is_file() else b""
    return hashlib.sha256(data).hexdigest()[:16]


def revision_hash(root: Path, rels: list[str]) -> str:
    h = hashlib.sha256()
    for rel in rels:
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        path = Path(root) / rel
        if path.is_file():
            h.update(path.read_bytes())
        h.update(b"\n")
    return h.hexdigest()[:24]


def artifact_hash(path: Path) -> str:
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    if not path.is_dir():
        return ""
    h = hashlib.sha256()
    for p in sorted(path.rglob("*")):
        if not p.is_file():
            continue
        h.update(str(p.relative_to(path)).encode("utf-8"))
        h.update(path_bytes := p.read_bytes())
        h.update(str(len(path_bytes)).encode("ascii"))
    return h.hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def evidence_path(root: Path) -> Path:
    return Path(root) / EVIDENCE_REL


def load_evidence(root: Path) -> dict[str, Any] | None:
    path = evidence_path(root)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def write_evidence(root: Path, doc: dict[str, Any]) -> Path:
    path = evidence_path(root)
    _write_json(path, doc)
    return path


def stale_hashes(root: Path, hashes: dict[str, str] | None) -> bool:
    if not hashes:
        return False
    for rel, prev in hashes.items():
        if prev and prev != file_hash(root, rel):
            return True
    return False


def verdict_of(stages: dict[str, dict[str, Any]], *, stale: bool = False) -> str:
    if stale:
        return "BLOCKED"
    for name in STAGES:
        row = stages.get(name)
        if not row:
            continue
        if row.get("skipped") or row.get("ok") is False:
            return "BLOCKED"
    for name in PUBLISH_GATES:
        row = stages.get(name) or {}
        if not row.get("ok") or row.get("skipped"):
            if name in stages:
                return "BLOCKED"
    return "APPROVED"


def gates_green(
    stages: dict[str, dict[str, Any]],
    names: tuple[str, ...] = PUBLISH_GATES,
    *,
    stale: bool = False,
) -> bool:
    if stale:
        return False
    for name in names:
        row = stages.get(name) or {}
        if not row.get("ok") or row.get("skipped"):
            return False
    return True


def publish_ready(stages: dict[str, dict[str, Any]], *, stale: bool = False) -> bool:
    return gates_green(stages, PUBLISH_GATES, stale=stale)


def adn_publish_block(root: Path, kind: str = "") -> str:
    """Block ADN zip only when capability evidence exists and is not APPROVED."""
    doc = load_evidence(root)
    if not doc:
        return ""
    if str(doc.get("verdict") or "") != "APPROVED":
        return (
            doc.get("error")
            or "capability pipeline is BLOCKED — ADN zip is not a release"
        )
    if str(doc.get("target") or "") == "mobile":
        return (
            "MobileAdapter publish is the store-direction artifact, "
            "not an Expo/Vite web preview zip."
        )
    return ""


def store_release(
    root: Path,
    *,
    slug: str,
    artifact: Path,
    target: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Copy an immutable artifact to $DAEDALUS_DATA/releases/{slug}/ with rollback."""
    from .paths import data_root

    root = Path(root)
    slug = slug or root.name
    src = Path(artifact)
    if not src.is_file() and not src.is_dir():
        return stage_row(
            "publish",
            ok=False,
            blockers=["no_immutable_artifact"],
            error="Publish requires a green export artifact. Preview HTML is not a release.",
        )
    digest = artifact_hash(src)
    rel_dir = data_root() / "releases" / slug
    current = rel_dir / "current"
    previous = rel_dir / "previous"
    rel_dir.mkdir(parents=True, exist_ok=True)
    prev_hash = ""
    if current.exists():
        if previous.exists():
            shutil.rmtree(previous, ignore_errors=True)
        shutil.move(str(current), str(previous))
        man_prev = previous / "manifest.json"
        if man_prev.is_file():
            try:
                prev_hash = str(json.loads(man_prev.read_text(encoding="utf-8")).get("sha256") or "")
            except Exception:
                prev_hash = ""
    current.mkdir(parents=True, exist_ok=True)
    if src.is_file():
        dest = current / src.name
        shutil.copy2(src, dest)
        artifact_name = dest.name
        dest_str = str(dest)
    else:
        dest = current / "bundle"
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        shutil.copytree(src, dest)
        artifact_name = "bundle"
        dest_str = str(dest)
    version = _next_version(root)
    manifest = {
        "slug": slug,
        "version": version,
        "target": target,
        "artifact": artifact_name,
        "sha256": digest,
        "previous": prev_hash,
        "published_at": datetime.now(timezone.utc).isoformat(),
        "url": f"/releases/{slug}/current/{artifact_name}",
        **(extra or {}),
    }
    _write_json(current / "manifest.json", manifest)
    _write_json(root / "studio" / "release_manifest.json", manifest)
    return stage_row(
        "publish",
        ok=True,
        version=version,
        sha256=digest,
        artifact=dest_str,
        previous=prev_hash,
        url=manifest["url"],
        rollback=str(previous) if prev_hash else "",
        target=target,
    )


def _next_version(root: Path) -> str:
    man = Path(root) / "studio" / "release_manifest.json"
    try:
        cur = str((json.loads(man.read_text(encoding="utf-8")) if man.is_file() else {}).get("version") or "0.0.0")
    except Exception:
        cur = "0.0.0"
    parts = [int(x) if x.isdigit() else 0 for x in cur.split(".")[:3]]
    while len(parts) < 3:
        parts.append(0)
    parts[2] += 1
    return ".".join(str(x) for x in parts)


def cap(available: bool, *, reason: str = "", tool_version: str = TOOL_VERSION) -> dict[str, Any]:
    return {
        "available": bool(available),
        "reason": "" if available else (reason or "unavailable"),
        "tool_version": tool_version,
    }


def probe_capabilities(target: str, root: Path | None = None) -> dict[str, dict[str, Any]]:
    """What this machine can actually do for the target. Missing runner = unavailable."""
    t = (target or "").strip().lower()
    if t == "web":
        from .web_first import probe as web_probe
        return web_probe(root)
    if t == "engine":
        from .godot_first import preflight
        pf = preflight(root)
        run_ok = bool(pf.get("ok")) and not pf.get("skipped")
        export_ok = bool(pf.get("export_capability")) and run_ok
        return {
            "create": cap(True),
            "write": cap(True),
            "run": cap(run_ok, reason=str(pf.get("error") or "Godot missing")),
            "input": cap(run_ok, reason="Godot missing — no input probe"),
            "render_proof": cap(run_ok, reason="Godot missing — no render proof"),
            "gameplay_proof": cap(run_ok, reason="Godot missing — no gameplay proof"),
            "export": cap(export_ok, reason="Godot export templates missing"),
            "publish": cap(True, reason=""),
            "rollback": cap(True),
        }
    if t == "mobile":
        from .mobile_first import probe as mobile_probe
        return mobile_probe(root)
    if t == "unity":
        gap = "unity_local"
        return {name: cap(name in {"create", "write", "rollback"}, reason=gap) for name in CAPABILITIES}
    return {name: cap(False, reason="unknown_target") for name in CAPABILITIES}


def normalize_role(role: str) -> str:
    r = (role or "coder").strip().lower()
    return ROLE_ALIASES.get(r, r)


def is_writer_role(role: str) -> bool:
    r = (role or "").strip().lower()
    if r in READ_ROLES:
        return False
    return r in WRITER_ROLES or normalize_role(r) == "coder"


def polish_allowed(root: Path | None = None, slug: str = "") -> bool:
    """Polish writes only after a real visual-quality failure."""
    doc = None
    if root is not None:
        doc = load_evidence(root)
    if doc is None and slug:
        try:
            from . import playground as pg
            doc = load_evidence(pg.project_dir(slug))
        except Exception:
            doc = None
    visual = ((doc or {}).get("stages") or {}).get("visual") or {}
    return visual.get("ok") is False


def acquire_write(
    slug: str,
    *,
    holder: str,
    files: list[str] | None = None,
) -> dict[str, Any]:
    """One writer per file. Empty files list locks the whole slug."""
    slug = (slug or "").strip()
    holder = (holder or "").strip() or "writer"
    keys = [str(f).replace("\\", "/").lstrip("/") for f in (files or []) if str(f).strip()]
    if not keys:
        keys = ["*"]
    with _LOCK:
        locks = _WRITE_LOCKS.setdefault(slug, {})
        for key in keys:
            other = locks.get(key) or locks.get("*")
            if key != "*" and locks.get("*") and locks["*"] != holder:
                other = locks["*"]
            if other and other != holder:
                return {
                    "ok": False,
                    "error": f"exclusive writer: {key} held by {other}",
                    "holder": other,
                    "file": key,
                }
            if key == "*":
                others = {h for k, h in locks.items() if h != holder}
                if others:
                    who = sorted(others)[0]
                    return {
                        "ok": False,
                        "error": f"exclusive writer: slug held by {who}",
                        "holder": who,
                        "file": "*",
                    }
        for key in keys:
            locks[key] = holder
    return {"ok": True, "slug": slug, "holder": holder, "files": keys}


def release_write(slug: str, holder: str = "") -> None:
    slug = (slug or "").strip()
    with _LOCK:
        locks = _WRITE_LOCKS.get(slug) or {}
        if holder:
            drop = [k for k, v in locks.items() if v == holder]
            for k in drop:
                locks.pop(k, None)
        else:
            _WRITE_LOCKS.pop(slug, None)
        if not locks:
            _WRITE_LOCKS.pop(slug, None)


def clear_write_locks() -> None:
    with _LOCK:
        _WRITE_LOCKS.clear()


def refuse_parallel_writers(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep at most one writer per slug unless file sets are disjoint and named."""
    out: list[dict[str, Any]] = []
    claimed: dict[str, set[str]] = {}
    holders: dict[str, str] = {}
    for i, raw in enumerate(calls or []):
        call = dict(raw)
        role = str(call.get("role") or "coder").strip().lower()
        slug = str(call.get("slug") or "").strip()
        if role not in WRITER_ROLES and normalize_role(role) != "coder":
            out.append(call)
            continue
        if role in READ_ROLES:
            out.append(call)
            continue
        files = {
            str(f).replace("\\", "/").lstrip("/")
            for f in (call.get("files") or [])
            if str(f).strip()
        }
        prev = claimed.get(slug)
        if prev is None:
            claimed[slug] = files or {"*"}
            holders[slug] = str(call.get("holder") or f"writer-{i}")
            out.append(call)
            continue
        overlap = (not files) or ("*" in prev) or (bool(files & prev))
        if overlap:
            call["_refuse"] = {
                "ok": False,
                "error": (
                    f"No parallel writers on {slug}. "
                    "One builder/specialist per file; verifier does not write."
                ),
                "slug": slug,
                "role": role,
            }
        else:
            claimed[slug] = prev | files
        out.append(call)
    return out


def _empty_evidence(
    *,
    slug: str,
    kind: str,
    target: str,
    adapter: str,
    caps: dict[str, dict[str, Any]],
    hashes: dict[str, str],
    revision: str,
) -> dict[str, Any]:
    return {
        "project_id": slug,
        "revision_hash": revision,
        "target": target,
        "kind": kind,
        "adapter": adapter,
        "tool_version": TOOL_VERSION,
        "hashes": hashes,
        "capabilities": caps,
        "stages": {},
        "checks": [],
        "screenshots": [],
        "runtime_logs": [],
        "artifact_hash": "",
        "roles": {
            "planner": "contract",
            "builder": "version-checked patches",
            "specialist": "adapter files",
            "verifier": "prove, do not write",
            "polish": "visual quality failure only",
        },
        "verdict": "BLOCKED",
        "stale": False,
        "updated": datetime.now(timezone.utc).isoformat(),
    }


def _collect_checks(stages: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for name, row in stages.items():
        for c in row.get("checks") or []:
            if isinstance(c, dict):
                out.append(c)
        for b in row.get("blockers") or []:
            out.append({"id": f"{name}:{b}", "ok": False, "detail": str(b)})
    return out[:80]


def _screenshots_of(root: Path, stages: dict[str, dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for row in stages.values():
        for name in row.get("frames") or row.get("screenshots") or []:
            if name and name not in names:
                names.append(str(name))
    proof = Path(root) / "studio" / "proof"
    if proof.is_dir():
        for p in sorted(proof.glob("*.png"))[:8]:
            rel = f"studio/proof/{p.name}"
            if rel not in names:
                names.append(rel)
    return names[:12]


def _runtime_logs(stages: dict[str, dict[str, Any]]) -> list[str]:
    logs: list[str] = []
    for row in stages.values():
        for key in ("error", "stdout_tail", "stderr_tail", "summary"):
            val = row.get(key)
            if val:
                logs.append(str(val)[:400])
    return logs[:12]


def finalize_evidence(
    root: Path,
    *,
    slug: str,
    kind: str,
    target: str,
    adapter: str,
    caps: dict[str, dict[str, Any]],
    hashes: dict[str, str],
    stages: dict[str, dict[str, Any]],
    artifact: str = "",
) -> dict[str, Any]:
    stale = stale_hashes(root, hashes)
    art_hash = artifact_hash(Path(artifact)) if artifact else ""
    doc = _empty_evidence(
        slug=slug,
        kind=kind,
        target=target,
        adapter=adapter,
        caps=caps,
        hashes=hashes,
        revision=revision_hash(root, list(hashes)),
    )
    doc["stages"] = stages
    doc["checks"] = _collect_checks(stages)
    doc["screenshots"] = _screenshots_of(root, stages)
    doc["runtime_logs"] = _runtime_logs(stages)
    doc["artifact_hash"] = art_hash
    doc["stale"] = stale
    doc["verdict"] = verdict_of(stages, stale=stale)
    blockers = [
        b
        for r in stages.values()
        for b in (r.get("blockers") or ([r["stage"]] if r.get("ok") is False else []))
    ]
    doc["blockers"] = blockers
    doc["ok"] = doc["verdict"] == "APPROVED" and not stale
    write_evidence(root, doc)
    return doc


def record_from_engine(slug: str, pipe: dict[str, Any]) -> dict[str, Any] | None:
    """Map a Godot-first pipeline result onto the shared artifact contract."""
    from . import playground as pg
    from .godot_first import REQUIRED_FILES, file_hash as ghash, gameplay_probe

    slug = (slug or pipe.get("slug") or "").strip()
    if not slug:
        return None
    root = pg.project_dir(slug)
    godot_stages = pipe.get("stages") or {}
    hashes = {rel: ghash(root, rel) for rel in REQUIRED_FILES}
    pf = godot_stages.get("preflight") or {}
    contract = godot_stages.get("contract") or {}
    plan = stage_row(
        "plan",
        ok=bool(pf.get("ok")) and bool(contract.get("ok")),
        skipped=bool(pf.get("skipped")),
        blockers=list(pf.get("blockers") or []) + list(contract.get("blockers") or []),
        error=pf.get("error") or contract.get("error") or "",
        preflight=pf,
        contract=contract,
    )
    unified: dict[str, dict[str, Any]] = {"plan": plan}
    if "context" in godot_stages:
        unified["context"] = {**godot_stages["context"], "stage": "context"}
    if "slice" in godot_stages:
        sl = dict(godot_stages["slice"])
        sl["stage"] = "build"
        unified["build"] = sl
    if "runtime" in godot_stages:
        unified["runtime"] = godot_stages["runtime"]
        try:
            unified["gameplay"] = gameplay_probe(root)
        except Exception:
            unified["gameplay"] = stage_row(
                "gameplay",
                ok=bool((godot_stages["runtime"]).get("ok")),
                skipped=bool((godot_stages["runtime"]).get("skipped")),
                blockers=list((godot_stages["runtime"]).get("blockers") or []),
            )
    if "visual" in godot_stages:
        unified["visual"] = godot_stages["visual"]
    if "export" in godot_stages:
        unified["export"] = godot_stages["export"]
    if "publish" in godot_stages:
        unified["publish"] = godot_stages["publish"]
    caps = probe_capabilities("engine", root)
    art = str((godot_stages.get("export") or {}).get("artifact") or "")
    return finalize_evidence(
        root,
        slug=slug,
        kind="godot",
        target="engine",
        adapter="EngineAdapter",
        caps=caps,
        hashes=hashes,
        stages=unified,
        artifact=art,
    )


def ingest_visual_proof(slug: str, kind: str, result: Any) -> None:
    """Attach kernel visual-proof frames to existing evidence. Does not re-run adapters."""
    slug = (slug or "").strip()
    if not slug:
        return
    try:
        from . import playground as pg
        root = pg.project_dir(slug)
    except Exception:
        return
    doc = load_evidence(root)
    if not doc:
        return
    payload = result.as_dict() if hasattr(result, "as_dict") else dict(result or {})
    visual = stage_row(
        "visual",
        ok=bool(payload.get("ok")) and not payload.get("skipped"),
        skipped=bool(payload.get("skipped")),
        blockers=["visual_skipped"] if payload.get("skipped") else (
            [] if payload.get("ok") else ["visual"]
        ),
        error=str(payload.get("error") or payload.get("honest_gap") or ""),
        frames=list(payload.get("frames") or []),
        honest_gap=payload.get("honest_gap") or "",
        ingested=True,
    )
    stages = dict(doc.get("stages") or {})
    stages["visual"] = visual
    doc["stages"] = stages
    doc["screenshots"] = _screenshots_of(root, stages)
    doc["stale"] = stale_hashes(root, doc.get("hashes") or {})
    doc["verdict"] = verdict_of(stages, stale=bool(doc["stale"]))
    doc["ok"] = doc["verdict"] == "APPROVED"
    doc["updated"] = datetime.now(timezone.utc).isoformat()
    write_evidence(root, doc)


def run_pipeline(
    slug: str,
    *,
    through: str = "publish",
    kind: str = "",
    target: str = "",
    text: str = "",
    plan: str = "",
) -> dict[str, Any]:
    """Walk the shared order on the adapter for this kind/target."""
    from . import playground as pg

    slug = (slug or "").strip()
    if not slug:
        return {"ok": False, "error": "slug required", "blockers": ["no_slug"]}
    root = pg.project_dir(slug)
    proj_kind = kind
    if not proj_kind:
        try:
            import asyncio
            from .playground import get_project
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop and loop.is_running():
                proj_kind = ""
            else:
                proj = asyncio.run(get_project(slug))
                if proj:
                    proj_kind = str(proj.get("kind") or "")
        except Exception:
            proj_kind = ""
    if (root / "project.godot").is_file():
        proj_kind = "godot"
    elif (root / "app.json").is_file() and (
        (root / "App.jsx").is_file() or (root / "App.tsx").is_file() or (root / "app").is_dir()
    ):
        proj_kind = proj_kind or "expo"
    elif not proj_kind:
        proj_kind = "static"

    resolved = resolve_target(proj_kind, text=text or plan)
    want = (target or resolved).strip().lower()
    blocked = refuse_crosswalk(proj_kind, want)
    if blocked and want != resolved:
        return {
            **blocked,
            "slug": slug,
            "kind": proj_kind,
            "target": want,
            "resolved": resolved,
        }
    if proj_kind == "godot":
        want = "engine"
    if proj_kind == "expo" and want == "web":
        cross = refuse_crosswalk("expo", "web")
        return {**(cross or {}), "slug": slug, "kind": proj_kind, "target": want}

    stop = (through or "publish").strip().lower()
    if stop not in STAGES:
        stop = "publish"

    if want == "unity" or proj_kind == "unity":
        caps = probe_capabilities("unity", root)
        stages = {
            "plan": stage_row(
                "plan",
                ok=False,
                skipped=True,
                blockers=["unity_local"],
                error="Unity Editor is not in this process. Scaffold is not Play.",
            )
        }
        doc = finalize_evidence(
            root, slug=slug, kind="unity", target="unity", adapter="none",
            caps=caps, hashes={}, stages=stages,
        )
        return {
            "ok": False,
            "slug": slug,
            "kind": "unity",
            "target": "unity",
            "through": stop,
            "stages": stages,
            "evidence": EVIDENCE_REL,
            "verdict": "BLOCKED",
            "blockers": ["unity_local"],
            "honest_gap": "unity_local",
            "capabilities": caps,
            "ok_pipeline": False,
        }

    if want == "engine":
        return _run_engine(slug, root=root, through=stop, plan=plan or text)
    if want == "mobile":
        from .mobile_first import run_pipeline as mobile_run
        return mobile_run(slug, root=root, kind=proj_kind, through=stop, plan=plan or text)
    from .web_first import run_pipeline as web_run
    return web_run(slug, root=root, kind=proj_kind, through=stop, plan=plan or text)


def _run_engine(slug: str, *, root: Path, through: str, plan: str = "") -> dict[str, Any]:
    """EngineAdapter — Godot-first stages remapped onto the shared contract."""
    from .godot_first import (
        REQUIRED_FILES,
        STAGES as GODOT_STAGES,
        ensure_contract,
        export_release,
        file_hash as ghash,
        gameplay_probe,
        preflight,
        publish_artifact,
        review_gate,
        run_pipeline as godot_run,
        runtime_verify,
        slice_static,
        visual_quality,
        write_context,
    )

    want = through if through in STAGES else "publish"
    # Map unified through → enough Godot stages to satisfy it.
    g_through = {
        "plan": "contract",
        "context": "context",
        "build": "slice",
        "runtime": "runtime",
        "gameplay": "runtime",
        "visual": "visual",
        "export": "export",
        "publish": "publish",
    }.get(want, "publish")
    pipe = godot_run(slug, through=g_through, plan=plan)
    godot_stages = pipe.get("stages") or {}
    hashes = {rel: ghash(root, rel) for rel in REQUIRED_FILES}
    pf = godot_stages.get("preflight") or preflight(root)
    contract = godot_stages.get("contract") or ensure_contract(root, slug=slug)
    unified: dict[str, dict[str, Any]] = {
        "plan": stage_row(
            "plan",
            ok=bool(pf.get("ok")) and bool(contract.get("ok")),
            skipped=bool(pf.get("skipped")),
            blockers=list(pf.get("blockers") or []) + list(contract.get("blockers") or []),
            error=str(pf.get("error") or contract.get("error") or ""),
            preflight=pf,
            contract=contract,
        )
    }
    stop_at = STAGES.index(want)
    if stop_at >= 1:
        unified["context"] = godot_stages.get("context") or write_context(root, plan=plan)
        unified["context"]["stage"] = "context"
    if stop_at >= 2:
        sl = dict(godot_stages.get("slice") or slice_static(root))
        sl["stage"] = "build"
        unified["build"] = sl
    if stop_at >= 3:
        unified["runtime"] = godot_stages.get("runtime") or runtime_verify(root)
    if stop_at >= 4:
        unified["gameplay"] = gameplay_probe(root)
    if stop_at >= 5:
        unified["visual"] = godot_stages.get("visual") or visual_quality(root, slug=slug)
    if stop_at >= 6:
        review = godot_stages.get("review") or review_gate(godot_stages, root=root)
        if not review.get("ok"):
            unified["export"] = stage_row(
                "export",
                ok=False,
                blockers=["review_not_approved"] + list(review.get("blockers") or []),
                error="Export waits for APPROVED review. Skip is not a pass.",
                review=review,
            )
        else:
            unified["export"] = godot_stages.get("export") or export_release(root, slug=slug)
    if stop_at >= 7:
        unified["publish"] = godot_stages.get("publish") or publish_artifact(
            root, slug=slug, export_row=unified.get("export"),
        )
    caps = probe_capabilities("engine", root)
    art = str((unified.get("export") or {}).get("artifact") or "")
    doc = finalize_evidence(
        root,
        slug=slug,
        kind="godot",
        target="engine",
        adapter="EngineAdapter",
        caps=caps,
        hashes=hashes,
        stages=unified,
        artifact=art,
    )
    ordered = [unified[s] for s in STAGES if s in unified]
    ok = all(r.get("ok") for r in ordered) and not any(r.get("skipped") for r in ordered)
    return {
        "ok": bool(ok) and doc.get("verdict") == "APPROVED",
        "slug": slug,
        "kind": "godot",
        "target": "engine",
        "adapter": "EngineAdapter",
        "through": want,
        "stages": unified,
        "godot_stages": {s: godot_stages[s] for s in GODOT_STAGES if s in godot_stages},
        "evidence": EVIDENCE_REL,
        "verdict": doc.get("verdict"),
        "blockers": doc.get("blockers") or [],
        "capabilities": caps,
    }


def production_pipeline_action(
    slug: str,
    *,
    through: str = "publish",
    kind: str = "",
    target: str = "",
    text: str = "",
    plan: str = "",
) -> dict[str, Any]:
    return run_pipeline(
        slug, through=through, kind=kind, target=target, text=text, plan=plan,
    )
