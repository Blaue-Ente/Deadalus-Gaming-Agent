"""Single catalog of Daedalus capabilities.

Native function names are canonical. NEED_* markers, JSON/XML dump aliases,
skills, and knowledge files are indexed here so the kernel can pick a path
without the LLM guessing where a tool lives.

The OpenAI schemas stay in ``native_tools.NATIVE_TOOL_SPECS``. This module
adds routing metadata: tags, when-to-use, aliases, module path, docs.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .paths import package_root

# Dump / marker names that must resolve to a native function.
# Keep in sync with ``tool_dumps`` and ``tool_protocol.MARKER_TO_NATIVE``.
ALIASES: dict[str, str] = {
    "playground_write": "write_file",
    "playground_read": "read_file",
    "playground_run": "run_command",
    "need_playground_write": "write_file",
    "need_playground_new": "playground_new",
    "need_playground_run": "run_command",
    "need_playground_read": "read_file",
    "need_read_file": "read_file",
    "need_web": "web_search",
    "need_search": "web_search",
    "need_web_fetch": "web_fetch",
    "need_code_search": "code_search",
    "need_endpoints": "list_endpoints",
    "need_modules": "list_modules",
    "need_pg": "postgres_query",
    "need_subagent": "spawn_subagent",
    "need_verify": "playtest",
    "shell": "run_command",
    "ls": "list_dir",
    "cat": "read_file",
}


@dataclass(frozen=True)
class ToolEntry:
    name: str
    kind: str  # native | need | skill | knowledge
    module: str = ""
    tags: tuple[str, ...] = ()
    when: str = ""
    aliases: tuple[str, ...] = ()
    need_marker: str = ""
    knowledge: tuple[str, ...] = ()
    args_hint: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "module": self.module,
            "tags": list(self.tags),
            "when": self.when,
            "aliases": list(self.aliases),
            "need_marker": self.need_marker,
            "knowledge": list(self.knowledge),
            "args_hint": self.args_hint,
        }


# Overlay on NATIVE_TOOL_SPECS. Keys must exist as native names.
_NATIVE_META: dict[str, dict[str, Any]] = {
    "read_file": {
        "module": "daedalus.native_tools",
        "tags": ("fs", "playground"),
        "when": "Read a file in the jail or a Playground slug. Not for mapping the harness on a game build.",
        "aliases": ("NEED_READ_FILE", "NEED_PLAYGROUND_READ", "cat"),
        "need_marker": "NEED_READ_FILE",
        "args_hint": "path, optional line_start/line_end",
    },
    "list_dir": {
        "module": "daedalus.native_tools",
        "tags": ("fs", "playground"),
        "when": "List a directory inside the jail. Playground sessions refuse daedalus/ harness paths.",
        "aliases": ("ls",),
        "args_hint": "path (default .)",
    },
    "write_file": {
        "module": "daedalus.playground",
        "tags": ("playground", "build"),
        "when": "Write a file in a Playground project. Creates a static slug if missing. Never a harness patch.",
        "aliases": ("NEED_PLAYGROUND_WRITE", "playground_write"),
        "need_marker": "NEED_PLAYGROUND_WRITE",
        "args_hint": "slug, path, content",
    },
    "playground_new": {
        "module": "daedalus.playground",
        "tags": ("playground", "build"),
        "when": "Create a Playground project. kind=static|vite|vite-react|godot|unity|expo.",
        "aliases": ("NEED_PLAYGROUND_NEW",),
        "need_marker": "NEED_PLAYGROUND_NEW",
        "args_hint": "name, kind, description, optional slug",
    },
    "run_command": {
        "module": "daedalus.shell_center",
        "tags": ("shell", "verify"),
        "when": "Jailed shell: pytest, npm, ls, git status. Static HTML preview does not need a dev server.",
        "aliases": ("NEED_PLAYGROUND_RUN", "shell"),
        "need_marker": "NEED_PLAYGROUND_RUN",
        "args_hint": "command, optional cwd/slug/timeout",
    },
    "web_search": {
        "module": "daedalus.tools",
        "tags": ("web",),
        "when": "Search the public web for docs or APIs.",
        "aliases": ("NEED_WEB", "NEED_SEARCH"),
        "need_marker": "NEED_WEB",
        "args_hint": "query",
    },
    "web_fetch": {
        "module": "daedalus.tools",
        "tags": ("web",),
        "when": "Fetch a URL as cleaned text.",
        "aliases": ("NEED_WEB_FETCH",),
        "need_marker": "NEED_WEB_FETCH",
        "args_hint": "url, optional max_chars",
    },
    "web_read": {
        "module": "daedalus.reach",
        "tags": ("web", "reach"),
        "when": "Readable article text via Jina Reader.",
        "args_hint": "url",
    },
    "reach_status": {
        "module": "daedalus.reach",
        "tags": ("web", "reach"),
        "when": "Probe Jina / yt-dlp / gh backends.",
    },
    "generate_image": {
        "module": "daedalus.playground_assets",
        "tags": ("assets", "playground"),
        "when": "Generate a sprite/asset into the Playground slug.",
        "args_hint": "prompt, optional slug/filename",
    },
    "remember": {
        "module": "daedalus.preferences",
        "tags": ("memory",),
        "when": "Store an owner preference forever.",
        "args_hint": "text, optional kind",
    },
    "code_search": {
        "module": "daedalus.tools",
        "tags": ("harness", "recon"),
        "when": "Search this product's code. Blocked on Playground game builds.",
        "aliases": ("NEED_CODE_SEARCH",),
        "need_marker": "NEED_CODE_SEARCH",
        "args_hint": "query",
    },
    "postgres_query": {
        "module": "daedalus.tools",
        "tags": ("db", "harness"),
        "when": "Read-only SQL. Not for Playground games.",
        "aliases": ("NEED_PG",),
        "need_marker": "NEED_PG",
        "args_hint": "sql",
    },
    "list_endpoints": {
        "module": "daedalus.tools",
        "tags": ("harness", "recon"),
        "when": "List HTTP routes.",
        "aliases": ("NEED_ENDPOINTS",),
        "need_marker": "NEED_ENDPOINTS",
    },
    "list_modules": {
        "module": "daedalus.tools",
        "tags": ("harness", "recon"),
        "when": "List Python modules in the product.",
        "aliases": ("NEED_MODULES",),
        "need_marker": "NEED_MODULES",
    },
    "browser": {
        "module": "daedalus.browser_session",
        "tags": ("verify", "playground"),
        "when": "Interactive Chromium: goto, click, screenshot a live page.",
        "args_hint": "action=goto|click|type|extract|screenshot|close",
    },
    "mcp_call": {
        "module": "daedalus.mcp_client",
        "tags": ("mcp",),
        "when": "Call an optional MCP server from $DAEDALUS_DATA/mcp.json.",
        "knowledge": ("skills/mcp.md",),
    },
    "spawn_subagent": {
        "module": "daedalus.subagents",
        "tags": ("agents",),
        "when": "Isolated explorer/coder/verify with a fresh window. Hard multi-file jobs.",
        "aliases": ("NEED_SUBAGENT",),
        "need_marker": "NEED_SUBAGENT",
        "knowledge": ("skills/hard_jobs.md",),
    },
    "playtest": {
        "module": "daedalus.playtest",
        "tags": ("verify", "games"),
        "when": "Judge personas on a Playground game (first 30s, win/lose). Not Click-Start.",
        "aliases": ("NEED_VERIFY",),
        "need_marker": "NEED_VERIFY",
        "knowledge": ("skills/playtest.md",),
        "args_hint": "slug",
    },
    "mirofish": {
        "module": "daedalus.mirofish_client",
        "tags": ("sidecar",),
        "when": "HTTP to optional MiroFish sidecar. Never vendor AGPL source.",
        "knowledge": ("skills/mirofish.md",),
    },
    "play_lab": {
        "module": "daedalus.play_lab",
        "tags": ("dissection",),
        "when": "Virtual computer for a specimen install. Local HTTP; optional SmolVM.",
        "knowledge": ("skills/dissection.md",),
    },
    "dissect_game": {
        "module": "daedalus.dissection",
        "tags": ("dissection", "games"),
        "when": "Turn a name/URL/git/path into a specimen. Steal loop/feel, never clone assets.",
        "knowledge": ("skills/dissection.md",),
    },
    "git_forge": {
        "module": "daedalus.playground_github",
        "tags": ("git",),
        "when": "Clone, push, GitHub PR, GitLab MR for a Playground slug.",
        "knowledge": ("skills/live_publish.md",),
    },
    "engine": {
        "module": "daedalus.engine_session",
        "tags": ("games", "godot", "unity", "verify"),
        "when": "Godot headless bridge or Unity MCP/batchmode. A loaded .tscn is not Play.",
        "knowledge": ("skills/godot.md", "skills/godot_api.md", "skills/unity_runtime.md"),
        "args_hint": "action=run|inspect|input|screenshot|quit, slug",
    },
    "world": {
        "module": "daedalus.world_runtime",
        "tags": ("games", "epic"),
        "when": "Epic campaign runtime: save, quest, dialogue, gated graph. Empty JSON is not a campaign.",
        "knowledge": ("skills/world_kit.md", "skills/campaign.md"),
        "args_hint": "action=status|prove|talk|travel, slug",
    },
    "assets": {
        "module": "daedalus.asset_pipeline",
        "tags": ("assets", "games"),
        "when": "2D atlas/sfx/font (stdlib PNG). 3D mesh jobs fail closed without keys.",
        "knowledge": ("skills/asset_pipeline.md",),
        "args_hint": "action=atlas|tileset|sfx|font|mesh|greybox, slug",
    },
    "skill": {
        "module": "daedalus.learned_skills",
        "tags": ("docs", "skills"),
        "when": "Catalog or load one SKILL.md body. Pending drafts wait for owner Approve.",
        "knowledge": ("skills/skill_memory.md",),
        "args_hint": "action=catalog|load|pending, id",
    },
    "production": {
        "module": "daedalus.production_layer",
        "tags": ("games", "ship"),
        "when": "Prototype vs store-ready systems map. Empty JSON is not a production layer.",
        "knowledge": ("skills/production_layer.md",),
    },
    "math3d": {
        "module": "daedalus.math3d",
        "tags": ("games", "math", "3d"),
        "when": "SLERP, look-at, rays, projection. Pass claimed to referee a formula before C++/JS.",
        "knowledge": (
            "knowledge/transform_math.md",
            "skills/transform_math.md",
            "skills/senior_tools.md",
        ),
        "args_hint": "op=quat_slerp|look_at|ray_triangle|perspective|catalog, claimed",
    },
    "spatial": {
        "module": "daedalus.spatial",
        "tags": ("games", "physics"),
        "when": "Many colliders. advise BEFORE nested loops. Grid/quad/octree/BVH — not O(N²).",
        "knowledge": ("skills/spatial_simd.md", "knowledge/data_oriented_design.md"),
        "args_hint": "action=advise|aabb|scan, n, dim=2|3",
    },
    "game_lint": {
        "module": "daedalus.game_lint",
        "tags": ("games", "verify"),
        "when": "ruff / clang-tidy / Roslyn / GDScript smells. format does not write.",
        "args_hint": "action=check|format, path or slug",
    },
    "profiler": {
        "module": "daedalus.profiler",
        "tags": ("games", "verify"),
        "when": "Parse Tracy CSV, NUnit, Chrome traces, FPS logs. Unity tests are not Play.",
        "args_hint": "action=parse|unity_tests, kind=tracy|…",
    },
    "game_canon": {
        "module": "daedalus.game_canon",
        "tags": ("games", "docs", "canon"),
        "when": "Study OSS (GLM, idTech, tinyrenderer). Steal structure; never copy books or Chop Chop.",
        "knowledge": ("knowledge/game_canon.md", "knowledge/game_roadmaps.md", "skills/game_canon.md"),
        "args_hint": "action=list|study|open|chunk, id=glm|milo-path|…",
    },
}

# Markers that are not native function calls (persona / NEED loop only).
_NEED_ONLY: tuple[ToolEntry, ...] = (
    ToolEntry(
        name="NEED_WORKER",
        kind="need",
        module="daedalus.workers",
        tags=("agents",),
        when="Stateless LLM helper. No files. fast|balanced|deep.",
        need_marker="NEED_WORKER",
        args_hint="fast|balanced|deep | task",
    ),
    ToolEntry(
        name="NEED_PROPOSE",
        kind="need",
        module="daedalus.agent",
        tags=("harness",),
        when="Harness patch. Waits for owner Approve. Never silent-edit the kernel.",
        aliases=("NEED_PROPOSE_START", "NEED_PROPOSE_END"),
        need_marker="NEED_PROPOSE_START",
    ),
    ToolEntry(
        name="NEED_PLAYBOOK",
        kind="need",
        module="daedalus.playbooks",
        tags=("docs",),
        when="RIPER process playbook (research/plan/execute/debug). Kernel task graphs are separate.",
        need_marker="NEED_PLAYBOOK",
        args_hint="riper-research | riper-plan | name",
    ),
    ToolEntry(
        name="NEED_BLOCKED",
        kind="need",
        module="daedalus.agent",
        tags=("flow",),
        when="Last resort after split/search/simplify. Not the first reply.",
        need_marker="NEED_BLOCKED",
    ),
    ToolEntry(
        name="NEED_BUNDLE",
        kind="need",
        module="daedalus.agent",
        tags=("harness",),
        when="Multi-file proposal bundle.",
        aliases=("NEED_BUNDLE_START", "NEED_STAGE", "NEED_BUNDLE_COMMIT"),
        need_marker="NEED_BUNDLE_START",
    ),
    ToolEntry(
        name="NEED_PLAYGROUND_EDIT",
        kind="need",
        module="daedalus.playground",
        tags=("playground",),
        when="Exact search/replace in a Playground file. Prefer write_file for new files.",
        need_marker="NEED_PLAYGROUND_EDIT",
    ),
    ToolEntry(
        name="NEED_TASK_STATE",
        kind="need",
        module="daedalus.task_state",
        tags=("flow",),
        when="Cross-session checklist.",
        need_marker="NEED_TASK_STATE",
    ),
    ToolEntry(
        name="NEED_MODE",
        kind="need",
        module="daedalus.mode",
        tags=("flow",),
        when="Switch talk|build|auto.",
        need_marker="NEED_MODE",
    ),
    ToolEntry(
        name="NEED_LOGS",
        kind="need",
        module="daedalus.agent",
        tags=("harness", "debug"),
        when="Last N harness log lines.",
        need_marker="NEED_LOGS",
    ),
)


def canonical(raw: str) -> str:
    """Map dump names, NEED_* markers, and aliases to the native name."""
    name = (raw or "").strip()
    if not name:
        return name
    low = name.lower()
    if low in ALIASES:
        return ALIASES[low]
    if name in ALIASES:
        return ALIASES[name]
    try:
        from .tool_protocol import MARKER_TO_NATIVE
        if name in MARKER_TO_NATIVE:
            return MARKER_TO_NATIVE[name]
    except Exception:
        pass
    meta = _NATIVE_META.get(name) or _NATIVE_META.get(low)
    if meta:
        return name if name in _NATIVE_META else low
    for native, row in _NATIVE_META.items():
        aliases = {str(a).lower() for a in row.get("aliases") or ()}
        if low in aliases or name in (row.get("aliases") or ()):
            return native
        marker = str(row.get("need_marker") or "")
        if name == marker or low == marker.lower():
            return native
    return name


def native_entries() -> list[ToolEntry]:
    from .native_tools import NATIVE_TOOL_SPECS

    out: list[ToolEntry] = []
    for spec in NATIVE_TOOL_SPECS:
        name = str(spec["name"])
        meta = _NATIVE_META.get(name, {})
        desc = str(spec.get("description") or "")
        out.append(ToolEntry(
            name=name,
            kind="native",
            module=str(meta.get("module") or "daedalus.native_tools"),
            tags=tuple(meta.get("tags") or ()),
            when=str(meta.get("when") or desc.split(".")[0][:180]),
            aliases=tuple(meta.get("aliases") or ()),
            need_marker=str(meta.get("need_marker") or ""),
            knowledge=tuple(meta.get("knowledge") or ()),
            args_hint=str(meta.get("args_hint") or ""),
        ))
    return out


def skill_entries() -> list[ToolEntry]:
    root = package_root() / "skills"
    rows: list[ToolEntry] = []
    if not root.is_dir():
        return rows
    for path in sorted(root.glob("*.md")):
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            continue
        meta: dict[str, str] = {}
        if raw.startswith("---"):
            end = raw.find("\n---", 3)
            if end > 0:
                for line in raw[3:end].splitlines():
                    if ":" in line:
                        k, v = line.split(":", 1)
                        meta[k.strip()] = v.strip().strip('"')
        sid = meta.get("name") or path.stem
        rows.append(ToolEntry(
            name=f"skill:{sid}",
            kind="skill",
            module="daedalus.learned_skills",
            tags=("docs", "skills"),
            when=meta.get("description") or "",
            knowledge=(f"skills/{path.name}",),
            args_hint=f"skill(action=load, id={sid})",
        ))
    return rows


def knowledge_entries() -> list[ToolEntry]:
    root = package_root() / "knowledge"
    rows: list[ToolEntry] = []
    if not root.is_dir():
        return rows
    for path in sorted(root.glob("*.md")):
        rows.append(ToolEntry(
            name=f"knowledge:{path.stem}",
            kind="knowledge",
            module="daedalus.knowledge",
            tags=("docs", "knowledge"),
            when=f"RAG source {path.name}",
            knowledge=(f"knowledge/{path.name}",),
        ))
    return rows


def full_catalog() -> list[ToolEntry]:
    return native_entries() + list(_NEED_ONLY) + skill_entries() + knowledge_entries()


def native_names() -> set[str]:
    from .native_tools import NATIVE_TOOL_SPECS
    return {str(t["name"]) for t in NATIVE_TOOL_SPECS}


def get(name: str) -> ToolEntry | None:
    key = canonical(name)
    for row in native_entries():
        if row.name == key:
            return row
    for row in _NEED_ONLY:
        if row.name == name or row.need_marker == name:
            return row
    return None


def doc_path(rel: str) -> Path | None:
    """Resolve a package-relative knowledge/skill path."""
    rel = (rel or "").strip().lstrip("/")
    if not rel or ".." in rel.split("/"):
        return None
    path = package_root() / rel
    return path if path.is_file() else None


def excerpt(rel: str, *, limit: int = 1400) -> str:
    path = doc_path(rel)
    if path is None:
        return ""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end > 0:
            text = text[end + 4:].lstrip("\n")
    text = text.strip()
    if len(text) > limit:
        return text[:limit].rstrip() + "\n…"
    return text
