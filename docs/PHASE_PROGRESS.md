# Phase progress — games-first harness

Owner-approved plan (2026-09-04). Each phase lands as its own merge request. The next phase starts only after this file records the previous one as **done** and the owner approves.

## Status

| Phase | Title | Status |
|-------|--------|--------|
| F0 | Hard-job slices | **Done** — merged to `main` (`bd7d58c`) |
| F1 | Godot runtime bridge | **Done** — merged to `main` (`b4594e7`) |
| F2 | Gameplay bot + performance gates (web) | **This MR** — awaiting owner approval |
| F3 | Content systems as code (world_kit v2) | Not started |
| F4 | Asset pipeline v2 (atlas, SFX, 3D jobs) | Not started |
| F5 | Skill memory (progressive disclosure + Approve) | Not started |
| F6 | Production layer vs prototype wall | Not started |
| F7 | Unity: real MCP bridge or honest non-promise | Not started |
| F8 | `game_agent_bench` as a real suite | Not started (partial metrics can start after F2) |

## F0 — Hard-job slices (done)

Persistent job ledger (`original ask`, acceptance, named artifacts), `spawn_subagent` roles `explorer|coder|verify`, Definition of Done that refuses a false "готово". Whisper chat and greenfield M0 slices unchanged.

## F1 — Godot runtime bridge (done)

Breaks runtime-blindness for Godot without vendoring an Editor. Autoload `DaedalusBridge` (JSON-lines TCP), native `engine` tool, live DoD (player must move, no SCRIPT ERROR), static signal check, `.import` sidecars. Merged as `b4594e7`.

## F2 — Gameplay bot + performance gates (this MR)

**Intent:** web games are proven by a scenario bot, not by "Start was clicked".

**Shipped**

- `playtest/scenario.json` — ops `wait` / `hold_keys` / `observe` / `click`; expect `canvas_changed` / `win` / `lose`. Racer M0 scaffold writes a default (`require_lose`, no win) plus `window.__DAEDALUS_STATE__`.
- Pixel-diff: 16×16 canvas fingerprint, Playwright screenshot bytes as fallback.
- `requestAnimationFrame` FPS sample; `performance.memory` heap. Thin headless samples warn; a real sample under budget fails.
- `playtest/balance.json` → `balance_sim.py` (1000 rounds). Resource with no sink, never-win, or always-win (when a lose condition exists) **fails**. No file keeps the old keyword heuristic as warn-only. Optional `playtest/balance.mjs` if `node` is on PATH.
- Gate `play-check`: missing scenario on a racer **on disk** fails; pre-build (no files) warns and does not block guided. Live FPS/win/lose apply when verify attached gameplay checks.
- Planner / arcade-craft addendum: keep `playtest/scenario.json`. Skill `gameplay_bot`.

**Done when (acceptance)**

| Criterion | How we know |
|-----------|-------------|
| Racer M0 ships a bot scenario | `write_racer_scaffold` includes `playtest/scenario.json`; `__DAEDALUS_STATE__` in `tick()` |
| `require_lose` without a lose observation fails | `test_require_lose_without_lose_fails_gameplay_passed` |
| Degenerate economy fails; healthy loop passes | `tests/test_gameplay_bot.py` balance cases |
| Guided racing craft-check still does not block without files | `test_craft_check_warns_but_does_not_block` + play-check warn |
| Core tests do not need Chromium | scenario / fingerprint / sim are pure Python |

**Not in F2** (next phases): world_kit quest/dialogue/save runtimes, atlas/Tripo jobs, skill memory Approve, Unity MCP, Unreal.

## How to approve F2 and start F3

1. Review this MR (scenario schema, bot runner, balance sim, play-check).
2. Optional: with Chromium, `pytest tests/test_gameplay_bot.py -q` (the live HTML fixture skips if no binary).
3. Reply that F2 is approved. F3 is world_kit as code (save schema, quest engine, dialogue runner, level graph) — empty JSON/markdown is not a runtime.
