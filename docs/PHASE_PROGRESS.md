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
| F7 | Unity: real MCP bridge or honest non-promise | **Done** — merged to `main` (`0ee2010`) |
| F8 | `game_agent_bench` as a real suite | **This MR** — awaiting owner approval |

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

## F7 — Unity MCP or honest non-promise (done)

Native `engine` on a Unity slug: `DAEDALUS_UNITY_MCP` or `UNITY_PATH` batchmode (import ≠ Play) or honest refuse (`need=unity_local`). Gate `unity-check` is N/A for racing. We do not vendor Unity. Merged as `0ee2010` (`4478a87`).

## F8 — `game_agent_bench` as a real suite (this MR)

**Intent:** The gate list was only flags. F8 runs those honesty checks against this harness. It is still **not** a vendor bake-off — public docs name only Daedalus.

**Shipped**

- `run_suite()` / `evaluate()` — in-process cases (skip ≠ ok, racer ship ≠ store, empty world/systems JSON fail closed, Unity without Editor is honest refuse, explorer cannot write, native tools present, guided racing gates do not block, compact persona, one builder).
- CLI `python -m daedalus.game_agent_bench`. HTTP `GET|POST /api/v1/daedalus/bench`. Optional `$DAEDALUS_DATA/bench/latest.json`.
- Studio **Свят** line `честност`. Game Studio **Честност**. Capability `game-agent-bench`.
- Chromium, Godot, and Unity are not required for the default path.

**Done when (acceptance)**

| Criterion | How we know |
|-----------|-------------|
| Gates still allow measurement | `test_gates_still_allow_measurement` + existing `test_game_agent_bench_gates` |
| Suite runs without editors | `test_run_suite_passes_without_editors` |
| CLI exit 0 | `test_cli_main_zero` |
| HTTP + Studio copy | `test_http_bench_and_studio_copy` |
| Guided racing still not blocked | `test_craft_check_warns_but_does_not_block` |
| Not a vendor bake-off | `not_vendor_bakeoff` case + public docs |
| Persona compact | `test_persona_mentions_honesty_suite` |

**Not in F8** (this is the last planned phase): vendor bake-off, Unreal, extra Godot Editors, injecting atlas/runtime/Unity into the Vite racer M0. There is no F9 unless the owner invents one.

## How to approve F8 (last planned phase)

1. Review this MR (suite cases, CLI, HTTP, Studio честност line). Confirm public docs still refuse a vendor bake-off.
2. Optional: `python -m daedalus.game_agent_bench --no-persist` on your machine — should print `ok` without Unity or Chromium.
3. Reply that F8 is approved. The F0–F8 plan is complete. Do not start a next phase unless you name it.
