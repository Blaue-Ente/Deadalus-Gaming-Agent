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
        "recommend": "Полезно за log/diff. За PR/MR ползвай native git_forge, не този сървър.",
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
        "recommend": "Не включвай — native web_read (Jina) вече чете страници без ключ.",
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
        "recommend": "Токенът NETLIFY_AUTH_TOKEN в Свързаност стига за publish. MCP е за dashboard.",
        "env_keys": ["NETLIFY_AUTH_TOKEN"],
        "command": "npx",
        "args_template": ["-y", "@netlify/mcp"],
    },
    {
        "id": "blender-mcp",
        "kind": "mcp",
        "label": "Blender jobs (MCP)",
        "blurb": "Емитира bpy скриптове (примитив, PBR, риг, GLB). Не изисква Blender в cloud. stdio: python3 -m daedalus.blender_mcp",
        "offline_ok": True,
        "command": "python3",
        "args_template": ["-m", "daedalus.blender_mcp"],
    },
    {
        "id": "github-mcp",
        "kind": "mcp",
        "label": "GitHub MCP",
        "blurb": "Официален GitHub MCP (issues, PRs, contents). Дедал вече има native git_forge — това е допълнение.",
        "offline_ok": False,
        "recommend": "Включи само ако native PR/issues не стигат. Ползва GITHUB_TOKEN от Свързаност.",
        "env_keys": ["GITHUB_PERSONAL_ACCESS_TOKEN"],
        "env_from": {"GITHUB_PERSONAL_ACCESS_TOKEN": "GITHUB_TOKEN"},
        "docs": "https://github.com/modelcontextprotocol/servers/tree/main/src/github",
        "command": "npx",
        "args_template": ["-y", "@modelcontextprotocol/server-github"],
    },
    {
        "id": "gitlab-mcp",
        "kind": "mcp",
        "label": "GitLab MCP",
        "blurb": "GitLab MCP за issues/MR. Native git_forge вече прави clone, push и Merge Request.",
        "offline_ok": False,
        "recommend": "Включи само за допълнително GitLab API. Ползва GITLAB_TOKEN от Свързаност.",
        "env_keys": ["GITLAB_PERSONAL_ACCESS_TOKEN"],
        "env_from": {"GITLAB_PERSONAL_ACCESS_TOKEN": "GITLAB_TOKEN"},
        "command": "npx",
        "args_template": ["-y", "@modelcontextprotocol/server-gitlab"],
    },
    {
        "id": "godot-headless",
        "kind": "native",
        "label": "Godot headless probe",
        "blurb": "Един мост: файлове + godot --headless. Не пет MCP. Жив Editor MCP — след Gate 1.",
        "offline_ok": True,
        "native": "godot_runtime_probe",
    },
    {
        "id": "smolvm",
        "kind": "native",
        "label": "SmolVM guest computer",
        "blurb": "Optional microVM browser (pip install smolvm). Live viewer for Play Lab. Not vendored. Windows guests off.",
        "offline_ok": True,
        "native": "play_lab_smolvm",
        "docs": "https://github.com/CelestoAI/SmolVM",
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


def enable(addon_id: str, extra_env: dict[str, str] | None = None) -> dict[str, Any]:
    item = next((x for x in CATALOG if x["id"] == addon_id), None)
    if not item:
        return {"ok": False, "error": f"unknown addon {addon_id}"}
    if item.get("kind") != "mcp":
        return {"ok": True, "item": item, "message": "native/sidecar: no mcp.json entry"}
    import os
    path = config_path()
    data: dict[str, Any] = {"servers": {}}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data.get("servers"), dict):
                data["servers"] = {}
        except (OSError, json.JSONDecodeError):
            data = {"servers": {}}
    spec: dict[str, Any] = {
        "command": item["command"],
        "args": _expand(list(item.get("args_template") or [])),
    }
    env: dict[str, str] = {}
    for dest, src in (item.get("env_from") or {}).items():
        val = (os.environ.get(str(src)) or os.environ.get(str(dest)) or "").strip()
        if val:
            env[str(dest)] = val
    if extra_env:
        for k, v in extra_env.items():
            key = str(k).strip()
            val = str(v or "").strip()
            if key and val:
                env[key] = val
    if env:
        spec["env"] = env
    data["servers"][item["id"]] = spec
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
