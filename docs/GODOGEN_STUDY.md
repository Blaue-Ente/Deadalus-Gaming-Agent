# Godogen study — proof from the running game

Internal research (2026-09-07). Public README / ARCHITECTURE stay Daedalus-only.
This file is a decision brief. No harness change ships from it until the owner
approves a slice (likely **S2** in `docs/BUILD_LOOP_PROGRESS.md`).

Source studied: [htdt/godogen](https://github.com/htdt/godogen) (MIT, Alex Ermolov),
cloned 2026-09-07. ~6.7k GitHub stars. Demos: 15–20s recordings of Godot / Bevy /
Babylon.js games built from a short brief.

## Verdict

Godogen is **not** a better Daedalus. It is a **thin runtime contract** that
rides Claude Code or Codex (Opus-class / GPT-5.5-class hosts) and refuses to
call the work done until the agent has **watched the running game** — a live
URL, or a 15–20s movie it encodes and looks at.

Daedalus is the opposite product: **we are the host**. ~78k lines of Python,
~222 modules, our own tool loop, playbooks, and Definition of Done. The live
eval on 2026-09-05 (Nemotron 3.5 Lightning) showed the real failure: tools and
plans exist, but nobody holds the wheel, and **nobody looks at the game**.

The transferable key is the **proof surface** (pixels of the running game,
injected back into the model). It is **not** Godogen's "drop the planner and
trust the model" — that only works on their host. Copying that onto our loop
would make Snake and SLERP worse.

Keep the kernel. Steal the eyes.

---

## 1. What Godogen actually is

A published game repo is three things:

1. A 12-line runtime manifest (`CLAUDE.md` or `AGENTS.md`)
2. A one-page engine guide (`godot.md` / `bevy.md` / `babylon.md`)
3. One skill: `asset-gen` (Python CLIs + a short SKILL.md)

`publish.sh --engine godot|bevy|babylon --agent claude|codex --out ~/game`
renders those into a fresh git repo. **No project scaffold is shipped.** The
host agent recreates `project.godot`, Vite, or Cargo from the one-page sketch.

This repository is not a game and not an agent. Layout:

| Path | Role |
|------|------|
| `prompts/runtime.md` | Engine-agnostic process. Token-substituted into the manifest. |
| `engines/{godot,bevy,babylon}.md` | Stack, layout, **silent-failure traps**, capture recipe. |
| `asset-gen/` | Sole published skill: Gemini / Grok images, Grok video sprites, Tripo3D GLB/rig/retarget, rembg, loop trim. |
| `publish.sh` + `scripts/render_dir.py` | Render-time engine × host-agent matrix. |
| `setup.md` | Workstation: Godot **.NET**, Vulkan, xvfb, ffmpeg, ImageMagick, API keys. |
| `docs/gdscript-vs-csharp.md` | Why they left GDScript. |
| `docs/demo_prompts.md` | The briefs that produce the YouTube demos (CartoRally, Amsterdam cyclist, Alpine snowboard, nature scene). |

Counts (this clone, excluding `.git`): **27 files, ~2.4k lines of markdown + Python**.
`asset_gen.py` is more than half of the code. The "agent" is Claude Code / Codex.

### Changelog trajectory (they deleted what we are still adding)

| Date | What they did |
|------|----------------|
| 2026-03-09 | Initial: image gen, 3D, screenshot QA, video capture |
| 2026-04-03 | Single-context pipeline (orchestrator merged into one loop) |
| 2026-04-06 | **GDScript → C# / .NET 9.** `dotnet build` replaces per-file validation |
| 2026-04-14 | Codex as a second host (parallel skill tree) |
| 2026-04-26 | Bevy. **Dropped Gemini-as-verifier** — Opus/GPT self-verify from frames |
| 2026-05-18 | Babylon.js + Playwright capture |
| **2026-07-02** | **Docs-only runtime.** Deleted planner / decomposer / architecture / scene / scaffold / quirks / capture **skills**, deleted `godot-api` / `bevy-help` / `babylon-help` lookup skills, **deleted all hooks**. One manifest + one engine page + `asset-gen`. |

Their own words (`docs/PROJECT.md`):

> There is no fixed multi-stage pipeline and no prescribed document protocol —
> a capable model plans and decomposes the work itself. The two things the
> manifest fixes are *where durable state lives* (`README.md`) and *that the
> result is proven from the running game*.

> Don't give obvious guidance. The agent is a highly capable LLM, and the
> deliverable (a recorded video, or a live URL) surfaces its own mistakes —
> so keep the guides to what the model can't infer or discover fast.

That last sentence is the product doctrine. Everything they still write is a
**silent-failure trap** or a **capture recipe**.

---

## 2. Runtime, in detail

### 2.1 The entire process (runtime.md, ~12 bullets)

Paraphrased, not copied:

- Durable status lives in the **game repo `README.md`**: built, remaining, asset table.
- Visual assets go through the `asset-gen` skill. Confirm spend before the first paid call.
- Read the engine guide for stack, layout, run, capture.
- **Judge from the running game, never from a clean build.**
- Framing decides involvement: open-ended brief → live game early, checkpoint on taste/scope/cost. Finished spec → reasonable calls, no blocking, close with a 15–20s video **and watch it back before calling done**.

There is no stage machine. Compaction survival is one file the agent updates
(`README.md`), not a second database.

### 2.2 Godot guide — the traps they kept

Stack they actually use: **Godot 4 .NET / C#**, Jolt physics, fixed tick. All
Godot C# classes `partial`. Build gate: `dotnet build` → `godot --headless --import`
→ `godot --headless --quit`.

**Scenes are not hand-written `.tscn`.** A C# `SceneTree` script runs once
headless (`godot --headless --script scenes/BuildX.cs`), builds the hierarchy,
packs, validates, writes `.tscn`, quits. Leaf scenes first, parents after.
Runtime scripts live in `scripts/*.cs` and contain the game; builders contain
**no** `_Ready` / `_Process`.

Silent failures they document because they **compile and look like success**:

| Trap | What happens if you miss it |
|------|-----------------------------|
| Owner chain | Node without `Owner = root` is dropped from the packed `.tscn` |
| GLB recursion | Recursing `Owner` into a GLB inlines meshes as text → 100MB+ `.tscn` |
| Pack without instantiate-and-count | Silent node drop looks like a successful save |
| `SetScript()` | Disposes the C# wrapper; must re-fetch via a temp parent |
| `CreateTrimeshShape()` on imported GLB | FPS collapse (<1) |
| `ArrayMesh.GenerateNormals()` omitted | Shadows vanish; no error |
| MultiMeshInstance3D + GLB | Mesh lost on pack |
| `MaterialOverride` on GLB internals | Won't serialize (owner skipped) |
| Raycast vs `ConcavePolygonShape3D` | Misses; use shape query or analytic height |
| `.gdignore` in `assets/` | Importer skips the whole tree silently |
| Guessed C# enum names | Training data is GDScript (`BGMode.Sky` vs `BGModeEnum.Sky`) |
| Per-tick `speed *= (1 - drag)` | Frame-rate dependent; use `Exp(-rate * delta)` |

Capture: **hardware Vulkan required for video**. Dedicated capture `SceneTree`
under `test/`:

```text
godot --write-movie screenshots/result/frame.png --fixed-fps 30 --quit-after 450 --script test/Presentation.cs
ffmpeg … frame*.png → video.mp4
```

450 frames @ 30 fps = 15s. Camera pre-positioned (first movie frame is *before*
`_Process`). Input is scripted, not live keys. Software Vulkan (`llvmpipe`) may
do stills; skip video and say so.

### 2.3 Why C# (they measured it)

`docs/gdscript-vs-csharp.md` is the most important Godogen file for Daedalus,
because **our Godot path is still GDScript + hand `.tscn`**.

GDScript failure mode they named: *death by a thousand paper cuts*. `:=` vs
`=`, `load()` returning Variant, `instantiate()` Variant, `abs`/`clamp`/`lerp`
Variant, `preload` vs `load`, `@onready` vs `init()`, `await` during
`--write-movie` advancing the frame counter. The LLM emits plausible code that
fails silently or with cryptic messages.

C# trades that diffuse minefield for three contained edges (`SetScript()`
disposal, `.csproj`, enum-name bias) and a compiler that catches the rest
**before** runtime. Their task-execution doc shrank 14% after the migration
because per-file `--check-only -s` went away.

