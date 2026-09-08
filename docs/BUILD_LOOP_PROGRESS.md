# Build loop progress — finished products, not tool lists

Owner-approved plan (2026-09-05). Root cause from the live eval: Daedalus had the
tools and the plan, but nobody held the wheel. The Definition of Done ran once,
after the reply was final, and its verdict was appended as text nobody could act
on. There was no closed *write → verify → fix* loop.

Order of work was **S1 → S3 → S4 → S2 → S6 → S5**. S1 and S2 are on `main`
(MR !57 squashed S2). S3 and S4 are this MR. S5–S6 stay pending.

## Status

| Stage | Title | Status |
|-------|-------|--------|
| S1 | Closed build loop instead of post-mortem DoD | **Done** — on `main` |
| S2 | Kernel-run visual proof (browser / Godot) | **Done** — on `main` (MR !57) |
| S3 | Tool results as navigation (paths, slug, NEXT) | **This MR** |
| S4 | Write-time quality gate (syntax) + arcade lint | **This MR** |
| S6 | Router: negations, explicit merge only | Partial — do not merge `harness.fix` into a Playground game; strip «не чети / don't read» windows. math3d+crowd+arcade on leftover keywords still pending |
| S5 | One build layer, model roles, wrap-up sanitizing | Pending |

## Evidence the plan is built on (live eval, 2026-09-05, Nemotron 3.5 Lightning)

| Case | What happened |
|------|---------------|
| Snake | Files written in 3 rounds, then 8 rounds of `run_command`/`list_dir`/`find /workspace` hunting for them (jail BLOCKED). `game.js` had a duplicate `let gameLoopId`. Never opened in a browser. Reply ended with a DoD sticker the model never saw. |
| SLERP (mixed prompt) | Router merged `math3d+crowd+arcade` on negated keywords. 5× `list_dir`, zero `write_file` with content, zero `math3d`. |
| SLERP (clean prompt) | Router chose `playground.math3d` correctly. Model emitted `<need_list_dir>` XML, one `list_dir`, then quit. No files. |

## Live eval 2026-09-08 — MiniMax M2.7 dungeon (not Snake)

OpenRouter `minimax/minimax-m2.7` (~$0.24/$0.96 per 1M). DeepSeek V3.2 was the cheaper fallback; MiniMax returned a real `playground_new` tool call in ~3s so we kept it. Runner: `scripts/live_eval_build.py`.

| Pass | What happened |
|------|---------------|
| 1 | MiniMax wrote `dungeon-keys/index.html` (~23KB). Prompt said «Не чети daedalus/agent.py» → router merged `playground.arcade+harness.fix`. NEXT became `read_file`. Native `browser` guessed `file://` / localhost. Kernel proof never ran. Budget exhausted. |
| 2 (after router + browser→visual_proof) | Playbook `playground.arcade` only. `playground_new` → `write_file` → `browser` mapped to kernel `visual_proof`. **complete=true** in ~126s. Frame `studio/proof/frame_000.png`: painted room, HP/KEY/ROOM HUD, player, slime, gold key. Not a blank screen. |

S2's proof is "the page painted", not a playtest bot. MiniMax put the slime in room 1 with the key (the ask wanted three rooms). That is model layout, not a skip-pass.

## Live eval 2026-09-08 — Claude Sonnet 4.6 3D SLERP (not Snake)

OpenRouter `anthropic/claude-sonnet-4.6` (~$3/$15 per 1M). Budget cap $2–3. Runner: `scripts/live_eval_build.py --better --preset slerp`. Playbook `playground.math3d`. Isolated `$DAEDALUS_DATA` under `/tmp`.

| Pass | What happened |
|------|---------------|
| 1 (~$1.02, 14 rounds, ~296s) | `math3d` quat_slerp + look_at (first claim mismatched rounding, then pass). Wrote `slerp-lookat/index.html` (~18KB, raw WebGL). Chromium proof painted the HUD (Bulgarian targets, verified quat `[0, 0.38268, 0, 0.92388]`). **WebGL 1 `uniformMatrix4fv(..., true)` is INVALID_VALUE** — canvas cleared, no geometry. `complete=false` because leftover `run_command` failures. `kernel_proof_ran=false` because the model called `browser` itself (mapped to `visual_proof`). |
| 2 (~$0.39, 7 rounds, ~177s, after S4 `webgl-transpose`) | Claude wrote column-major mats with `transpose=false`. **complete=true**. HUD painted again (keys 1/2/3, SLERP LOOK-AT). Headless Chromium still shows a dark viewport (no greybox in the frame) — kernel proof is HUD text, not a geometry bot. No `run_command` this time. |

