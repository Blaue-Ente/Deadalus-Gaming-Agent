"""Platform Acceptance QA — fail-closed evidence bound to one candidate.

A build is identified by ``project_id + target + revision + artifact_hash``.
``skipped`` / ``blocked`` / ``fail`` are never success. Missing runners are
named blockers. The skill lives in ``daedalus/skills/platform_acceptance_qa.md``.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Sequence

from .capability_registry import (
    DESKTOP_PROFILE,
    DEVICE_SMOKE_PROOFS,
    EVIDENCE_STATUSES,
    GODOT_GAMEPLAY_PROOFS,
    MOBILE_PROFILE,
    PLATFORMS,
    REGISTRY,
    RUNNER_VERSION,
    WEB_GAMEPLAY_PROOFS,
    BuildArtifact,
    Capability,
    CapabilityEvidence,
    UnsupportedPlatform,
    canonicalize_platform,
    evidence_record,
    gameplay_proofs_for,
    hash_missing,
    normalize_hash,
    status_is_pass,
)

FORBIDDEN_SHIP_WORDS = ("ready", "done", "shipped", "publishable", "готово")
CONTRADICT_APPROVED = "NOT APPROVED"

CHECK_ORDER = (
    "create",
    "write",
    "run",
    "input",
    "gameplay_proof",
    "render_proof",
    "export",
    "device_smoke",
)

PROFILES: dict[str, dict[str, Any]] = {
    "web": {
        "id": "web",
        "label": "Web (Vite, HTML, Phaser)",
        "targets": ("web",),
        "capabilities": tuple(c.value for c in DESKTOP_PROFILE),
        "gameplay_proofs": WEB_GAMEPLAY_PROOFS,
        "device_smoke": False,
        "runner": "chromium",
        "scenario": "launch -> input -> core mechanic -> state change -> win/lose -> restart",
    },
    "godot": {
        "id": "godot",
        "label": "Godot 4",
        "targets": ("godot",),
        "capabilities": tuple(c.value for c in DESKTOP_PROFILE),
        "gameplay_proofs": GODOT_GAMEPLAY_PROOFS,
        "device_smoke": False,
        "runner": "godot-bridge",
        "scenario": "load scene -> move -> collide/interact -> solve mechanic -> completion -> restart",
    },
    "mobile": {
        "id": "mobile",
        "label": "Mobile (Expo / Capacitor)",
        "targets": ("expo", "capacitor"),
        "capabilities": tuple(c.value for c in MOBILE_PROFILE),
        "gameplay_proofs": WEB_GAMEPLAY_PROOFS,
        "device_smoke": True,
        "device_smoke_proofs": DEVICE_SMOKE_PROOFS,
        "runner": "emulator-or-device",
        "scenario": "launch -> navigate -> touch primary action -> verify result -> back/reopen",
    },
}

ASSERTIONS: tuple[tuple[str, str], ...] = (
    ("A1", "Candidate identity is project_id + target + revision + artifact_hash."),
    ("A2", "Statuses are only pass, fail, skipped, blocked."),
    ("A3", "skipped, blocked, and fail are not success."),
    ("A4", "Unknown platforms fail closed; never remap to Vite/HTML."),
    ("A5", "Web gameplay proof is Chromium scenario checks, not a canvas tag."),
    ("A6", "Godot gameplay proof is a live scene plus input; files are not Play."),
    ("A7", "Mobile export requires device_smoke; web preview is not device smoke."),
    ("A8", "Missing runner / emulator / engine / browser / export template / device is blocked."),
    ("A9", "Stale revision or hash mismatch invalidates evidence."),
    ("A10", "Publish only the immutable artifact whose hash matches every required row."),
    ("A11", "Never say ready / done / shipped / publishable when a required check is not pass."),
    ("A12", "Do not accept APPROVED inside NOT APPROVED."),
    ("A13", "Compile/run and visual/gameplay quality are separate gates."),
    ("A14", "A model overall score cannot override missing screenshots or failed gameplay."),
    ("A15", "Do not drop promised features (save, audio, gamepad) to make the test pass."),
)

EXAMPLE_PROMPTS: tuple[str, ...] = (
    "Run Platform Acceptance QA on this Phaser dungeon. Chromium must prove keyboard input, one combat hit, and a lose state. A <canvas> tag is not gameplay.",
    "Vite Three.js racer — wait for WebGPU settle, capture a real frame, prove steer + boost + fail. A dark canvas is not visual proof.",
    "QA this Godot slug. Bridge must load scenes/main.tscn, move the player, collide, and capture a running-scene frame. Headless import is not visual proof.",
    "Prove camera framing and readable geometry. A HUD-only or blank frame fails visual proof.",
    "Emulator launch, touch the primary action, capture a device screenshot at the declared size. Expo web preview is a blocker.",
    "Build web first, sync native, launch the wrapper, produce an APK/IPA. A web ZIP is not a mobile release.",
)

SELECT_KEYWORDS: tuple[str, ...] = (
    "platform acceptance",
    "acceptance qa",
    "приемни тестове",
    "render_proof",
    "gameplay_proof",
    "device_smoke",
    "artifact hash",
    "release gate",
    "fail-closed",
    "fail closed",
    "ship gate",
    "platform matrix",
    "платформена qa",
)

_QA_RE = re.compile(
    r"(platform acceptance|acceptance qa|приемни|render_proof|gameplay_proof|"
    r"device_smoke|artifact hash|release gate|fail-closed|fail closed|"
    r"platform matrix|платформена qa|ship gate)",
    re.I,
)

_FAKE_WEB_GAMEPLAY = re.compile(
    r"\b(canvas|index\.html|dom text|non-empty body|moving background)\b",
    re.I,
)
_FAKE_GODOT_GAMEPLAY = re.compile(
    r"\b(\.tscn|\.gd|files exist|preview html|headless import)\b",
    re.I,
)
_FAKE_DEVICE_SMOKE = re.compile(
    r"\b(web preview|expo web|static preview|browser preview)\b",
    re.I,
)


def looks_like_qa(text: str) -> bool:
    return bool(_QA_RE.search(text or ""))


def candidate_id(
    project_id: str,
    target: str,
    revision: str,
    artifact_hash: str,
) -> str:
    digest = artifact_hash if str(artifact_hash).startswith("sha256:") else (
        f"sha256:{artifact_hash}" if artifact_hash else ""
    )
    return f"{project_id}+{canonicalize_platform(target)}+{revision}+{digest}"


def profile_for(target: str) -> dict[str, Any]:
    p = canonicalize_platform(target)
    if p == "godot":
        return PROFILES["godot"]
    if p in {"expo", "capacitor"}:
        return PROFILES["mobile"]
    if p == "web":
        return PROFILES["web"]
    raise UnsupportedPlatform(f"unsupported platform: {target or p}")


def scenario_shape(target: str) -> str:
    return str(profile_for(target)["scenario"])


def required_check_ids(target: str) -> tuple[str, ...]:
    p = canonicalize_platform(target)
    names = [c.value for c in REGISTRY.release_capabilities(p)]
    ordered = [n for n in CHECK_ORDER if n in names]
    extra = [n for n in names if n not in ordered]
    return tuple(ordered + extra)


def status_ok(status: str) -> bool:
    return status_is_pass(status)


def contradictory_approval(text: str) -> bool:
    """True when APPROVED remains after stripping NOT APPROVED."""
    upper = (text or "").upper()
    if CONTRADICT_APPROVED not in upper:
        return False
    stripped = upper.replace(CONTRADICT_APPROVED, " ")
    return bool(re.search(r"\bAPPROVED\b", stripped))


def claims_approved(text: str) -> bool:
    """Fail-closed: APPROVED inside NOT APPROVED does not count as a pass."""
    upper = (text or "").upper()
    if CONTRADICT_APPROVED in upper:
        return False
    return bool(re.search(r"\bAPPROVED\b", upper))


def ship_language_blocked(text: str, *, approved: bool) -> list[str]:
    if approved:
        return []
    low = (text or "").lower()
    return [w for w in FORBIDDEN_SHIP_WORDS if w in low]


def _as_hash(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    if raw.lower().startswith("sha256:"):
        return f"sha256:{normalize_hash(raw)}"
    return f"sha256:{raw}"


def _evidence_paths(
    *,
    check_id: str,
    screenshots: Sequence[str] | None = None,
    logs: Sequence[str] | None = None,
    extra: Sequence[str] | None = None,
) -> list[str]:
    out: list[str] = []
    for item in extra or ():
        if item and item not in out:
            out.append(str(item))
    for item in logs or ():
        if item and item not in out:
            out.append(str(item))
    for item in screenshots or ():
        if item and item not in out:
            out.append(str(item))
    if not out:
        if check_id in {"run", "runtime"}:
            out.append("logs/runtime.txt")
        if check_id in {"render_proof", "gameplay_proof", "visual"}:
            out.append("studio/proof/")
    return out[:8]


def _check_row(
    *,
    check_id: str,
    status: str,
    details: str = "",
    evidence: Sequence[str] | None = None,
    runner_version: str = RUNNER_VERSION,
) -> dict[str, Any]:
    st = (status or "").strip().lower()
    if st not in EVIDENCE_STATUSES:
        st = "blocked"
        details = details or f"unknown status (not in {EVIDENCE_STATUSES})"
    return {
        "id": check_id,
        "status": st,
        "details": details,
        "runner_version": runner_version or RUNNER_VERSION,
        "evidence": list(evidence or []),
    }


def _fake_gameplay_fail(target: str, details: str, checks: Mapping[str, bool] | None) -> str | None:
    p = canonicalize_platform(target)
    blob = details or ""
    proofs = gameplay_proofs_for(p)
    if proofs and checks is not None and not all(bool(checks.get(k)) for k in proofs):
        if p == "web" and _FAKE_WEB_GAMEPLAY.search(blob):
            return "canvas/DOM is not Chromium gameplay proof"
        if p == "godot" and _FAKE_GODOT_GAMEPLAY.search(blob):
            return "files on disk are not a live Godot scene"
        return "gameplay proofs incomplete"
    return None


def _downgrade(ev: CapabilityEvidence, *, status: str, details: str) -> CapabilityEvidence:
    return CapabilityEvidence(
        capability=ev.capability,
        status=status,
        project_id=ev.project_id,
        revision=ev.revision,
        artifact_hash=ev.artifact_hash,
        details=details,
        runner_version=ev.runner_version,
        checks=ev.checks,
    )


def _coerce_evidence(
    raw: CapabilityEvidence | Mapping[str, Any],
    *,
    project_id: str,
    revision: str,
    artifact_hash: str,
) -> CapabilityEvidence:
    if isinstance(raw, CapabilityEvidence):
        return raw
    return evidence_record(
        str(raw.get("capability") or raw.get("id") or ""),
        status=str(raw.get("status") or ""),
        project_id=str(raw.get("project_id") or project_id),
        revision=str(raw.get("revision") or revision),
        artifact_hash=str(raw.get("artifact_hash") or artifact_hash),
        details=str(raw.get("details") or ""),
        runner_version=str(raw.get("runner_version") or RUNNER_VERSION),
        checks=raw.get("checks") if isinstance(raw, Mapping) else None,
    )


def acceptance_result(
    *,
    project_id: str,
    target: str,
    revision: str,
    artifact_hash: str,
    evidence: Iterable[CapabilityEvidence | Mapping[str, Any]] | None = None,
    artifact: BuildArtifact | Mapping[str, Any] | None = None,
    screenshots: Sequence[str] | None = None,
    logs: Sequence[str] | None = None,
    stale: bool = False,
    rollback_ready: bool = False,
    extra_promises: Sequence[str] | None = None,
    quality_score: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Machine-readable QA verdict. skipped/blocked/fail never approve."""
    blockers: list[str] = []
    try:
        p = canonicalize_platform(target)
        profile = profile_for(p)
        REGISTRY.adapter(p)
    except (UnsupportedPlatform, ValueError) as exc:
        return {
            "ok": False,
            "project_id": project_id,
            "target": canonicalize_platform(target) or (target or ""),
            "revision": revision,
            "artifact_hash": _as_hash(artifact_hash),
            "candidate": candidate_id(project_id, target, revision, artifact_hash),
            "profile": "unknown",
            "checks": [],
            "release": {
                "approved": False,
                "blockers": [str(exc)],
                "rollback_ready": False,
            },
            "summary": human_summary(
                approved=False,
                blockers=[str(exc)],
                passed=[],
                untested=list(CHECK_ORDER),
                target=target or "unknown",
            ),
        }

    if artifact is None:
        art: BuildArtifact | Mapping[str, Any] = BuildArtifact(
            project_id=project_id,
            revision=revision,
            target=p,
            artifact_hash=_as_hash(artifact_hash),
            path="",
            immutable=True,
        )
    else:
        art = artifact

    required = REGISTRY.release_capabilities(p)
    if stale:
        blockers.append("stale evidence — start a fresh set for this revision")

    fake_notes: dict[str, str] = {}
    normalized: list[CapabilityEvidence] = []
    for raw in evidence or ():
        ev = _coerce_evidence(
            raw, project_id=project_id, revision=revision, artifact_hash=artifact_hash,
        )
        if ev.capability == Capability.GAMEPLAY_PROOF.value:
            note = _fake_gameplay_fail(p, ev.details, ev.checks)
            if note:
                fake_notes[ev.capability] = note
                ev = _downgrade(ev, status="fail", details=note)
        if ev.capability == Capability.DEVICE_SMOKE.value and _FAKE_DEVICE_SMOKE.search(ev.details or ""):
            fake_notes[ev.capability] = "web preview is not device_smoke"
            ev = _downgrade(ev, status="fail", details="web preview is not device_smoke")
        normalized.append(ev)

    decision = REGISTRY.release_decision(art, required, normalized)
    blockers.extend(list(decision.blockers))
    digest = ""
    if isinstance(art, BuildArtifact):
        digest = art.artifact_hash
    elif isinstance(art, Mapping):
        digest = str(art.get("artifact_hash") or "")
    if hash_missing(digest) and "artifact hash is missing" not in blockers:
        blockers.append("artifact hash is missing")

    if quality_score is not None:
        overall = quality_score.get("overall")
        missing_visual = not any(
            isinstance(e, CapabilityEvidence)
            and e.capability == "render_proof"
            and status_is_pass(e.status)
            for e in normalized
        )
        failed_gameplay = any(
            isinstance(e, CapabilityEvidence)
            and e.capability == "gameplay_proof"
            and not status_is_pass(e.status)
            for e in normalized
        )
        if overall is not None and (missing_visual or failed_gameplay):
            blockers.append(
                "model overall score cannot override missing screenshots or failed gameplay"
            )

    for promise in extra_promises or ():
        name = str(promise).strip()
        if not name:
            continue
        covered = any(
            name.lower() in (e.details or "").lower() or name.lower() in e.capability
            for e in normalized
        )
        if not covered:
            blockers.append(f"promised feature not tested: {name}")

    by_id = {e.capability: e for e in normalized}
    checks: list[dict[str, Any]] = []
    passed: list[str] = []
    untested: list[str] = []
    for cid in required_check_ids(p):
        ev = by_id.get(cid)
        if ev is None:
            untested.append(cid)
            checks.append(_check_row(
                check_id=cid,
                status="blocked",
                details="no evidence for this candidate",
            ))
            continue
        st = ev.status if ev.status in EVIDENCE_STATUSES else "blocked"
        if status_is_pass(st):
            passed.append(cid)
        else:
            untested.append(cid)
        checks.append(_check_row(
            check_id=cid,
            status=st,
            details=fake_notes.get(cid) or ev.details,
            evidence=_evidence_paths(
                check_id=cid, screenshots=screenshots, logs=logs,
            ),
            runner_version=ev.runner_version,
        ))

    extra_fail = any(
        b.startswith("model overall") or b.startswith("promised feature") or b.startswith("stale")
        for b in blockers
    )
    approved = bool(decision.approved) and not stale and not extra_fail
    seen: list[str] = []
    for b in blockers:
        if b not in seen:
            seen.append(b)
    blockers = seen
    if not approved and not blockers:
        blockers = [f"invalid evidence: {c}" for c in untested[:4]] or ["release not approved"]

    summary = human_summary(
        approved=approved,
        blockers=blockers,
        passed=passed,
        untested=untested,
        target=p,
    )
    return {
        "ok": approved,
        "project_id": project_id,
        "target": p,
        "revision": revision,
        "artifact_hash": _as_hash(artifact_hash),
        "candidate": candidate_id(project_id, p, revision, artifact_hash),
        "profile": profile["id"],
        "scenario": profile["scenario"],
        "checks": checks,
        "release": {
            "approved": approved,
            "blockers": blockers,
            "rollback_ready": bool(rollback_ready),
        },
        "summary": summary,
        "runner_version": RUNNER_VERSION,
    }


