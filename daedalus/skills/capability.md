---
name: capability
description: One production pipeline with Web, Godot, Expo, and Capacitor adapters. The capability registry is the only source of truth.
---

# SKILL: Capability pipeline

**When**: owner asks to ship a Playground game for web, Godot, iOS/Android, or `production({action: pipeline})`. Godot-first is the engine path of this same order, not a separate product.

**Order** (kernel walks this; do not skip):

Intent → Plan → Context → Build → Verify → Polish → Export → Publish

The registry (`daedalus.capability_registry`) is the only source of truth. Adapters declare capabilities. A missing capability is a named blocker. There is no silent fallback (Godot ↛ HTML, Expo ↛ web preview).

| Platform | Adapter | Runner | Honest refuse |
|---|---|---|---|
| web | `web_first` | Chromium | missing Chromium |
| godot | `godot_first` | Godot 4.3 | missing Godot / templates; never HTML |
| expo | `mobile_first` | emulator or device | Expo web export; preview HTML |
| capacitor | `mobile_first` | native WebView + device | config file without Gradle/Xcode |

**Capabilities**: create, write, run, input, render_proof, gameplay_proof, export, device_smoke, publish, rollback.

Web and Godot require everything except `device_smoke`. Expo and Capacitor require `device_smoke` as well. Only `status=pass` satisfies a capability. skipped / blocked / fail never count.

**How**:
1. `CapabilityRegistry.adapter(platform)` fails closed on unknown platforms (`unity` is not Vite).
2. Evidence is per capability: `{capability, status, project_id, revision, artifact_hash, details, runner_version}`.
3. An artifact needs `project_id`, `revision`, `target`, `artifact_hash`, `path`, `immutable=true`.
4. `release_decision` allows a release only when required capabilities are supported, every required evidence record is pass for the same project/revision/hash, and the artifact is hashed and immutable.
5. `production({action: pipeline, slug})` is the cross-platform entry. `engine({action: pipeline, slug})` stays Godot.
6. Roles: planner describes the contract; builder patches with version hashes; specialist writes adapter files; verifier does not write game files; polish only after a recorded visual failure. No two writers on the same file.

**Anti-patterns**:
- Treating a missing runner as a skip-pass.
- Publishing ADN zip / Expo web `dist/` as an iOS/Android release.
- Parallel `spawn_subagent` coders rewriting the same path.
- Reusing evidence from another revision or another artifact hash.
- Calling Godot “done” because `.tscn` files exist or preview HTML loaded.
- Calling a web game “done” because a canvas tag exists.
