---
name: godot
description: Build Godot 4 games as text (.tscn / GDScript) — vertical slice, local Editor, optional headless CLI.
---

# SKILL: Godot 4

**When**: owner asks for Godot, GDScript, Metroidvania / 2D RPG on desktop, or Playground kind is `godot`.

**How**:
1. Playground kind `godot`. Scaffold already has `project.godot`, `scenes/main.tscn`, `scripts/player.gd`, autoload `Game`.
2. One `.tscn` or `.gd` per stage. Use `godot_tools.scene_tscn` / `script_template` / `signal_connect_snippet`.
3. Roles (not a CrewAI process): architect → gameplay → ui → npc_ai → vfx → audio → assets → tester.
4. Hero meshes: `blender_jobs.emit_job` → GLB in `assets/`. Mixamo for humanoid clips.
5. Cloud: do not `NEED_PLAYGROUND_RUN`. Owner opens the folder in Godot 4.3. If `godot` is on PATH: `engine({action: run, slug})` or `godot --headless --path . -- --daedalus-bridge <port>`.
6. API pin: Godot 4.3 only (`CharacterBody2D`, typed `.connect`). See skill `godot_api`.

**Anti-patterns**:
- Phaser / React / HTML gameplay in this kind.
- Rewriting `project.godot` every stage.
- Inventing GLB as ASCII vertices.
- Declaring done because the preview HTML page loads.
