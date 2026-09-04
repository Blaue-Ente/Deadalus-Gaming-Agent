# Phase progress — games-first harness

Owner-approved plan (2026-09-04). Each phase lands as its own merge request. The next phase starts only after this file records the previous one as **done** and the owner approves.

## Status

| Phase | Title | Status |
|-------|--------|--------|
| F0 | Hard-job slices | **Done** — merged to `main` (`bd7d58c`) |
| F1 | Godot runtime bridge | **Done** — merged to `main` (`b4594e7`) |
| F2 | Gameplay bot + performance gates (web) | **Done** — merged to `main` (`d16cb9f`) |
| F3 | Content systems as code (world_kit v2) | **Done** — merged to `main` (`bcff039`) |
| F4 | Asset pipeline v2 (atlas, SFX, 3D jobs) | **This MR** — awaiting owner approval |
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

## F3 — Content systems as code (done)

Campaign runtime: save `{version, region, act, flags, inventory, quests}`, quests, dialogue, gated graph. Native `world` tool. Gate `world-check`. Merged as `bcff039`.

## F4 — Asset pipeline v2 (this MR)

**Intent:** a 2D game can pack an atlas and play kit SFX/font without paid APIs. A 3D hero mesh job fails closed without keys (no fake GLB). With a key, submit/poll/write GLB and still run `asset_contract`.

**Shipped**

- Stdlib PNG codec (RGBA8, filters 0–4). No Pillow required.
- `assets({action: atlas|tileset})` — shelf-pack / grid. Default 16×16 kit when no sources.
- `assets({action: sfx|font})` — procedural WAV (22050 Hz mono) + 5×7 ASCII bitmap font.
- `assets({action: mesh})` — fal / Tripo / Meshy only if `FAL_KEY`/`FAL_API_KEY`, `TRIPO_API_KEY`, or `MESHY_API_KEY` is set. Jobs live under `$DAEDALUS_DATA/asset_jobs/`. Offline and missing keys return `{ok:false, need_key:true}`.
- `assets({action: greybox})` — labelled unit cube. Not a hero mesh. Contract check still runs.
- Suno: no submit. Drop `assets/music.mp3`.
- Native tool is **coder-only**. Explorer/verify stay read-only.
- Gate `asset-check`: pre-build / racing-without-GLB warns, does not block. Claimed empty GLB fails.
- HTTP `POST /api/v1/playground/projects/{slug}/assets/pipeline`.

**Done when (acceptance)**

| Criterion | How we know |
|-----------|-------------|
| PNG roundtrip + non-overlapping atlas | `test_png_roundtrip_and_atlas_frames` |
| SFX is a real WAV; font has glyph A | `test_sfx_wav_and_font_glyph` |
| Mesh without keys is need_key, no GLB | `test_mesh_submit_fails_closed_without_keys` |
| Fake key + mock HTTP writes GLB | `test_mesh_poll_with_mock_transport` |
| Greybox passes asset_contract | `test_greybox_is_not_a_hero` |
| Guided racing still non-blocking | `test_craft_check_warns_but_does_not_block` + asset-check |
| No Pillow / no network in unit tests | pure Python + transport injection |

**Not in F4** (next phases): skill memory Approve, GDD systems map, Unity MCP, Unreal, real GameAgentBench suite. Do not auto-inject atlas into the Vite racer M0.

## How to approve F4 and start F5

1. Review this MR (atlas/SFX/font, fail-closed mesh jobs, `assets` tool, asset-check).
2. Optional: `assets({action: atlas, slug})` on a 2D playground project; confirm no GLB appears from `action: mesh` without keys.
3. Reply that F4 is approved. F5 is skill memory (progressive disclosure + Approve).
