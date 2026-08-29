"""Curated connector catalog with honest configuration and health status."""
from __future__ import annotations

import os
import shutil
from dataclasses import asdict, dataclass
from typing import Any, Literal

ConnectorKind = Literal["git", "deploy", "backend", "observability", "tool", "notification", "storage"]


@dataclass(frozen=True)
class ConnectorDefinition:
    id: str
    label: str
    kind: ConnectorKind
    capabilities: tuple[str, ...]
    env_keys: tuple[str, ...] = ()
    offline_ok: bool = False
    setup_hint: str = ""


CONNECTORS: tuple[ConnectorDefinition, ...] = (
    ConnectorDefinition("github", "GitHub", "git", ("clone", "push", "repositories"), ("GITHUB_TOKEN",)),
    ConnectorDefinition("gitlab", "GitLab", "git", ("clone", "push", "pages"), ("GITLAB_TOKEN",)),
    ConnectorDefinition("netlify", "Netlify", "deploy", ("public-deploy",), ("NETLIFY_AUTH_TOKEN",)),
    ConnectorDefinition("cloudflare", "Cloudflare Pages", "deploy", ("public-deploy",), ("CLOUDFLARE_API_TOKEN",)),
    ConnectorDefinition("itch", "itch.io Butler", "deploy", ("game-distribution",), ("ITCHIO_API_KEY",)),
    ConnectorDefinition("supabase", "Supabase", "backend", ("database", "auth", "storage"), ("SUPABASE_URL", "SUPABASE_ANON_KEY")),
    ConnectorDefinition("sentry", "Sentry", "observability", ("error-reporting",), ("SENTRY_DSN",)),
    ConnectorDefinition("discord", "Discord webhook", "notification", ("deploy-notifications",), ("DISCORD_WEBHOOK_URL",)),
    ConnectorDefinition("object-storage", "Object Storage", "storage", ("workspace-backup", "published-apps"), offline_ok=True),
    ConnectorDefinition("mcp", "MCP servers", "tool", ("external-tools",), offline_ok=True),
    ConnectorDefinition("mirofish", "MiroFish", "tool", ("simulation",), ("MIROFISH_URL",), offline_ok=True),
    ConnectorDefinition("reach", "Agent Reach", "tool", ("web-research",)),
    ConnectorDefinition("blender", "Blender MCP", "tool", ("3d-assets",), offline_ok=True),
)


def _has_any(*names: str) -> bool:
    return any((os.environ.get(name) or "").strip() for name in names)


def _base_status(definition: ConnectorDefinition) -> dict[str, Any]:
    configured = all((os.environ.get(name) or "").strip() for name in definition.env_keys)
    return {
        **asdict(definition),
        "capabilities": list(definition.capabilities),
        "env_keys": list(definition.env_keys),
        "configured": configured if definition.env_keys else False,
        "ok": configured if definition.env_keys else False,
        "status": "ready" if configured else "not_configured",
        "detail": "",
        "testable": definition.id in {
            "github",
            "gitlab",
            "netlify",
            "supabase",
            "discord",
            "mirofish",
            "reach",
        },
    }


async def _status_for(definition: ConnectorDefinition, *, probe: bool) -> dict[str, Any]:
    row = _base_status(definition)
    connector_id = definition.id
    if connector_id == "github":
        from .connectors_config import github_token, test_github_connection

        row["configured"] = bool(github_token())
        if probe and row["configured"]:
            row.update(await test_github_connection())
        else:
            row["ok"] = row["configured"]
    elif connector_id == "gitlab":
        from .connectors_config import test_gitlab_connection
        from .playground_gitlab import is_configured

        row["configured"] = is_configured()
        if probe and row["configured"]:
            row.update(await test_gitlab_connection())
        else:
            row["ok"] = row["configured"]
    elif connector_id == "netlify":
        from .live_publish import netlify_token

        row["configured"] = bool(netlify_token())
        row["ok"] = row["configured"] and bool(shutil.which("npx") or shutil.which("npx.cmd"))
        row["detail"] = "Netlify CLI ready" if row["ok"] else "Requires token and npx"
    elif connector_id == "cloudflare":
        row["configured"] = _has_any("CLOUDFLARE_API_TOKEN")
        row["ok"] = False
        row["testable"] = False
        row["detail"] = "Token configured; deploy adapter is not enabled yet"
    elif connector_id == "itch":
        row["configured"] = _has_any("ITCHIO_API_KEY")
        row["ok"] = row["configured"] and bool(shutil.which("butler"))
        row["detail"] = "Ready" if row["ok"] else "Requires ITCHIO_API_KEY and butler"
        row["testable"] = False
    elif connector_id == "sentry":
        row["detail"] = "Configuration detection; project SDK injection is not enabled yet"
        row["testable"] = False
    elif connector_id == "object-storage":
        from .connectors_config import storage_status

        status = storage_status()
        row.update(status)
        row["ok"] = bool(status.get("configured"))
    elif connector_id == "mcp":
        from .mcp_client import load_servers

        servers = sorted(load_servers())
        row.update({"configured": bool(servers), "ok": bool(servers), "servers": servers})
    elif connector_id == "mirofish":
        from .mirofish_client import ping

        row["configured"] = _has_any("MIROFISH_URL")
        if probe and row["configured"]:
            row.update(await ping())
        else:
            row["ok"] = row["configured"]
    elif connector_id == "reach":
        from .reach import reach_status

        status = reach_status()
        row.update(status)
        row["configured"] = True
        row["ok"] = bool(status.get("ok", True))
    elif connector_id == "blender":
        from .addons import catalog

        addon: dict[str, Any] = next(
            (item for item in catalog() if item["id"] == "blender-mcp"),
            {},
        )
        row.update({"configured": bool(addon.get("enabled")), "ok": bool(addon.get("enabled"))})
    row["status"] = (
        "ready" if row.get("ok")
        else "error" if row.get("configured")
        else "not_configured"
    )
    return row


async def catalog(*, probe: bool = False) -> list[dict[str, Any]]:
    return [await _status_for(definition, probe=probe) for definition in CONNECTORS]


async def test_connector(connector_id: str, *, confirm: bool = False) -> dict[str, Any]:
    definition = next((item for item in CONNECTORS if item.id == connector_id), None)
    if not definition:
        return {"ok": False, "error": f"unknown connector: {connector_id}"}
    if connector_id == "discord":
        webhook = (os.environ.get("DISCORD_WEBHOOK_URL") or "").strip()
        if not webhook:
            return {"ok": False, "configured": False, "error": "DISCORD_WEBHOOK_URL is missing"}
        if not confirm:
            return {"ok": False, "configured": True, "needs_confirm": True}
        import httpx

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(webhook, json={"content": "Daedalus connector test ✅"})
            return {"ok": response.status_code in (200, 204), "configured": True, "status_code": response.status_code}
        except Exception as exc:
            return {"ok": False, "configured": True, "error": str(exc)[:240]}
    if connector_id == "supabase":
        url = (os.environ.get("SUPABASE_URL") or "").strip().rstrip("/")
        key = (os.environ.get("SUPABASE_ANON_KEY") or "").strip()
        if not url or not key:
            return {"ok": False, "configured": False, "error": "SUPABASE_URL / SUPABASE_ANON_KEY missing"}
        import httpx

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{url}/rest/v1/", headers={"apikey": key, "Authorization": f"Bearer {key}"})
            return {"ok": response.status_code < 500, "configured": True, "status_code": response.status_code}
        except Exception as exc:
            return {"ok": False, "configured": True, "error": str(exc)[:240]}
    return await _status_for(definition, probe=True)