def human_summary(
    *,
    approved: bool,
    blockers: Sequence[str],
    passed: Sequence[str],
    untested: Sequence[str],
    target: str,
) -> str:
    bits: list[str] = []
    if passed:
        bits.append("Passed: " + ", ".join(passed) + ".")
    else:
        bits.append("Nothing passed.")
    if untested:
        bits.append("Not tested: " + ", ".join(untested) + ".")
    if approved:
        bits.append(f"This {target} artifact is publishable.")
    else:
        bits.append("This artifact is not publishable.")
        if blockers:
            bits.append("Blockers: " + "; ".join(list(blockers)[:8]) + ".")
        bits.append("Do not claim ready, done, or shipped.")
    return " ".join(bits)


def rollback_ready_for(slug: str) -> bool:
    try:
        from .paths import data_root
        prev = data_root() / "releases" / slug / "previous" / "manifest.json"
        return prev.is_file()
    except Exception:
        return False


def evaluate_evidence_doc(
    doc: Mapping[str, Any] | None,
    *,
    slug: str,
    kind: str = "",
    target: str = "",
) -> dict[str, Any]:
    if not doc:
        tgt = canonicalize_platform(target) or "web"
        return acceptance_result(
            project_id=slug,
            target=tgt,
            revision="",
            artifact_hash="",
            evidence=[],
            stale=False,
            rollback_ready=rollback_ready_for(slug),
        )
    tgt = canonicalize_platform(str(target or doc.get("target") or kind or "web"))
    art = doc.get("artifact") if isinstance(doc.get("artifact"), dict) else {}
    revision = str(art.get("revision") or doc.get("revision_hash") or doc.get("revision") or "")
    digest = str(art.get("artifact_hash") or doc.get("artifact_hash") or "")
    records = doc.get("records") or []
    stale = bool(doc.get("stale"))
    screenshots = list(doc.get("screenshots") or [])
    logs = list(doc.get("runtime_logs") or [])
    artifact = None
    if art:
        artifact = BuildArtifact(
            project_id=str(art.get("project_id") or slug),
            revision=revision,
            target=tgt,
            artifact_hash=digest,
            path=str(art.get("path") or ""),
            immutable=bool(art.get("immutable", True)),
        )
    return acceptance_result(
        project_id=slug,
        target=tgt,
        revision=revision,
        artifact_hash=digest,
        evidence=records if isinstance(records, list) else [],
        artifact=artifact,
        screenshots=screenshots,
        logs=logs,
        stale=stale,
        rollback_ready=rollback_ready_for(slug),
    )


