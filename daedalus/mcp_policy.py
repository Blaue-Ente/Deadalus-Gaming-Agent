"""MCP security policy — pin, allowlist, write grant, host and child-env gates.

Enforced at install / connect / call. Default fail-closed for unverified
``npx -y`` and unsigned custom servers. ``DAEDALUS_MCP_POLICY=open`` restores
today's spawn. Do not import agent.py.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from daedalus.contracts.commands import NetworkPolicy

log = logging.getLogger(__name__)

WRITE_PERM = "mcp_write"

_NPX_NAMES = frozenset({"npx", "npx.cmd"})
_YES_FLAGS = frozenset({"-y", "--yes"})
_LOOPBACK_HOSTS = frozenset({"localhost", "localhost.localdomain", "ip6-localhost"})
_METADATA_HOSTS = frozenset({
    "metadata",
    "metadata.google.internal",
    "metadata.google.com",
})
_CHILD_ENV_KEYS = frozenset({
    "PATH", "HOME", "LANG", "TERM", "TMPDIR", "TMP", "TEMP",
    "USER", "LOGNAME", "TZ", "SYSTEMROOT", "PATHEXT", "COMSPEC", "WINDIR",
})
_CHILD_ENV_PREFIXES = ("LC_", "NODE_", "npm_", "NPM_", "XDG_")
_WRITE_HINTS = (
    "write", "create", "delete", "update", "run", "execute", "play",
    "set", "call", "export", "manage", "remove", "put", "patch", "send",
    "start", "stop", "clear", "modify", "parent", "apply", "add", "batch",
)
_READ_HINTS = (
    "list", "get", "read", "status", "search", "inspect", "tree",
    "errors", "fps", "find", "dump", "log", "ping",
)
_NETWORK_HINTS = ("fetch", "http", "web", "download", "browse", "request")


class InstallTrust(str, Enum):
    CATALOG = "catalog"
    PINNED = "pinned"
    UNVERIFIED = "unverified"


class McpToolClass(str, Enum):
    READ = "read"
    WRITE = "write"
    NETWORK = "network"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class McpProvenance:
    source: str = ""
    catalog_id: str = ""
    digest: str = ""
    enabled_at: str = ""
    approved_at: str = ""
    approved_by: str = ""
    npx_yes: bool = False
    version_pinned: bool = False


@dataclass(frozen=True)
class McpServerPolicy:
    name: str
    trust: InstallTrust = InstallTrust.UNVERIFIED
    tools_allow: tuple[str, ...] = ()
    write_tools: tuple[str, ...] = ()
    network: NetworkPolicy = NetworkPolicy.ALLOWLIST
    hosts_allow: tuple[str, ...] = ()
    require_approval_for_write: bool = True
    provenance: McpProvenance = field(default_factory=McpProvenance)


@dataclass(frozen=True)
class McpDecision:
    allowed: bool
    blocked: bool = False
    reason: str = ""
    trust: InstallTrust = InstallTrust.UNVERIFIED
    need_approval: bool = False
    need_pin: bool = False
    tool_class: McpToolClass = McpToolClass.UNKNOWN
    provenance: McpProvenance | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _env_flag(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def policy_is_open() -> bool:
    raw = _env_flag("DAEDALUS_MCP_POLICY").lower()
    return raw in {"open", "0", "false", "no", "off"}


def overlay_path() -> Path:
    env = _env_flag("DAEDALUS_MCP_POLICY_FILE")
    if env:
        return Path(env)
    from .paths import data_root
    return data_root() / "mcp_policy.json"


def transport_shape(spec: dict[str, Any]) -> dict[str, Any]:
    command = spec.get("command")
    args = spec.get("args") or []
    if isinstance(command, list):
        argv = [str(x) for x in command]
        cmd = argv[0] if argv else ""
        rest = argv[1:]
    else:
        cmd = str(command or "")
        rest = [str(x) for x in args]
    return {
        "command": cmd,
        "args": rest,
        "url": str(spec.get("url") or "").strip(),
    }


def spec_argv(spec: dict[str, Any]) -> list[str]:
    shape = transport_shape(spec)
    if not shape["command"]:
        return []
    return [shape["command"], *shape["args"]]


def spec_digest(spec: dict[str, Any]) -> str:
    blob = json.dumps(transport_shape(spec), ensure_ascii=False, sort_keys=True)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _package_version_pinned(token: str) -> bool:
    raw = (token or "").strip()
    if not raw or raw.startswith("-"):
        return False
    if "integrity=" in raw or raw.startswith("sha512-") or raw.startswith("sha256-"):
        return True
    if raw.startswith("@"):
        if "/" not in raw:
            return False
        return "@" in raw.rsplit("/", 1)[-1]
    return "@" in raw


def version_pinned(spec: dict[str, Any]) -> bool:
    if str(spec.get("integrity") or "").strip():
        return True
    argv = spec_argv(spec)
    pkgs = [a for a in argv[1:] if not str(a).startswith("-")]
    return bool(pkgs) and _package_version_pinned(pkgs[0])


def is_unverified_npx(spec: dict[str, Any]) -> bool:
    argv = spec_argv(spec)
    if not argv:
        return False
    exe = Path(argv[0]).name.lower()
    if exe not in _NPX_NAMES:
        return False
    if not any(str(a) in _YES_FLAGS for a in argv[1:]):
        return False
    return not version_pinned(spec)


def _allow(
    *,
    trust: InstallTrust = InstallTrust.PINNED,
    tool_class: McpToolClass = McpToolClass.READ,
    provenance: McpProvenance | None = None,
) -> McpDecision:
    return McpDecision(
        allowed=True,
        blocked=False,
        trust=trust,
        tool_class=tool_class,
        provenance=provenance,
    )


def _deny(
    reason: str,
    *,
    trust: InstallTrust = InstallTrust.UNVERIFIED,
    need_approval: bool = False,
    need_pin: bool = False,
    tool_class: McpToolClass = McpToolClass.UNKNOWN,
    provenance: McpProvenance | None = None,
) -> McpDecision:
    return McpDecision(
        allowed=False,
        blocked=True,
        reason=reason,
        trust=trust,
        need_approval=need_approval,
        need_pin=need_pin,
        tool_class=tool_class,
        provenance=provenance,
    )


def _row_to_policy(name: str, row: dict[str, Any]) -> McpServerPolicy:
    try:
        trust = InstallTrust(str(row.get("trust") or InstallTrust.UNVERIFIED.value))
    except ValueError:
        trust = InstallTrust.UNVERIFIED
    try:
        network = NetworkPolicy(str(row.get("network") or NetworkPolicy.ALLOWLIST.value))
    except ValueError:
        network = NetworkPolicy.ALLOWLIST
    prov = McpProvenance(
        source=str(row.get("source") or ""),
        catalog_id=str(row.get("catalog_id") or ""),
        digest=str(row.get("digest") or ""),
        enabled_at=str(row.get("enabled_at") or ""),
        approved_at=str(row.get("approved_at") or ""),
        approved_by=str(row.get("approved_by") or ""),
        npx_yes=bool(row.get("npx_yes")),
        version_pinned=bool(row.get("version_pinned")),
    )
    return McpServerPolicy(
        name=name,
        trust=trust,
        tools_allow=tuple(str(x) for x in (row.get("tools_allow") or []) if str(x).strip()),
        write_tools=tuple(str(x) for x in (row.get("write_tools") or []) if str(x).strip()),
        network=network,
        hosts_allow=tuple(str(x) for x in (row.get("hosts_allow") or []) if str(x).strip()),
        require_approval_for_write=row.get("require_approval_for_write", True) is not False,
        provenance=prov,
    )


def _policy_to_row(policy: McpServerPolicy) -> dict[str, Any]:
    p = policy.provenance
    return {
        "trust": policy.trust.value,
        "tools_allow": list(policy.tools_allow),
        "write_tools": list(policy.write_tools),
        "network": policy.network.value,
        "hosts_allow": list(policy.hosts_allow),
        "require_approval_for_write": policy.require_approval_for_write,
        "source": p.source,
        "catalog_id": p.catalog_id,
        "digest": p.digest,
        "enabled_at": p.enabled_at,
        "approved_at": p.approved_at,
        "approved_by": p.approved_by,
        "npx_yes": p.npx_yes,
        "version_pinned": p.version_pinned,
    }


def load_overlay() -> dict[str, McpServerPolicy]:
    path = overlay_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("mcp_policy.json unreadable: %s", exc)
        return {}
    servers = data.get("servers") if isinstance(data, dict) else None
    if not isinstance(servers, dict):
        return {}
    out: dict[str, McpServerPolicy] = {}
    for key, row in servers.items():
        if isinstance(row, dict):
            out[str(key)] = _row_to_policy(str(key), row)
    return out


def save_overlay(policies: dict[str, McpServerPolicy]) -> None:
    path = overlay_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"servers": {name: _policy_to_row(pol) for name, pol in policies.items()}}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def remove_overlay(name: str) -> None:
    key = (name or "").strip()
    if not key:
        return
    policies = load_overlay()
    if key not in policies:
        return
    policies.pop(key, None)
    save_overlay(policies)


def catalog_pin(name: str) -> dict[str, Any] | None:
    """Expanded catalog transport spec, or None if the id is not an MCP row."""
    from .addons import CATALOG, _expand

    item = next((x for x in CATALOG if x["id"] == name and x.get("kind") == "mcp"), None)
    if not item:
        return None
    return {
        "command": item.get("command"),
        "args": _expand(list(item.get("args_template") or [])),
    }


def _blender_tools() -> tuple[str, ...]:
    try:
        from .blender_jobs import JOB_IDS
        return tuple(JOB_IDS)
    except Exception:
        return ()


def _catalog_tools(name: str) -> tuple[str, ...]:
    if name == "blender-mcp":
        return _blender_tools()
    static = {
        "filesystem": ("read_file", "list_directory", "read_text_file"),
        "git": ("git_status", "git_log", "git_diff"),
        "memory": ("read_graph", "open_nodes", "search_nodes"),
        "fetch": ("fetch",),
    }
    return static.get(name, ())


def resolve_policy(name: str, spec: dict[str, Any]) -> McpServerPolicy:
    key = (name or "").strip()
    live = spec_digest(spec)
    overlay = load_overlay().get(key)
    npx = is_unverified_npx(spec) or (bool(spec_argv(spec)) and Path(spec_argv(spec)[0]).name.lower() in _NPX_NAMES and any(a in _YES_FLAGS for a in spec_argv(spec)[1:]))
    if overlay is None:
        return McpServerPolicy(
            name=key,
            trust=InstallTrust.UNVERIFIED,
            provenance=McpProvenance(
                digest=live,
                npx_yes=is_unverified_npx(spec) or npx,
                version_pinned=version_pinned(spec),
            ),
        )
    trust = overlay.trust
    if overlay.provenance.digest and overlay.provenance.digest != live:
        trust = InstallTrust.UNVERIFIED
    prov = McpProvenance(
        source=overlay.provenance.source,
        catalog_id=overlay.provenance.catalog_id,
        digest=live,
        enabled_at=overlay.provenance.enabled_at,
        approved_at=overlay.provenance.approved_at,
        approved_by=overlay.provenance.approved_by,
        npx_yes=is_unverified_npx(spec) or overlay.provenance.npx_yes,
        version_pinned=version_pinned(spec),
    )
    return McpServerPolicy(
        name=key,
        trust=trust,
        tools_allow=overlay.tools_allow,
        write_tools=overlay.write_tools,
        network=overlay.network,
        hosts_allow=overlay.hosts_allow,
        require_approval_for_write=overlay.require_approval_for_write,
        provenance=prov,
    )


def hosts_allow_for(name: str, spec: dict[str, Any] | None = None) -> tuple[str, ...]:
    if spec is None:
        return ()
    return resolve_policy(name, spec).hosts_allow


def _hard_deny_host(host: str, ip: ipaddress.IPv4Address | ipaddress.IPv6Address | None) -> bool:
    if host in _METADATA_HOSTS:
        return True
    if ip is None:
        return False
    if ip.is_link_local:
        return True
    if isinstance(ip, ipaddress.IPv4Address) and ip in ipaddress.ip_network("169.254.0.0/16"):
        return True
    return False


def _parse_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def http_url_allowed(
    url: str,
    *,
    hosts_allow: tuple[str, ...] | list[str] = (),
) -> McpDecision:
    raw = (url or "").strip()
    if policy_is_open():
        return _allow(trust=InstallTrust.UNVERIFIED)
    if not raw:
        return _deny("MCP HTTP url required", need_pin=True)
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        return _deny(f"MCP HTTP scheme {parsed.scheme or 'missing'!r} is not allowed", need_pin=True)
    host = (parsed.hostname or "").lower()
    if not host:
        return _deny("MCP HTTP host required", need_pin=True)
    ip = _parse_ip(host)
    if _hard_deny_host(host, ip):
        return _deny(f"MCP HTTP host {host!r} is not allowed", need_pin=True)
    if ip is not None and ip.is_loopback:
        return _allow()
    if host in _LOOPBACK_HOSTS:
        return _allow()
    extra = {str(h).strip().lower() for h in hosts_allow if str(h).strip()}
    if host in extra:
        return _allow()
    return _deny(
        f"MCP HTTP host {host!r} is outside the loopback allowlist",
        need_pin=True,
    )


def evaluate_install(name: str, spec: dict[str, Any]) -> McpDecision:
    """Gate custom upsert. Catalog ``enable`` writes even when this would deny spawn."""
    if policy_is_open():
        return _allow(trust=InstallTrust.UNVERIFIED)
    key = (name or "").strip()
    if not key:
        return _deny("invalid server name", need_pin=True)
    if is_unverified_npx(spec):
        return _deny(
            "unverified npx -y is blocked — pin a package version and approve the digest",
            need_pin=True,
        )
    url = str(spec.get("url") or "").strip()
    if url:
        http = http_url_allowed(url)
        if not http.allowed:
            return http
    return _allow(trust=InstallTrust.UNVERIFIED)


def evaluate_connect(name: str, spec: dict[str, Any]) -> McpDecision:
    if policy_is_open():
        return _allow(trust=InstallTrust.UNVERIFIED)
    policy = resolve_policy(name, spec)
    prov = policy.provenance
    if is_unverified_npx(spec):
        if not (version_pinned(spec) and policy.trust in {InstallTrust.PINNED, InstallTrust.CATALOG}):
            return _deny(
                "unverified npx -y will not spawn — pin @version and approve the server",
                trust=policy.trust,
                need_pin=True,
                provenance=prov,
            )
    if policy.trust == InstallTrust.UNVERIFIED:
        return _deny(
            f"MCP server {name!r} is unverified — owner must pin the digest",
            trust=InstallTrust.UNVERIFIED,
            need_pin=True,
            provenance=prov,
        )
    url = str(spec.get("url") or "").strip()
    if url:
        http = http_url_allowed(url, hosts_allow=policy.hosts_allow)
        if not http.allowed:
            return _deny(http.reason, trust=policy.trust, need_pin=True, provenance=prov)
    return _allow(trust=policy.trust, provenance=prov)


def classify_tool(
    server: str,
    tool: str,
    arguments: dict[str, Any] | None = None,
    *,
    write_tools: tuple[str, ...] = (),
) -> McpToolClass:
    name = (tool or "").strip()
    if not name:
        return McpToolClass.UNKNOWN
    if name in write_tools:
        return McpToolClass.WRITE
    args = arguments if isinstance(arguments, dict) else {}
    if server == "blender-mcp" and args.get("execute"):
        return McpToolClass.WRITE
    low = name.lower()
    if any(hint in low for hint in _NETWORK_HINTS):
        return McpToolClass.NETWORK
    if any(hint in low for hint in _WRITE_HINTS):
        return McpToolClass.WRITE
    if any(hint in low for hint in _READ_HINTS):
        return McpToolClass.READ
    return McpToolClass.UNKNOWN


def _write_granted() -> bool:
    try:
        from .outbox import check_permission
        return bool(check_permission(WRITE_PERM))
    except Exception:
        return False


def _write_denied_reason(tool: str) -> str:
    try:
        from .outbox import format_permission_denied
        return format_permission_denied(WRITE_PERM, tool=f"mcp_call:{tool}")
    except Exception:
        return f"MCP write tool {tool!r} needs permission {WRITE_PERM}"


def evaluate_call(
    name: str,
    spec: dict[str, Any],
    tool: str,
    arguments: dict[str, Any] | None = None,
) -> McpDecision:
    if policy_is_open():
        return _allow(trust=InstallTrust.UNVERIFIED)
    connected = evaluate_connect(name, spec)
    if not connected.allowed:
        return connected
    policy = resolve_policy(name, spec)
    tool_name = (tool or "").strip()
    if not tool_name:
        return _deny("MCP tool name required", trust=policy.trust, provenance=policy.provenance)
    if policy.tools_allow and tool_name not in policy.tools_allow:
        return _deny(
            f"MCP tool {tool_name!r} is not on the allowlist for {name!r}",
            trust=policy.trust,
            provenance=policy.provenance,
        )
    kind = classify_tool(name, tool_name, arguments, write_tools=policy.write_tools)
    if kind is McpToolClass.NETWORK:
        try:
            from .offline import deny as offline_deny
            blocked = offline_deny("mcp_call")
        except Exception:
            blocked = None
        if blocked:
            return _deny(blocked, trust=policy.trust, tool_class=kind, provenance=policy.provenance)
        try:
            from .offline import enabled as offline_on
            if offline_on():
                return _deny(
                    "── OFFLINE KIT: MCP network tool is off ──",
                    trust=policy.trust,
                    tool_class=kind,
                    provenance=policy.provenance,
                )
        except Exception:
            pass
    if kind in {McpToolClass.WRITE, McpToolClass.UNKNOWN} and policy.require_approval_for_write:
        if not _write_granted():
            return _deny(
                _write_denied_reason(tool_name),
                trust=policy.trust,
                need_approval=True,
                tool_class=kind,
                provenance=policy.provenance,
            )
    return _allow(trust=policy.trust, tool_class=kind, provenance=policy.provenance)


def child_env(spec: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, val in os.environ.items():
        if key in _CHILD_ENV_KEYS or key.startswith(_CHILD_ENV_PREFIXES):
            out[key] = val
    extra = spec.get("env") if isinstance(spec.get("env"), dict) else {}
    for key, val in extra.items():
        name = str(key).strip()
        if name:
            out[name] = str(val)
    return out


def record_enable(
    name: str,
    spec: dict[str, Any],
    *,
    source: str,
    catalog_id: str = "",
) -> McpServerPolicy:
    key = (name or "").strip()
    digest = spec_digest(spec)
    catalog = catalog_pin(key) if source == "catalog" else None
    catalog_match = bool(catalog) and spec_digest(catalog) == digest
    npx = is_unverified_npx(spec)
    if source == "catalog" and catalog_match and not npx:
        trust = InstallTrust.CATALOG
        tools = _catalog_tools(key)
    else:
        trust = InstallTrust.UNVERIFIED
        tools = _catalog_tools(key) if source == "catalog" else ()
    existing = load_overlay()
    prev = existing.get(key)
    enabled_at = prev.provenance.enabled_at if prev else _now()
    policy = McpServerPolicy(
        name=key,
        trust=trust,
        tools_allow=tools,
        write_tools=(),
        network=NetworkPolicy.ALLOWLIST,
        hosts_allow=(),
        require_approval_for_write=True,
        provenance=McpProvenance(
            source=source,
            catalog_id=catalog_id or (key if source == "catalog" else ""),
            digest=digest,
            enabled_at=enabled_at or _now(),
            npx_yes=npx or (bool(spec_argv(spec)) and Path(spec_argv(spec)[0]).name.lower() in _NPX_NAMES and any(a in _YES_FLAGS for a in spec_argv(spec)[1:])),
            version_pinned=version_pinned(spec),
        ),
    )
    existing[key] = policy
    save_overlay(existing)
    return policy


def pin_server(
    name: str,
    spec: dict[str, Any] | None = None,
    *,
    tools_allow: tuple[str, ...] | list[str] = (),
    write_tools: tuple[str, ...] | list[str] = (),
    hosts_allow: tuple[str, ...] | list[str] = (),
    approved_by: str = "owner",
) -> dict[str, Any]:
    key = (name or "").strip()
    if spec is None:
        from .mcp_client import load_servers
        spec = load_servers().get(key)
    if not spec:
        return {"ok": False, "error": f"unknown MCP server {key!r}"}
    existing = load_overlay()
    prev = existing.get(key)
    tools = tuple(str(x) for x in tools_allow if str(x).strip()) or (prev.tools_allow if prev else ())
    writes = tuple(str(x) for x in write_tools if str(x).strip()) or (prev.write_tools if prev else ())
    hosts = tuple(str(x) for x in hosts_allow if str(x).strip()) or (prev.hosts_allow if prev else ())
    catalog = catalog_pin(key)
    digest = spec_digest(spec)
    trust = InstallTrust.PINNED
    if catalog and spec_digest(catalog) == digest and not is_unverified_npx(spec):
        trust = InstallTrust.CATALOG
        if not tools:
            tools = _catalog_tools(key)
    source = prev.provenance.source if prev else ("catalog" if catalog else "custom")
    policy = McpServerPolicy(
        name=key,
        trust=trust,
        tools_allow=tools,
        write_tools=writes,
        network=NetworkPolicy.ALLOWLIST,
        hosts_allow=hosts,
        require_approval_for_write=True,
        provenance=McpProvenance(
            source=source,
            catalog_id=prev.provenance.catalog_id if prev else (key if catalog else ""),
            digest=digest,
            enabled_at=(prev.provenance.enabled_at if prev else "") or _now(),
            approved_at=_now(),
            approved_by=approved_by,
            npx_yes=is_unverified_npx(spec) or (
                bool(spec_argv(spec))
                and Path(spec_argv(spec)[0]).name.lower() in _NPX_NAMES
                and any(a in _YES_FLAGS for a in spec_argv(spec)[1:])
            ),
            version_pinned=version_pinned(spec),
        ),
    )
    existing[key] = policy
    save_overlay(existing)
    return {"ok": True, "name": key, "trust": trust.value, "digest": digest}


def status_entry(name: str, spec: dict[str, Any]) -> dict[str, Any]:
    policy = resolve_policy(name, spec)
    p = policy.provenance
    return {
        "trust": policy.trust.value,
        "digest": p.digest or spec_digest(spec),
        "source": p.source,
        "npx_yes": p.npx_yes or is_unverified_npx(spec),
        "version_pinned": version_pinned(spec),
        "tools_allow": len(policy.tools_allow),
        "need_pin": policy.trust is InstallTrust.UNVERIFIED or is_unverified_npx(spec),
    }


def denied_payload(name: str, decision: McpDecision, *, tool: str = "") -> dict[str, Any]:
    row: dict[str, Any] = {
        "ok": False,
        "blocked": True,
        "error": decision.reason,
        "server": name,
        "trust": decision.trust.value,
        "need_pin": decision.need_pin,
        "need_approval": decision.need_approval,
    }
    if tool:
        row["tool"] = tool
    return row


__all__ = [
    "WRITE_PERM",
    "InstallTrust",
    "McpDecision",
    "McpProvenance",
    "McpServerPolicy",
    "McpToolClass",
    "catalog_pin",
    "child_env",
    "classify_tool",
    "denied_payload",
    "evaluate_call",
    "evaluate_connect",
    "evaluate_install",
    "hosts_allow_for",
    "http_url_allowed",
    "is_unverified_npx",
    "load_overlay",
    "overlay_path",
    "pin_server",
    "policy_is_open",
    "record_enable",
    "remove_overlay",
    "resolve_policy",
    "spec_digest",
    "status_entry",
    "transport_shape",
    "version_pinned",
]
