# Phase progress — games-first harness

Owner-approved plan (2026-09-04). Each phase lands as its own merge request. The next phase starts only after this file records the previous one as **done** and the owner approves.

## Status

| Phase | Title | Status |
|-------|--------|--------|
| F0 | Hard-job slices | **Done** — merged to `main` (`bd7d58c`) |
| F1 | Godot runtime bridge | **Done** — merged to `main` (`b4594e7`) |
| F2 | Gameplay bot + performance gates (web) | **Done** — merged to `main` (`d16cb9f`) |
| F3 | Content systems as code (world_kit v2) | **Done** — merged to `main` (`bcff039`) |
| F4 | Asset pipeline v2 (atlas, SFX, 3D jobs) | **Done** — merged to `main` (`e77d0d9`) |
| F5 | Skill memory (progressive disclosure + Approve) | **Done** — merged to `main` (`a6ed4ed`) |
| F6 | Production layer vs prototype wall | **Done** — merged to `main` (`53512f3`) |
| F7 | Unity: real MCP bridge or honest non-promise | **This MR** — awaiting owner approval |
| F8 | `game_agent_bench` as a real suite | Not started (partial metrics can start after F2) |

## F0 — Hard-job slices (done)

Persistent job ledger (`original ask`, acceptance, named artifacts), `spawn_subagent` roles `explorer|coder|verify`, Definition of Done that refuses a false "готово". Whisper chat and greenfield M0 slices unchanged.

## F1 — Godot runtime bridge (done)

Breaks runtime-blindness for Godot without vendoring an Editor. Autoload `DaedalusBridge` (JSON-lines TCP), native `engine` tool, live DoD (player must move, no SCRIPT ERROR), static signal check, `.import` sidecars. Merged as `b4594e7`.

## F2 — Gameplay bot + performance gates (done)

Web games are proven by `playtest/scenario.json` (pixel-diff, FPS/memory, required win/lose), not Click-Start. Balance sim fails degenerate economies. Merged as `d16cb9f`.

## F3 — Content systems as code (done)

Campaign runtime: save `{version, region, act, flags, inventory, quests}`, quests, dialogue, gated graph. Native `world` tool. Gate `world-check`. Merged as `bcff039`.

## F4 — Asset pipeline v2 (done)

2D atlas/tileset/SFX/font without paid APIs. 3D mesh jobs fail closed without keys. Native `assets` is coder-only. Gate `asset-check` does not block guided racing before files exist. Merged as `e77d0d9`.

## F5 — Skill memory (done)

Progressive catalog (name + description) plus at most one SKILL.md body. Failure/success drafts wait for owner Approve in Studio **Уроци**. Native `skill` cannot approve itself. Merged as `a6ed4ed`.

## F6 — Production layer vs prototype wall (done)

M0–M3 stays a labelled prototype. `design/systems.json` plus persist/pause is the production layer. Empty JSON is not a production layer. Native `production`. Gate `production-check`. Merged as `53512f3`.

## F7 — Unity MCP or honest non-promise (this MR)

**Intent:** Daedalus must not pretend there is a Unity Editor in the cloud. Either talk to a real owner-installed MCP plugin, or refuse honestly. Do not vendor Unity.

**Shipped**

- Native `engine` on a Unity slug: `DAEDALUS_UNITY_MCP` adapter (local Editor), else `UNITY_PATH` batchmode (import ≠ Play), else honest refuse (`need=unity_local`).
- Gate `unity-check` — N/A for racing/web; in-cloud Editor claims fail; pre-build does not block guided racing.
- Studio **Свят** line `unity · Hub/MCP/batchmode`. Game Studio **Unity**. HTTP `GET|POST /api/v1/playground/projects/{slug}/unity`.
- We do not vendor a Unity Editor, Hub, or a third-party MCP server.

**Done when (acceptance)**

| Criterion | How we know |
|-----------|-------------|
| No Editor → honest refuse, not a skip-pass | `test_unity_no_editor_is_honest_non_promise` |
| Bare “unity” in a GDD is not a cloud claim | `test_bare_unity_is_not_a_cloud_claim` |
| In-cloud Editor claim without MCP fails | `test_cloud_editor_claim_fails_prove` |
| Guided racing not blocked | `test_unity_check_does_not_block_racing` + `test_craft_check_warns_but_does_not_block` |
| MCP tool mapping | `test_unity_mcp_tool_mapping` |
| Batchmode is not Play | `test_batchmode_is_not_play_mode` |
| Persona compact | `test_standalone_persona_is_compact` |

**Not in F7** (next phase): real GameAgentBench suite. Do not vendor Unreal. Do not inject Unity into the Vite racer M0.

## How to approve F7 and start F8

1. Review this MR (honest refuse, MCP adapter, `unity-check`, Studio Unity line).
2. Optional: create a Unity playground project, call `engine(action=run)` without `UNITY_PATH`, confirm the reply says we do not vendor Unity.
3. Reply that F7 is approved. F8 is `game_agent_bench` as a real suite — still not a vendor bake-off in this repo.