def run_qa(
    slug: str,
    *,
    kind: str = "",
    target: str = "",
    text: str = "",
) -> dict[str, Any]:
    """Evaluate existing pipeline evidence. Does not invent a pass."""
    from . import playground as pg
    from .capability import load_evidence, resolve_target, stale_hashes

    slug = (slug or "").strip()
    empty = {
        "ok": False,
        "release": {"approved": False, "blockers": [], "rollback_ready": False},
        "summary": "Not tested. Do not claim ready, done, or shipped.",
    }
    if not slug:
        empty["error"] = "slug required"
        empty["release"]["blockers"] = ["no_slug"]
        return empty
    root = pg.project_dir(slug)
    if not root.is_dir():
        empty["project_id"] = slug
        empty["error"] = f"no project {slug}"
        empty["release"]["blockers"] = ["no_project"]
        return empty
    doc = load_evidence(root)
    resolved = ""
    try:
        resolved = resolve_target(kind, text=text)
    except Exception:
        resolved = target or ""
    want = canonicalize_platform(target or resolved or (doc or {}).get("target") or "web")
    if doc:
        hashes = doc.get("hashes") if isinstance(doc.get("hashes"), dict) else {}
        if hashes and stale_hashes(root, hashes):
            doc = dict(doc)
            doc["stale"] = True
    result = evaluate_evidence_doc(doc, slug=slug, kind=kind, target=want)
    result["evidence_path"] = "studio/capability_evidence.json"
    if not doc:
        blockers = list(result.get("release", {}).get("blockers") or [])
        msg = "no evidence — call production({action: pipeline, slug}) first"
        if msg not in blockers:
            blockers.insert(0, msg)
        result["release"]["blockers"] = blockers
        result["release"]["approved"] = False
        result["ok"] = False
        untested = list(required_check_ids(want)) if want in PLATFORMS else list(CHECK_ORDER)
        result["summary"] = human_summary(
            approved=False,
            blockers=blockers,
            passed=[],
            untested=untested,
            target=want,
        )
    result["hint"] = (
        "production({action: pipeline, slug}) collects evidence. "
        "This action only judges the current candidate."
    )
    return result
