"""Blender job templates — bpy scripts for the owner desktop, not in-cloud bpy.

Daedalus cannot import bpy in the cloud VM. We emit scripts under
``tools/blender/`` and optionally run ``blender -b --python`` when the binary
exists.

Recipes stolen from a dumped Blender MCP (modify/modifier/light/render/Y-up
GLB). Not stolen: arbitrary ``exec``, SSE-inside-Blender, mock-when-no-bpy.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

JOB_IDS: tuple[str, ...] = (
    "clear_scene",
    "create_primitive",
    "create_mesh_grid",
    "apply_material_pbr",
    "add_camera_light",
    "add_light",
    "set_transform",
    "modify_mesh",
    "add_modifier",
    "create_rig",
    "parent_armature",
    "set_pivot",
    "collision_hint",
    "render_preview",
    "export_glb",
    "export_fbx",
    "batch_export",
)

_HEADER = '''\
"""Daedalus Blender job — run: blender -b --python this_file.py
Do not run this with system Python; it needs bpy.
"""
import bpy
from mathutils import Vector

def _ensure_object_mode():
    if bpy.context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
'''

JOBS: dict[str, str] = {
    "clear_scene": _HEADER
    + '''
_ensure_object_mode()
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
print("cleared")
''',
    "create_primitive": _HEADER
    + '''
KIND = "{kind}"
NAME = "{name}"
_ensure_object_mode()
ops = {{
    "cube": bpy.ops.mesh.primitive_cube_add,
    "uv_sphere": bpy.ops.mesh.primitive_uv_sphere_add,
    "cylinder": bpy.ops.mesh.primitive_cylinder_add,
    "plane": bpy.ops.mesh.primitive_plane_add,
    "torus": bpy.ops.mesh.primitive_torus_add,
    "cone": bpy.ops.mesh.primitive_cone_add,
    "monkey": bpy.ops.mesh.primitive_monkey_add,
}}
fn = ops.get(KIND, bpy.ops.mesh.primitive_cube_add)
fn(location=(0, 0, 0))
obj = bpy.context.active_object
obj.name = NAME
print("primitive", obj.name)
''',
    "create_mesh_grid": _HEADER
    + '''
_ensure_object_mode()
bpy.ops.mesh.primitive_grid_add(x_subdivisions={subdiv}, y_subdivisions={subdiv}, size={size})
bpy.context.active_object.name = "{name}"
print("grid", bpy.context.active_object.name)
''',
    "apply_material_pbr": _HEADER
    + '''
obj = bpy.data.objects.get("{name}") or bpy.context.active_object
if obj is None:
    raise SystemExit("no object")
mat = bpy.data.materials.new(name="{mat_name}")
mat.use_nodes = True
nt = mat.node_tree
bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
bsdf.inputs["Base Color"].default_value = ({r}, {g}, {b}, 1.0)
bsdf.inputs["Metallic"].default_value = {metallic}
bsdf.inputs["Roughness"].default_value = {roughness}
if obj.data.materials:
    obj.data.materials[0] = mat
else:
    obj.data.materials.append(mat)
print("pbr", mat.name)
''',
    "add_camera_light": _HEADER
    + '''
_ensure_object_mode()
bpy.ops.object.camera_add(location=(6, -8, 5))
cam = bpy.context.active_object
cam.name = "DaedalusCam"
cam.rotation_euler = (1.1, 0.0, 0.7)
bpy.context.scene.camera = cam
bpy.ops.object.light_add(type="SUN", location=(4, 2, 8))
bpy.context.active_object.name = "DaedalusSun"
print("camera+light")
''',
    "add_light": _HEADER
    + '''
KIND = "{kind}"
NAME = "{name}"
_ensure_object_mode()
data = bpy.data.lights.new(name=NAME, type=KIND)
data.energy = {energy}
data.color = ({r}, {g}, {b})
obj = bpy.data.objects.new(name=NAME, object_data=data)
obj.location = ({x}, {y}, {z})
bpy.context.collection.objects.link(obj)
print("light", NAME, KIND)
''',
    "set_transform": _HEADER
    + '''
obj = bpy.data.objects.get("{name}") or bpy.context.active_object
if obj is None:
    raise SystemExit("no object")
obj.location = Vector(({x}, {y}, {z}))
obj.scale = Vector(({sx}, {sy}, {sz}))
print("transform", obj.name)
''',
    "modify_mesh": _HEADER
    + '''
NAME = "{name}"
OPERATION = "{operation}"
obj = bpy.data.objects.get(NAME)
if obj is None or obj.type != "MESH":
    raise SystemExit("need a mesh named " + NAME)
_ensure_object_mode()
bpy.ops.object.select_all(action="DESELECT")
obj.select_set(True)
bpy.context.view_layer.objects.active = obj
bpy.ops.object.mode_set(mode="EDIT")
try:
    if OPERATION == "subdivide":
        bpy.ops.mesh.subdivide(number_cuts={cuts})
    elif OPERATION == "extrude":
        bpy.ops.mesh.extrude_region_move(
            TRANSFORM_OT_translate={{ "value": ({ex_x}, {ex_y}, {ex_z}) }}
        )
    elif OPERATION == "bevel":
        bpy.ops.mesh.bevel(offset={offset}, segments={segments})
    elif OPERATION == "inset":
        bpy.ops.mesh.inset(thickness={thickness})
    elif OPERATION == "delete_faces":
        bpy.ops.mesh.delete(type="FACE")
    elif OPERATION == "merge_by_distance":
        bpy.ops.mesh.remove_doubles(threshold={distance})
    else:
        raise SystemExit("unknown mesh op " + OPERATION)
finally:
    bpy.ops.object.mode_set(mode="OBJECT")
print("modified", NAME, OPERATION, "verts", len(obj.data.vertices))
''',
    "add_modifier": _HEADER
    + '''
NAME = "{name}"
MOD = "{modifier_type}"
obj = bpy.data.objects.get(NAME)
if obj is None:
    raise SystemExit("no object " + NAME)
mod = obj.modifiers.new(name=MOD + "_mod", type=MOD)
if MOD == "SUBSURF":
    mod.levels = {levels}
    mod.render_levels = {render_levels}
elif MOD == "MIRROR":
    mod.use_axis = ({mirror_x} == 1, {mirror_y} == 1, {mirror_z} == 1)
elif MOD == "ARRAY":
    mod.count = {count}
    mod.relative_offset_displace = ({ox}, {oy}, {oz})
elif MOD == "SOLIDIFY":
    mod.thickness = {thickness}
elif MOD == "BEVEL":
    mod.width = {width}
    mod.segments = {segments}
elif MOD == "BOOLEAN":
    mod.operation = "{bool_op}"
    target = bpy.data.objects.get("{target}")
    if target is not None:
        mod.object = target
print("modifier", MOD, "on", NAME)
''',
    "create_rig": _HEADER
    + '''
# Humanoid armature — 15 bones (12+). Not Mixamo quality; good enough to parent a GLB.
_ensure_object_mode()
bpy.ops.object.armature_add(enter_editmode=True, location=(0, 0, 0))
arm_obj = bpy.context.active_object
arm_obj.name = "{name}"
arm = arm_obj.data
edit = arm.edit_bones
root = edit[0]
root.name = "Root"
bones = [
    ("Hips", "Root", (0, 0, 1.0), (0, 0, 1.15)),
    ("Spine", "Hips", (0, 0, 1.15), (0, 0, 1.4)),
    ("Chest", "Spine", (0, 0, 1.4), (0, 0, 1.65)),
    ("Neck", "Chest", (0, 0, 1.65), (0, 0, 1.8)),
    ("Head", "Neck", (0, 0, 1.8), (0, 0, 2.05)),
    ("L_UpperArm", "Chest", (0.15, 0, 1.6), (0.4, 0, 1.4)),
    ("L_Forearm", "L_UpperArm", (0.4, 0, 1.4), (0.6, 0, 1.2)),
    ("L_Hand", "L_Forearm", (0.6, 0, 1.2), (0.72, 0, 1.12)),
    ("R_UpperArm", "Chest", (-0.15, 0, 1.6), (-0.4, 0, 1.4)),
    ("R_Forearm", "R_UpperArm", (-0.4, 0, 1.4), (-0.6, 0, 1.2)),
    ("R_Hand", "R_Forearm", (-0.6, 0, 1.2), (-0.72, 0, 1.12)),
    ("L_Thigh", "Hips", (0.1, 0, 1.0), (0.12, 0, 0.55)),
    ("L_Shin", "L_Thigh", (0.12, 0, 0.55), (0.12, 0, 0.12)),
    ("R_Thigh", "Hips", (-0.1, 0, 1.0), (-0.12, 0, 0.55)),
    ("R_Shin", "R_Thigh", (-0.12, 0, 0.55), (-0.12, 0, 0.12)),
]
for name, parent, head, tail in bones:
    b = edit.new(name)
    b.head = head
    b.tail = tail
    b.parent = edit[parent]
bpy.ops.object.mode_set(mode="OBJECT")
print("rig", arm_obj.name, "bones", len(arm.bones))
''',
    "parent_armature": _HEADER
    + '''
mesh = bpy.data.objects.get("{mesh}")
arm = bpy.data.objects.get("{armature}")
if mesh is None or arm is None:
    raise SystemExit("need mesh and armature names")
_ensure_object_mode()
bpy.ops.object.select_all(action="DESELECT")
mesh.select_set(True)
arm.select_set(True)
bpy.context.view_layer.objects.active = arm
bpy.ops.object.parent_set(type="ARMATURE_AUTO")
print("skinned", mesh.name, "→", arm.name)
''',
    "set_pivot": _HEADER
    + '''
NAME = "{name}"
MODE = "{mode}"
obj = bpy.data.objects.get(NAME) or bpy.context.active_object
if obj is None:
    raise SystemExit("no object")
_ensure_object_mode()
bpy.ops.object.select_all(action="DESELECT")
obj.select_set(True)
bpy.context.view_layer.objects.active = obj
if MODE == "geometry":
    bpy.ops.object.origin_set(type="ORIGIN_GEOMETRY", center="BOUNDS")
elif MODE == "cursor":
    bpy.ops.object.origin_set(type="ORIGIN_CURSOR")
else:
    bpy.ops.object.origin_set(type="ORIGIN_GEOMETRY", center="BOUNDS")
obj["daedalus_pivot"] = "{pivot}"
print("pivot", obj.name, MODE, obj.get("daedalus_pivot"))
''',
    "collision_hint": _HEADER
    + '''
NAME = "{name}"
HINT = "{hint}"
obj = bpy.data.objects.get(NAME) or bpy.context.active_object
if obj is None:
    raise SystemExit("no object")
obj["daedalus_collision"] = HINT
print("collision_hint", obj.name, HINT)
''',
    "render_preview": _HEADER
    + '''
OUT = "{out}"
ENGINE = "{engine}"
scene = bpy.context.scene
scene.render.engine = ENGINE
scene.render.filepath = OUT
scene.render.resolution_x = {res_x}
scene.render.resolution_y = {res_y}
if ENGINE == "CYCLES" and hasattr(scene, "cycles"):
    scene.cycles.samples = {samples}
if scene.camera is None:
    bpy.ops.object.camera_add(location=(6, -8, 5))
    cam = bpy.context.active_object
    cam.rotation_euler = (1.1, 0.0, 0.7)
    scene.camera = cam
bpy.ops.render.render(write_still=True)
print("rendered", OUT)
''',
    "export_glb": _HEADER
    + '''
OUT = "{out}"
_ensure_object_mode()
bpy.ops.export_scene.gltf(
    filepath=OUT,
    export_format="GLB",
    export_apply=True,
    use_selection=False,
    export_yup=True,
)
print("exported", OUT)
''',
    "export_fbx": _HEADER
    + '''
OUT = "{out}"
_ensure_object_mode()
bpy.ops.export_scene.fbx(filepath=OUT, use_selection=False, apply_scale_options="FBX_SCALE_ALL")
print("exported", OUT)
''',
    "batch_export": _HEADER
    + '''
import os
OUT_DIR = "{out_dir}"
os.makedirs(OUT_DIR, exist_ok=True)
_ensure_object_mode()
for obj in list(bpy.data.objects):
    if obj.type != "MESH":
        continue
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    path = os.path.join(OUT_DIR, obj.name + ".glb")
    bpy.ops.export_scene.gltf(
        filepath=path,
        export_format="GLB",
        use_selection=True,
        export_apply=True,
        export_yup=True,
    )
    print("exported", path)
''',
}

DEFAULTS: dict[str, dict[str, Any]] = {
    "clear_scene": {},
    "create_primitive": {"kind": "cube", "name": "HeroMesh"},
    "create_mesh_grid": {"name": "Ground", "subdiv": 8, "size": 4.0},
    "apply_material_pbr": {
        "name": "HeroMesh",
        "mat_name": "HeroPBR",
        "r": 0.2,
        "g": 0.6,
        "b": 0.85,
        "metallic": 0.2,
        "roughness": 0.4,
    },
    "add_camera_light": {},
    "add_light": {
        "kind": "SUN",
        "name": "KeyLight",
        "energy": 10.0,
        "r": 1.0,
        "g": 1.0,
        "b": 1.0,
        "x": 4.0,
        "y": 2.0,
        "z": 8.0,
    },
    "set_transform": {
        "name": "HeroMesh",
        "x": 0,
        "y": 0,
        "z": 0,
        "sx": 1,
        "sy": 1,
        "sz": 1,
    },
    "modify_mesh": {
        "name": "HeroMesh",
        "operation": "subdivide",
        "cuts": 1,
        "ex_x": 0.0,
        "ex_y": 0.0,
        "ex_z": 1.0,
        "offset": 0.1,
        "segments": 2,
        "thickness": 0.1,
        "distance": 0.001,
    },
    "add_modifier": {
        "name": "HeroMesh",
        "modifier_type": "SUBSURF",
        "levels": 2,
        "render_levels": 3,
        "mirror_x": 1,
        "mirror_y": 0,
        "mirror_z": 0,
        "count": 2,
        "ox": 1.0,
        "oy": 0.0,
        "oz": 0.0,
        "thickness": 0.01,
        "width": 0.1,
        "segments": 2,
        "bool_op": "UNION",
        "target": "",
    },
    "create_rig": {"name": "HeroArmature"},
    "parent_armature": {"mesh": "HeroMesh", "armature": "HeroArmature"},
    "set_pivot": {"name": "HeroMesh", "mode": "geometry", "pivot": "bottom_center"},
    "collision_hint": {"name": "HeroMesh", "hint": "box"},
    "render_preview": {
        "out": "studio/preview/blender.png",
        "engine": "CYCLES",
        "res_x": 1280,
        "res_y": 720,
        "samples": 32,
    },
    "export_glb": {"out": "assets/hero.glb"},
    "export_fbx": {"out": "assets/hero.fbx"},
    "batch_export": {"out_dir": "assets/glb"},
}


def tool_schema(job_id: str) -> dict[str, Any]:
    props = {
        key: {"type": "string" if not isinstance(val, (int, float)) else "number"}
        for key, val in DEFAULTS.get(job_id, {}).items()
    }
    props["execute"] = {
        "type": "boolean",
        "description": "If true and blender is on PATH, run blender -b --python",
    }
    return {
        "name": job_id,
        "description": f"Emit bpy script for {job_id}. Writes tools/blender/{job_id}.py",
        "inputSchema": {"type": "object", "properties": props, "additionalProperties": True},
    }


def list_tool_schemas() -> list[dict[str, Any]]:
    return [tool_schema(j) for j in JOB_IDS]


def render_script(job_id: str, params: dict[str, Any] | None = None) -> str:
    if job_id not in JOBS:
        raise KeyError(job_id)
    ctx = dict(DEFAULTS.get(job_id) or {})
    for k, v in (params or {}).items():
        if k == "execute":
            continue
        ctx[k] = v
    return JOBS[job_id].format(**ctx)


def emit_job(
    pdir: Path,
    job_id: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    script = render_script(job_id, params)
    rel = f"tools/blender/{job_id}.py"
    target = Path(pdir) / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(script, encoding="utf-8")
    blender = blender_bin()
    cmd = f"{blender} -b --python {rel}" if blender else f"blender -b --python {rel}"
    return {
        "ok": True,
        "job": job_id,
        "path": rel,
        "command": cmd,
        "blender": blender or "",
        "note": "Run on a machine with Blender. Cloud Daedalus only emits the script.",
    }


def blender_bin() -> str:
    env = (os.environ.get("BLENDER_BIN") or "").strip()
    if env and Path(env).exists():
        return env
    found = shutil.which("blender")
    return found or ""


def run_job(pdir: Path, job_id: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Emit + optionally execute. Never raises on missing Blender."""
    info = emit_job(pdir, job_id, params)
    want = bool((params or {}).get("execute"))
    binary = blender_bin()
    if not want:
        return info
    if not binary:
        # Execution was requested but could not happen: that is a skip, never a pass.
        info["ok"] = False
        info["skipped"] = True
        info["ran"] = False
        info["error"] = "Blender not on PATH; script written only"
        return info
    import subprocess

    proc = subprocess.run(
        [binary, "-b", "--python", str(Path(pdir) / info["path"])],
        cwd=str(pdir),
        capture_output=True,
        text=True,
        timeout=120,
    )
    info["ran"] = True
    info["returncode"] = proc.returncode
    info["stdout"] = (proc.stdout or "")[-2000:]
    info["stderr"] = (proc.stderr or "")[-1000:]
    info["ok"] = proc.returncode == 0
    return info


def mcp_call(job_id: str, arguments: dict[str, Any] | None = None, *, pdir: Path | None = None) -> dict[str, Any]:
    args = dict(arguments or {})
    root = pdir or Path(args.pop("project_dir", ".") or ".")
    if job_id not in JOB_IDS:
        return {"ok": False, "error": f"unknown blender job {job_id}"}
    return run_job(root, job_id, args)


def prompt_block() -> str:
    names = ", ".join(JOB_IDS)
    return (
        "━━━ BLENDER JOBS (emit bpy; do not fake GLB) ━━━\n"
        f"Jobs: {names}\n"
        "Write tools/blender/<job>.py via daedalus.blender_jobs.emit_job.\n"
        "Owner runs: blender -b --python tools/blender/export_glb.py\n"
        "Optional MCP: python -m daedalus.blender_mcp (stdio).\n"
        "Cloud: never assume bpy. Mixamo for humanoid clips."
    )
