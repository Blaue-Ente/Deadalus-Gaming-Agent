# Phase progress — games-first harness

Owner-approved plan (2026-09-04). Each phase lands as its own merge request. The next phase starts only after this file records the previous one as **done** and the owner approves.

## Status

| Phase | Title | Status |
|-------|--------|--------|
| F0 | Hard-job slices | **Done** — merged to `main` (`bd7d58c`) |
| F1 | Godot runtime bridge | **Done** — merged to `main` (`b4594e7`) |
| F2 | Gameplay bot + performance gates (web) | **Done** — merged to `main` (`d16cb9f`) |
| F3 | Content systems as code (world_kit v2) | **This MR** — awaiting owner approval |
| F4 | Asset pipeline v2 (atlas, SFX, 3D jobs) | Not started |
| F5 | Skill memory (progressive disclosure + Approve) | Not started |
| F6 | Production layer vs prototype wall | Not started |
| F7 | Unity: real MCP bridge or honest non-promise | Not started |
| F8 | `game_agent_bench` as a real suite | Not started (partial metrics can start after F2) |

## F0 — Hard-job slices (done)

Persistent job ledger (`original ask`, acceptance, named artifacts), `spawn_subagent` roles `explorer|coder|verify`, Definition of Done that refuses a false "готово". Whisper chat and greenfield M0 slices unchanged.

## F1 — Godot runtime bridge (done)

Breaks runtime-blindness for Godot without vendoring an Editor. Autoload `DaedalusBridge` (JSON-lines TCP), native `engine` tool, live DoD (player must move, no SCRIPT ERROR), static signal check, `.import` sidecars. Merged as `b4594e7`.

## F2 — Gameplay bot + performance gates (done)

Web games are proven by `playtest/scenario.json` (pixel-diff, FPS/memory, required win/lose), not Click-Start. Balance sim fails degenerate economies. Merged as `d16cb9f`.

## F3 — Content systems as code (this MR)

**Intent:** a campaign is a runtime (save, quests, dialogue, gated graph). Empty JSON/markdown is not Play.

**Shipped**

- Save document `{version, region, act, flags, inventory, quests}` with v0 migrate. Schema at `world/save.schema.json`.
- Quest engine: `complete_when` flags → `reward_flags`. Dialogue runner: `characters/*.dialogue.json` choices set flags.
- Level graph: `require_flags` on map regions and `levels/graph.json` edges. Travel fails closed without the flag.
- Epic scaffold is a slice: hearth → keeper → `gate_open` → wilds. `src/world/runtime.js` is the game-loop copy (Python is source of truth).
- Native tool `world({action: status|prove|talk|travel|script, slug})`. Explorer/verify may call it.
- Gate `world-check`: hollow kit on disk fails; pre-build / non-campaign N/A or warn, does not block guided racing.

**Done when (acceptance)**

| Criterion | How we know |
|-----------|-------------|
| New epic kit proves talk → travel | `test_default_kit_proves_hearth_to_wilds` |
| Hollow map/graph fails | `test_hollow_map_is_not_a_runtime` |
| v0 save migrates | `test_migrate_save_v0` |
| Guided racing still non-blocking | `test_craft_check_warns_but_does_not_block` + world-check N/A |
| No Godot/Chromium required | pure Python |

**Not in F3** (next phases): atlas/Tripo/Suno jobs, skill memory Approve, Unity MCP, Unreal.

## How to approve F3 and start F4

1. Review this MR (runtime, kit templates, `world` tool, world-check).
2. Optional: create an epic playground project and call `world({action: prove})`.
3. Reply that F3 is approved. F4 is the asset pipeline (atlas, tileset, SFX, font; Tripo/Meshy/Suno only if keys exist).
