---
name: godot
description: Build Godot 4 games as text (.tscn / GDScript) — vertical slice, local Editor, optional headless CLI.
---

# SKILL: Godot 4

**When**: owner asks for Godot, GDScript, Metroidvania / 2D RPG on desktop, or Playground kind is `godot`.

**Godot-first order** (EngineAdapter of the shared capability pipeline; kernel walks this; do not skip):
preflight → contract → context → slice → runtime → visual → review → export → publish.

`engine({action: pipeline, slug})` and `production({action: pipeline, slug})` both land on the Godot adapter. The capability registry platform is `godot` (not Vite, not HTML). Missing Godot or export templates is **FAIL**, not skip. Preview HTML is not Play. Vite/Phaser are not this kind. Files on disk are not gameplay proof.

**How**:
1. Playground kind `godot`. Scaffold already has `project.godot`, `scenes/main.tscn`, `scripts/player.gd`, autoload `Game`, HUD, win/lose/restart.
2. Patch with `edit_file`. Do not rewrite a whole `.gd`/`.tscn` each turn. `write_file` is for new files.
3. One `.tscn` or `.gd` per slice. Use `godot_tools.scene_tscn` / `script_template` / `signal_connect_snippet`.
4. Roles (not a CrewAI process): architect → gameplay → ui → npc_ai → vfx → audio → assets → tester.
5. Hero meshes: `blender_jobs.emit_job` → GLB in `assets/`. Mixamo for humanoid clips. Primitive collision from the AABB — never `create_trimesh_shape()` on an imported mesh (FPS collapse).
6. Cloud: do not `NEED_PLAYGROUND_RUN`. Owner opens the folder in Godot 4.3. If `godot` is on PATH: `engine({action: run, slug})` or `godot --headless --path . -- --daedalus-bridge <port>`. The kernel also opens the project for visual proof after the last write.
7. API pin: Godot 4.3 only (`CharacterBody2D`, typed `.connect`). See skill `godot_api`.
8. README.md in the project: keep **Status** (built / left / proof) and an **Assets** table with in-game Size (meters or display px).
9. Do not start `world/` campaign files until this slice is green, unless the owner asked for **epic**.

**Silent failures** (compile / save look fine, the running game is wrong):

- Packed `.tscn`: every node needs `owner` = scene root or it is dropped on save. After building a tree, walk descendants and set owner — **do not recurse into instantiated GLB/`.tscn` children** (`scene_file_path` set). Recursing a GLB inlines meshes as text → 100MB+ scene.
- Validate a pack: count nodes, `instantiate()` the `PackedScene`, compare counts, only then save.
- `ArrayMesh` without `generate_normals()` receives no shadows (no error). Fix winding; do not disable cull as a "safety net".
- Raycasts miss `ConcavePolygonShape3D` (trimesh). Use a shape query or sample height analytically.
- `.gdignore` in a directory makes the importer skip it silently — only `screenshots/` (or `studio/proof/`) may have one, never `assets/`.
- Frame-rate-independent damping: `speed *= exp(-rate * delta)`, not `speed *= (1 - drag)` per tick.

**Anti-patterns**:
- Phaser / React / HTML gameplay in this kind.
- Rewriting `project.godot` every stage.
- Inventing GLB as ASCII vertices.
- Declaring done because the preview HTML page loads. Proof is the running game (engine / kernel frames).
- Publishing preview HTML. Stage 8 ships only the Stage 7 export artifact.
