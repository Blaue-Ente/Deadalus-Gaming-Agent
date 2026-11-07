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
    "exa": { "command": "npx", "args": ["-y", "@modelcontextprotocol/server-everything"] },
    "remote": { "url": "http://127.0.0.1:3100/mcp" }
  }
}
```

**How**: native tool `mcp_call` with `server` + `list_tools: true`, then `tool` + `arguments`.

**Anti-patterns**:
- Do not invent a server that is not in `mcp.json`.
- Fail open: if spawn/HTTP fails, report the error and continue with `web_search`.