Total model spend ~**$1.41**. Screenshots: `slerp-lookat-pass1-hud.png`, `slerp-lookat-pass2.png`.

S4 now fails `uniformMatrix*fv(..., true)` so the next write can ship WebGL 1-legal matrices. The remaining gap is "page painted ≠ 3D greybox in view" — that is still model camera/matrix quality, not a skip-pass.

## Live eval 2026-09-08 — Claude Sonnet 4.6 Vite/Phaser (Lumen Bloom)

Full **`Daedalus.chat`** path (`scripts/live_eval_agent_chat.py --better --preset lumen`), not kernel-only. Isolated `$DAEDALUS_DATA` under `/tmp/daedalus-live-eval-chat`. Effort **craft**. ~464s wall.

Prompt named `kind=vite` + Phaser 3 and avoided arcade (`2d`/`canvas`/`snake`) and mobile (`app store`) traps.

| Probe | Result |
|-------|--------|
| Playbook | `playground.generic` (default kind **static**; model still passed `kind=vite` on `playground_new`) |
| RAG | Chroma indexed 49 files → 63 chunks. Turn used **4** knowledge hits. `rank_bm25` missing → dense-only. |
| Navigator | Fast-path `code_change` with `NEED_PROPOSE` / `NEED_READ_FILE` — ADN leftover, not Playground tools |
| Skills disclosed | `arcade_craft`, `playtest`, `godot`, `senior_tools`, `spatial_simd`, `platform_acceptance_qa`, `game_canon` |
| Native cap | 10 LLM rounds. Stop: **round budget exhausted**. Kernel visual proof **did not run**. |
| `generate_image` | **8/8 FAILED**. Novita 403; OpenRouter returned `data:` URLs; `httpx.get` → `URL too long`. No PNG sprites. |
| Workers / subagents | **None**. After writes the persona is told not to emit `NEED_*`; it started a **bundle** and dumped leftover JS into the chat reply. |
| playtest / production / browser | **Not called**. `production` is not on the generic playbook subset. |
| Review-gate (Haiku) | Ran. Flagged incomplete `main.js`, missing assets. Closest thing to a "Mentor" on the chat path. Visual critic (`playground_quality`) lives on **autopilot only**. |
| DoD | `complete=false`. `browser` check stayed true because chat `evaluate()` does not pass `browser_ok`. |

**As shipped:** `index.html` has `<canvas id="lumen-canvas">`; `src/main.js` is still the Vite scaffold (`#app` innerHTML). Chromium: **blank near-black page**. Scenes `Boot.js` / `Menu.js` / `Play.js` exist; `Play.js` write was FAILED by `js-duplicate-binding` on two legal `for (let i` loops. `UniformGrid.js` never written. `npm install` never ran.

**Evaluator wiring (not Daedalus):** boot `main.js` + stub grid + `vite build` → menu and play **paint** with procedural canvas textures (Boot.js). That is a polished prototype, not App Store PNG art, and it is **not** what the agent left on disk.

Fixes from this eval: decode `data:` image URLs; sibling `for (let i` is not a SyntaxError.

## Mentor helm (2026-09-08)

Toggle: Studio **Mentor ON|OFF** (default on). `DAEDALUS_MENTOR=0` forces off. KV `adn:daedalus_mentor`.

Director: Studio model picker (Claude Fable 5.1, GPT-6 Astra, GPT-5.5 / 5.6, Opus 4.8, Kimi K3, GLM 5.3, Grok 4.6; Auto prefers Grok 4.6 on OpenRouter). Custom ⋯ for any provider+model. KV `adn:daedalus_mentor_choice`. Token ledger `adn:daedalus_mentor_spend` (default 40k/day). `DAEDALUS_MENTOR_LLM=0` is rules-only. The director does **not** ingest harness RAG — a compact inspect digest plus the model's own knowledge. Unambiguous Vite `#app` scaffold stays on the cheap `steer()` path.

Always on even when Mentor is off: Phaser.Game counts as a loop; `playground.vite` playbook; kind sync; proof on exhaust; retry cap; `browser_ok` on chat DoD; no `NEED_PROPOSE` on game fast-path.

