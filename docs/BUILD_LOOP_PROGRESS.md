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

The remaining gap is "page painted ≠ 3D greybox in view" — that is still model camera/matrix quality, not a skip-pass.

## This MR — empty 3D frames fail; cheap workers; Play Lab

HUD text on a navy clear-color is no longer a visual-proof pass. `frame_shows_scene` looks at the **center** of the PNG. WebGL `INVALID_VALUE` is a console fail. Headless Chromium launches with ANGLE/SwiftShader (`--disable-gpu` was painting a black WebGL canvas).

The native loop now runs `<NEED_WORKER>` and a cheap **fast** reviewer after the first HTML/JS write. Live eval pins worker slots to MiniMax, not Claude Sonnet. Play Lab (`computer.open_project_computer`) is opened after the build — local HTTP on this machine. SmolVM is optional and not installed here.

S4 also rejects U+2212 (unicode minus) in JS — Claude's pass 3 used `−1` in vertex arrays; that is a SyntaxError, so WebGL never ran.

Pytest sets `DAEDALUS_WRITE_REVIEW=0` / `DAEDALUS_WORKERS=0` so unit tests never spend tokens.

## Live eval 2026-09-08 — pass 3 / pass 4 (same day)

| Pass | What happened |
|------|---------------|
| 3 (~$0.91, 12 rounds) | MiniMax **fast** worker probe `PONG`. Kernel write-review ran (MiniMax, ~1.3KB). Play Lab local HTTP booted. Chromium proof still HUD on black — Claude wrote **U+2212** in `mkBox` arrays so the script never executed. `shows_scene=false`. S4 now fails that write. |
| 4 (~$0.51, 11 rounds) | ASCII `-1`. **complete=true**. Proof + Play Lab screenshots: green floor, lime / red / gold cubes, HUD `Keys 1 / 2 / 3`. `frame_shows_scene=true` on all three exported PNGs. Chroma still missing. SmolVM still missing. Cheap worker was called; this pass the review body was empty (kernel now treats that as not-ok). |

Screenshots: `slerp-lookat-pass3-frame_000.png` (black HUD), `slerp-lookat-pass4-lab.png` (painted greybox).

Total spend this branch ~**$2.83** (pass 1+2 $1.41 + pass 3 $0.91 + pass 4 $0.51).

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

1. `DAEDALUS_STANDALONE=1 python3 -m pytest tests/test_write_gate.py tests/test_visual_proof.py tests/test_build_loop.py tests/test_tool_router.py tests/test_or_free_preset.py tests/test_frame_geometry.py -q`
2. Live 3D: `scripts/live_eval_build.py --better --preset slerp --max-rounds 12 --shot-tag pass4`

## Research

Internal study `docs/GODOGEN_STUDY.md`. Steal: pixels of the running game.
Keep: playbooks, scaffolds, build loop. Do not delete the kernel.