### 2.4 Bevy / Babylon.js (same contract, different capture)

- **Bevy:** current stable, APIs verified against *installed* crate source
  (newer than training data). Offscreen `RenderTarget::Image` capture binary;
  do not record the windowed app under xvfb (it panics). Manual 1/30s clock.
- **Babylon.js:** Vite + TS, bind `0.0.0.0:5173`, user watches the URL.
  Side-effect imports (`instancedMesh`, `ray`, `glTF`) compile then throw.
  Capture: headless Chrome, **read WebGL RENDERER**, warn on SwiftShader.
  Wait for a ready flag — early screenshot is a blank lie.

### 2.5 asset-gen (the only skill)

Paid APIs, JSON to stdout, costs in cents, **user confirm before first spend**.

| Job | Backend | Typical cost |
|-----|---------|--------------|
| Texture / simple sprite | Grok (`grok-imagine-image`) | 2¢ |
| Precise character / layout ref | Gemini 1K | 7¢ |
| Sprite video | Grok video, 5¢/s | 10–75¢ |
| PNG → GLB | Tripo3D | 30¢ (60¢ HD) |
| Biped rig | Tripo3D | +25¢ |
| Animation retarget | Tripo3D preset | 10¢/clip |

Operational rules we do not have, and they exist because they burned money:

- Never prompt "transparent background" — the model draws a checkerboard. Solid
  color, then matte (`rembg_matting.py`, BiRefNet). QA is a `_qa.png` composite;
  the agent cannot judge alpha from a raw PNG.
- Review every PNG before GLB (a bad image wastes 30¢+).
- Sprite recipe: Gemini ref → pose img2img → video → ffmpeg frames →
  `find_loop_frame.py` → batch rembg. One reference for all actions.
- Tripo jobs hang at 99%. Timeout ≠ failure. Sidecar `<out>.tripo.json` holds
  the task id. **Resume, do not resubmit** (double charge).
- README asset table **must** include in-game Size (`4m long`, `128x128 px`).
  Without it, the coder scales everything wrong.
- Independent images: parallel bash in one turn.

### 2.6 Host and hardware

- Claude Code (`.claude/skills/`, `/asset-gen`) or Codex (`.agents/skills/`).
- A run can take **hours**. They recommend a GPU box, `tmux`, official remote
  control. Hardware Vulkan for Godot video; WebGL2 GPU for Babylon.
- Godot **.NET edition** is required. Standard Godot cannot run their C#.

They are not competing with 8-round chat loops. They are competing with a
weekend of Claude Code on a GPU machine.

---

## 3. Daedalus as it stands (honest)

| Layer | What we actually have |
|-------|------------------------|
| Host | Us. `agent.py` + `native_tools.run_native_discovery_pass` (default **8 rounds**, +2 per fix, max 3 fix nudges). |
| Router | `tool_catalog` + `task_playbooks` + `tool_router`. Kernel matches the ask *before* the LLM talks. Necessary: Nemotron emitted `<need_list_dir>` XML and quit. |
| Build loop (S1) | `build_loop.py` scores the plan inside the native loop. Quiet + incomplete → PLAN PROGRESS. Awaiting owner approval. |
| DoD | `job_contract`: wrote files? ran a tool? browser_ok for visual kinds? Model does not get to say "готово". Still **tool-shaped**, not pixel-shaped. |
| Godot | Kind `godot`, **GDScript + text `.tscn`**, scaffold in `playground_godot_scaffold.py`. `godot_dod.py` static signal/player/camera checks. `godot_bridge.py` TCP autoload: tree, input, one screenshot, FPS. `godot_runtime_probe.py` `--quit-after`. **No `--write-movie`, no 15s clip, no "watch it back".** |
| Browser games | `playground_browser.py`: Chromium, blank screen, console, **one** screenshot. Smoke, not gameplay. |
| Playtest | `playtest.py`: 4–6 LLM **personas reading source files**. Heuristic canvas/win-lose. Not pixels. |
| Assets | `atlas.py` / `sfx_kit.py` stdlib PNG. `mesh_jobs.py` Tripo/Meshy/fal — **fail closed without keys**. Greybox cube is honest. No Grok video sprite pipeline, no rembg QA, no cost table, no Size column. |
| Durable state | Job ledger, continuity, Chroma, Studio. The *game repo* often has no README the model treats as the run log. Compaction loses "what is built". |
| Identity | Games first, verify is part of building, no silent harness edits. Explicitly **not** "one sentence → finished game". |

Live eval (`docs/BUILD_LOOP_PROGRESS.md`, 2026-09-05):

- **Snake:** files in 3 rounds, then 8 rounds hunting them (`find /workspace`, jail BLOCKED). Duplicate `let gameLoopId`. **Never opened in a browser.** DoD sticker appended after the reply.
- **SLERP (mixed):** router merged three playbooks on negated keywords. 5× `list_dir`, zero writes, zero `math3d`.
- **SLERP (clean):** right playbook. Model emitted XML, one `list_dir`, quit. No files.

