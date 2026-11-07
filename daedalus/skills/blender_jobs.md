---
name: blender_jobs
description: Emit Blender Python (bpy) jobs for meshes, PBR, rigs, GLB export — never assume bpy in the cloud.
---

# SKILL: Blender jobs

**When**: a game needs a hero mesh, PBR material, humanoid armature, or GLB/FBX for Godot/Unity/Vite.

**How**:
1. Call `daedalus.blender_jobs.emit_job(project_dir, job_id, params)` or MCP `python -m daedalus.blender_mcp`.
2. Jobs: clear_scene, create_primitive, create_mesh_grid, apply_material_pbr, add_camera_light, set_transform, create_rig, parent_armature, export_glb, export_fbx, batch_export.
3. Script lands in `tools/blender/<job>.py`. Owner runs `blender -b --python tools/blender/export_glb.py`.
4. `execute: true` only if `BLENDER_BIN` or `blender` is on PATH.

**Anti-patterns**:
- `import bpy` in Daedalus cloud Python.
- Shipping a fake GLB.
- Replacing Mixamo with the 15-bone stub for cinematic animation.
