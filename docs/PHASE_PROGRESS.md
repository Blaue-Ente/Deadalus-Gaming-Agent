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
| F5 | Skill memory (progressive disclosure + Approve) | **This MR** — awaiting owner approval |
| F6 | Production layer vs prototype wall | Not started |
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

## F5 — Skill memory (this MR)

**Intent:** agentskills.io progressive disclosure. Failure/success drafts wait for owner Approve. Pending drafts must not inject into the next prompt. The agent cannot approve itself.

**Shipped**

- Catalog (name + description) in the turn; at most one SKILL.md body (`disclose` / `format_block`).
- Drafts live in `$DAEDALUS_DATA/learned_skills/pending/*.json` until Approve writes a live `*.md`.
- Native tool `skill` — action catalog | load | pending. Read-only. All sub-agent roles. No approve/reject action.
- Studio **Уроци**: Одобри / Откажи. HTTP `POST /api/v1/daedalus/learned-skills/{id}/approve|reject`.
- Evolution radar notes queue a pending draft instead of a live skill.
- Autopilot records `draft_from_success` only when tests passed.

**Done when (acceptance)**

| Criterion | How we know |
|-----------|-------------|
| Failure draft exists but is not in the next prompt | `test_pending_draft_does_not_inject` |
| Approve makes the body loadable | `test_approve_then_disclose_includes_body` |
| Reject never goes live | `test_reject_never_goes_live` |
| Catalog is name+description, pending excluded | `test_catalog_is_name_and_description` |
| Native `skill` cannot approve | `test_native_skill_cannot_approve` |
| Explorer has `skill`, not `write_file` | `test_explorer_has_skill_not_write` |
| Persona stays compact | `test_standalone_persona_is_compact` |
| Guided racing still non-blocking | `test_craft_check_warns_but_does_not_block` |

**Not in F5** (next phases): production layer vs prototype wall, Unity MCP, Unreal, real GameAgentBench suite.

## How to approve F5 and start F6

1. Review this MR (progressive catalog, pending JSON, Studio Уроци, native `skill`).
2. Optional: fail a coding turn, confirm Studio shows a draft, Одобри, then confirm the next catalog can load it.
3. Reply that F5 is approved. F6 is the production layer vs the prototype wall.
