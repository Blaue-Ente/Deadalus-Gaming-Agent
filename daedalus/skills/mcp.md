---
name: mcp
description: Call optional MCP servers declared in data/daedalus/mcp.json.
---

# SKILL: MCP

**When**: the owner configured an MCP server (Exa, GitHub, browser, …) and the task needs that tool.

**Config** (`$DAEDALUS_DATA/mcp.json`):

```json
{
  "servers": {
    "blender-mcp": { "command": "python3", "args": ["-m", "daedalus.blender_mcp"] },
    "remote": { "url": "http://127.0.0.1:3100/mcp" }
  }
}
```

Unverified `npx -y` and non-loopback HTTP do not connect until the owner pins the digest. Write-class tools need the `mcp_write` permission.

**How**: native tool `mcp_call` with `server` + `list_tools: true`, then `tool` + `arguments`.

**Anti-patterns**:
- Do not invent a server that is not in `mcp.json`.
- Fail closed: unverified install/connect is blocked. Use native `web_search` / `web_read` instead of spawning an unsigned MCP server.
