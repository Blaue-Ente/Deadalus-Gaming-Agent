# Phase progress — games-first harness

Owner-approved plan (2026-09-04). Each phase lands as its own merge request. The next phase starts only after this file records the previous one as **done** and the owner approves.

## Status

| Phase | Title | Status |
|-------|--------|--------|
| F0 | Hard-job slices | **Done** — merged to `main` (`bd7d58c`) |
| F1 | Godot runtime bridge | **This MR** — awaiting owner approval |
| F2 | Gameplay bot + performance gates (web) | Not started |
| F3 | Content systems as code (world_kit v2) | Not started |
| F4 | Asset pipeline v2 (atlas, SFX, 3D jobs) | Not started |
| F5 | Skill memory (progressive disclosure + Approve) | Not started |
| F6 | Production layer vs prototype wall | Not started |
| F7 | Unity: real MCP bridge or honest non-promise | Not started |
| F8 | `game_agent_bench` as a real suite | Not started (partial metrics can start after F2) |

## F0 — Hard-job slices (done)

Persistent job ledger (`original ask`, acceptance, named artifacts), `spawn_subagent` roles `explorer|coder|verify`, Definition of Done that refuses a false "готово". Whisper chat and greenfield M0 slices unchanged.

## F1 — Godot runtime bridge (this MR)

**Intent:** break runtime-blindness for Godot. Files are not Play.

**Shipped**

- Autoload `scripts/autoload/daedalus_bridge.gd` — JSON-lines TCP on `127.0.0.1` (`ping`, `scene_tree`, `inspect_node`, `set_prop`, `call_method`, `send_input`, `send_key`, `wait_frames`, `screenshot`, `errors`, `fps_sample`, `quit`).
- Python `GodotSession` + `engine_session.run_action` + native tool `engine({action, slug})`. Explorer/verify/coder sub-agents may call `engine` (still cannot write files as explorer/verify).
- When `GODOT_BIN` exists, `run_godot` requires a live pass: bridge ping, player node moves on `ui_right`, no SCRIPT ERROR. Static file DoD is not enough.
- `.tscn` `[connection method="…"]` whose method is missing fails **without** a binary.
- `.import` sidecars for PNG/SVG/JPEG/WebP/GLB/audio; `godot --import` before the probe.
- Optional `DAEDALUS_GODOT_MCP` adapter against one server in `mcp.json`; headless is the default. No vendored Godot Editor.
- Godot 4.3 API pin: `daedalus/skills/godot_api.md`.

**Done when (acceptance)**

| Criterion | How we know |
|-----------|-------------|
| Godot 2D/3D M0 still scaffolds a playable loop | Existing scaffold tests + bridge autoload present |
| Broken signal is caught without Godot | `test_broken_tscn_connection_fails_static_dod` |
| Protocol + movement probe work without a binary | Fake TCP server in `tests/test_godot_runtime_bridge.py` |
| Missing `GODOT_BIN` still fails closed | Existing `test_godot_missing_binary_is_not_ok` |
| With a real binary, runtime DoD runs | `run_runtime` uses the bridge; CI image has no Godot so this stays skip-fail, not skip-pass |

**Not in F1** (next phases): Playwright gameplay bots, quest/dialogue/save runtimes, Tripo/Suno clients, Unity MCP as first-class, Unreal.

## How to approve F1 and start F2

1. Review this MR (bridge protocol, DoD, `engine` tool).
2. If you have Godot 4.3 locally: `GODOT_BIN=/path/to/godot pytest tests/test_godot_runtime_bridge.py tests/test_godot_blender_harness.py -q` and play an M0 folder.
3. Reply that F1 is approved. F2 is the web gameplay bot (`playtest/scenario.json`, pixel-diff, FPS/memory gates).
