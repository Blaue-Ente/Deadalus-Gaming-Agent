"""Native Anthropic/OpenAI tool API for Daedalus.

Files, shell, search, fetch, image gen, and playground writes as structured
tool calls. ``NEED_*`` markers remain the fallback path in ``agent.py``.

Enabled by default. Set ``DAEDALUS_NATIVE_TOOLS=0`` to disable.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Awaitable, Callable

log = logging.getLogger(__name__)

PERM_MAP: dict[str, str | None] = {
    "read_file": None,
    "list_dir": None,
    "web_search": "web_search",
    "web_fetch": "web_search",
    "web_read": "web_search",
    "reach_status": None,
    "postgres_query": "postgres_query",
    "code_search": None,
    "list_endpoints": None,
    "list_modules": None,
    "run_command": None,
    "write_file": None,
    "edit_file": None,
    "playground_new": None,
    "generate_image": None,
    "remember": None,
    "browser": None,
    "mcp_call": None,
    "spawn_subagent": None,
    "playtest": None,
    "mirofish": None,
    "play_lab": None,
    "dissect_game": None,
    "engine": None,
    "world": None,
    "assets": None,
    "skill": None,
    "production": None,
    "git_forge": "github_access",
    "math3d": None,
    "spatial": None,
    "game_lint": None,
    "profiler": None,
    "game_canon": None,
}

NATIVE_TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "read_file",
        "description": "Read a source file (harness or playground). Optional line range.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "line_start": {"type": "integer"},
                "line_end": {"type": "integer"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "list_dir",
        "description": "List files in a directory inside the Daedalus jail.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative directory, default '.'"},
            },
        },
    },
    {
        "name": "write_file",
        "description": (
            "Write a file in a Playground project (slug required). "
            "Call playground_new first if the slug does not exist — "
            "otherwise a static project is created (Godot paths create kind=godot). "
            "For existing .gd/.tscn prefer edit_file. "
            "Do not use this to patch the Daedalus harness — propose instead."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string"},
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["slug", "path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": (
            "Surgical search/replace in one Playground file. "
            "search must match exactly once. Prefer this over rewriting a whole "
            ".gd/.tscn. write_file is for new files."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string"},
                "path": {"type": "string"},
                "search": {"type": "string"},
                "replace": {"type": "string"},
            },
            "required": ["slug", "path", "search", "replace"],
        },
    },
    {
        "name": "playground_new",
        "description": (
            "Create a Playground project and return its slug. "
            "kind=static for HTML/canvas; vite / vite-react / godot / unity / expo when asked. "
            "Godot words or kind=godot never become Vite/HTML. "
            "Then write_file / edit_file with that slug. Do not map the Daedalus harness."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "kind": {"type": "string", "description": "static | vite | vite-react | godot | unity | expo"},
                "description": {"type": "string"},
                "slug": {"type": "string"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "run_command",
        "description": (
            "Run a command in the playground jail as argv (not a shell). "
            "pytest, npm install, npm run build, ls, git status. "
            "No pipes, &&, or 2>&1 — call npm install with slug= as one command, "
            "then npm run build as another. Secrets are stripped from env."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "cwd": {"type": "string"},
                "slug": {"type": "string", "description": "Playground project slug as cwd"},
                "timeout": {"type": "integer"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "web_search",
        "description": "Search the web for documentation, APIs, or examples.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "web_fetch",
        "description": "Fetch a URL and return cleaned text (docs, README, API reference).",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "max_chars": {"type": "integer"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "generate_image",
        "description": (
            "Generate a game/app asset image and save it under the slug's assets/ folder. "
            "filename is the name inside assets/ (keeper.png or sfx/icon.png) — do not prefix assets/."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "slug": {"type": "string"},
                "filename": {
                    "type": "string",
                    "description": "Name under assets/ (keeper.png). Do not prefix assets/.",
                },
                "size": {"type": "string", "description": "e.g. 512x512"},
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "remember",
        "description": "Store an owner preference forever (language, stack, style, name).",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "kind": {"type": "string"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "code_search",
        "description": "Semantic + keyword search across the codebase.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "postgres_query",
        "description": "Read-only SQL (SQLite standalone, Postgres when hosted by ADN).",
        "input_schema": {
            "type": "object",
            "properties": {"sql": {"type": "string"}},
            "required": ["sql"],
        },
    },
    {
        "name": "list_endpoints",
        "description": "List HTTP routes matching a filter.",
        "input_schema": {
            "type": "object",
            "properties": {"filter": {"type": "string", "default": "*"}},
        },
    },
    {
        "name": "list_modules",
        "description": "List Python modules in the Daedalus product.",
        "input_schema": {
            "type": "object",
            "properties": {"filter": {"type": "string", "default": "*"}},
        },
    },
    {
        "name": "web_read",
        "description": (
            "Read a URL as clean markdown via Jina Reader (Agent Reach web channel). "
            "Use instead of web_fetch when you need readable article text."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "max_chars": {"type": "integer"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "reach_status",
        "description": "Probe internet backends (Jina, yt-dlp, gh, agent-reach doctor).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "browser",
        "description": (
            "Open a page in Chromium: goto, click, type, extract, screenshot. "
            "For a Playground game pass slug (or a playground/projects/… path) — "
            "the kernel opens the running slice. Do not invent file:// or localhost URLs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "goto | click | type | extract | screenshot | close",
                },
                "url": {"type": "string"},
                "selector": {"type": "string"},
                "text": {"type": "string"},
                "slug": {"type": "string", "description": "Playground slug for screenshots"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "mcp_call",
        "description": "List or call a tool on an optional MCP server from $DAEDALUS_DATA/mcp.json.",
        "input_schema": {
            "type": "object",
            "properties": {
                "server": {"type": "string"},
                "tool": {"type": "string"},
                "arguments": {"type": "object"},
                "list_tools": {"type": "boolean"},
            },
        },
    },
    {
        "name": "spawn_subagent",
        "description": (
            "Run an isolated sub-agent with a fresh context window, jailed to a playground slug. "
            "Roles: planner (read-only contract), builder/coder (version-checked patches), "
            "specialist (adapter files), verifier (prove, do not write), "
            "polish (only after a visual-quality failure). "
            "No parallel writers on the same file. Pass files[] to claim exclusive paths. "
            "Pass return_contexts so the sub-agent returns named artifacts; "
            "pass context_refs to inject prior artifacts."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string"},
                "task": {"type": "string"},
                "max_rounds": {"type": "integer"},
                "role": {
                    "type": "string",
                    "description": (
                        "planner | explorer | builder | coder | specialist | "
                        "verifier | verify | polish"
                    ),
                },
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Exclusive write paths. Two writers cannot share a file.",
                },
                "job_id": {"type": "string"},
                "original_ask": {"type": "string"},
                "task_id": {"type": "string"},
                "return_contexts": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "context_refs": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "context_bootstrap": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "reason": {"type": "string"},
                        },
                    },
                },
            },
            "required": ["slug", "task"],
        },
    },
    {
        "name": "playtest",
        "description": (
            "MatrAIx-style simulated players critique a Playground game "
            "(loop, controls, first 30s, win/lose)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string"},
                "kind": {"type": "string"},
                "n": {"type": "integer"},
                "use_browser": {"type": "boolean"},
            },
            "required": ["slug"],
        },
    },
    {
        "name": "mirofish",
        "description": (
            "Talk to an optional MiroFish-Offline sidecar (AGPL, separate process). "
            "Ping, list projects, or submit GDD/press-release text."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "ping | list | submit"},
                "text": {"type": "string"},
                "requirement": {"type": "string"},
                "project_name": {"type": "string"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "play_lab",
        "description": (
            "Daedalus virtual computer for specimen games and to watch what it builds. "
            "Create/list/install/start/stop a Play Lab (jailed HTML5/git/zip). "
            "Local HTTP always works. Optional SmolVM guest browser with a live viewer "
            "(pip install smolvm). Docker optional. Not Steam/.exe/Windows."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "status | create | list | install | start | stop | destroy | open | watch",
                },
                "title": {"type": "string"},
                "source": {"type": "string"},
                "lab_id": {"type": "string"},
                "path": {"type": "string", "description": "Folder, HTML, or zip to install"},
                "slug": {"type": "string", "description": "Playground project to open as a computer"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "git_forge",
        "description": (
            "GitHub/GitLab for playground games: clone, push, open a Pull Request "
            "or Merge Request. Local files stay the source of truth."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "status | clone | push | pull_request | merge_request",
                },
                "url": {"type": "string", "description": "Git remote URL for clone"},
                "slug": {"type": "string"},
                "provider": {
                    "type": "string",
                    "description": "auto | github | gitlab",
                },
                "repo": {"type": "string", "description": "owner/repo or group/project"},
                "title": {"type": "string"},
                "body": {"type": "string"},
                "head": {"type": "string", "description": "source branch"},
                "base": {"type": "string", "description": "target branch, default main"},
                "name": {"type": "string", "description": "project name on clone"},
                "commit_msg": {"type": "string"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "dissect_game",
        "description": (
            "Dissect a reference game (name, URL, git, path, or existing lab) into a specimen "
            "used as a мостра when building the owner's game. Inspire loop/feel/stack; never clone assets."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "Game name, URL, git, or path"},
                "slug": {"type": "string", "description": "Playground project to attach the specimen to"},
                "lab_id": {"type": "string"},
                "path": {"type": "string"},
                "create_lab": {"type": "boolean"},
                "live": {"type": "boolean", "description": "Chromium probe when possible"},
                "description": {"type": "string", "description": "What the owner wants to build"},
            },
        },
    },
    {
        "name": "engine",
        "description": (
            "Drive a running Godot or Unity project, or the EngineAdapter pipeline. "
            "Godot is the engine adapter of the shared capability pipeline "
            "(plan → context → build → runtime → gameplay → visual → export → publish). "
            "Godot: headless TCP bridge; optional DAEDALUS_GODOT_MCP. "
            "Pipeline (no live session): preflight | contract | context | slice | "
            "pipeline | review | export | publish | runtime_dod. "
            "Vite/HTML/Phaser and Expo/Capacitor use production({action: pipeline}), not this tool. "
            "Live: run | inspect | input | screenshot | tree | errors | fps | wait | quit | status. "
            "Unity: DAEDALUS_UNITY_MCP for a local Editor, else UNITY_PATH batchmode "
            "(import is not Play). Without either: honest refuse — we do not vendor Unity. "
            "Files are not Play. Missing Godot/export is FAIL, not skip."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": (
                        "pipeline: preflight | contract | context | slice | pipeline | "
                        "review | export | publish | runtime_dod. "
                        "live: run | inspect | input | screenshot | tree | errors | fps | wait | quit | set | call | status"
                    ),
                },
                "slug": {"type": "string"},
                "path": {"type": "string", "description": "Node path for inspect/set/call"},
                "prop": {"type": "string"},
                "value": {"description": "JSON property value for action=set"},
                "method": {"type": "string"},
                "args": {"type": "array"},
                "action_name": {"type": "string", "description": "InputMap action, e.g. ui_right"},
                "key": {"type": "string", "description": "Physical key W/A/S/D/Space"},
                "pressed": {"type": "boolean"},
                "frames": {"type": "integer"},
            },
            "required": ["action", "slug"],
        },
    },
    {
        "name": "world",
        "description": (
            "Campaign runtime: save, quests, dialogue, level graph. "
            "action: status | prove | talk | travel | script. "
            "Empty world/*.json is not a campaign."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "status | prove | talk | travel | script",
                },
                "slug": {"type": "string"},
                "who": {"type": "string", "description": "Dialogue id for talk"},
                "pick": {"description": "Choice index or substring"},
                "to": {"type": "string", "description": "Region id for travel"},
                "save": {"type": "object"},
                "steps": {"description": "JSON list of script steps"},
            },
            "required": ["action", "slug"],
        },
    },
    {
        "name": "assets",
        "description": (
            "Asset pipeline v2: atlas/tileset/sfx/font with no paid API; "
            "3D mesh jobs (fal/Tripo/Meshy) fail closed without keys — never a fake hero GLB. "
            "action: atlas | tileset | sfx | font | mesh | greybox | status | providers | music. "
            "Coder-only (writes files)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "atlas | tileset | sfx | font | mesh | greybox | status | providers | music",
                },
                "slug": {"type": "string"},
                "sources": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Existing assets/*.png to pack (atlas/tileset)",
                },
                "prompt": {"type": "string", "description": "Text-to-3D prompt for action=mesh"},
                "name": {"type": "string", "description": "Mesh/greybox stem, e.g. hero"},
                "provider": {"type": "string", "description": "auto | fal | tripo | meshy"},
                "job_id": {"type": "string", "description": "Poll a mesh job (action=status)"},
            },
            "required": ["action", "slug"],
        },
    },
    {
        "name": "skill",
        "description": (
            "Progressive skill memory. action catalog lists name+description; "
            "action load returns one approved SKILL.md body; action pending lists "
            "drafts waiting for owner Approve. The agent cannot approve or reject."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "catalog | load | pending",
                },
                "query": {"type": "string", "description": "Catalog search text"},
                "id": {"type": "string", "description": "Skill id for action=load"},
                "name": {"type": "string", "description": "Alias of id"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "production",
        "description": (
            "Production layer vs the prototype wall, plus the capability pipeline. "
            "action: status | prove | seed | runtime | map | promote | pipeline | qa. "
            "pipeline walks plan → context → build → runtime → gameplay → visual → "
            "export → publish on the adapter for the kind (Web / Engine / Mobile). "
            "qa returns a fail-closed release verdict bound to revision + artifact hash. "
            "Empty systems map is not a production layer. "
            "store-ready / готово за стора on a prototype fails. "
            "seed writes design/systems.json only — do not inject into the Vite racer M0. "
            "Skip is not a pass. Godot is not HTML. Expo web is not a device build."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "status | prove | seed | runtime | map | promote | pipeline | qa",
                },
                "slug": {"type": "string"},
                "claim": {
                    "type": "string",
                    "description": "Owner/GDD text for prove (store-ready language)",
                },
                "systems": {
                    "description": "System ids or objects for seed/map",
                },
                "layer": {"type": "string", "description": "prototype | production"},
                "build_mode": {"type": "string"},
                "through": {
                    "type": "string",
                    "description": "pipeline stop: plan|context|build|runtime|gameplay|visual|export|publish",
                },
                "target": {
                    "type": "string",
                    "description": "web | engine | mobile (optional; inferred from kind)",
                },
                "kind": {"type": "string"},
                "text": {"type": "string", "description": "Owner ask — used to pick Capacitor vs Expo"},
                "plan": {"type": "string"},
            },
            "required": ["action", "slug"],
        },
    },
    {
        "name": "math3d",
        "description": (
            "Reference 3D math (stdlib; numpy cross-check if installed). "
            "Ops: dot, cross, facing, triangle_normal, quat_slerp, quat_rotate, "
            "look_at, perspective, ray_triangle, ray_aabb, mat4_mul, catalog. "
            "Pass claimed to verify a formula before embedding it in C++/C#/GDScript. "
            "Quat xyzw, mat4 row-major, OpenGL Y-up."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "Op name or catalog | batch",
                },
                "op": {"type": "string", "description": "Alias of action"},
                "a": {"description": "vec3 / quat / mat4"},
                "b": {"description": "vec3 / quat / mat4"},
                "q0": {"description": "quat xyzw"},
                "q1": {"description": "quat xyzw"},
                "t": {"type": "number"},
                "eye": {"description": "look_at eye vec3"},
                "center": {"description": "look_at target vec3"},
                "up": {"description": "look_at up vec3"},
                "origin": {"description": "ray origin"},
                "direction": {"description": "ray direction"},
                "v0": {"description": "triangle p0"},
                "v1": {"description": "triangle p1"},
                "v2": {"description": "triangle p2"},
                "claimed": {"description": "Agent's result to verify"},
                "ops": {"description": "batch: list of {op,...}"},
            },
        },
    },
    {
        "name": "spatial",
        "description": (
            "Spatial partitioning advisor. action=advise picks brute/grid/quadtree/octree/BVH "
            "from N and dim so the agent does not ship O(N²) collision. action=aabb tests overlap. "
            "action=scan flags nested for+distance in a Playground slug."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "advise | aabb | scan | catalog",
                },
                "n": {"description": "Entity count for advise"},
                "count": {"description": "Alias of n"},
                "dim": {"description": "2 or 3"},
                "moving": {"type": "boolean"},
                "rays": {"type": "boolean"},
                "a_min": {"description": "AABB min vec3"},
                "a_max": {"description": "AABB max vec3"},
                "b_min": {"description": "AABB min vec3"},
                "b_max": {"description": "AABB max vec3"},
                "slug": {"type": "string"},
                "text": {"type": "string", "description": "Inline source for scan"},
                "path": {"type": "string"},
            },
        },
    },
    {
        "name": "game_lint",
        "description": (
            "Architectural lint for game code. Python ruff; C++ clang-format/tidy "
            "when installed plus leak heuristics; C# Unity heuristics and dotnet format; "
            "GDScript Godot 3 API smells. action=format returns text and does not write. "
            "Missing clang-tidy is not a leak pass."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "status | check | format",
                },
                "slug": {"type": "string"},
                "path": {"type": "string"},
                "content": {"type": "string", "description": "Unsaved buffer to lint"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "profiler",
        "description": (
            "Parse Unity Test / NUnit XML, Chrome traces, Tracy CSV, Godot/Unity/generic FPS logs. "
            "kind=tracy for Tracy zone exports. action=unity_tests needs UNITY_PATH batchmode "
            "and is not Play. RenderDoc .rdc is owner-side — do not fake a capture here."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "status | parse | unity_tests",
                },
                "slug": {"type": "string"},
                "path": {"type": "string", "description": "Log/XML/trace path"},
                "kind": {"type": "string", "description": "auto | nunit | chrome | tracy | godot | unity | generic"},
                "text": {"type": "string", "description": "Inline log instead of path"},
                "platform": {"type": "string", "description": "EditMode | PlayMode for unity_tests"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "game_canon",
        "description": (
            "Study curated OSS game trees (DOOM 3 BFG, Mindustry, Game Programming Patterns, "
            "tinyrenderer, stb, GLM, DirectXMath, Tracy, Milo Yip path, engine-architecture samples, "
            "Chop Chop wiki). action=list|study|open|clone|search|chunk. "
            "Steal structure; never copy assets, books, or Chop Chop into a Playground game. "
            "game-program-books clone/open is refused. RenderDoc captures happen on the owner's machine."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "list | study | open | clone | search | chunk",
                },
                "id": {"type": "string", "description": "doom3-bfg | milo-path | unity-chop | engine-arch-samples | …"},
                "path": {"type": "string", "description": "Allowlisted file for open/chunk (wiki/… for Chop Chop wiki)"},
                "query": {"type": "string", "description": "Local grep after clone"},
                "limit": {"type": "integer", "description": "Max RAG chunks for action=chunk"},
            },
            "required": ["action"],
        },
    },
]


def native_tools_enabled() -> bool:
    flag = os.environ.get("DAEDALUS_NATIVE_TOOLS", "").strip().lower()
    if flag in ("0", "false", "no", "off"):
        return False
    return True


_PLAYGROUND_BUILD_NAMES = frozenset({
    "playground_new", "write_file", "edit_file", "read_file", "list_dir",
    "math3d", "spatial", "game_canon", "game_lint", "profiler",
    "assets", "generate_image", "playtest", "browser",
    "run_command", "skill", "engine", "world",
})

_HARNESS_JAIL = (
    "PLAYGROUND JAIL: do not map the Daedalus harness. "
    "Create/write the game with playground_new + write_file. "
    "Call math3d / spatial / game_canon when the slice needs them."
)

_PG_SLUG_PATH = re.compile(r"playground/projects/([A-Za-z0-9_-]+)", re.I)


def _slug_from_text(*blobs: str) -> str:
    for blob in blobs:
        m = _PG_SLUG_PATH.search(blob or "")
        if m:
            return m.group(1)
    return ""


def _nav_hint(*, slug: str = "", path: str = "", next_line: str = "") -> str:
    """S3: every Playground tool result names the slug path so the model stops hunting."""
    slug = (slug or "").strip() or _slug_from_text(path)
    loc = f"playground/projects/{slug}/" if slug else "playground/projects/{slug}/"
    lines = [
        f"Playground files live under {loc} Do not find /workspace or list the harness.",
    ]
    if slug:
        lines.append(f"Keep tools on slug={slug}.")
    if next_line:
        lines.append(next_line)
    return "\n" + "\n".join(lines)


def playground_build_specs(
    message: str = "",
    *,
    kind: str = "",
    intent: str = "",
    session_id: str = "",
) -> list[dict[str, Any]]:
    """Tools for Playground/game builds — no harness code_search archaeology.

    When ``message`` is set, the tool router narrows the subset to the
    matched playbook (snake does not get math3d; SLERP does).
    """
    if (message or "").strip():
        from .tool_router import native_specs, route
        specs = native_specs(route(
            message, session_id=session_id, kind=kind, intent=intent,
        ))
        if specs:
            return specs
    return [t for t in NATIVE_TOOL_SPECS if t["name"] in _PLAYGROUND_BUILD_NAMES]


def _playground_session(session_id: str) -> bool:
    sid = (session_id or "").strip().lower()
    return sid.startswith(("playground:", "pg-", "pg_auto", "autopilot", "pg-auto"))


_PG_SLUG_IN_URL = re.compile(
    r"(?:playground/projects/|(?:localhost|127\.0\.0\.1):\d+/)([A-Za-z0-9_-]+)",
    re.I,
)


def _slug_from_browser_url(url: str) -> str:
    blob = url or ""
    m = _PG_SLUG_IN_URL.search(blob)
    if m:
        return m.group(1)
    m = re.match(r"https?://([A-Za-z0-9_-]+)/?$", blob.strip())
    if m and m.group(1).lower() not in {"localhost", "www"}:
        return m.group(1)
    return ""


def _harness_target(path: str) -> bool:
    p = (path or "").replace("\\", "/").lower().strip()
    if not p:
        return False
    if "daedalus/agent" in p or "daedalus/kernel" in p or "daedalus/native_tools" in p:
        return True
    if p.rstrip("/") in {".", "/workspace", "/workspace/daedalus", "daedalus"}:
        return True
    if p.startswith("/workspace/daedalus") and "playground" not in p:
        return True
    return False


def openai_tool_specs(specs: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    out = []
    for t in specs or NATIVE_TOOL_SPECS:
        out.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t.get("input_schema") or {"type": "object", "properties": {}},
            },
        })
    return out


def to_openai_messages(system: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert unified (Anthropic-style) tool turns into OpenAI chat messages."""
    oa: list[dict[str, Any]] = []
    if system:
        oa.append({"role": "system", "content": system})
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")
        if isinstance(content, str):
            oa.append({"role": role, "content": content})
            continue
        if not isinstance(content, list):
            continue
        if role == "assistant":
            text_parts = [b.get("text", "") for b in content if b.get("type") == "text"]
            tool_uses = [b for b in content if b.get("type") == "tool_use"]
            row: dict[str, Any] = {"role": "assistant", "content": "\n".join(text_parts) or None}
            if tool_uses:
                row["tool_calls"] = [
                    {
                        "id": tu.get("id") or f"call_{i}",
                        "type": "function",
                        "function": {
                            "name": tu.get("name"),
                            "arguments": json.dumps(tu.get("input") or {}, ensure_ascii=False),
                        },
                    }
                    for i, tu in enumerate(tool_uses)
                ]
            oa.append(row)
        else:
            tool_results = [b for b in content if b.get("type") == "tool_result"]
            text_parts = [b.get("text") or "" for b in content if b.get("type") == "text"]
            images = [b for b in content if b.get("type") == "image_url"]
            for block in tool_results:
                oa.append({
                    "role": "tool",
                    "tool_call_id": block.get("tool_use_id") or "",
                    "content": block.get("content") or "",
                })
            if images:
                vision: list[dict[str, Any]] = []
                blob = "\n".join(t for t in text_parts if t).strip()
                if blob:
                    vision.append({"type": "text", "text": blob})
                vision.extend(images)
                oa.append({"role": "user", "content": vision})
            elif text_parts:
                oa.append({"role": "user", "content": "\n".join(text_parts)})
    return oa


