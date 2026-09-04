---
name: unity_runtime
description: Unity is local Hub or DAEDALUS_UNITY_MCP. This cloud has no Editor. Scaffold is not Play. We do not vendor Unity.
---

# SKILL: Unity runtime

**When**: Playground kind is `unity`, the owner mentions Unity Hub / Play mode / MCP, or gate `unity-check`.

**How**:
1. Scaffold C# under `Assets/` + `Packages/manifest.json`. That is files, not Play.
2. `engine(action=status|run, slug)`:
   - `DAEDALUS_UNITY_MCP` names a server in `$DAEDALUS_DATA/mcp.json` (owner-installed Editor plugin). That is the live adapter.
   - Else `UNITY_PATH` batchmode **imports** the project and quits. Import is not Play mode.
   - Else honest refuse: no Editor in this cloud. Do not claim Play.
3. Open the folder in Unity Hub on the owner's machine. Do not download an Editor into Daedalus.

**Anti-patterns**:
- Vendoring a Unity Editor, Hub, or a third-party MCP server into this repo.
- Declaring done because `studio/preview/index.html` loads.
- "Unity Editor in the cloud" / "pressed Play in Unity" without MCP.
- Auto-injecting Unity into the Vite racer M0.
