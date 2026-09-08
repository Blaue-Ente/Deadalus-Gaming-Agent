---
name: platform-acceptance-qa
description: Fail-closed platform matrix QA on every build, repair, export, or publish. Web=Chromium gameplay, Godot=live scene+input, mobile=emulator/device. Evidence tied to revision+artifact hash. skipped is not pass.
---

# SKILL: Platform Acceptance QA

**When**: a project is built, repaired, exported, or published. Owner says QA, acceptance, "тествай", ship, or `production({action: qa})`. Prove the product is **running** — files on disk and a clean compile are not enough.

**Core contract**: every build is a versioned candidate `project_id + target + revision + artifact_hash`. Every check produces evidence for **that** candidate. A change to code, scenes, assets, dependencies, config, or export settings is a new revision and invalidates affected evidence.

**Statuses**: `pass | fail | skipped | blocked`. Only `pass` is success. Do not silently substitute one platform for another. If a required runner, emulator, engine, browser, export template, or device is missing, name the blocker — never claim ready or publishable.

**Matrix**: web = Chromium gameplay. godot = live scene + input (bridge). expo/capacitor = emulator or device (`device_smoke`). A web preview is not device smoke. A canvas, `.tscn`, or non-empty body is not gameplay.

**Tool**: `production({action: pipeline, slug})` collects evidence. `production({action: qa, slug})` returns the machine verdict. Skip is fail.

## Workflow

1. Identify the target: web, Godot, Expo/React Native, or Capacitor.
2. Resolve the adapter and required capabilities **before** writing or repairing.
3. Capture revision and artifact identity. If the project changed since last evidence, start a fresh set.
4. Build or repair in small, version-checked patches. Preserve behavior; do not replace whole files from truncated context.
5. Run checks in this order: create/write integrity → runtime launch → input → gameplay or UX scenario → rendered visual proof → export/package → device smoke when required.
6. On failure: isolate the first actionable cause, smallest patch, rerun the failed check **and** every check that patch invalidated.
7. Review independently. Approve only a structured `approved` verdict with no high-risk omissions.
8. Publish only the immutable artifact whose hash and revision match every required evidence row. Keep the previous release for rollback.

## Profiles

### Web (Vite, HTML, Phaser)

Required: `create, write, run, input, render_proof, gameplay_proof, export, publish, rollback`.

- Serve the real project entry — not a guessed file or `file://`.
- Wait for settle after cold start (longer for WebGL/WebGPU).
- Capture a real screenshot from the running app and collect console/page errors.
- Prove primary input (keyboard, pointer, touch, or gamepad) as specified.
- Genre scenario: start → core action → state change → win/lose → optional restart.
- Do **not** accept a canvas, DOM text, moving background, or non-empty body as gameplay.
- Responsive builds: viewport and touch separately from desktop.

### Godot 4

Required: `create, write, run, input, render_proof, gameplay_proof, export, publish, rollback`.

- Confirm the project is Godot. Never fall back to HTML/Vite instructions.
- Validate `project.godot`, main scene, scripts, imports, scene refs, export preset.
- Launch through the Godot headless/runtime bridge (`engine` / DaedalusBridge). Scaffold → build → run_headless → capture → probe_errors.
- Prove: scene load, player/primary movement, input, collision/interaction, core mechanic, win/lose or completion, restart, clean runtime errors.
- Capture a frame from the **running** scene. Headless compile/import is not visual proof.
- 3D: camera, lighting, materials, visible assets, contrast, readable geometry. HUD-only or dark/blank fails visual proof.
- Prefer node/state assertions plus screenshots over OCR or file-existence checks.
- External automation stays local/authenticated — do not send the project to a third-party editor.

### Mobile — Expo / React Native

Required: `create, write, run, input, render_proof, gameplay_proof, export, device_smoke, publish, rollback`.

- Validate Expo SDK, lockfile, native config, and platform targets.
- Run on an emulator or real device when native behavior is required. A web preview is not device smoke.
- Prove launch without a startup crash, navigation, touch, keyboard where applicable, loading/error/empty states, and the primary user flow.
- Capture device/emulator screenshots at declared size and orientation.
- Build the requested Android/iOS artifact; record command, target, version, and hash.
- Missing Android SDK, Xcode, simulator, emulator, signing, or device is a blocker, not a pass.

### Mobile — Capacitor

Same capabilities as Expo.

- Build and verify the web source independently first.
- `npx cap sync` — the native project must contain the **current** web artifact.
- Launch the native wrapper: touch, back, orientation, deep links, permissions, declared native APIs.
- Native screenshot plus platform/version/build identity.
- Produce the requested Android/iOS package. A web ZIP is not a mobile release.
- Native-build or device-smoke gaps are blockers.

## Result format

Return compact JSON plus a human summary:

