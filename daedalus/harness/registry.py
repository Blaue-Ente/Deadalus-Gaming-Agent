"""Read-only capability registry.

One list of everything Daedalus can do: brain tools, HTTP routes, Studio panels, header
settings and workflow steps. Tools and routes are generated from the code that already
defines them (``native_tools.NATIVE_TOOL_SPECS``, ``PERM_MAP``, ``task_playbooks``, the
mounted routers); only what code cannot say lives in ``manifest``. Building the registry
imports modules and reads their route tables. It never calls a provider, touches a project
or writes state.
"""
from __future__ import annotations

import importlib
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

from . import manifest

KINDS = ("tool", "route", "panel", "setting", "workflow")
STATUSES = ("implemented", "partial", "stub", "retired", "planned")
COSTS = ("none", "llm", "external", "unknown")

_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")


@dataclass(frozen=True)
class Capability:
    id: str
    kind: str
    title: str
    owner_module: str = ""
    http: tuple[str, ...] = ()
    ui_home: str | None = None
    surface: str = ""
    brain: dict[str, Any] = field(default_factory=lambda: {"exposed": False, "tool_name": None, "playbooks": []})
    permission_class: str | None = None
    proof_level_max: int | None = None
    status: str = "implemented"
    cost: str = "unknown"
    notes: str = ""

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["http"] = list(self.http)
        return out


def _surface(path: str) -> str:
    for prefix, label in manifest.SURFACES:
        if path.startswith(prefix):
            return label
    return "app"


def _playbooks_for(tool: str) -> list[str]:
    from .. import task_playbooks as tp

    core = set(getattr(tp, "_CORE", ()))
    out = [pb.id for pb in tp.PLAYBOOKS if tool in pb.tools or tool in core]
    return sorted(set(out))


def tool_capabilities() -> list[Capability]:
    from .. import native_tools, tool_catalog

    meta = {entry.name: entry for entry in tool_catalog.native_entries()}
    perm = getattr(native_tools, "PERM_MAP", {})
    out: list[Capability] = []
    for spec in native_tools.NATIVE_TOOL_SPECS:
        name = str(spec["name"])
        entry = meta.get(name)
        out.append(Capability(
            id=f"tool:{name}",
            kind="tool",
            title=str(spec.get("description") or name).split(".")[0][:140],
            owner_module=(entry.module if entry else "daedalus.native_tools"),
            surface="brain",
            brain={"exposed": True, "tool_name": name, "playbooks": _playbooks_for(name)},
            permission_class=perm.get(name),
            proof_level_max=manifest.TOOL_PROOF.get(name),
            status="implemented",
            cost=manifest.TOOL_COST.get(name, "none"),
            notes=(entry.when if entry else ""),
        ))
    return out


def _api_routes(routes: Iterable[Any]) -> Iterable[tuple[str, str, Any]]:
    try:
        from fastapi.routing import APIRoute
    except Exception:  # pragma: no cover - fastapi is a hard dependency
        return
    for route in routes:
        if isinstance(route, APIRoute):
            for method in sorted(route.methods or ()):
                if method in _METHODS:
                    yield method, route.path, route.endpoint


def iter_routes(app: Any | None = None) -> list[tuple[str, str, Any]]:
    """(method, path, endpoint) for every served API route, de-duplicated and sorted."""
    seen: dict[tuple[str, str], Any] = {}
    for mod, attr in manifest.ROUTER_SOURCES:
        try:
            router = getattr(importlib.import_module(mod), attr)
        except Exception:
            continue  # a router that cannot import is simply absent from the registry
        for method, path, endpoint in _api_routes(getattr(router, "routes", ())):
            seen.setdefault((method, path), endpoint)
    if app is not None:
        for method, path, endpoint in _api_routes(getattr(app, "routes", ())):
            seen.setdefault((method, path), endpoint)
    return sorted(((m, p, e) for (m, p), e in seen.items()), key=lambda r: (r[1], r[0]))


def _route_home(path: str) -> str | None:
    for pattern, home in _ROUTE_HOMES:
        if re.match(pattern, path):
            return home
    return None


# Where a route's UI lives today. Unlisted routes have no Studio home yet (ui_home None).
_ROUTE_HOMES: tuple[tuple[str, str], ...] = (
    (r"^/api/v1/playground/projects", "panel:rail"),
    (r"^/api/v1/daedalus/chat", "panel:chat"),
    (r"^/api/v1/daedalus/(mentor|writer-assist|run-budget|effort)", "panel:settings"),
    (r"^/admin/daedalus/(proposals|skills|bundles|permissions|outbox)", "panel:review"),
    (r"^/admin/daedalus/(blockers|planner|discoveries)", "panel:review"),
    (r"^/admin/daedalus/connectors/(config|overview|secrets)", "panel:settings"),
    (r"^/admin/daedalus/game-studio/", "panel:build"),
    (r"^/admin/daedalus/playground/api/projects/\{slug\}/(guided|autopilot)", "panel:build"),
)


def route_capabilities(app: Any | None = None) -> list[Capability]:
    out: list[Capability] = []
    for method, path, endpoint in iter_routes(app):
        if path == manifest.CATCH_ALL_PATH:
            out.append(Capability(
                id=f"route:{method} {path}", kind="route",
                title="Catch-all for unwired /admin/daedalus/* calls (answers 404)",
                owner_module=f"{endpoint.__module__}.{endpoint.__name__}",
                http=(f"{method} {path}",), surface=_surface(path),
                status="stub", cost="none",
            ))
            continue
        key = f"{method} {path}"
        out.append(Capability(
            id=f"route:{key}", kind="route",
            title=(getattr(endpoint, "__doc__", None) or endpoint.__name__).strip().split("\n")[0][:140],
            owner_module=f"{getattr(endpoint, '__module__', '')}.{getattr(endpoint, '__name__', '')}",
            http=(key,), ui_home=_route_home(path), surface=_surface(path),
            status=manifest.ROUTE_STATUS.get(key, "implemented"), cost="unknown",
        ))
    return out


def manifest_capabilities() -> list[Capability]:
    out: list[Capability] = []
    for cid, (title, status, notes) in manifest.PANELS.items():
        out.append(Capability(id=cid, kind="panel", title=title, surface="studio", status=status,
                              cost="none", notes=notes))
    for cid, (title, status) in manifest.SETTINGS.items():
        out.append(Capability(id=cid, kind="setting", title=title, surface="studio", status=status, cost="none"))
    for cid, (title, status) in manifest.WORKFLOWS.items():
        out.append(Capability(id=cid, kind="workflow", title=title, surface="studio", status=status))
    return out


def build_registry(app: Any | None = None) -> list[Capability]:
    """All capabilities, sorted by (kind, id) so the output is deterministic."""
    caps = tool_capabilities() + route_capabilities(app) + manifest_capabilities()
    unique: dict[str, Capability] = {}
    for cap in caps:
        unique.setdefault(cap.id, cap)
    return sorted(unique.values(), key=lambda c: (KINDS.index(c.kind), c.id))


def registry_payload(app: Any | None = None, *, kind: str = "", status: str = "") -> dict[str, Any]:
    caps = build_registry(app)
    if kind:
        caps = [c for c in caps if c.kind == kind]
    if status:
        caps = [c for c in caps if c.status == status]
    counts: dict[str, int] = {}
    for c in caps:
        counts[c.kind] = counts.get(c.kind, 0) + 1
    return {"ok": True, "count": len(caps), "by_kind": counts, "capabilities": [c.as_dict() for c in caps]}
