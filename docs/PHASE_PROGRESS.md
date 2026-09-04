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
| F6 | Production layer vs prototype wall | **This MR** — awaiting owner approval |
| F7 | Unity: real MCP bridge or honest non-promise | Not started |
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

## F6 — Production layer vs prototype wall (this MR)

**Intent:** a playable M0–M3 slice is not a shipped product. Hermes/Claude Code games die at that wall. F6 names the wall and adds a real production layer.

**Shipped**

- `design/systems.json` — `{version, layer: prototype|production, systems: [{id, purpose, mvp, files[], status}]}`. Empty JSON is not a production layer.
- Optional `src/layer/runtime.js` — persist / restore / pause / resume / settings. Game Studio start seeds the map only; it does not inject the runtime into the Vite racer M0.
- Native `production` — action status | prove | seed | runtime | map | promote. Explorer/verify/coder all have it (explorer still cannot `write_file`).
- Gate `production-check` — pre-build / guided racing with no files does not block. Store-ready / готово за стора on a prototype fails. A racer “ship” mesh is not a store claim.
- Studio World kit line `слой · prototype|production`. Game Studio **Слой**. HTTP `GET|POST /api/v1/playground/projects/{slug}/production`.

**Done when (acceptance)**

| Criterion | How we know |
|-----------|-------------|
| Racer “ship” is not a store claim | `test_racer_ship_is_not_a_store_claim` |
| Empty systems map is not production | `test_empty_map_is_not_production` |
| Pre-build / guided racing not blocked | `test_pre_build_does_not_block` + `test_craft_check_warns_but_does_not_block` |
| Honest prototype with planned systems | `test_honest_prototype_with_planned_systems` |
| Missing claimed files fail | `test_claimed_missing_files_fail` |
| Promote needs persist+pause+files | `test_promote_requires_evidence` |
| Promote blocked without evidence | `test_promote_blocked_without_evidence` |
| Persona compact | `test_standalone_persona_is_compact` |

**Not in F6** (next phases): Unity MCP or honest non-promise, Unreal, real GameAgentBench suite. Do not vendor a Unity Editor. Do not inject layer runtime into the Vite racer M0.

## How to approve F6 and start F7

1. Review this MR (systems map, prototype wall, native `production`, gate `production-check`, Studio **Слой**).
2. Optional: start a Game Studio racing project, confirm `design/systems.json` exists and `src/layer/runtime.js` does not, then prove a store-ready claim fails until persist/pause exist.
3. Reply that F6 is approved. F7 is Unity: a real MCP bridge **or** an honest non-promise — not a fake Editor in the cloud.