S1 fixes "quiet round with an incomplete plan". It does **not** yet make the
kernel *run* the game and *show* the model the frames (that's S2).

`godot_platform_guide.py` still says "CrewAI-style roles and phases" and
"Godot Editor does not run in the cloud". Godogen's whole point is that the
**headless engine is the Editor** for the agent.

---

## 4. Side by side

| Question | Godogen | Daedalus |
|----------|---------|----------|
| What is the product? | Generator source → thin game repo → host agent builds the game | The agent + Studio + Playground, one process |
| Who holds the tool loop? | Claude Code / Codex (hours, compaction, remote control) | `native_tools` 8+6 rounds |
| Who plans? | The model | The kernel playbook, then the model |
| What is shipped into the game repo? | Manifest + 1 engine page + asset skill | Scaffold + many skills + factory rooms |
| Godot language | C# / .NET 9, compiler gate | GDScript 4.3, static `.tscn` parse |
| How are scenes born? | Headless `SceneTree` builders, pack-validate | Hand-written `.tscn` templates |
| What is "done"? | Model watched a 15–20s clip or the live URL | Files exist + smoke + (sometimes) one browser screenshot + persona text |
| Visual feedback into the model | Movie frames / live page (vision) | Optional one screenshot; playtest reads **code** |
| Asset path | Gemini + Grok + Tripo, cost-tracked, Size in README | Atlas/SFX always; 3D only with keys; greybox otherwise |
| Durable run state | Game `README.md` | Ledger + Chroma + continuity (parallel to the game) |
| Docs philosophy | Only silent failures + capture | Encyclopedic skills + playbooks + identity |
| Typical hardware | GPU + Vulkan + xvfb + Godot Mono | Laptop / Docker / VPS; Godot optional |
| Failure they optimized against | "It compiled" / silent `.tscn` drop / blank capture | Model stops calling tools; false "готово"; jail hunting |

---

## 5. Where the key actually hides

Three layers. Only the middle one is cheap to steal.

### 5.1 Host quality (cannot copy with code)

Godogen's demos are Opus / GPT-5.5 inside Claude Code, unattended for hours,
on a GPU. Our live eval is Nemotron in 8 rounds. **No amount of prompt thinning
turns Nemotron into that host.** Their 2026-07-02 deletion of skills is
evidence that *their* model no longer needed the crutches. Ours still does —
the XML-and-quit SLERP case is the proof.

Do not delete playbooks, scaffolds, or the build loop because Godogen did.

### 5.2 Proof from pixels the model sees (the steal)

This is the key that looks like "they make real games" from the outside.

1. The engine **runs**.
2. Capture is **deterministic** (fixed fps, scripted input, camera already in
   place).
3. The artifact is a **clip or a live URL**, not a compile log.
4. The **same model looks at the frames** and the defects drive the next edit.
5. "Done" is illegal until that look has happened.

Daedalus already has fragments: Chromium smoke, Godot TCP bridge, one
screenshot, `game_build_contract.skip_is_not_ok`. They are not wired as the
**closed visual loop**. Browser verify is a tool the model may never call
(Snake). Playtest critiques source. Headless Godot FPS=0 is warn-only. The
screenshot, when it exists, is rarely injected as vision on the next native
round.

Godogen dropped *external* Gemini verification because **the coding model
looking at its own frames** was enough. We have `chat_attachments.vision_blocks`
and `web_screenshot_tool` already. S2 should use that path for *our* game, not
a third-party URL.

### 5.3 Write only what the model cannot discover (hygiene)

Their engine pages are ~50–70 lines. Ours inject TASK PLAN, two Godot skills,
platform addendum, CrewAI-shaped phases, identity, continuity. For a strong
host that is noise. For a weak host some of it is load-bearing.

The subset that is load-bearing **and** missing: **silent-failure traps**
(owner chain, GLB inline, trimesh FPS, missing normals, `.gdignore`) and
**capture recipes** (`--write-movie`, xvfb, Vulkan ICD, "first frame is before
`_Process`"). Those do not belong in a 200-line identity. They belong in the
Godot skill the playbook already auto-loads.

### 5.4 C# / scene-as-code (high leverage, later)

If we stay on GDScript + hand `.tscn`, we keep the Variant/`:=` minefield
their migration was designed to escape, and we keep silent node drops on pack.
A `godot-cs` kind (optional, Godot Mono on PATH) plus SceneTree builders with
instantiate-and-count is the Godot-shaped version of S4's syntax gate.

Not S2. Do not block visual proof on a language migration.

---

## 6. What to give Daedalus

Mapped onto the already-approved build-loop stages. **Do not start S3–S6 from
this study.** Steal into **S2**, then a small Godot follow-up after F8/S1 land.

### S2 — kernel-run visual proof (do this)

When the playbook is a visual kind (`static` / `vite*` / `godot`) and the model
goes quiet or the write step is marked done:

1. Kernel runs verify **without waiting for the model to call `browser` /
   `engine` / `playtest`.** (This is already the S2 title.)
2. For web: Chromium as now, **plus** 8–12 paced screenshots (or a short
   Playwright screencast) while a tiny scenario drives input if
   `playtest/scenario.json` exists.
3. For Godot when `GODOT_BIN` exists: `--write-movie` (or bridge screenshots on
   a fixed clock) → ffmpeg 10–15s mp4 under `studio/proof/`. Hardware Vulkan if
   present; otherwise stills + honest "no video".
4. Sample 3–5 frames into the **next native user turn as vision blocks**, with
   a one-line caption each (`t=0.0 blank`, `t=2.1 player`, `t=8.0 still title`).
5. `job_contract` / `BuildVerdict.complete` requires this proof artifact, not
   merely `ran=True`. Missing Chromium/Godot → incomplete, not skipped-ok
   (we already teach `skip_is_not_ok`; apply it here).
6. Persona playtest stays as a **second** critic, never a substitute for
   frames.

Done when: the Snake eval cannot end without a browser (or an honest
"Chromium missing") and the Godot playbook cannot say complete without
`studio/proof/` or an honest "Godot not on PATH".

### Godot skill — trap page (small, after S2 or with it)

Add a "silent failures" section to `daedalus/skills/godot.md` (and the 3D
scaffold comments): owner chain, do not recurse GLB, validate pack counts,
no trimesh on imported meshes, `GenerateNormals`, `.gdignore` only in
`screenshots/`, frame-rate independent damping. Do **not** paste the C#
builder sketch until we have a C# kind.

### Durable status in the game repo (small)

On playground create / first write, ensure `README.md` with:

- What is built / what is left (the agent updates this; kernel can also patch
  the "proof" line).
- Asset table with **Size** (meters or display px), path, cost-or-greybox.

This survives compaction better than Chroma for a single game run. Continuity
stays for the owner; the game README is for the builder.

### Assets — wrap what we already have (later)

When `TRIPO_API_KEY` / image keys exist, expose a single `assets` CLI shape
closer to Godogen: JSON stdout, cost, resume-from-sidecar, "confirm spend".
Add: never prompt transparent bg; `_qa.png` for mattes; Size column required
before `engine run`. Do not vendor their `asset_gen.py`. Do not require Grok
for M0 (greybox remains honest).

### Optional later (owner must name)

- Kind `godot-cs` (Godot Mono + `dotnet build`) as an *opt-in* beside GDScript.
- SceneTree builder pattern for GDScript first (same pack-validate, no C#).
- Delivery modes in the persona: "steer live" vs "unattended proof clip".
- `publish.sh`-style export of a Playground slug as a Claude Code repo — a
  **sidecar**, not a replacement for Daedalus. Only if the owner wants to ride
  a frontier host without giving up Studio.

### Do not

- Delete playbooks, identity, scaffolds, or the build loop.
- Vendor Godogen or copy their prompts verbatim into public docs.
- Promise "one sentence → finished game".
- Treat LLM persona playtest as visual proof.
- Make Claude Code a required runtime. Daedalus stays one process.

---

## 7. Why "always something goes wrong" on our side

Not one bug. A stack:

1. **The host is weaker** than the one Godogen assumed when they deleted the
   pipeline. The model stops, emits the wrong protocol, or tours the jail.
2. **Done is a sticker** (S1 is the fix for the sticker; S2 is the fix for
   never seeing the game).
3. **Godot is file-DoD** plus a probe the cloud often skips. Silent `.tscn`
   defects match Godogen's trap list and we do not check pack counts.
4. **GDScript Variant inference** produces plausible wrong code; we have no
   `dotnet build` equivalent on that path (S4 syntax gate helps JS; Godot `.gd`
   still needs `godot --headless --check-only` or gdformat/ruff-equivalent).
5. **Assets are honest cubes** unless keys exist — demos that look like
   CartoRally *require* the paid image→GLB path they budget in cents.
6. **Rounds are chat-sized**, not hour-sized. A 15s movie after 8 tool rounds
   is feasible; a full Alpine snowboard sim is not, on this host, in one turn.

S1+S2 close (1)–(2) for M0 slices. (3)–(4) are Godot-shaped follow-ups. (5) is
keys + Size table. (6) is effort=`epic` + a frontier model in the wardrobe,
not more Python.

---

## 8. Approval

This study does not change runtime code.

If the owner agrees the steal is **visual proof into S2**, reply that S2 may
include: kernel-run browser/engine, proof clip or paced frames, vision inject,
`complete` gated on that artifact.

C# Godot, scene builders, and asset-gen CLI remain unnamed until the owner
names them as a phase.
