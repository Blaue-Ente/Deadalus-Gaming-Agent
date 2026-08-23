"""Addon shelf — curated MCP + native extras, not an app store.

Owner enables a row → we write ``$DAEDALUS_DATA/mcp.json`` (or mark native).
No surprise ``npx`` on boot. Offline mode skips addons that need the net.
"""
from __future__ import annotations

import json
from typing import Any

from .mcp_client import config_path, load_servers
from .paths import playground_projects_dir

CATALOG: list[dict[str, Any]] = [
    {
        "id": "filesystem",
        "kind": "mcp",
        "label": "Файлове (MCP filesystem)",
        "blurb": "Чете playground дървото през MCP. Дублира native read_file — полезно за външни агенти.",
        "offline_ok": True,
        "command": "npx",
        "args_template": ["-y", "@modelcontextprotocol/server-filesystem", "{playground}"],
    },
    {
        "id": "git",
        "kind": "mcp",
        "label": "Git (MCP)",
        "blurb": "Локален git status/log/diff за проекта. Мрежа само ако remote-ът е онлайн.",
        "offline_ok": True,
        "command": "npx",
        "args_template": ["-y", "@modelcontextprotocol/server-git", "--repository", "{playground}"],
    },
    {
        "id": "memory",
        "kind": "mcp",
        "label": "MCP memory",
        "blurb": "Ключ-стойност памет на MCP сървъра. Дедал вече има Chroma + preferences.",
        "offline_ok": True,
        "command": "npx",
        "args_template": ["-y", "@modelcontextprotocol/server-memory"],
    },
    {
        "id": "fetch",
        "kind": "mcp",
        "label": "Fetch (MCP)",
        "blurb": "HTTP fetch. В офлайн кит се отказва. Предпочитай native web_read.",
        "offline_ok": False,
        "command": "npx",
        "args_template": ["-y", "@modelcontextprotocol/server-fetch"],
    },
    {
        "id": "reach",
        "kind": "native",
        "label": "Agent Reach (Jina)",
        "blurb": "Вече вградено: native web_read. Няма какво да инсталираш.",
        "offline_ok": False,
        "native": "web_read",
    },
    {
        "id": "playtest",
        "kind": "native",
        "label": "Playtest personas",
        "blurb": "Вградено. Работи офлайн като heuristic; с локален LLM — пълни съдии.",
        "offline_ok": True,
        "native": "playtest",
    },
    {
        "id": "mirofish",
        "kind": "sidecar",
        "label": "MiroFish swarm",
        "blurb": "AGPL sidecar. Не се вендорира. docker compose в nikmcfly/MiroFish-Offline.",
        "offline_ok": True,
        "docs": "https://github.com/nikmcfly/MiroFish-Offline",
    },
    {
        "id": "netlify",
        "kind": "mcp",
        "label": "Netlify MCP",
        "blurb": "Официален MCP за dashboard. Studio → Наживо вече качва native (токен или anonymous).",
        "offline_ok": False,
        "command": "npx",
        "args_template": ["-y", "@netlify/mcp"],
    },
]


def _expand(args: list[str]) -> list[str]:
    playground = str(playground_projects_dir())
    return [a.replace("{playground}", playground) for a in args]


def catalog(*, offline: bool | None = None) -> list[dict[str, Any]]:
    from .offline import enabled as offline_on
    off = offline_on() if offline is None else offline
    enabled_mcp = set(load_servers())
    rows = []
    for item in CATALOG:
        row = dict(item)
        row["enabled"] = item["id"] in enabled_mcp if item.get("kind") == "mcp" else True
        row["blocked_offline"] = bool(off and not item.get("offline_ok"))
        rows.append(row)
    return rows


def enable(addon_id: str) -> dict[str, Any]:
    item = next((x for x in CATALOG if x["id"] == addon_id), None)
    if not item:
        return {"ok": False, "error": f"unknown addon {addon_id}"}
    if item.get("kind") != "mcp":
        return {"ok": True, "item": item, "message": "native/sidecar — няма mcp.json ред"}
    path = config_path()
    data: dict[str, Any] = {"servers": {}}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data.get("servers"), dict):
                data["servers"] = {}
        except (OSError, json.JSONDecodeError):
            data = {"servers": {}}
    data["servers"][item["id"]] = {
        "command": item["command"],
        "args": _expand(list(item.get("args_template") or [])),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "item": item, "config": str(path)}


def disable(addon_id: str) -> dict[str, Any]:
    path = config_path()
    if not path.is_file():
        return {"ok": True, "removed": False}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"ok": False, "error": "mcp.json unreadable"}
    servers = data.get("servers") if isinstance(data, dict) else None
    if not isinstance(servers, dict) or addon_id not in servers:
        return {"ok": True, "removed": False}
    servers.pop(addon_id)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "removed": True}