When Mentor is on: PLAN PROGRESS NEXT comes from `daedalus/mentor.py` (inspect slug, RAG, forbid `generate_image` after two fails), overlayed by the director cache after quiet/exhaust. Extra recover rounds after native budget exhaust (once, +2). Persona wrap-up tells the model not to dump leftover JS.

Mentor does **not** write `index.html` / `src/main.js`. Two writers stay forbidden.

## S1 — Closed build loop (done)

The plan drives the model. Quiet + incomplete → PLAN PROGRESS inside the native
round loop. See `daedalus/build_loop.py`. `DAEDALUS_BUILD_FIX_ROUNDS` (default 3).

## S2 — Kernel-run visual proof (done on main)

**Intent:** after the last write on a visual kind, the kernel opens the running
game. The model does not get to skip `browser` / `engine`. Skip is not success.
Frames land in `studio/proof/` and are injected as vision on the persona turn.

Shipped in `daedalus/visual_proof.py` + `BuildLoop.wants_kernel_proof()`.
`DAEDALUS_VISUAL_PROOF=0` restores the pre-S2 stop.

## S3 — Tool results as navigation (this MR)

**Intent:** after a write, the model hunted with `list_dir` / `find /workspace`.
S1 already injects `NEXT: call write_file with slug=…` on PLAN PROGRESS. S3 puts
the same breadcrumb on **every** Playground tool result, not only write/new/failed
shell.

**Shipped**

- `_nav_hint` in `native_tools.py` — `playground/projects/{slug}/`, do not find
  `/workspace`, NEXT tool with slug.
- `read_file` (ok and FAILED), `list_dir` (including harness-refused listings),
  `math3d`, `spatial`, `game_lint`, `engine`, `visual_proof`.
- `math3d` mismatch is **FAILED** (not a named MISMATCH that the loop treated as ok).
  NEXT is another `math3d` call, then `write_file`.
- Successful `math3d` says NEXT `write_file` with the verified numbers.

**Done when (acceptance)**

| Criterion | How we know |
|-----------|-------------|
| Failed `read_file` names the slug path | `test_read_file_failed_names_playground` |
| Successful `read_file` still names NEXT write_file | `test_read_file_ok_names_playground` |
| `list_dir /workspace` in a Playground session names projects | `test_list_dir_harness_refused_names_playground` |
| `math3d` ok → NEXT write_file | `test_math3d_ok_names_next_write` |
| `math3d` mismatch is FAILED | `test_math3d_mismatch_is_failed` |
| `spatial` / `game_lint` name slug | `test_spatial_and_game_lint_name_slug` |

## S4 — Write-time quality gate (this MR)

**Intent:** Snake's `game.js` had `duplicate let gameLoopId`. `game_lint` had no
JS/HTML parser. The gate runs inside `write_file` after the bytes hit disk:
FAILED so the loop's NEXT is a corrected `write_file`, not eight hunting rounds.

**Shipped**

- `daedalus/write_gate.py` — `DAEDALUS_WRITE_GATE=0` restores the old write.
- `game_lint` JS/HTML: duplicate `let`/`const` in the same brace scope, unbalanced
  delimiters, inline `<script>` in HTML. Python `ast.parse` is a blocker even
  without ruff.
- Arcade lint on main web files (`index.html`, `game.js`, …): missing `<canvas>`
  / `requestAnimationFrame`.
- File stays on disk so `read_file` can see the defect.

**Done when (acceptance)**

| Criterion | How we know |
|-----------|-------------|
| Duplicate `let gameLoopId` → write FAILED | `test_write_file_duplicate_let_returns_failed` |
| Clean canvas + rAF → write OK | `test_write_file_clean_canvas_ok` |
| `DAEDALUS_WRITE_GATE=0` skips | `test_write_gate_off_skips` |
| JS duplicate is a `game_lint` blocker | `test_game_lint_html_duplicate_let` |

## How to review S3+S4

1. `DAEDALUS_STANDALONE=1 python3 -m pytest tests/test_write_gate.py tests/test_visual_proof.py tests/test_build_loop.py tests/test_tool_router.py tests/test_or_free_preset.py -q`
2. Live 3D: `scripts/live_eval_build.py --better --preset slerp --max-rounds 12`

## Research

Internal study `docs/GODOGEN_STUDY.md`. Steal: pixels of the running game.
Keep: playbooks, scaffolds, build loop. Do not delete the kernel.