def _image_url_to_anthropic(block: dict[str, Any]) -> dict[str, Any] | None:
    url = ""
    img = block.get("image_url")
    if isinstance(img, dict):
        url = str(img.get("url") or "")
    elif isinstance(img, str):
        url = img
    if not url.startswith("data:") or "," not in url:
        return None
    header, data = url.split(",", 1)
    mime = "image/png"
    if ":" in header:
        mime = header.split(":", 1)[1].split(";", 1)[0].strip() or mime
    if not data:
        return None
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": mime, "data": data},
    }


def to_anthropic_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pass-through with OpenAI image_url blocks converted to Anthropic images."""
    out: list[dict[str, Any]] = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str) or content is None:
            out.append(msg)
            continue
        if not isinstance(content, list):
            out.append(msg)
            continue
        blocks: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "image_url":
                conv = _image_url_to_anthropic(block)
                if conv:
                    blocks.append(conv)
                continue
            blocks.append(block)
        row = dict(msg)
        row["content"] = blocks
        out.append(row)
    return out


def _user_turn(text: str, vision: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if vision:
        blocks: list[dict[str, Any]] = [{"type": "text", "text": text}]
        blocks.extend(vision)
        return {"role": "user", "content": blocks}
    return {"role": "user", "content": text}


async def _kernel_visual_proof(loop: Any) -> str:
    from .visual_proof import run_visual_proof

    result = await run_visual_proof(getattr(loop, "slug", "") or "", getattr(loop, "kind", "") or "static")
    loop.note_kernel_proof(result)
    ok = bool(result.ok) and not result.skipped
    loop.observe(
        result.tool,
        {"slug": loop.slug, "action": "kernel_proof"},
        result.as_tool_text(),
        ok=ok,
        blocked=False,
    )
    return result.nudge_block()


async def _kernel_play_bot(loop: Any) -> str:
    """Virtual computer: drive playtest/scenario.json after a painted proof."""
    slug = str(getattr(loop, "slug", "") or "")
    kind = str(getattr(loop, "kind", "") or "static")
    if not slug:
        return ""
    try:
        from .playground_gameplay_verify import gameplay_verify_app
        raw = await gameplay_verify_app(slug, kind)
    except Exception as exc:
        log.warning("kernel play bot failed: %s", exc)
        raw = {
            "ok": False,
            "skipped": False,
            "summary": str(exc)[:200],
            "error": str(exc)[:200],
        }
    if hasattr(loop, "note_play_bot"):
        loop.note_play_bot(raw)
    ok = bool(raw.get("ok")) and not raw.get("skipped")
    loop.observe(
        "playtest",
        {"slug": slug, "kind": kind, "mode": "bot"},
        str(raw.get("summary") or raw.get("error") or "play bot")[:500],
        ok=ok,
        blocked=False,
    )
    status = "OK" if ok else "FAILED"
    summary = str(raw.get("summary") or "")[:240]
    return (
        "━━━ PLAYABLE (kernel computer) ━━━\n"
        f"{status}: {summary or 'scenario bot'}\n"
        "Painted Menu is not this check. Do not ship unless this is OK."
    )


def before_tool_call(tool_name: str, *, marker_name: str | None = None) -> tuple[bool, str]:
    perm = PERM_MAP.get(tool_name)
    if perm is None:
        return True, ""
    try:
        from .outbox import check_permission, format_permission_denied
        if check_permission(perm):
            return True, ""
        label = marker_name or f"native:{tool_name}"
        return False, format_permission_denied(perm, tool=label)
    except Exception as exc:
        log.debug("before_tool_call failed open: %s", exc)
        return True, ""


def _run_read_file(
    args: dict[str, Any],
    *,
    session_id: str,
    read_file_fn: Callable[..., dict],
) -> str:
    path = str(args.get("path", ""))
    slug_h = str(args.get("slug") or "").strip() or _slug_from_text(path)
    if _playground_session(session_id) and _harness_target(path):
        return f"── read_file BLOCKED ──\n{_HARNESS_JAIL}"
    ls = args.get("line_start")
    le = args.get("line_end")
    r = read_file_fn(path, int(ls) if ls else None, int(le) if le else None)
    if r.get("ok"):
        trunc = " [TRUNCATED]" if r.get("truncated") else ""
        loc = str(r.get("path") or path)
        return (
            f"── read_file {loc}{trunc} ──\n{r['content']}"
            + _nav_hint(
                slug=slug_h or _slug_from_text(loc),
                path=loc,
                next_line=(
                    f"NEXT: call write_file with slug={slug_h or '<project>'} "
                    f"path={path or 'index.html'} if you are changing this file."
                ),
            )
        )
    err = r.get("error") or "path required"
    return (
        f"── read_file FAILED: {err} ──"
        + _nav_hint(
            slug=slug_h,
            path=path,
            next_line=(
                f"NEXT: pass path like index.html plus slug={slug_h or '<project>'}, "
                "not /workspace."
            ),
        )
    )


def _run_list_dir(
    args: dict[str, Any],
    *,
    session_id: str,
    read_file_fn: Callable[..., dict] | None = None,
) -> str:
    del read_file_fn
    from .shell_center import resolve_cwd
    rel = str(args.get("path") or ".")
    slug_h = str(args.get("slug") or "").strip() or _slug_from_text(rel)
    if _playground_session(session_id) and _harness_target(rel):
        from .paths import playground_projects_dir
        root = playground_projects_dir()
        names = []
        if root.is_dir():
            for p in sorted(root.iterdir())[:80]:
                names.append(p.name + ("/" if p.is_dir() else ""))
        listing = "\n".join(names) or "(empty playground)"
        return (
            f"── list_dir playground (harness listing refused) ──\n"
            f"{listing}\n{_HARNESS_JAIL}"
            + _nav_hint(
                slug=slug_h,
                next_line=(
                    "NEXT: call write_file with slug= from playground_new. "
                    "Do not find /workspace."
                ),
            )
        )
    try:
        root = resolve_cwd(rel)
    except ValueError as exc:
        return (
            f"── list_dir FAILED: {exc} ──"
            + _nav_hint(
                slug=slug_h,
                path=rel,
                next_line="NEXT: list playground/projects/{slug}/ or pass slug=.",
            )
        )
    names = []
    for p in sorted(root.iterdir())[:80]:
        names.append(p.name + ("/" if p.is_dir() else ""))
    return (
        f"── list_dir {root} ──\n"
        + "\n".join(names)
        + _nav_hint(
            slug=slug_h or _slug_from_text(str(root)),
            path=str(root),
            next_line=(
                f"NEXT: call write_file with slug={slug_h or '<project>'} "
                "— listing is not a substitute for writing the game."
            ),
        )
    )


async def _run_code_search(
    args: dict[str, Any],
    *,
    session_id: str,
    read_file_fn: Callable[..., dict] | None = None,
) -> str:
    del read_file_fn
    if _playground_session(session_id):
        return f"── code_search BLOCKED ──\n{_HARNESS_JAIL}"
    from .tools import search_code_tool
    q = str(args.get("query", ""))
    r = await search_code_tool(q)
    lines = [f"── code_search: {q} ──"]
    for m in (r.get("semantic_matches") or [])[:5]:
        if isinstance(m, dict):
            lines.append(f"  [sem] {m.get('filepath', '')} → {m.get('qualified_name', '')}")
        else:
            lines.append(f"  [sem] {m}")
    for m in (r.get("keyword_matches") or [])[:5]:
        if isinstance(m, dict):
            lines.append(f"  [kw] {m.get('file', '')}:{m.get('line', '')}")
        else:
            lines.append(f"  [kw] {m}")
    return "\n".join(lines)


async def _run_write_file(
    args: dict[str, Any],
    *,
    session_id: str,
    read_file_fn: Callable[..., dict] | None = None,
) -> str:
    del session_id, read_file_fn
    from . import playground as pg
    slug = str(
        args.get("slug") or args.get("project") or args.get("name") or ""
    ).strip()
    path = str(
        args.get("path")
        or args.get("file")
        or args.get("filename")
        or args.get("file_path")
        or ""
    ).strip()
    content = str(args.get("content") or args.get("text") or args.get("code") or "")
    if "/" in slug and not path:
        slug, path = slug.split("/", 1)
    if not slug or not path:
        keys = ",".join(sorted(str(k) for k in args if k != "content"))
        return (
            "── write_file FAILED: slug and path required ──\n"
            f"Got keys={keys or '(none)'}. "
            "Example: write_file slug=<project> path=index.html content=…"
        )
    existing = None
    try:
        existing = await pg.get_project(slug)
    except Exception:
        existing = None
    if not existing:
        from .godot_first import coerce_kind
        kind_new = coerce_kind("static", slug, "auto-created on write", path)
        await pg.create_project(
            slug, kind=kind_new, source="built",
            description="auto-created on write", slug=slug,
        )
        existing = {"slug": slug, "kind": kind_new}
    kind = str((existing or {}).get("kind") or "static")
    pg.write_file(slug, path, content)
    from .write_gate import check_write
    gate = check_write(path, content, kind=kind, slug=slug)
    if not gate.ok:
        return (
            f"── write_file FAILED {slug}/{path} ──\n"
            f"Wrote playground/projects/{slug}/{path} ({len(content)} chars) "
            "but the quality gate rejected it.\n"
            f"{gate.summary()}\n"
            f"NEXT: call write_file with slug={slug} path={path} and fix the blockers. "
            "Do not find /workspace or list the harness."
        )
    try:
        from .project_status import record_built
        from .playground import project_dir as _pdir
        record_built(_pdir(slug), f"wrote {path}")
    except Exception:
        pass
    return (
        f"── write_file OK {slug}/{path} ({len(content)} chars) ──\n"
        f"File is `{path}` in Playground slug `{slug}` "
        f"(playground/projects/{slug}/{path}). "
        "Do not find /workspace or list the harness. "
        "Keep write_file with this slug. "
        "When you stop, the kernel opens the running game for visual proof."
    )


async def _run_edit_file(
    args: dict[str, Any],
    *,
    session_id: str,
    read_file_fn: Callable[..., dict] | None = None,
) -> str:
    del session_id, read_file_fn
    from . import playground as pg
    slug = str(args.get("slug") or args.get("project") or "").strip()
    path = str(args.get("path") or args.get("file") or "").strip()
    search = str(args.get("search") or args.get("old") or "")
    replace = str(args.get("replace") or args.get("new") or "")
    if not slug or not path:
        return (
            "── edit_file FAILED: slug and path required ──\n"
            "Example: edit_file slug=<project> path=scripts/player.gd "
            "search=… replace=…"
        )
    existing = None
    try:
        existing = await pg.get_project(slug)
    except Exception:
        existing = None
    if not existing:
        return (
            f"── edit_file FAILED {slug}/{path} ──\n"
            "Project does not exist. Call playground_new first, or write_file for a new file."
        )
    r = pg.edit_file(slug, path, search, replace)
    if not r.get("ok"):
        return (
            f"── edit_file FAILED {slug}/{path} ──\n"
            f"{r.get('error') or 'search/replace failed'}\n"
            f"NEXT: read_file slug={slug} path={path} then edit_file with exact search text."
        )
    return (
        f"── edit_file OK {slug}/{path} ──\n"
        f"Replaced one block in playground/projects/{slug}/{path} "
        f"({r.get('old_size')} → {r.get('new_size')} chars)."
        + _nav_hint(
            slug=slug,
            path=path,
            next_line=f"Keep edit_file / write_file with slug={slug}.",
        )
    )


def _run_run_command(
    args: dict[str, Any],
    *,
    session_id: str,
    read_file_fn: Callable[..., dict] | None = None,
) -> str:
    del read_file_fn
    cmd = str(args.get("command") or "")
    if _playground_session(session_id) and re.search(r"\bfind\s+/", cmd):
        return f"── run_command BLOCKED ──\n{_HARNESS_JAIL}"
    from .shell_center import run_command
    r = run_command(
        cmd,
        cwd=str(args.get("cwd") or "") or None,
        slug=str(args.get("slug") or ""),
        timeout=args.get("timeout"),
    )
    body = (r.get("stdout") or "") + (("\n" + r["stderr"]) if r.get("stderr") else "")
    status = "OK" if r.get("ok") else "FAILED"
    hint = ""
    slug_h = str(args.get("slug") or "").strip()
    ran = str(r.get("ran") or "")
    if ran and ran.strip() != cmd.strip():
        hint += f"\nKernel dropped pipes/&& and ran: {ran}"
    if status == "FAILED":
        err = str(r.get("error") or "")
        low = (body + " " + err).lower()
        if "metacharacters" in low:
            hint += (
                "\nNEXT: retry run_command without | && or 2>&1. "
                f'command="npm install" slug={slug_h or "<project>"}'
            )
        elif "no such file" in low or "not found" in low:
            hint += (
                "\nHint: Playground files live in the project slug, not /workspace. "
                "Pass slug=" + (slug_h or "<project>") + " (cwd = that folder). "
                "Do not find / or list_dir the harness."
            )
    nxt = ""
    if status == "FAILED" and slug_h:
        nxt = (
            f"NEXT: fix the command (plain argv, slug={slug_h}), "
            "or write_file the defect. Do not open browser yet."
        )
    return (
        f"── run_command {status} exit={r.get('exit_code')} cwd={r.get('cwd')} ──\n"
        f"{body[:8000]}{hint}"
        + (_nav_hint(slug=slug_h, next_line=nxt) if slug_h or nxt else "")
    )


async def execute_native_tool(
    name: str,
    args: dict[str, Any],
    *,
    memory: Any,
    session_id: str,
    read_file_fn: Callable[..., dict],
) -> str:
    allowed, denial = before_tool_call(name)
    if not allowed:
        return denial

    try:
        from .offline import deny
        blocked = deny(name)
        if blocked:
            return blocked
    except Exception:
        pass

    from .tools import (
        web_search_tool, postgres_query_tool,
        list_endpoints_tool, list_modules_tool, web_fetch_tool,
    )

    try:
        from .tool_registry import try_dispatch

        dispatched = await try_dispatch(
            name,
            args,
            memory=memory,
            session_id=session_id,
            read_file_fn=read_file_fn,
        )
        if dispatched is not None:
            return dispatched

        if name == "read_file":
            return _run_read_file(
                args, session_id=session_id, read_file_fn=read_file_fn,
            )

        if name == "list_dir":
            return _run_list_dir(
                args, session_id=session_id, read_file_fn=read_file_fn,
            )

        if name == "playground_new":
            from . import playground as pg
            name_ = str(args.get("name") or args.get("slug") or "").strip()
            if not name_:
                return "── playground_new FAILED: name required ──"
            kind = str(args.get("kind") or "static").strip() or "static"
            desc = str(args.get("description") or "")
            slug_in = str(args.get("slug") or "")
            from .godot_first import coerce_kind
            kind = coerce_kind(kind, name_, desc, slug_in)
            proj = await pg.create_project(
                name_, kind=kind, source="built", description=desc, slug=slug_in,
            )
            slug = (proj or {}).get("slug") or ""
            seed_hint = ""
            try:
                from .writer_assist import seed_note
                seed_hint = seed_note(slug)
            except Exception:
                seed_hint = ""
            return (
                f"── playground_new OK slug={slug} kind={(proj or {}).get('kind')} ──\n"
                f"Write files with write_file slug={slug}. Patch with edit_file. "
                f"Paths are inside the Playground project `{slug}` "
                f"(playground/projects/{slug}/), not /workspace. "
                "README.md holds Status (built / left / proof) and an Assets size table."
                f"{seed_hint}"
            )

        if name == "write_file":
            return await _run_write_file(
                args, session_id=session_id, read_file_fn=read_file_fn,
            )

        if name == "edit_file":
            return await _run_edit_file(
                args, session_id=session_id, read_file_fn=read_file_fn,
            )

        if name == "run_command":
            return _run_run_command(
                args, session_id=session_id, read_file_fn=read_file_fn,
            )

        if name == "web_search":
            q = str(args.get("query", ""))
            ws = await web_search_tool(memory, q, limit=5)
            lines = [f"── web_search: {q} ──"]
            for res in ws.get("results") or []:
                lines.append(
                    f"  • {res.get('title', '')} — {res.get('url', '')}\n"
                    f"    {str(res.get('snippet', ''))[:140]}"
                )
            return "\n".join(lines) if len(lines) > 1 else "── web_search: no results ──"

        if name == "web_fetch":
            url = str(args.get("url") or "")
            max_chars = int(args.get("max_chars") or 8000)
            r = await web_fetch_tool(url, max_chars=max_chars)
            if r.get("ok"):
                return f"── web_fetch {url} ──\n{(r.get('text') or '')[:max_chars]}"
            return f"── web_fetch FAILED: {r.get('error')} ──"

        if name == "web_read":
            from .reach import looks_like_rss, read_rss, web_read, youtube_captions
            url = str(args.get("url") or "")
            max_chars = int(args.get("max_chars") or 8000)
            low = url.lower()
            if "youtube.com" in low or "youtu.be" in low:
                yt = youtube_captions(url, max_chars=max_chars)
                if yt.get("ok"):
                    return f"── youtube_captions {url} ──\n{yt.get('text')}"
            if looks_like_rss(url):
                rss = read_rss(url)
                if rss.get("ok"):
                    lines = [f"── rss {url} ──"]
                    for it in rss.get("items") or []:
                        lines.append(f"  • {it.get('title')} — {it.get('link')}")
                    return "\n".join(lines)
            r = await web_read(url, max_chars=max_chars)
            if r.get("ok"):
                return f"── web_read [{r.get('backend')}] {r.get('jina_url') or url} ──\n{r.get('text')}"
            return f"── web_read FAILED: {r.get('error')} ──"

        if name == "reach_status":
            from .reach import reach_status
            st = reach_status()
            lines = ["── reach_status ──"]
            for ch, info in (st.get("channels") or {}).items():
                flag = "UP" if info.get("ready") else "DOWN"
                lines.append(f"  {flag}  {ch}  {info.get('backend') or '—'}  {info.get('note')}")
            if st.get("doctor") and st["doctor"].get("output"):
                lines.append((st["doctor"].get("output") or "")[:1500])
            return "\n".join(lines)

        if name == "browser":
            slug = str(args.get("slug") or "").strip() or _slug_from_browser_url(
                str(args.get("url") or "")
            )
            action = str(args.get("action") or "extract").strip().lower()
            if slug and action in {
                "", "extract", "goto", "screenshot", "kernel_proof", "verify", "open",
            }:
                from . import playground as pg
                from .visual_proof import run_visual_proof
                kind = "static"
                try:
                    proj = await pg.get_project(slug)
                    kind = str((proj or {}).get("kind") or "static")
                except Exception:
                    pass
                result = await run_visual_proof(slug, kind)
                return result.as_tool_text()
            from .browser_session import run_action
            r = await run_action(
                str(args.get("action") or "extract"),
                url=str(args.get("url") or ""),
                selector=str(args.get("selector") or ""),
                text=str(args.get("text") or ""),
                slug=slug,
            )
            if r.get("ok"):
                text = r.get("text") or r.get("path") or r.get("title") or ""
                return f"── browser {r.get('action')} OK {r.get('url') or ''} ──\n{text}"
            return f"── browser FAILED: {r.get('error')} ──"

        if name == "mcp_call":
            from . import mcp_client
            from .session_context import mcp_allowed
            server = str(args.get("server") or "")
            ok_mcp, why = mcp_allowed(server)
            if not ok_mcp:
                return f"── mcp_call BLOCKED ──\n{why}"
            if args.get("list_tools") or not args.get("tool"):
                if not server:
                    status = mcp_client.status()
                    pack = None
                    try:
                        from .session_context import current as _turn
                        pack = _turn()
                    except Exception:
                        pack = None
                    if pack and pack.mcp_ids:
                        servers = status.get("servers") or {}
                        status["servers"] = {
                            k: v for k, v in servers.items() if k in pack.mcp_ids
                        }
                        status["pinned"] = list(pack.mcp_ids)
                    return "── mcp ──\n" + json.dumps(status, ensure_ascii=False, indent=2)[:3000]
                r = await mcp_client.list_tools(server)
            else:
                r = await mcp_client.call_tool(
                    server,
                    str(args.get("tool") or ""),
                    args.get("arguments") if isinstance(args.get("arguments"), dict) else {},
                )
            return f"── mcp_call ──\n{json.dumps(r, ensure_ascii=False, default=str)[:6000]}"

        if name == "spawn_subagent":
            from .subagents import run_subagent
            refs = args.get("context_refs")
            wanted = args.get("return_contexts")
            boot = args.get("context_bootstrap")
            r = await run_subagent(
                str(args.get("slug") or ""),
                str(args.get("task") or ""),
                memory=memory,
                session_id=session_id,
                max_rounds=int(args.get("max_rounds") or 6),
                read_file_fn=read_file_fn,
                role=str(args.get("role") or "coder"),
                job_id=str(args.get("job_id") or ""),
                original_ask=str(args.get("original_ask") or ""),
                return_contexts=list(wanted) if isinstance(wanted, list) else None,
                context_refs=list(refs) if isinstance(refs, list) else None,
                context_bootstrap=list(boot) if isinstance(boot, list) else None,
                task_id=str(args.get("task_id") or ""),
                files=list(args.get("files") or []) if isinstance(args.get("files"), list) else None,
            )
            status = "OK" if r.get("ok") else "FAILED"
            extra = f" {r.get('role') or ''} {r.get('verdict') or ''}".rstrip()
            arts = r.get("artifacts") or []
            art_line = ""
            if arts:
                art_line = "\nartifacts: " + ", ".join(
                    a.get("id", "?") for a in arts if isinstance(a, dict)
                )
            return (
                f"── spawn_subagent {status} {r.get('slug')}{extra} ──\n"
                f"{r.get('summary') or r.get('error')}{art_line}"
            )

        if name == "playtest":
            from .playtest import format_for_prompt, run_playtest
            r = await run_playtest(
                str(args.get("slug") or ""),
                kind=str(args.get("kind") or "static"),
                n=int(args.get("n") or 4),
                use_browser=bool(args.get("use_browser")),
            )
            return "── playtest ──\n" + format_for_prompt(r)

        if name == "play_lab":
            from . import play_lab as pl
            action = str(args.get("action") or "status").lower().strip()
            if action == "list":
                r = {"ok": True, "labs": pl.list_labs(), "host": pl.probe_host()}
            elif action == "create":
                r = pl.create_lab(
                    title=str(args.get("title") or ""),
                    source=str(args.get("source") or ""),
                )
                r = {"ok": True, **r}
            elif action == "install":
                r = pl.install_into_lab(str(args.get("lab_id") or ""), str(args.get("path") or ""))
            elif action == "start":
                r = pl.start_vm(str(args.get("lab_id") or ""))
            elif action == "stop":
                r = pl.stop_vm(str(args.get("lab_id") or ""))
            elif action == "destroy":
                r = pl.destroy_lab(str(args.get("lab_id") or ""))
            elif action in ("open", "watch", "computer"):
                from .computer import open_project_computer
                lab_id = str(args.get("lab_id") or "")
                try:
                    if lab_id:
                        r = pl.start_vm(lab_id)
                    else:
                        r = open_project_computer(str(args.get("slug") or args.get("title") or ""))
                    r = {"ok": True, **r} if isinstance(r, dict) and "ok" not in r else r
                except ValueError as extra:
                    r = {"ok": False, "error": str(extra)}
            else:
                r = pl.probe_host()
                lab_id = str(args.get("lab_id") or "")
                if lab_id:
                    r["lab"] = pl.load_lab(lab_id)
            return f"── play_lab {action} ──\n{json.dumps(r, ensure_ascii=False, default=str)[:5000]}"

        if name == "dissect_game":
            from .dissection import dissect, format_specimen_block
            r = await dissect(
                str(args.get("source") or ""),
                slug=str(args.get("slug") or "") or None,
                lab_id=str(args.get("lab_id") or "") or None,
                install_path=str(args.get("path") or ""),
                create_lab=args.get("create_lab", True) is not False,
                live=args.get("live", True) is not False,
                project_name=str(args.get("slug") or ""),
                description=str(args.get("description") or ""),
                memory=memory,
            )
            block = r.get("specimen_block") or format_specimen_block(r)
            compact = {
                "ok": r.get("ok"),
                "title": r.get("title"),
                "source_kind": r.get("source_kind"),
                "lab_id": r.get("lab_id"),
                "stack": r.get("stack"),
                "playable_url": r.get("playable_url"),
                "error": r.get("error"),
            }
            return (
                f"── dissect_game ──\n{json.dumps(compact, ensure_ascii=False, default=str)[:2500]}\n\n"
                f"{block}"
            )

        if name == "engine":
            from .engine_session import run_action
            slug_e = str(args.get("slug") or "").strip()
            r = await run_action(
                str(args.get("action") or "run"),
                slug_e,
                path=str(args.get("path") or ""),
                prop=str(args.get("prop") or ""),
                value=args.get("value"),
                method=str(args.get("method") or ""),
                args=args.get("args"),
                action_name=str(args.get("action_name") or ""),
                key=str(args.get("key") or ""),
                pressed=args.get("pressed"),
                frames=args.get("frames"),
            )
            status = "OK" if r.get("ok") else "FAILED"
            nxt = (
                f"NEXT: call write_file with slug={slug_e or '<project>'} then engine again."
                if status == "FAILED"
                else f"Keep engine on slug={slug_e}." if slug_e else ""
            )
            return (
                f"── engine {args.get('action')} {status} {slug_e} ──\n"
                f"{json.dumps(r, ensure_ascii=False, default=str)[:6000]}"
                + _nav_hint(slug=slug_e, next_line=nxt)
            )

        if name == "world":
            from .world_runtime import run_action as world_action
            r = world_action(
                str(args.get("action") or "status"),
                str(args.get("slug") or ""),
                who=str(args.get("who") or ""),
                pick=args.get("pick"),
                to=str(args.get("to") or ""),
                save=args.get("save"),
                steps=args.get("steps"),
            )
            status = "OK" if r.get("ok") else "FAILED"
            return (
                f"── world {args.get('action')} {status} {args.get('slug')} ──\n"
                f"{json.dumps(r, ensure_ascii=False, default=str)[:6000]}"
            )

        if name == "assets":
            from .asset_pipeline import run_action as assets_action
            r = assets_action(
                str(args.get("action") or "status"),
                str(args.get("slug") or ""),
                sources=args.get("sources"),
                prompt=str(args.get("prompt") or ""),
                name=str(args.get("name") or ""),
                provider=str(args.get("provider") or "auto"),
                job_id=str(args.get("job_id") or ""),
            )
            status = "OK" if r.get("ok") else "FAILED"
            return (
                f"── assets {args.get('action')} {status} {args.get('slug')} ──\n"
                f"{json.dumps(r, ensure_ascii=False, default=str)[:6000]}"
            )

        if name == "skill":
            from .learned_skills import run_action as skill_action
            r = skill_action(
                str(args.get("action") or "catalog"),
                query=str(args.get("query") or ""),
                id=str(args.get("id") or ""),
                name=str(args.get("name") or ""),
            )
            status = "OK" if r.get("ok") else "FAILED"
            return (
                f"── skill {args.get('action')} {status} ──\n"
                f"{json.dumps(r, ensure_ascii=False, default=str)[:6000]}"
            )

        if name == "production":
            from .production_layer import run_action as production_action
            r = production_action(
                str(args.get("action") or "status"),
                str(args.get("slug") or ""),
                claim=str(args.get("claim") or ""),
                systems=args.get("systems"),
                layer=str(args.get("layer") or ""),
                build_mode=str(args.get("build_mode") or ""),
                through=str(args.get("through") or ""),
                kind=str(args.get("kind") or ""),
                target=str(args.get("target") or ""),
                text=str(args.get("text") or ""),
                plan=str(args.get("plan") or ""),
            )
            status = "OK" if r.get("ok") else "FAILED"
            return (
                f"── production {args.get('action')} {status} {args.get('slug')} ──\n"
                f"{json.dumps(r, ensure_ascii=False, default=str)[:6000]}"
            )

        if name == "mirofish":
            from . import mirofish_client as mf
            action = str(args.get("action") or "ping").lower()
            if action == "list":
                r = await mf.list_projects()
            elif action == "submit":
                r = await mf.submit_document(
                    str(args.get("text") or ""),
                    requirement=str(args.get("requirement") or ""),
                    project_name=str(args.get("project_name") or "Daedalus GDD"),
                )
            else:
                r = await mf.ping()
            return f"── mirofish {action} ──\n{json.dumps(r, ensure_ascii=False, default=str)[:4000]}"

        if name == "git_forge":
            from .playground_git_push import (
                clone_project_remote,
                create_upstream_review,
                push_project_remote,
                remote_status,
            )
            from .playground_github import create_pull_request, parse_github_repo
            from .playground_gitlab import create_merge_request, parse_gitlab_repo
            action = str(args.get("action") or "status").strip().lower()
            slug = str(args.get("slug") or "").strip()
            provider = str(args.get("provider") or "auto").strip().lower()
            if action == "status":
                r = remote_status()
            elif action == "clone":
                r = await clone_project_remote(
                    str(args.get("url") or args.get("repo") or ""),
                    name=str(args.get("name") or ""),
                    slug=slug,
                    source="cloned",
                    keep_git=True,
                )
            elif action == "push":
                r = await push_project_remote(
                    slug,
                    provider=provider,
                    repo_full_name=str(args.get("repo") or ""),
                    commit_msg=str(args.get("commit_msg") or ""),
                )
            elif action in ("pull_request", "pr", "merge_request", "mr"):
                repo = str(args.get("repo") or "").strip()
                head = str(args.get("head") or "").strip()
                if slug and not (repo and head):
                    r = await create_upstream_review(
                        slug,
                        title=str(args.get("title") or ""),
                        body=str(args.get("body") or ""),
                        base=str(args.get("base") or ""),
                    )
                elif action in ("merge_request", "mr") or provider == "gitlab":
                    parsed = parse_gitlab_repo(repo)
                    ns = parsed[0] if parsed else repo
                    r = create_merge_request(
                        ns,
                        source_branch=head,
                        target_branch=str(args.get("base") or "main"),
                        title=str(args.get("title") or ""),
                        description=str(args.get("body") or ""),
                    )
                else:
                    parsed = parse_github_repo(repo)
                    if not parsed:
                        r = {"ok": False, "error": "repo must be owner/repo"}
                    else:
                        owner, name = parsed
                        r = create_pull_request(
                            owner,
                            name,
                            title=str(args.get("title") or ""),
                            head=head,
                            base=str(args.get("base") or "main"),
                            body=str(args.get("body") or ""),
                        )
            else:
                r = {"ok": False, "error": f"unknown git_forge action {action}"}
            status = "OK" if r.get("ok") else "FAILED"
            return (
                f"── git_forge {action} {status} {slug} ──\n"
                f"{json.dumps(r, ensure_ascii=False, default=str)[:6000]}"
            )

        if name == "generate_image":
            from .image_client import decode_image_bytes, generate_image
            from .paths import data_root, playground_projects_dir
            prompt = str(args.get("prompt") or "")
            slug = str(args.get("slug") or "")
            filename = str(args.get("filename") or "asset.png")
            size = str(args.get("size") or "512x512")
            img = await generate_image(prompt=prompt, size=size)
            images = img.get("images") or []
            if not images:
                return f"── generate_image FAILED: no images ({img}) ──"
            first = images[0]
            from .playground_assets import filename_under_assets
            dest_dir = (playground_projects_dir() / slug / "assets") if slug else (data_root() / "generated")
            dest_dir.mkdir(parents=True, exist_ok=True)
            rel = filename_under_assets(filename)
            dest = dest_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            decoded = decode_image_bytes(first)
            if decoded:
                dest.write_bytes(decoded)
            elif first.get("url"):
                import httpx
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.get(first["url"])
                    resp.raise_for_status()
                    dest.write_bytes(resp.content)
            else:
                return "── generate_image FAILED: no b64/url ──"
            return f"── generate_image OK {dest} ──\nprovider={img.get('provider')} model={img.get('model')}"

        if name == "remember":
            from .preferences import add as pref_add
            row = pref_add(str(args.get("text") or ""), kind=str(args.get("kind") or "preference"))
            return f"── remember OK {row.get('id')} ── {row.get('text')}"

        if name == "postgres_query":
            sql = str(args.get("sql", ""))
            r = await postgres_query_tool(sql, session_id=session_id)
            if r.get("ok"):
                rows = "\n".join(str(row) for row in (r.get("rows") or [])[:20])
                return f"── postgres ({r.get('row_count', 0)} rows) ──\n{rows}"
            return f"── postgres FAILED: {r.get('error')} ──"

        if name == "code_search":
            return await _run_code_search(
                args, session_id=session_id, read_file_fn=read_file_fn,
            )

        if name == "list_endpoints":
            flt = str(args.get("filter", "*"))
            r = await list_endpoints_tool(flt)
            return f"── endpoints ──\n{r.get('text', str(r))[:3000]}"

        if name == "list_modules":
            flt = str(args.get("filter", "*"))
            r = await list_modules_tool(flt)
            return f"── modules ──\n{r.get('text', str(r))[:3000]}"

        if name == "math3d":
            from .math3d import run_action as math3d_action
            op = str(args.get("action") or args.get("op") or "catalog")
            slug_m = str(args.get("slug") or "").strip()
            payload = {k: v for k, v in args.items() if k not in ("action", "op", "slug")}
            r = math3d_action(op, **payload)
            status = "OK" if r.get("ok") else "FAILED"
            if r.get("verdict") == "mismatch":
                status = "FAILED"
            op_l = op.strip().lower().replace("-", "_")
            if op_l in {"catalog", "help", "ops", "status"}:
                nxt = (
                    "NEXT: call math3d with op=quat_slerp (or look_at) and claimed numbers, "
                    f"then write_file slug={slug_m or '<project>'}."
                )
            elif status == "FAILED":
                nxt = (
                    "NEXT: call math3d again with the corrected claim, then "
                    f"write_file slug={slug_m or '<project>'}."
                )
            else:
                nxt = (
                    f"NEXT: call write_file with slug={slug_m or '<project>'} "
                    "using the verified numbers. Do not list_dir the harness."
                )
            return (
                f"── math3d {op} {status} ──\n"
                f"{json.dumps(r, ensure_ascii=False, default=str)[:6000]}"
                + _nav_hint(slug=slug_m, next_line=nxt)
            )

        if name == "spatial":
            from .spatial import run_action as spatial_action
            op = str(args.get("action") or "advise")
            slug_s = str(args.get("slug") or "").strip()
            payload = {k: v for k, v in args.items() if k != "action"}
            r = spatial_action(op, **payload)
            status = "OK" if r.get("ok") else "FAILED"
            nxt = (
                f"NEXT: call write_file with slug={slug_s or '<project>'} "
                "using the advised structure. Do not nest O(N²) loops."
            )
            return (
                f"── spatial {op} {status} ──\n"
                f"{json.dumps(r, ensure_ascii=False, default=str)[:6000]}"
                + _nav_hint(slug=slug_s, next_line=nxt)
            )

        if name == "game_lint":
            from .game_lint import run_action as game_lint_action
            slug_g = str(args.get("slug") or "").strip()
            path_g = str(args.get("path") or "")
            r = game_lint_action(
                str(args.get("action") or "status"),
                path=path_g,
                slug=slug_g,
                content=args.get("content"),
                format_diff=bool(args.get("format_diff")),
            )
            status = "OK" if r.get("ok") else "FAILED"
            nxt = (
                f"NEXT: call write_file with slug={slug_g or '<project>'} "
                f"path={path_g or 'index.html'} and fix the blockers."
                if status == "FAILED"
                else f"Keep writing under playground/projects/{slug_g or '{slug}'}/."
            )
            return (
                f"── game_lint {args.get('action')} {status} ──\n"
                f"{json.dumps(r, ensure_ascii=False, default=str)[:6000]}"
                + _nav_hint(slug=slug_g, path=path_g, next_line=nxt)
            )

        if name == "profiler":
            from .profiler import run_action as profiler_action
            r = profiler_action(
                str(args.get("action") or "status"),
                slug=str(args.get("slug") or ""),
                path=str(args.get("path") or ""),
                kind=str(args.get("kind") or "auto"),
                text=args.get("text"),
                platform=str(args.get("platform") or "EditMode"),
            )
            status = "OK" if r.get("ok") else "FAILED"
            return (
                f"── profiler {args.get('action')} {status} ──\n"
                f"{json.dumps(r, ensure_ascii=False, default=str)[:6000]}"
            )

        if name == "game_canon":
            from .game_canon import run_action as game_canon_action
            r = game_canon_action(
                str(args.get("action") or "list"),
                id=str(args.get("id") or args.get("name") or ""),
                path=str(args.get("path") or args.get("file") or ""),
                query=str(args.get("query") or args.get("q") or ""),
                limit=args.get("limit"),
            )
            status = "OK" if r.get("ok") else "FAILED"
            return (
                f"── game_canon {args.get('action')} {status} ──\n"
                f"{json.dumps(r, ensure_ascii=False, default=str)[:8000]}"
            )

        return f"── unknown native tool: {name} ──"
    except Exception as exc:
        log.warning("native tool %s failed: %s", name, exc)
        return f"── {name} ERROR: {exc} ──"


def _mentor_record_outcome(loop: Any) -> None:
    if loop is None:
        return
    try:
        from . import mentor as _mentor
        _mentor.record_outcome(loop)
    except Exception:
        log.debug("mentor record_outcome skipped", exc_info=True)


async def run_native_discovery_pass(
    system: str,
    user_content: str,
    call_with_tools_fn: Callable[..., Awaitable[dict[str, Any]]],
    *,
    memory: Any,
    session_id: str,
    read_file_fn: Callable[..., dict],
    max_rounds: int = 8,
    tools: list[dict[str, Any]] | None = None,
    loop: Any = None,
) -> tuple[str | None, list[dict[str, Any]]]:
    """Run native tool rounds. Returns (result_text, tool_log).

    Returns (None, []) if no tools were called — caller uses the marker path.
    JSON/XML tool dumps in assistant text are executed like native tool_calls.

    ``loop`` is a ``build_loop.BuildLoop``. With it, a quiet round (no tool
    calls) does not end the turn: the loop scores the plan and, while fix
    rounds remain, injects PLAN PROGRESS as the next user turn.
    """
    from .tool_dumps import extract_tool_dumps

    try:
        from . import writer_assist as _writer_assist
        _writer_assist.set_ask(user_content)
    except Exception:
        pass
    specs = tools or NATIVE_TOOL_SPECS
    messages: list[dict[str, Any]] = [{"role": "user", "content": user_content}]
    tool_log: list[dict[str, Any]] = []
    all_results: list[str] = []

    budget = max_rounds
    _round = -1
    while True:
        if _round + 1 >= budget:
            if loop is not None and not getattr(loop, "stop_reason", ""):
                if getattr(loop, "wants_exhaust_proof", lambda: False)():
                    try:
                        proof_block = await _kernel_visual_proof(loop)
                        if proof_block:
                            all_results.append(proof_block)
                        if loop.observations:
                            tool_log.append(loop.observations[-1].as_call())
                    except Exception as exc:
                        log.warning("kernel visual proof on exhaust failed: %s", exc)
                if loop is not None and getattr(loop, "wants_play_bot", lambda: False)():
                    try:
                        play_block = await _kernel_play_bot(loop)
                        if play_block:
                            all_results.append(play_block)
                        if loop.observations:
                            tool_log.append(loop.observations[-1].as_call())
                    except Exception as exc:
                        log.warning("kernel play bot on exhaust failed: %s", exc)
                extra = 0
                try:
                    from . import mentor as _mentor
                    await _mentor.refresh_helm(loop)
                    extra = int(_mentor.extra_round_budget(loop) or 0)
                except Exception:
                    extra = 0
                if extra > 0:
                    loop.mentor_grants = int(getattr(loop, "mentor_grants", 0) or 0) + 1
                    budget += extra
                    try:
                        from . import mentor as _mentor
                        _mentor.maybe_capture_lesson(loop, memory)
                    except Exception:
                        pass
                    nudge = loop.progress_block()
                    vision = list(getattr(loop, "proof_vision", None) or [])
                    messages.append(_user_turn(nudge, vision or None))
                    continue
                loop.stop_reason = "round budget exhausted"
                loop.verdict("")
            break
        _round += 1
        resp = await call_with_tools_fn(system, messages, specs)
        if resp.get("budget_stopped"):
            final = ""
            for block in resp.get("content") or []:
                if block.get("type") == "text":
                    final = str(block.get("text") or "")
                    break
            if loop is not None:
                loop.stop_reason = "run budget exhausted"
                loop.verdict(final)
            _mentor_record_outcome(loop)
            return (final or None), tool_log
        content_blocks = resp.get("content", [])

        assistant_content: list[dict[str, Any]] = []
        tool_uses: list[dict[str, Any]] = []
        text_parts: list[str] = []

        for block in content_blocks:
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
                assistant_content.append(block)
            elif block.get("type") == "tool_use":
                tool_uses.append(block)
                assistant_content.append(block)

        if not tool_uses:
            for i, dump in enumerate(extract_tool_dumps("\n".join(text_parts))):
                tool_uses.append({
                    "type": "tool_use",
                    "id": f"dump_{_round}_{i}",
                    "name": dump.name,
                    "input": dump.args,
                })
                assistant_content.append(tool_uses[-1])

        if not tool_uses:
            final = "\n".join(text_parts).strip()
            proof_block = ""
            if loop is not None and getattr(loop, "wants_kernel_proof", lambda: False)():
                try:
                    proof_block = await _kernel_visual_proof(loop)
                    if loop.observations:
                        tool_log.append(loop.observations[-1].as_call())
                except Exception as exc:
                    log.warning("kernel visual proof failed: %s", exc)
                    proof_block = f"━━━ VISUAL PROOF (kernel) ━━━\nProof crashed: {exc}\n"
            play_block = ""
            if loop is not None and getattr(loop, "wants_play_bot", lambda: False)():
                try:
                    play_block = await _kernel_play_bot(loop)
                    if loop.observations:
                        tool_log.append(loop.observations[-1].as_call())
                except Exception as exc:
                    log.warning("kernel play bot failed: %s", exc)
                    play_block = f"━━━ PLAYABLE (kernel computer) ━━━\nFAILED: {exc}\n"
            nudge = None
            if loop is not None:
                try:
                    from . import mentor as _mentor
                    await _mentor.refresh_helm(loop)
                except Exception:
                    pass
                nudge = loop.on_quiet(final)
            if nudge:
                extra = "\n\n".join(p for p in (proof_block, play_block, nudge) if p)
                messages.append({
                    "role": "assistant",
                    "content": assistant_content or [{"type": "text", "text": final or "(no reply)"}],
                })
                vision = list(getattr(loop, "proof_vision", None) or []) if loop is not None else []
                messages.append(_user_turn(extra, vision or None))
                budget += loop.rounds_per_fix
                continue
            for block in (proof_block, play_block):
                if block:
                    all_results.append(block)
            _mentor_record_outcome(loop)
            if all_results:
                combined = (
                    "━━━ NATIVE TOOL RESULTS ━━━\n"
                    + "\n\n".join(all_results)
                    + ("\n\n━━━ MODEL FOLLOW-UP ━━━\n" + final if final else "")
                )
                return combined, tool_log
            return None, []

        messages.append({"role": "assistant", "content": assistant_content})

        tool_result_blocks: list[dict[str, Any]] = []
        for tu in tool_uses:
            tid = tu.get("id", "")
            tname = tu.get("name", "")
            tinput = tu.get("input") or {}
            if isinstance(tinput, str):
                try:
                    tinput = json.loads(tinput)
                except json.JSONDecodeError:
                    tinput = {"raw": tinput}

            result_text = await execute_native_tool(
                tname, tinput,
                memory=memory, session_id=session_id,
                read_file_fn=read_file_fn,
            )
            all_results.append(result_text)
            blocked = "BLOCKED" in result_text
            ok = blocked or ("FAILED" not in result_text and "ERROR" not in result_text)
            tool_log.append({
                "tool": tname,
                "input": {k: v for k, v in tinput.items() if k != "content"} if isinstance(tinput, dict) else {},
                "ok": ok,
                "blocked": blocked,
            })
            if loop is not None:
                loop.observe(
                    tname, tinput if isinstance(tinput, dict) else {}, result_text,
                    ok=ok, blocked=blocked,
                )
                from .tool_catalog import canonical as _can
                tcan = _can(tname).lower()
                if tcan in {"browser", "engine", "visual_proof", "run_command"} and not ok:
                    try:
                        from . import mentor as _mentor
                        if _mentor.enabled():
                            ret = _mentor.progress_next(loop)
                            if ret:
                                result_text = result_text + "\n" + ret
                                all_results[-1] = result_text
                    except Exception:
                        pass
            tool_result_blocks.append({
                "type": "tool_result",
                "tool_use_id": tid,
                "content": result_text,
            })

        messages.append({"role": "user", "content": tool_result_blocks})

    _mentor_record_outcome(loop)
    if all_results:
        return "━━━ NATIVE TOOL RESULTS ━━━\n" + "\n\n".join(all_results), tool_log
    return None, []
