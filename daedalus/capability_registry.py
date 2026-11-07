"""Capability registry — sole source of truth for platforms and release gates.

Pipeline (kernel stages still map Verify onto runtime/gameplay/visual):

    Intent → Plan → Context → Build → Verify → Polish → Export → Publish

Adapters declare capabilities. A missing capability is a named blocker.
There is no silent fallback (Godot ↛ HTML, mobile ↛ web preview).
Unknown platforms fail closed.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

RUNNER_VERSION = "daedalus-0.4"

PLATFORMS = ("web", "godot", "expo", "capacitor")

PLATFORM_ALIASES = {
    "engine": "godot",
    "html": "web",
    "vite": "web",
    "phaser": "web",
    "mobile": "expo",
}

# Playground kinds that are not capability platforms (scaffold / honest refuse).
UNSUPPORTED_PLATFORMS = frozenset({
    "unity", "unreal", "flutter", "xamarin", "cordova",
})

PLAYGROUND_KINDS = frozenset({
    "static", "vite", "vite-react", "phaser", "streamlit",
    "expo", "godot", "unity", "fastapi", "node", "auto",
})

KIND_ALIASES = {
    "vite-game": "vite",
    "node-api": "node",
    "html": "static",
    "engine": "godot",
}

WEB_PLAYGROUND_KINDS = frozenset({
    "static", "vite", "vite-react", "phaser", "streamlit",
})
BROWSER_VISUAL_KINDS = frozenset({
    "static", "vite", "vite-react", "streamlit", "phaser",
})
NON_STATIC_LIVE_KINDS = frozenset({
    "fastapi", "streamlit", "node", "unity", "godot", "expo",
})

EVIDENCE_STATUSES = ("pass", "fail", "skipped", "blocked")


class Capability(str, Enum):
    CREATE = "create"
    WRITE = "write"
    RUN = "run"
    INPUT = "input"
    RENDER_PROOF = "render_proof"
    GAMEPLAY_PROOF = "gameplay_proof"
    EXPORT = "export"
    DEVICE_SMOKE = "device_smoke"
    PUBLISH = "publish"
    ROLLBACK = "rollback"


CAPABILITIES = tuple(c.value for c in Capability)

DESKTOP_PROFILE = (
    Capability.CREATE,
    Capability.WRITE,
    Capability.RUN,
    Capability.INPUT,
    Capability.RENDER_PROOF,
    Capability.GAMEPLAY_PROOF,
    Capability.EXPORT,
    Capability.PUBLISH,
    Capability.ROLLBACK,
)
MOBILE_PROFILE = DESKTOP_PROFILE + (Capability.DEVICE_SMOKE,)

# Evidence required to ship. publish/rollback must be *supported*, not pre-proven.
RELEASE_EVIDENCE = frozenset({
    Capability.RUN,
    Capability.INPUT,
    Capability.RENDER_PROOF,
    Capability.GAMEPLAY_PROOF,
    Capability.EXPORT,
    Capability.DEVICE_SMOKE,
})

WEB_GAMEPLAY_PROOFS = (
    "started",
    "input_accepted",
    "state_changed",
    "mechanic",
    "outcome",
    "no_console_errors",
)
GODOT_GAMEPLAY_PROOFS = (
    "scene_loaded",
    "player_moved",
    "collision",
    "interaction",
    "mechanic",
    "outcome",
    "console_clean",
    "scene_screenshot",
)
DEVICE_SMOKE_PROOFS = (
    "native_build",
    "app_launched",
    "touch",
    "flow",
    "no_startup_crash",
)

PLAYGROUND_KIND_RULES: dict[str, str] = {
    "static": (
        "STATIC HTML/CSS/JS. Single or multi-file. No ES modules without bundler. "
        "CDN libs OK. Entry: index.html. Chromium runtime. Never Godot."
    ),
    "vite": (
        "VITE vanilla JS. Required: index.html, main.js, vite.config.js with base:'./'. "
        "ES modules in main.js."
    ),
    "vite-react": (
        "VITE+REACT 18. Required: index.html, src/main.jsx, src/App.jsx, vite.config.js "
        "with @vitejs/plugin-react. React from npm only."
    ),
    "phaser": (
        "PHASER on Vite/HTML. Canvas game loop, keyboard/pointer, win/lose. Chromium. Not Godot."
    ),
    "fastapi": (
        "FASTAPI Python. main.py with `app = FastAPI()`, requirements.txt with fastapi+uvicorn."
    ),
    "streamlit": (
        "STREAMLIT Python. streamlit_app.py with `import streamlit as st`, requirements.txt."
    ),
    "node": (
        "NODE.JS. index.js + package.json. Optional GET /health for smoke."
    ),
    "expo": (
        "EXPO React Native. App.jsx, app.json, package.json, babel.config.js. "
        "Only RN components — no HTML. Web preview is not device_smoke."
    ),
    "godot": (
        "GODOT 4. project.godot, scenes/main.tscn, GDScript. Headless/runtime bridge. "
        "Never Vite, HTML, Phaser, or preview HTML as Play."
    ),
    "unity": (
        "UNITY is unsupported in this process. Scaffold is not Play. Do not emit Vite/HTML."
    ),
    "auto": (
        "AUTO: pick the platform from the ask. Godot words stay Godot. "
        "Do not convert an unknown engine into Vite."
    ),
}


class UnsupportedPlatform(ValueError):
    """Unknown platform — fail closed, never remap to Vite/HTML."""


def cap_name(value: Capability | str) -> str:
    if isinstance(value, Capability):
        return value.value
    return str(value or "").strip().lower()


def as_capability(value: Capability | str) -> Capability:
    return value if isinstance(value, Capability) else Capability(cap_name(value))


def canonicalize_platform(platform: str) -> str:
    p = (platform or "").strip().lower()
    return PLATFORM_ALIASES.get(p, p)


def normalize_hash(value: str) -> str:
    raw = (value or "").strip()
    if raw.lower().startswith("sha256:"):
        raw = raw.split(":", 1)[1].strip()
    return raw


def hash_missing(value: str) -> bool:
    return not normalize_hash(value)


def status_is_pass(status: str) -> bool:
    return (status or "").strip().lower() == "pass"


def playground_kind(kind: str, *, default: str | None = None) -> str:
    """Map a builder/platform name onto a Playground kind. Unknown platforms raise."""
    k = (kind or "").strip().lower()
    if k in KIND_ALIASES:
        k = KIND_ALIASES[k]
    if k == "capacitor":
        return "static"
    if k == "web":
        return default or "static"
    if k in PLAYGROUND_KINDS:
        return k
    if k in PLATFORMS:
        return {"godot": "godot", "expo": "expo", "web": "static"}[k]
    if k in UNSUPPORTED_PLATFORMS:
        raise UnsupportedPlatform(f"unsupported platform: {k}")
    if k:
        raise UnsupportedPlatform(f"unsupported platform: {k}")
    if default:
        return default
    raise UnsupportedPlatform("unsupported platform")


def platform_for_kind(kind: str, text: str = "", path: str = "") -> str | None:
    """Playground kind / free text → registry platform, or None if unsupported."""
    from .godot_first import coerce_kind

    k = coerce_kind(kind or "", description=text, path=path)
    if k == "godot":
        return "godot"
    if k == "unity" or canonicalize_platform(kind) in UNSUPPORTED_PLATFORMS:
        return None
    if k == "expo":
        return "expo"
    if k == "capacitor":
        return "capacitor"
    if k in WEB_PLAYGROUND_KINDS:
        blob = f"{text} {path}"
        if _wants_mobile(blob=blob, kind=k):
            return "capacitor"
        return "web"
    if _wants_mobile(blob=text, kind=k):
        return "capacitor"
    p = canonicalize_platform(k)
    if p in PLATFORMS:
        return p
    return None


def _wants_mobile(*, blob: str, kind: str) -> bool:
    if kind == "expo":
        return True
    try:
        from .capability import wants_mobile
        return wants_mobile(blob)
    except Exception:
        import re
        return bool(re.search(
            r"\b(expo|react[- ]native|capacitor|eas|ios|iphone|android|apk)\b",
            blob or "",
            re.I,
        ))


def visual_kinds() -> tuple[str, ...]:
    """Chromium visual kinds. Godot/Expo native are not this tuple."""
    return tuple(sorted(BROWSER_VISUAL_KINDS))


def proof_kinds() -> frozenset[str]:
    """Kinds that must not declare ready without visual/engine proof."""
    return BROWSER_VISUAL_KINDS | frozenset({"godot", "expo", "phaser"})


def allows_static_live_publish(kind: str) -> bool:
    k = (kind or "").strip().lower()
    if k in NON_STATIC_LIVE_KINDS:
        return False
    platform = platform_for_kind(k)
    if platform in {"godot", "expo", "capacitor"}:
        return False
    return True


def kind_rules(kind: str) -> str:
    k = (kind or "").strip().lower()
    if k in KIND_ALIASES:
        k = KIND_ALIASES[k]
    if k == "godot":
        try:
            from .godot_platform_guide import kind_rules_block
            return kind_rules_block()
        except Exception:
            return PLAYGROUND_KIND_RULES["godot"]
    if k == "expo":
        try:
            from .expo_platform_guide import expo_build_rules
            return expo_build_rules()
        except Exception:
            return PLAYGROUND_KIND_RULES["expo"]
    if k == "unity":
        try:
            from .unity_platform_guide import kind_rules_block
            return kind_rules_block()
        except Exception:
            return PLAYGROUND_KIND_RULES["unity"]
    if k in PLAYGROUND_KIND_RULES:
        return PLAYGROUND_KIND_RULES[k]
    p = canonicalize_platform(k)
    if p in UNSUPPORTED_PLATFORMS or p not in PLAYGROUND_KINDS and p not in PLATFORMS:
        return (
            f"UNSUPPORTED PLATFORM: {kind}. Do not emit Vite, HTML, Phaser, "
            "or a web preview fallback."
        )
    return PLAYGROUND_KIND_RULES.get(k, PLAYGROUND_KIND_RULES["static"])


def gameplay_proofs_for(platform: str) -> tuple[str, ...]:
    p = canonicalize_platform(platform)
    if p == "godot":
        return GODOT_GAMEPLAY_PROOFS
    if p in {"expo", "capacitor"}:
        return WEB_GAMEPLAY_PROOFS
    return WEB_GAMEPLAY_PROOFS


def device_smoke_proofs_for(platform: str) -> tuple[str, ...]:
    p = canonicalize_platform(platform)
    if p in {"expo", "capacitor"}:
        return DEVICE_SMOKE_PROOFS
    return ()


@dataclass(frozen=True)
class AdapterSpec:
    platform: str
    name: str
    module: str
    capabilities: frozenset[Capability]
    playground_kinds: frozenset[str] = field(default_factory=frozenset)
    visual: bool = True
    static_deploy: bool = False

    def supports(self, cap: Capability | str) -> bool:
        return as_capability(cap_name(cap)) in self.capabilities


@dataclass
class CapabilityEvidence:
    capability: str
    status: str
    project_id: str
    revision: str
    artifact_hash: str
    details: str = ""
    runner_version: str = RUNNER_VERSION
    checks: dict[str, bool] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        row = {
            "capability": self.capability,
            "status": self.status,
            "project_id": self.project_id,
            "revision": self.revision,
            "artifact_hash": self.artifact_hash,
            "details": self.details,
            "runner_version": self.runner_version,
        }
        if self.checks:
            row["checks"] = dict(self.checks)
        return row


@dataclass
class BuildArtifact:
    project_id: str
    revision: str
    target: str
    artifact_hash: str
    path: str
    immutable: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "revision": self.revision,
            "target": canonicalize_platform(self.target),
            "artifact_hash": self.artifact_hash,
            "path": self.path,
            "immutable": bool(self.immutable),
        }


@dataclass(frozen=True)
class ReleaseDecision:
    approved: bool
    blockers: tuple[str, ...] = ()


def evidence_record(
    capability: Capability | str,
    *,
    status: str,
    project_id: str,
    revision: str,
    artifact_hash: str,
    details: str = "",
    runner_version: str = RUNNER_VERSION,
    checks: Mapping[str, bool] | None = None,
) -> CapabilityEvidence:
    return CapabilityEvidence(
        capability=cap_name(capability),
        status=(status or "").strip().lower(),
        project_id=project_id,
        revision=revision,
        artifact_hash=artifact_hash,
        details=details,
        runner_version=runner_version or RUNNER_VERSION,
        checks=dict(checks or {}),
    )


def _as_evidence(raw: CapabilityEvidence | Mapping[str, Any]) -> CapabilityEvidence:
    if isinstance(raw, CapabilityEvidence):
        return raw
    checks = raw.get("checks") or {}
    if not isinstance(checks, dict):
        checks = {}
    return CapabilityEvidence(
        capability=cap_name(str(raw.get("capability") or "")),
        status=str(raw.get("status") or "").strip().lower(),
        project_id=str(raw.get("project_id") or ""),
        revision=str(raw.get("revision") or ""),
        artifact_hash=str(raw.get("artifact_hash") or ""),
        details=str(raw.get("details") or ""),
        runner_version=str(raw.get("runner_version") or RUNNER_VERSION),
        checks={str(k): bool(v) for k, v in checks.items()},
    )


def _as_artifact(raw: BuildArtifact | Mapping[str, Any]) -> BuildArtifact:
    if isinstance(raw, BuildArtifact):
        return replace(raw, target=canonicalize_platform(raw.target))
    return BuildArtifact(
        project_id=str(raw.get("project_id") or ""),
        revision=str(raw.get("revision") or ""),
        target=canonicalize_platform(str(raw.get("target") or "")),
        artifact_hash=str(raw.get("artifact_hash") or ""),
        path=str(raw.get("path") or ""),
        immutable=bool(raw.get("immutable", True)),
    )


def _proofs_ok(platform: str, capability: str, ev: CapabilityEvidence) -> bool:
    need: tuple[str, ...] = ()
    if capability == Capability.GAMEPLAY_PROOF.value:
        need = gameplay_proofs_for(platform)
    elif capability == Capability.DEVICE_SMOKE.value:
        need = device_smoke_proofs_for(platform)
    if not need:
        return True
    checks = ev.checks or {}
    return all(bool(checks.get(key)) for key in need)


def _default_adapters() -> dict[str, AdapterSpec]:
    web_caps = frozenset(DESKTOP_PROFILE)
    mobile_caps = frozenset(MOBILE_PROFILE)
    return {
        "web": AdapterSpec(
            platform="web",
            name="WebAdapter",
            module="daedalus.web_first",
            capabilities=web_caps,
            playground_kinds=WEB_PLAYGROUND_KINDS,
            visual=True,
            static_deploy=True,
        ),
        "godot": AdapterSpec(
            platform="godot",
            name="EngineAdapter",
            module="daedalus.godot_first",
            capabilities=web_caps,
            playground_kinds=frozenset({"godot"}),
            visual=True,
            static_deploy=False,
        ),
        "expo": AdapterSpec(
            platform="expo",
            name="MobileAdapter",
            module="daedalus.mobile_first",
            capabilities=mobile_caps,
            playground_kinds=frozenset({"expo"}),
            visual=True,
            static_deploy=False,
        ),
        "capacitor": AdapterSpec(
            platform="capacitor",
            name="MobileAdapter",
            module="daedalus.mobile_first",
            capabilities=mobile_caps,
            playground_kinds=WEB_PLAYGROUND_KINDS,
            visual=True,
            static_deploy=False,
        ),
    }


class CapabilityRegistry:
    def __init__(self, adapters: Mapping[str, AdapterSpec] | None = None) -> None:
        self._adapters = dict(adapters or _default_adapters())

    def platforms(self) -> tuple[str, ...]:
        return tuple(self._adapters.keys())

    def profile(self, platform: str) -> tuple[Capability, ...]:
        spec = self.adapter(platform)
        order = MOBILE_PROFILE if Capability.DEVICE_SMOKE in spec.capabilities else DESKTOP_PROFILE
        return tuple(c for c in order if c in spec.capabilities)

    def release_capabilities(self, platform: str) -> tuple[Capability, ...]:
        return tuple(c for c in self.profile(platform) if c in RELEASE_EVIDENCE)

    def adapter(self, platform: str) -> AdapterSpec:
        p = canonicalize_platform(platform)
        if p in UNSUPPORTED_PLATFORMS or p not in self._adapters:
            raise UnsupportedPlatform(f"unsupported platform: {platform or p}")
        return self._adapters[p]

    def missing(
        self,
        platform: str,
        required: Sequence[Capability | str],
    ) -> tuple[str, ...]:
        spec = self.adapter(platform)
        have = spec.capabilities
        out: list[str] = []
        for item in required:
            name = cap_name(item)
            if not name:
                continue
            cap = as_capability(name)
            if cap not in have:
                out.append(name)
        return tuple(out)

    def assert_supported(
        self,
        platform: str,
        required: Sequence[Capability | str],
    ) -> AdapterSpec:
        spec = self.adapter(platform)
        miss = self.missing(platform, required)
        if miss:
            raise ValueError(
                f"{canonicalize_platform(platform)} lacks capabilities: {', '.join(miss)}"
            )
        return spec

    def release_decision(
        self,
        artifact: BuildArtifact | Mapping[str, Any],
        required: Sequence[Capability | str],
        evidence: Iterable[CapabilityEvidence | Mapping[str, Any]] | None,
    ) -> ReleaseDecision:
        blockers: list[str] = []
        art = _as_artifact(artifact)
        try:
            self.assert_supported(art.target, required)
        except ValueError as exc:
            blockers.append(str(exc))
            return ReleaseDecision(approved=False, blockers=tuple(blockers))

        if hash_missing(art.artifact_hash):
            blockers.append("artifact hash is missing")
        if not art.immutable:
            blockers.append("artifact is not immutable")

        by_cap: dict[str, CapabilityEvidence] = {}
        for raw in evidence or ():
            ev = _as_evidence(raw)
            if ev.capability:
                by_cap[ev.capability] = ev

        for item in required:
            name = cap_name(item)
            if name in {Capability.PUBLISH.value, Capability.ROLLBACK.value, Capability.CREATE.value, Capability.WRITE.value}:
                if name in {Capability.PUBLISH.value, Capability.ROLLBACK.value}:
                    continue
            ev = by_cap.get(name)
            if ev is None:
                blockers.append(f"invalid evidence: {name}")
                continue
            if not status_is_pass(ev.status):
                blockers.append(f"invalid evidence: {name}")
                continue
            if ev.project_id != art.project_id:
                blockers.append(f"invalid evidence: {name}")
                continue
            if ev.revision != art.revision:
                blockers.append(f"invalid evidence: {name}")
                continue
            if normalize_hash(ev.artifact_hash) != normalize_hash(art.artifact_hash):
                blockers.append(f"invalid evidence: {name}")
                continue
            if not _proofs_ok(art.target, name, ev):
                blockers.append(f"invalid evidence: {name}")

        seen: list[str] = []
        for b in blockers:
            if b not in seen:
                seen.append(b)
        return ReleaseDecision(approved=not seen, blockers=tuple(seen))

    def with_adapter(self, spec: AdapterSpec) -> CapabilityRegistry:
        adapters = dict(self._adapters)
        adapters[spec.platform] = spec
        return CapabilityRegistry(adapters)


REGISTRY = CapabilityRegistry()


def stage_status(row: Mapping[str, Any] | None) -> str:
    if not row:
        return "blocked"
    if row.get("skipped"):
        return "skipped"
    if row.get("ok"):
        return "pass"
    return "fail"


def records_from_stages(
    *,
    platform: str,
    project_id: str,
    revision: str,
    artifact_hash: str,
    stages: Mapping[str, Mapping[str, Any]],
    runner_version: str = RUNNER_VERSION,
) -> list[CapabilityEvidence]:
    """Map pipeline stages onto per-capability evidence. Skip is never pass."""
    p = canonicalize_platform(platform)
    mapping: list[tuple[str, tuple[str, ...]]] = [
        ("plan", (Capability.CREATE.value,)),
        ("context", (Capability.WRITE.value,)),
        ("runtime", (Capability.RUN.value,)),
        ("gameplay", (Capability.INPUT.value, Capability.GAMEPLAY_PROOF.value)),
        ("visual", (Capability.RENDER_PROOF.value,)),
        ("export", (Capability.EXPORT.value,)),
    ]
    if p in {"expo", "capacitor"}:
        mapping.append(("runtime", (Capability.DEVICE_SMOKE.value,)))
    records: dict[str, CapabilityEvidence] = {}
    for stage, caps in mapping:
        row = stages.get(stage) or {}
        status = stage_status(row)
        checks = _checks_dict(row)
        for name in caps:
            proofs = {}
            if name == Capability.GAMEPLAY_PROOF.value:
                proofs = {k: bool(checks.get(k)) for k in gameplay_proofs_for(p)}
                if status == "pass" and not all(proofs.values()):
                    status = "fail"
            elif name == Capability.DEVICE_SMOKE.value:
                proofs = {k: bool(checks.get(k)) for k in device_smoke_proofs_for(p)}
                if status == "pass" and not all(proofs.values()):
                    status = "fail"
            records[name] = evidence_record(
                name,
                status=status,
                project_id=project_id,
                revision=revision,
                artifact_hash=artifact_hash,
                details=str(row.get("error") or row.get("summary") or row.get("details") or ""),
                runner_version=runner_version,
                checks=proofs or checks,
            )
    return list(records.values())


def _checks_dict(row: Mapping[str, Any]) -> dict[str, bool]:
    out: dict[str, bool] = {}
    for extra in ("proofs", "proof"):
        blob = row.get(extra)
        if isinstance(blob, dict):
            for key, val in blob.items():
                out[str(key)] = bool(val)
    for item in row.get("checks") or []:
        if isinstance(item, dict) and item.get("id"):
            out[str(item["id"])] = bool(item.get("ok"))
    return out


def is_workspace_path(root: str | Any, artifact_path: str) -> bool:
    from pathlib import Path

    try:
        root_p = Path(root).resolve()
        art = Path(artifact_path).resolve()
    except OSError:
        return False
    return art == root_p


__all__ = [
    "AdapterSpec",
    "BROWSER_VISUAL_KINDS",
    "BuildArtifact",
    "CAPABILITIES",
    "Capability",
    "CapabilityEvidence",
    "CapabilityRegistry",
    "DESKTOP_PROFILE",
    "KIND_ALIASES",
    "MOBILE_PROFILE",
    "NON_STATIC_LIVE_KINDS",
    "PLATFORMS",
    "PLAYGROUND_KINDS",
    "REGISTRY",
    "RUNNER_VERSION",
    "ReleaseDecision",
    "UnsupportedPlatform",
    "WEB_PLAYGROUND_KINDS",
    "allows_static_live_publish",
    "as_capability",
    "canonicalize_platform",
    "cap_name",
    "evidence_record",
    "hash_missing",
    "is_workspace_path",
    "kind_rules",
    "normalize_hash",
    "platform_for_kind",
    "playground_kind",
    "proof_kinds",
    "records_from_stages",
    "stage_status",
    "visual_kinds",
]
