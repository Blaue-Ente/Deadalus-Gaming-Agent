# Build loop progress — finished products, not tool lists

Owner-approved plan (2026-09-05). Root cause from the live eval: Daedalus had the
tools and the plan, but nobody held the wheel. The Definition of Done ran once,
after the reply was final, and its verdict was appended as text nobody could act
on. There was no closed *write → verify → fix* loop.

Order of work was **S1 → S3 → S4 → S2 → S6 → S5**. S1 landed on `main`. The
owner then asked to take the useful steal from the Godogen study: kernel-run
visual proof (S2) plus path hints in tool results (a thin slice of S3). S4–S6
stay pending.

## Status

| Stage | Title | Status |
|-------|-------|--------|
| S1 | Closed build loop instead of post-mortem DoD | **Done** — on `main` |
| S2 | Kernel-run visual proof (browser / Godot) | **This MR** |
| S3 | Tool results as navigation (paths, slug, NEXT) | Partial — write_file / run_command / playground_new now name the slug path |
| S4 | Write-time quality gate (syntax) + arcade lint | Pending |
| S6 | Router: negations, explicit merge only | Pending |
| S5 | One build layer, model roles, wrap-up sanitizing | Pending |

## Evidence the plan is built on (live eval, 2026-09-05, Nemotron 3.5 Lightning)

| Case | What happened |
|------|---------------|
| Snake | Files written in 3 rounds, then 8 rounds of `run_command`/`list_dir`/`find /workspace` hunting for them (jail BLOCKED). `game.js` had a duplicate `let gameLoopId`. Never opened in a browser. Reply ended with a DoD sticker the model never saw. |
| SLERP (mixed prompt) | Router merged `math3d+crowd+arcade` on negated keywords. 5× `list_dir`, zero `write_file` with content, zero `math3d`. |
| SLERP (clean prompt) | Router chose `playground.math3d` correctly. Model emitted `<need_list_dir>` XML, one `list_dir`, then quit. No files. |

## S1 — Closed build loop (done)

The plan drives the model. Quiet + incomplete → PLAN PROGRESS inside the native
round loop. See `daedalus/build_loop.py`. `DAEDALUS_BUILD_FIX_ROUNDS` (default 3).

## S2 — Kernel-run visual proof (this MR)

**Intent:** after the last write on a visual kind, the kernel opens the running
game. The model does not get to skip `browser` / `engine`. Skip is not success.
Frames land in `studio/proof/` and are injected as vision on the persona turn.

**Shipped**

- `daedalus/visual_proof.py` — web: `playground_browser.verify_app`; Godot:
  runtime probe + bridge screenshot; Unity: honest local gap. Missing Chromium
  or Godot sets `honest_gap` and keeps `complete=False`.
- `BuildLoop.wants_kernel_proof()` — quiet round with writes, no successful
  proof yet. Re-runs after a later `write_file`. Infra gaps stop without
  burning remaining fix rounds.
- `native_tools.run_native_discovery_pass` — runs proof before `on_quiet`;
  failed pixels → PLAN PROGRESS + frames; success folds the proof into the
  native result. OpenAI `image_url` ↔ Anthropic image conversion.
- `daedalus/project_status.py` — Playground `README.md` Status (built / left /
  proof) + Assets size table. Kernel patches the Proof line.
- Godot skill: silent-failure traps (owner chain, GLB inline, trimesh FPS,
  normals, `.gdignore`).
- `DAEDALUS_VISUAL_PROOF=0` restores the pre-S2 stop (plan score only).

**Done when (acceptance)**

| Criterion | How we know |
|-----------|-------------|
| Quiet after `write_file` on snake → kernel proof observed as `browser` | `test_kernel_runs_visual_proof_after_writes` |
| Successful proof can complete the loop | `test_quiet_round_with_incomplete_plan_gets_plan_progress` (fake proof) |
| Blank / failed proof is not complete; NEXT is write_file | `test_failed_visual_proof_nudges_a_fix` |
| Missing Chromium is an honest gap, not a pass | `test_missing_chromium_is_not_a_pass` |
| SLERP still needs math3d; visual proof also required after writes | `test_slerp_nudge_names_the_missing_math3d_step` |
| write_file result names playground slug path | `test_write_file_result_names_the_slug_path` |
| Status README on create | `test_new_project_gets_status_readme` (create_project + `ensure_status_readme`) |
| `DAEDALUS_VISUAL_PROOF=0` keeps old complete-without-pixels | `test_visual_proof_off_skips_kernel_open` |

**Not in S2:** syntax gate on `write_file` (S4), full S3 navigation inside every
tool result, router negation handling (S6). The persona turn still runs after
the build turn (S5).

**What S2 changes for the snake case:** after the last `write_file` the kernel
serves the canvas in Chromium. A blank screen comes back as PLAN PROGRESS with
a frame, not a DoD sticker. `find /workspace` is still refused; write_file now
says the file lives under `playground/projects/{slug}/`.

## How to approve S2

1. Review `daedalus/visual_proof.py`, the `wants_kernel_proof` hook in
   `build_loop.py`, and the quiet-round branch in `native_tools.py`.
2. `DAEDALUS_STANDALONE=1 python3 -m pytest tests/test_build_loop.py tests/test_visual_proof.py tests/test_tool_router.py -q`.
3. Reply that S2 is approved. S4 (syntax gate) is the next named stage unless
   the owner wants S3 finished first.

## Research

Internal study `docs/GODOGEN_STUDY.md`. Steal: pixels of the running game.
Keep: playbooks, scaffolds, build loop. Do not delete the kernel.
