"""Studio shelf — tools Daedalus should reach for per game subsystem.

A complex game is not one LLM. FM1 (and the r/ClaudeCode write-up) succeeded by
chaining specialist tools: planning model, image gen, image-to-3D, stock SFX,
procedural code, browser verify.

This catalog is advice + routing hints. It does not call paid APIs by itself.
Callers inject ``prompt_block(description)`` into planner / GDD.
"""
from __future__ import annotations

from typing import Any

# Layers match how a senior would staff a game, not vendor marketing.
SHELF: tuple[dict[str, Any], ...] = (
    {
        "id": "three-webgpu-tsl",
        "layer": "engine",
        "label": "Three.js WebGPU + TSL",
        "use": "Browser 3D arcade (FM1's actual renderer). Custom materials, shadows, post.",
        "kind_hint": "vite",
        "url": "https://threejs.org/manual/#en/webgpu",
        "api": False,
        "daedalus": "vite + three WebGPURenderer; keep a WebGL fallback path if device is missing.",
    },
    {
        "id": "rapier3d",
        "layer": "physics",
        "label": "Rapier 3D (WASM)",
        "use": "Rigid bodies when you need real collisions. FM1 skipped this — arcade math instead.",
        "kind_hint": "vite",
        "url": "https://rapier.rs/",
        "api": False,
        "daedalus": "Prefer custom arcade for hover racers; Rapier for crates/world toys.",
    },
    {
        "id": "spline-track",
        "layer": "tracks",
        "label": "Catmull-Rom / tubular spline track",
        "use": "Race lines, banked turns, tubes (Órbita). Code, not a downloaded Unity track.",
        "kind_hint": "vite",
        "url": "https://threejs.org/docs/#api/en/extras/curves/CatmullRomCurve3",
        "api": False,
        "daedalus": "Generate mesh from a spline + width. Chevrons/waypoints as children along t.",
    },
    {
        "id": "procedural-city",
        "layer": "environment",
        "label": "Procedural architecture",
        "use": "Crisp buildings at speed. FM1 Mist City / Aaru pyramids. Photogrammetry looks mushy.",
        "kind_hint": "vite",
        "url": "",
        "api": False,
        "daedalus": "Instanced boxes + facade UV + emissive windows. Do not GLB every tower.",
    },
    {
        "id": "fft-ocean",
        "layer": "environment",
        "label": "FFT ocean (WebGPU/TSL)",
        "use": "Water levels (FM1 Aqua credits: FFT-Ocean + Poseidon TSL).",
        "kind_hint": "vite",
        "url": "https://github.com/gasgiant/FFT-Ocean",
        "api": False,
        "daedalus": "Port techniques, do not vendor AGPL. MIT references only.",
    },
    {
        "id": "tripo3d",
        "layer": "hero_mesh",
        "label": "Tripo3D",
        "use": "Image/text → game-ready GLB. OP's main ship pipeline.",
        "kind_hint": "vite",
        "url": "https://www.tripo3d.ai/",
        "api": True,
        "daedalus": "Need owner key. Input: concept image, not a blank prompt. Remesh for web.",
    },
    {
        "id": "meshy",
        "layer": "hero_mesh",
        "label": "Meshy",
        "use": "Text/image → 3D + auto-rig/animation when a character must move.",
        "kind_hint": "vite",
        "url": "https://www.meshy.ai/",
        "api": True,
        "daedalus": "Rigs for characters; hover ships usually stay unskinned.",
    },
    {
        "id": "hunyuan3d-fal",
        "layer": "hero_mesh",
        "label": "Hunyuan3D via fal.ai",
        "use": "Pay-per-call text/image-to-3D GLB with optional PBR. Easy to automate.",
        "kind_hint": "vite",
        "url": "https://fal.ai/models/fal-ai/hunyuan3d-v3/text-to-3d",
        "api": True,
        "daedalus": "If FAL_KEY exists later, generate GLB into assets/. Until then: tell owner the hand-off.",
    },
    {
        "id": "trellis-fal",
        "layer": "hero_mesh",
        "label": "Trellis (fal.ai)",
        "use": "Cheap image-to-3D drafts before committing a hero mesh.",
        "kind_hint": "vite",
        "url": "https://fal.ai/models/fal-ai/trellis",
        "api": True,
        "daedalus": "Draft silhouette first ($ cheap), then Hunyuan/Tripo for production.",
    },
    {
        "id": "image-gen",
        "layer": "concept",
        "label": "Image gen (Flux / GPT image / Novita / OpenRouter)",
        "use": "Turn a sketch or brief into orthographic concept art for image-to-3D.",
        "kind_hint": "any",
        "url": "",
        "api": True,
        "daedalus": "Native generate_image already exists. Use it as step 2 of sketch→2D→3D.",
    },
    {
        "id": "polyhaven",
        "layer": "textures",
        "label": "Polyhaven (CC0 HDRI + PBR)",
        "use": "Environment lighting and material sets that look designed.",
        "kind_hint": "vite",
        "url": "https://polyhaven.com/",
        "api": True,
        "daedalus": "Download CC0 HDRI/PBR into assets/. Do not hotlink at runtime.",
    },
    {
        "id": "kenney",
        "layer": "kit",
        "label": "Kenney CC0 kits",
        "use": "Placeholder-to-final for prototypes when AI meshes are overkill.",
        "kind_hint": "any",
        "url": "https://kenney.nl/",
        "api": False,
        "daedalus": "Fine for greybox; replace for FM1-grade hero art.",
    },
    {
        "id": "gltfpack",
        "layer": "optimize",
        "label": "gltfpack / Draco",
        "use": "Compress GLB for web load (FM1's Reddit thread: load time on fiber still hurt).",
        "kind_hint": "vite",
        "url": "https://github.com/zeux/meshoptimizer",
        "api": False,
        "daedalus": "Every imported GLB goes through size budget before ship.",
    },
    {
        "id": "howler",
        "layer": "audio",
        "label": "howler.js",
        "use": "Reliable SFX/music bus. FM1 used raw Web Audio oscillators for the engine.",
        "kind_hint": "vite",
        "url": "https://howlerjs.com/",
        "api": False,
        "daedalus": "Layer 2–3 one-shots for boost. Spatial optional.",
    },
    {
        "id": "suno",
        "layer": "music",
        "label": "Suno",
        "use": "BGM from a hummed/piano motif. OP's path. Instrumental in-race.",
        "kind_hint": "any",
        "url": "https://suno.com/",
        "api": True,
        "daedalus": "Owner generates; Daedalus wires <audio>/Howler. No fake lyrics over gameplay.",
    },
    {
        "id": "elevenlabs",
        "layer": "voice",
        "label": "ElevenLabs",
        "use": "Announcer / rival barks. Keep lines short.",
        "kind_hint": "any",
        "url": "https://elevenlabs.io/",
        "api": True,
        "daedalus": "Optional. Accessibility: captions + mono mix (FM1 has both).",
    },
    {
        "id": "mixamo",
        "layer": "animation",
        "label": "Mixamo",
        "use": "Humanoid clips if Meshy rig isn't enough.",
        "kind_hint": "unity",
        "url": "https://www.mixamo.com/",
        "api": False,
        "daedalus": "Not for hover ships. Use for characters only.",
    },
    {
        "id": "blender",
        "layer": "dcc",
        "label": "Blender (+ LLM script)",
        "use": "OP hooked a strong model to Blender for cleanup. Hero retopo, UVs.",
        "kind_hint": "any",
        "url": "https://www.blender.org/",
        "api": False,
        "daedalus": "Local-only. Emit Python bpy scripts; never assume Blender in the cloud.",
    },
    {
        "id": "playwright-verify",
        "layer": "verify",
        "label": "Playwright / Chromium proof",
        "use": "Render-and-see. Testing is the bottleneck the Reddit thread named.",
        "kind_hint": "any",
        "url": "",
        "api": False,
        "daedalus": "Already in playground_browser + playtest. Do not skip.",
    },
)


