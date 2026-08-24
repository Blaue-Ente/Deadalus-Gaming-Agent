"""Godot file helpers — the five CrewAI 'tools' as plain functions.

Scene Creator, Script Writer, Project Configurator, Build Exporter, Signal Manager.
No CrewAI. Callers write into a Playground slug.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


def script_template(
    *,
    class_name: str = "",
    extends: str = "Node",
    docstring: str = "",
    body: str = "",
) -> str:
    header = f"class_name {class_name}\n" if class_name else ""
    doc = f'"""{docstring}"""\n\n' if docstring else ""
    inner = body.strip() or (
        "func _ready() -> void:\n"
        "\tpass\n"
    )
    return f"{header}extends {extends}\n\n{doc}{inner.rstrip()}\n"


def signal_connect_snippet(
    emitter: str,
    signal: str,
    target: str,
    method: str,
) -> str:
    """GDScript 4 connect — one line, no deprecated string API."""
    return f"{emitter}.{signal}.connect({target}.{method})"


def scene_tscn(
    *,
    root_name: str = "Main",
    root_type: str = "Node2D",
    script_path: str = "",
    extra_nodes: str = "",
) -> str:
    lines = ["[gd_scene format=3]"]
    if script_path:
        lines.append("")
        lines.append(f'[ext_resource type="Script" path="{script_path}" id="1_script"]')
    lines.append("")
    lines.append(f'[node name="{root_name}" type="{root_type}"]')
    if script_path:
        lines.append('script = ExtResource("1_script")')
    if extra_nodes:
        lines.append("")
        lines.append(extra_nodes.rstrip())
    lines.append("")
    return "\n".join(lines)


def glb_instance_tscn(
    *,
    root_name: str = "Hero",
    glb_path: str = "res://assets/hero.glb",
    script_path: str = "",
) -> str:
    """Node3D wrapper that instances an imported GLB PackedScene.

    Godot imports ``.glb`` as a scene. Instance it — do not dump vertices into
    ``.tscn``. Path convention is ``res://assets/*.glb`` (not ``exports/``).
    """
    ext = 1 + (1 if script_path else 0)
    lines = [f"[gd_scene load_steps={ext + 1} format=3]", ""]
    lines.append(f'[ext_resource type="PackedScene" path="{glb_path}" id="1_glb"]')
    if script_path:
        lines.append(f'[ext_resource type="Script" path="{script_path}" id="2_script"]')
    lines.append("")
    lines.append(f'[node name="{root_name}" type="Node3D"]')
    if script_path:
        lines.append('script = ExtResource("2_script")')
    lines.append("")
    lines.append('[node name="Mesh" parent="." instance=ExtResource("1_glb")]')
    lines.append("")
    return "\n".join(lines)


def export_command(*, preset: str = "Web", path: str = ".") -> str:
    """Documented CLI. Cloud agents do not assume `godot` exists."""
    return (
        f"godot --headless --path {path} --export-release {preset} build/export "
        f"# only if Godot 4.3+ is installed locally"
    )


def patch_project_name(text: str, name: str) -> str:
    return text.replace("config/name=", f'config/name="{name}"', 1) if "config/name=" in text else text


def write_files(pdir: Path, files: dict[str, str], *, overwrite: bool = False) -> list[str]:
    written: list[str] = []
    for rel, content in files.items():
        target = Path(pdir) / rel
        if target.exists() and not overwrite:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(rel)
    return written


def crew_tool_catalog() -> list[dict[str, Any]]:
    """For GDD / planner — names match the described CrewAI tools."""
    return [
        {"id": "scene_creator", "writes": ".tscn", "fn": "godot_tools.scene_tscn"},
        {"id": "script_writer", "writes": ".gd", "fn": "godot_tools.script_template"},
        {"id": "project_configurator", "writes": "project.godot fields", "fn": "godot_tools.patch_project_name"},
        {"id": "build_exporter", "writes": "CLI hint", "fn": "godot_tools.export_command"},
        {"id": "signal_manager", "writes": "GDScript connect()", "fn": "godot_tools.signal_connect_snippet"},
        {"id": "glb_instance", "writes": "Node3D .tscn wrapping GLB", "fn": "godot_tools.glb_instance_tscn"},
    ]
