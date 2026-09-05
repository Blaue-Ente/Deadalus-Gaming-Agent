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
    "playground_new", "write_file", "edit_file", "read_file", "list_dir",
    "run_command", "skill", "ask_mentor",
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
            "engine", "game_canon", "game_lint", "assets", "world", "browser",
        ),
        expected_files=("project.godot", "scenes/main.tscn"),
        notes=(
            "Scaffold already has project.godot. One .tscn/.gd per slice. "
            "engine run is Play when GODOT_BIN exists. Preview HTML is not Play."
        ),
        steps=(
            Step(
                id="skill_godot",
                tool="skill",
                auto=True,
                args={"action": "load", "id": "godot"},
                docs=("skills/godot.md", "skills/godot_api.md"),
                why="Godot 4.3 pin: CharacterBody2D, typed .connect, autoload Game.",
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
                why="scenes/*.tscn and scripts/*.gd — do not rewrite project.godot every turn.",
            ),
            Step(
                id="engine",
                tool="engine",
                depends_on=("write",),
                why="engine({action: run|inspect, slug}) when GODOT_BIN exists. Files alone are not Play.",
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
        regex=r"\b(snake|tetris|pong|canvas|змия|аркад|2d|2-d)\b",
        tools=_CORE + (
            "assets", "generate_image", "playtest", "browser", "game_canon",
        ),
        expected_files=("index.html",),
        notes=(
            "Playable loop: input → update → render → win/lose. "
            "No math3d unless the ask names quaternions. Static preview — no RUN."
        ),
        steps=(
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
