---
name: capability
description: One production pipeline with Web, Engine, and Mobile adapters — Godot-strong, not Godot-only.
---

# SKILL: Capability pipeline

**When**: owner asks to ship a Playground game for web, Godot, iOS/Android, or `production({action: pipeline})`. Godot-first is the engine path of this same order, not a separate product.

**Order** (kernel walks this; do not skip):

plan → context → build → runtime → gameplay → visual → export → publish

Checks swap by target:

| Target | Adapter | Runner | Honest refuse |
|---|---|---|---|
| web | `web_first` | Chromium | missing Chromium |
| engine | `godot_first` | Godot 4.3 | missing Godot / templates; never HTML |
| mobile | `mobile_first` | Expo device or Capacitor + emulator | Expo web export; preview HTML |

**How**:
1. Kernel (`capability.py`) does not know HTML vs Godot vs iOS. It uses capability contracts (`create, write, run, input, render_proof, gameplay_proof, export, publish, rollback`) and versioned evidence in `studio/capability_evidence.json`.
2. `production({action: pipeline, slug})` is the cross-platform entry. `engine({action: pipeline, slug})` stays Godot.
3. Keep the kind. Capacitor wraps Vite/HTML canvas. Do not rewrite Godot to Phaser or a canvas game to Expo.
4. Publish accepts only an artifact that passed runtime, gameplay, visual, and export. A change of tracked files stale-invalidates old evidence.
5. Roles: planner describes the contract; builder patches with version hashes; specialist writes adapter files; verifier does not write game files; polish only after a recorded visual failure. No two writers on the same file.

**Anti-patterns**:
- Treating a missing runner as a skip-pass.
- Publishing ADN zip / Expo web `dist/` as an iOS/Android release.
- Parallel `spawn_subagent` coders rewriting the same path.
- Calling Godot “done” because preview HTML loaded.
