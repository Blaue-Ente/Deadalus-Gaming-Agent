"""Kernel task graphs — what to call, in what order, against which docs.

These are not the RIPER NEED_PLAYBOOK texts in ``playbooks.py``. The kernel
matches a user ask to a playbook *before* the LLM talks, then exposes only
the tools on the graph and scores the run against required nodes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Step:
    id: str
    tool: str | None = None
    required: bool = False
    auto: bool = False
    args: dict[str, Any] = field(default_factory=dict)
    docs: tuple[str, ...] = ()
    why: str = ""
    depends_on: tuple[str, ...] = ()
    accept_any: tuple[str, ...] = ()


@dataclass(frozen=True)
class Playbook:
    id: str
    title: str
    priority: int
    kind: str = "static"
    keywords: tuple[str, ...] = ()
    regex: str = ""
    kinds: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    steps: tuple[Step, ...] = ()
    expected_files: tuple[str, ...] = ()
    notes: str = ""
    use_build_subset: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "priority": self.priority,
            "kind": self.kind,
            "tools": list(self.tools),
            "expected_files": list(self.expected_files),
            "notes": self.notes,
            "steps": [
                {
                    "id": s.id,
                    "tool": s.tool,
                    "required": s.required,
                    "auto": s.auto,
                    "why": s.why,
                    "docs": list(s.docs),
                    "depends_on": list(s.depends_on),
                }
                for s in self.steps
            ],
        }


_CORE = (
    "playground_new", "write_file", "read_file", "list_dir",
    "run_command", "skill",
)

PLAYBOOKS: tuple[Playbook, ...] = (
    Playbook(
        id="talk",
        title="Conversation — no tools",
        priority=100,
        keywords=(),
        tools=(),
        use_build_subset=False,
        notes="Greetings and opinions. The kernel skips native discovery.",
    ),
    Playbook(
        id="playground.math3d",
        title="Playground 3D math slice (SLERP / look-at / rays)",
        priority=90,
        kind="static",
        keywords=(
            "slerp", "quaternion", "quat", "look-at", "look_at", "lookat",
            "ray-triangle", "raycast", "ray-cast", "perspective",
            "камера", "camera", "projection", "матриц",
        ),
        regex=r"\b(slerp|quaternion|look[-_ ]?at|ray[-_ ]?(cast|triangle)|webgl|3d demo)\b",
        tools=_CORE + (
            "math3d", "game_canon", "spatial", "browser", "playtest", "assets",
        ),
        expected_files=("index.html",),
        notes=(
            "Call math3d with claimed numbers before writing shaders/JS. "
            "Study GLM structure; never paste books. Static preview — no dev server."
        ),
        steps=(
            Step(
                id="docs",
                docs=("knowledge/transform_math.md", "skills/transform_math.md"),
                auto=True,
                why="Harness conventions: quat xyzw, row-major, Y-up, OpenGL look.",
            ),
            Step(
                id="canon_glm",
                tool="game_canon",
                auto=True,
                args={"action": "study", "id": "glm"},
                why="Steal GLM-style named ops, not a home-grown vec class.",
                docs=("skills/game_canon.md",),
            ),
            Step(
                id="math",
                tool="math3d",
                required=True,
                depends_on=("docs",),
                why="Referee SLERP / look_at / rays with claimed before embedding the formula.",
                args={"op": "quat_slerp"},
            ),
            Step(
                id="create",
                tool="playground_new",
                depends_on=("math",),
                why="kind=static unless the owner named vite/godot.",
                args={"kind": "static"},
                accept_any=("playground_new", "write_file"),
            ),
            Step(
                id="write",
                tool="write_file",
                required=True,
                depends_on=("create",),
                why="WebGL/canvas camera in the Playground slug — not daedalus/agent.py.",
            ),
            Step(
                id="prove",
                tool="browser",
                depends_on=("write",),
                accept_any=("browser", "engine"),
                why="Kernel opens the running page after the last write. A compile is not proof.",
            ),
        ),
    ),
    Playbook(
        id="playground.crowd",
        title="Playground crowd / many-body collision",
        priority=88,
        kind="static",
        keywords=(
            "crowd", "broadphase", "octree", "quadtree", "bvh",
            "n^2", "n²", "много тел", "colliders", "entities",
            "custom physics", "spatial",
        ),
        regex=r"\b(crowd|octree|quadtree|bvh|broadphase|n\s*>\s*\d{2,}|100 .*(bodies|entities))\b",
        tools=_CORE + ("spatial", "game_canon", "math3d", "browser", "playtest"),
        expected_files=("index.html",),
        notes="spatial(advise) before any nested collision loop. Grid/quad/octree/BVH.",
        steps=(
            Step(
                id="docs",
                docs=("skills/spatial_simd.md", "knowledge/data_oriented_design.md"),
                auto=True,
                why="No O(N²) broadphase past a handful of actors.",
            ),
            Step(
                id="advise",
                tool="spatial",
                required=True,
                auto=True,
                args={"action": "advise"},
                depends_on=("docs",),
                why="Kernel runs advise with N inferred from the ask.",
            ),
            Step(
                id="create",
                tool="playground_new",
                accept_any=("playground_new", "write_file"),
                why="Static canvas/WebGL unless asked otherwise.",
            ),
            Step(
                id="write",
                tool="write_file",
                required=True,
                depends_on=("advise", "create"),
                why="Use the advised structure (grid/quad/octree/BVH), not nested for-loops.",
            ),
            Step(
                id="prove",
                tool="browser",
                depends_on=("write",),
                accept_any=("browser", "engine"),
                why="Kernel opens the running page. Nested-loop compile is not proof.",
            ),
        ),
    ),
    Playbook(
        id="playground.godot",
        title="Godot 4 game (GDScript + .tscn)",
        priority=85,
        kind="godot",
        kinds=("godot",),
        keywords=("godot", "gdscript", ".tscn", "метroid", "metroidvania"),
        regex=r"\b(godot|gdscript|\.tscn)\b",
        tools=_CORE + (
            "edit_file", "engine", "game_canon", "game_lint", "assets", "world", "browser",
        ),
        expected_files=("project.godot", "scenes/main.tscn"),
        notes=(
            "Engine adapter of the shared capability pipeline "
            "(plan → context → build → runtime → gameplay → visual → export → publish). "
            "Godot-first order: preflight → contract → context → slice → runtime → "
            "visual → review → export → publish. "
            "kind=godot. Scaffold already has project.godot. "
            "Patch with edit_file; do not rewrite whole .gd/.tscn each turn. "
            "engine({action: pipeline, slug}) walks the Godot order. "
            "production({action: pipeline, slug}) is the same kernel for web/mobile. "
            "Preview HTML is not Play. Missing Godot/export is FAIL, not skip. "
            "Vite/HTML are a different adapter — never a Godot fallback. "
            "No world/ campaign until this slice is green (unless epic)."
        ),
        steps=(
            Step(
                id="skill_godot",
                tool="skill",
                auto=True,
                args={"action": "load", "id": "godot"},
                docs=("skills/godot.md", "skills/godot_api.md", "skills/platform_acceptance_qa.md"),
                why="Godot 4.3 pin: CharacterBody2D, typed .connect, autoload Game. Live scene+input is Play.",
            ),
            Step(
                id="preflight",
                tool="engine",
                auto=True,
                args={"action": "preflight"},
                depends_on=("skill_godot",),
                why="Refuse the build when Godot 4.3 or headless runner is missing. Skip is fail.",
            ),
            Step(
                id="create",
                tool="playground_new",
                required=True,
                args={"kind": "godot"},
                accept_any=("playground_new", "write_file"),
                why="kind=godot. Do not emit Phaser/HTML gameplay.",
            ),
            Step(
                id="write",
                tool="write_file",
                required=True,
                depends_on=("create",),
                accept_any=("write_file", "edit_file"),
                why="scenes/*.tscn and scripts/*.gd — patch with edit_file; do not rewrite project.godot.",
            ),
            Step(
                id="engine",
                tool="engine",
                depends_on=("write",),
                accept_any=("engine", "visual_proof"),
                why="engine({action: pipeline, slug}) walks the order. Files alone are not Play.",
            ),
        ),
    ),
    Playbook(
        id="playground.unity",
        title="Unity C# scaffold",
        priority=84,
        kind="unity",
        kinds=("unity",),
        keywords=("unity", "c#", "csharp", "urp", "scriptableobject"),
        regex=r"\b(unity|urp|scriptableobject)\b",
        tools=_CORE + ("engine", "game_lint", "profiler", "game_canon", "git_forge"),
        notes=(
            "This cloud has no Editor. Scaffold + Git. "
            "engine needs DAEDALUS_UNITY_MCP or UNITY_PATH. Scaffold is not Play."
        ),
        steps=(
            Step(
                id="skill_unity",
                tool="skill",
                auto=True,
                args={"action": "load", "id": "unity_runtime"},
                docs=("skills/unity_runtime.md",),
                why="Local Hub / MCP. Do not vendor Unity.",
            ),
            Step(
                id="create",
                tool="playground_new",
                required=True,
                args={"kind": "unity"},
                accept_any=("playground_new", "write_file"),
            ),
            Step(
                id="write",
                tool="write_file",
                required=True,
                depends_on=("create",),
                why="C# under Assets/_Project. Data-driven; one system per file.",
            ),
        ),
    ),
    Playbook(
        id="playground.mobile",
        title="Mobile (Expo / Capacitor)",
        priority=82,
        kind="static",
        kinds=("expo",),
        keywords=(
            "expo", "react native", "capacitor", "ios", "iphone", "android",
            "app store", "play store", "eas",
        ),
        regex=(
            r"\b(expo|react[- ]native|capacitor|eas(?:\s+build)?|"
            r"ios|iphone|ipad|android|apk|aab|ipa|app store|play store)\b"
        ),
        tools=_CORE + ("production", "browser", "playtest", "skill", "assets"),
        notes=(
            "Mobile adapter of the shared pipeline: plan → context → build → "
            "runtime → gameplay → visual → export → publish. "
            "Expo/React Native for native UI. Capacitor wraps the same Vite/HTML "
            "canvas — do not rewrite to Expo. "
            "Emulator/device smoke is required. Expo web export is not a native pass. "
            "Godot on iOS stays EngineAdapter (editor-export), not Capacitor. "
            "production({action: pipeline, slug}) walks the order. Skip is fail."
        ),
        steps=(
            Step(
                id="skill_capability",
                tool="skill",
                auto=True,
                args={"action": "load", "id": "capability"},
                docs=("skills/capability.md", "skills/capacitor_ship.md", "skills/platform_acceptance_qa.md"),
                why="Shared stages; checks swap by target. Missing runner is FAIL. Device smoke required.",
            ),
            Step(
                id="create",
                tool="playground_new",
                required=True,
                accept_any=("playground_new", "write_file"),
                why="kind=expo only for React Native. Vite/static stay web + Capacitor wrap.",
            ),
            Step(
                id="write",
                tool="write_file",
                required=True,
                depends_on=("create",),
                why="RN views or the existing canvas. Never Godot→HTML or canvas→Expo rewrite.",
            ),
            Step(
                id="pipeline",
                tool="production",
                depends_on=("write",),
                args={"action": "pipeline"},
                accept_any=("production", "browser"),
                why="Device/emulator smoke. A web preview is not a mobile pass.",
            ),
        ),
    ),
    Playbook(
        id="playground.epic",
        title="Epic / world-kit campaign",
        priority=80,
        kind="vite",
        keywords=("epic", "campaign", "world kit", "quest", "dialogue", "ниво", "levels/"),
        regex=r"\b(epic|campaign|world kit|quest graph)\b",
        tools=_CORE + ("world", "assets", "production", "game_canon", "playtest"),
        notes="world({action: prove}) must pass. Empty JSON is not a campaign.",
        steps=(
            Step(
                id="docs",
                docs=("skills/world_kit.md", "skills/campaign.md"),
                auto=True,
                why="Save schema, quest engine, dialogue, gated level graph.",
            ),
            Step(
                id="create",
                tool="playground_new",
                accept_any=("playground_new", "write_file"),
            ),
            Step(
                id="write",
                tool="write_file",
                required=True,
                why="world/, characters/, levels/ — not one giant index.html.",
            ),
            Step(
                id="prove",
                tool="world",
                required=True,
                depends_on=("write",),
                args={"action": "prove"},
                why="world prove is the campaign DoD.",
            ),
        ),
    ),
    Playbook(
        id="playground.arcade",
        title="2D arcade (canvas / snake / tetris / pong)",
        priority=70,
        kind="static",
        keywords=(
            "snake", "змия", "tetris", "pong", "arkanoid",
            "canvas", "аркад", "ябълка",
        ),
        regex=r"\b(snake|tetris|pong|canvas|змия|аркад)\b",
        tools=_CORE + (
            "assets", "generate_image", "playtest", "browser", "game_canon", "production",
        ),
        expected_files=("index.html",),
        notes=(
            "Web adapter of the shared pipeline: plan → context → build → runtime → "
            "gameplay → visual → export → publish. Chromium is the runner. "
            "Playable loop: input → update → render → win/lose. "
            "Not Godot. Missing Chromium is FAIL, not skip. "
            "production({action: pipeline, slug}) walks the order. "
            "No math3d unless the ask names quaternions."
        ),
        steps=(
            Step(
                id="skill_qa",
                tool="skill",
                auto=True,
                args={"action": "load", "id": "platform_acceptance_qa"},
                docs=("skills/platform_acceptance_qa.md", "skills/capability.md"),
                why="Fail-closed platform matrix. Chromium gameplay. skipped is not pass.",
            ),
            Step(
                id="create",
                tool="playground_new",
                args={"kind": "static"},
                accept_any=("playground_new", "write_file"),
                why="kind=static HTML+canvas.",
            ),
            Step(
                id="write",
                tool="write_file",
                required=True,
                depends_on=("create",),
                why="index.html + optional script.js. Real assets/*.png if sprites exist.",
            ),
            Step(
                id="prove",
                tool="browser",
                depends_on=("write",),
                accept_any=("browser", "engine"),
                why="Kernel opens the canvas. Files on disk are not Play.",
            ),
        ),
    ),
    Playbook(
        id="playground.vite",
        title="Vite + Phaser (or Pixi) browser game",
        priority=65,
        kind="vite",
        kinds=("vite", "vite-react", "phaser"),
        keywords=(
            "kind=vite", "kind = vite", "vite-react", "vite + phaser",
            "phaser 3", "phaser3",
        ),
        regex=(
            r"kind\s*=\s*vite|vite-react|"
            r"(?:\bvite\b.{0,96}\bphaser\b)|(?:\bphaser\b.{0,96}\bvite\b)"
        ),
        tools=_CORE + (
            "assets", "generate_image", "playtest", "browser",
            "game_canon", "production", "game_lint",
        ),
        expected_files=("index.html", "src/main.js", "package.json"),
        notes=(
            "kind=vite. src/main.js must call new Phaser.Game (or a Pixi ticker). "
            "Never leave the Vite #app scaffold if index.html has a canvas or #game. "
            "generate_image is optional — after two failures, Boot canvas textures. "
            "production({action: pipeline, slug}) walks web QA. Chromium proof after writes."
        ),
        steps=(
            Step(
                id="skill_qa",
                tool="skill",
                auto=True,
                args={"action": "load", "id": "platform_acceptance_qa"},
                docs=("skills/phaser_vite.md", "skills/platform_acceptance_qa.md"),
                why="Entry contract + fail-closed Chromium QA. Scaffold main.js is a blank screen.",
            ),
            Step(
                id="create",
                tool="playground_new",
                args={"kind": "vite"},
                accept_any=("playground_new", "write_file"),
                why="kind=vite (Phaser npm), not static canvas.",
            ),
            Step(
                id="write",
                tool="write_file",
                required=True,
                depends_on=("create",),
                why=(
                    "Overwrite src/main.js with Phaser.Game + scenes. "
                    "index.html mount + package.json phaser. Not the Vite Hello counter."
                ),
            ),
            Step(
                id="prove",
                tool="browser",
                depends_on=("write",),
                accept_any=("browser", "engine"),
                why="Kernel opens dist/ or the Vite root. Files on disk are not Play.",
            ),
        ),
    ),
    Playbook(
        id="playground.generic",
        title="Playground game (generic)",
        priority=50,
        kind="static",
        keywords=("игра", "game", "playground", "phaser", "three.js", "webgpu", "demo"),
        regex=r"\b(игра|game|playground|phaser|three\.?js|webgpu)\b",
        tools=_CORE + (
            "math3d", "spatial", "game_canon", "assets", "generate_image",
            "playtest", "browser", "engine", "world", "game_lint",
        ),
        notes="Unknown game: keep the senior kit available, build in the Playground slug.",
        steps=(
            Step(
                id="create",
                tool="playground_new",
                accept_any=("playground_new", "write_file"),
            ),
            Step(
                id="write",
                tool="write_file",
                required=True,
            ),
            Step(
                id="prove",
                tool="browser",
                depends_on=("write",),
                accept_any=("browser", "engine"),
                why="Kernel opens the running game after the last write.",
            ),
        ),
    ),
    Playbook(
        id="harness.fix",
        title="Harness patch (propose, do not Playground-write)",
        priority=60,
        kind="",
        keywords=("daedalus/agent", "harness", "поправи дедал", "kernel"),
        regex=r"\b(daedalus/(agent|kernel|native_tools)|harness patch)\b",
        tools=("read_file", "list_dir", "code_search", "run_command", "skill", "spawn_subagent"),
        use_build_subset=True,
        notes="NEED_PROPOSE / Approve. Never write_file into daedalus/.",
        steps=(
            Step(
                id="read",
                tool="read_file",
                required=True,
                why="Read the target module before proposing.",
            ),
        ),
    ),
    Playbook(
        id="app.build",
        title="App / recon (full native catalog)",
        priority=10,
        tools=(),
        use_build_subset=False,
        notes="Non-game coding. Full native tool list. NEED_* fallback still works.",
        steps=(
            Step(
                id="recon",
                tool="read_file",
                why="Gather context, then write or propose.",
            ),
        ),
    ),
)


def get(playbook_id: str) -> Playbook | None:
    pid = (playbook_id or "").strip()
    for pb in PLAYBOOKS:
        if pb.id == pid:
            return pb
    return None


def all_playbooks() -> list[Playbook]:
    return list(PLAYBOOKS)