```json
{
  "project_id": "...",
  "target": "web|godot|expo|capacitor",
  "revision": "...",
  "artifact_hash": "sha256:...",
  "checks": [
    {
      "id": "runtime",
      "status": "pass",
      "details": "...",
      "runner_version": "daedalus-0.4",
      "evidence": ["logs/runtime.txt", "screenshots/runtime.png"]
    }
  ],
  "release": {
    "approved": false,
    "blockers": ["..."],
    "rollback_ready": false
  }
}
```

The summary states what passed, what was **not** tested, and whether **this** artifact is publishable. Never use **ready**, **done**, **shipped**, or **publishable** when a required check is skipped, blocked, stale, or failed.

## Release gate

Approve only when:

- every required capability is supported by the selected adapter;
- every required evidence row is `pass`;
- project, target, revision, and artifact hash match;
- the artifact is immutable and hashed;
- the **exported** artifact was tested, not only the source workspace;
- rollback metadata points at the previous known-good release.

Before publishing, verify the public or installed result once more and record the URL or install identity. A local preview is not a public deployment. A source directory is not an exported release.

## Failure handling

When a check fails:

- name the first actionable failure and its exact evidence;
- do not let an unrelated successful command repair it;
- a tool invocation is not proof that its result passed;
- do not accept `APPROVED` embedded inside `NOT APPROVED`;
- rerun the failed check **after** the final repair, not before;
- invalidate screenshots, gameplay, quality scores, and export evidence affected by the change;
- stop at the repair budget and report remaining blockers honestly.

## Quality bar

Technical correctness (compile/run) and visual/gameplay quality are **separate** gates. A smoke test is not a good game.

- Visual: a real frame from the current build.
- Gameplay/UX: an observed state transition.
- Scores come from named dimensions in code. A model-supplied overall score cannot override missing screenshots, missing critical dimensions, or failed gameplay.

## Scenario shapes

Smallest scenario that proves the brief:

- web game: `launch -> input -> core mechanic -> state change -> win/lose -> restart`
- Godot game: `load scene -> move -> collide/interact -> solve mechanic -> completion -> restart`
- mobile app: `launch -> navigate -> touch primary action -> verify result -> back/reopen`

If the brief promises save/load, audio, localization, settings, gamepad, resolution scaling, permissions, or offline behavior, add explicit checks. Do not silently downgrade the brief to make QA pass.

## Acceptance assertions

1. **A1** Candidate identity is `project_id + target + revision + artifact_hash`.
2. **A2** Statuses are only `pass | fail | skipped | blocked`.
3. **A3** `skipped`, `blocked`, and `fail` are not success.
4. **A4** Unknown platforms fail closed; never remap to Vite/HTML.
5. **A5** Web gameplay proof is Chromium scenario checks, not a canvas tag.
6. **A6** Godot gameplay proof is a live scene plus input; files are not Play.
7. **A7** Mobile export requires `device_smoke`; web preview is not device smoke.
8. **A8** Missing runner / emulator / engine / browser / export template / device is `blocked`.
9. **A9** Stale revision or hash mismatch invalidates evidence.
10. **A10** Publish only the immutable artifact whose hash matches every required row.
11. **A11** Never say ready / done / shipped / publishable when a required check is not `pass`.
12. **A12** Do not accept `APPROVED` inside `NOT APPROVED`.
13. **A13** Compile/run and visual/gameplay quality are separate gates.
14. **A14** A model overall score cannot override missing screenshots or failed gameplay.
15. **A15** Do not drop promised features (save, audio, gamepad, …) to make the test pass.

## Example test prompts

- Web: "Run Platform Acceptance QA on this Phaser dungeon. Chromium must prove keyboard input, one combat hit, and a lose state. A `<canvas>` tag is not gameplay."
- Web 3D: "Vite Three.js racer — wait for WebGPU settle, capture a real frame, prove steer + boost + fail. A dark canvas is not visual proof."
- Godot: "QA this Godot slug. Bridge must load `scenes/main.tscn`, move the player, collide, and capture a running-scene frame. Headless import is not visual proof."
- Godot 3D: "Prove camera framing and readable geometry. A HUD-only or blank frame fails visual proof."
- Expo: "Emulator launch, touch the primary action, capture a device screenshot at the declared size. Expo web preview is a blocker."
- Capacitor: "Build web first, sync native, launch the wrapper, produce an APK/IPA. A web ZIP is not a mobile release."

## Anti-patterns

- Treating skipped Chromium / Godot / emulator as a pass.
- Publishing ADN zip or Expo web `dist/` as iOS/Android.
- Reusing evidence from another revision or another artifact hash.
- Calling Godot done because `.tscn` exists or preview HTML loaded.
- Calling a web game done because a canvas exists.
- Saying "готово" / ready while `release.approved` is false.