_LAYER_TRIGGERS: dict[str, tuple[str, ...]] = {
    "engine": ("3d", "three", "webgpu", "racer", "racing", "f-zero", "wipeout"),
    "physics": ("physics", "collision", "rigid"),
    "tracks": ("track", "circuit", "lap", "race", "spline"),
    "environment": ("city", "ocean", "orbit", "building", "volumetric"),
    "hero_mesh": ("ship", "vehicle", "character", "3d", "mesh", "gltf"),
    "concept": ("concept", "sketch", "art", "style"),
    "textures": ("pbr", "texture", "hdri", "material"),
    "kit": ("placeholder", "prototype", "greybox"),
    "optimize": ("load", "mobile", "compress", "glb"),
    "audio": ("sfx", "sound", "audio", "engine"),
    "music": ("music", "bgm", "suno"),
    "voice": ("voice", "announcer", "eleven"),
    "animation": ("rig", "walk", "mixamo"),
    "dcc": ("blender", "retopo"),
    "verify": ("playtest", "qa", "verify"),
}


def list_layers() -> list[str]:
    seen: list[str] = []
    for row in SHELF:
        layer = str(row["layer"])
        if layer not in seen:
            seen.append(layer)
    return seen


def tools_for(*layers: str, query: str = "") -> list[dict[str, Any]]:
    want = {str(x).strip().lower() for x in layers if x}
    q = (query or "").lower()
    extra: set[str] = set()
    if q:
        for layer, keys in _LAYER_TRIGGERS.items():
            if any(k in q for k in keys):
                extra.add(layer)
    if not want and not extra:
        return []
    out: list[dict[str, Any]] = []
    for row in SHELF:
        layer = str(row["layer"])
        if want and layer not in want and layer not in extra:
            continue
        if not want and extra and layer not in extra:
            continue
        out.append(dict(row))
    return out


def prompt_block(description: str = "", *, limit: int = 8) -> str:
    rows = tools_for(query=description)
    if not rows:
        rows = [r for r in SHELF if r["layer"] in ("concept", "audio", "verify", "kit")]
    rows = rows[:limit]
    lines = [
        "━━━ STUDIO SHELF (pick tools; do not fake their output) ━━━",
        "LLM writes code and procedural content. Specialist tools make art/audio/meshes.",
    ]
    for row in rows:
        api = "API" if row.get("api") else "lib"
        lines.append(f"- [{row['layer']}/{api}] {row['label']}: {row['use']} → {row['daedalus']}")
    return "\n".join(lines)


def fm1_case_block() -> str:
    from .arcade_craft import FM1_STACK, FIELD_URL, FIELD_VERSION

    lines = [
        f"━━━ FIELD STUDY: Formula Minus One {FIELD_VERSION} ({FIELD_URL}) ━━━",
    ]
    for k, v in FM1_STACK.items():
        lines.append(f"- {k}: {v}")
    return "\n".join(lines)
