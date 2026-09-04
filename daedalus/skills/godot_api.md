---
name: godot_api
description: Godot 4.3 API pin — CharacterBody2D, typed signals, .import sidecars, runtime bridge.
---

# SKILL: Godot 4.3 API (pinned)

**When**: kind is `godot`, files are `.tscn`/`.gd`, or the owner mentions GDScript / Metroidvania.

**Pin**: Godot **4.3**. Not 3.x. Not 4.0 leftovers.

## Do use
- `CharacterBody2D` / `CharacterBody3D` + `move_and_slide()`
- Typed connect: `area.body_entered.connect(_on_body)` — never `connect("body_entered", self, "_on_body")`
- Autoload `Game` for score/fail/win. Autoload `DaedalusBridge` is harness-owned — do not edit it
- `engine({action: run|inspect|input|errors, slug})` after a slice. Files are not Play
- PNG/GLB under `res://assets/` plus a `.import` sidecar (Daedalus writes one if missing)

## Do not use (hallucination magnets)
- `KinematicBody2D` / `KinematicBody` (Godot 3)
- `move_and_collide` as the only mover on the player
- String-named signals, `yield`, `get_tree().change_scene(` (use `change_scene_to_file`)
- Invented GLB vertex dumps
- Declaring done because `studio/preview/index.html` loads

## Runtime DoD (when `GODOT_BIN` exists)
0 SCRIPT ERROR after N frames. Player node moves when `ui_right` is pressed. A `[connection method="..."]` that does not exist in the target script is a fail even without the binary.
