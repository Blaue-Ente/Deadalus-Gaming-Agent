"""Curated open-source game canon — study well-written engines, never clone them into a product.

References the agent may read (clone on demand under ``$DAEDALUS_DATA/canon/``):

* id-Software/DOOM-3-BFG — idLib math, SIMD, game physics layout
* Anuken/Mindustry — lockstep-ish net, entity comps, Java game loop
* munificent/game-programming-patterns — named patterns (study, do not paste the book)
* ssloy/tinyrenderer — software rasterizer taught in small files
* nothings/stb — single-header C, data-oriented helpers
* g-truc/glm — GLSL-shaped C++ math (do not vendor the tree into the harness)
* microsoft/DirectXMath — SIMD vectors for Windows / D3D
* wolfpld/tracy — C++ profiler; parse exports with ``profiler`` kind=tracy
* miloyip/game-programmer — study path (readme + isbn.csv; not the PDFs)
* CIS-Team/Game-Development-Roadmap-2024 — modern stack README
* stevinz/awesome-game-engine-dev — engine-dev awesome list (README, truncated)
* Azurelol/GameEngineArchitectureSamples — C++ OOP samples; ``chunk`` by class
* UnityTechnologies/open-project-1 — Chop Chop wiki (events, FSM); do not clone the Unity tree into a game

kurong00/GameProgramBooks is listed only to refuse clone/open — it is a copyrighted ebook dump.

RenderDoc is owner-side frame capture — study notes in knowledge, do not claim a cloud GUI.

Inspiration only. Do not copy assets, trademarks, or paste large excerpts into Playground games.

Native tool: ``game_canon``. CLI: ``python -m daedalus.game_canon``.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx

CANON: tuple[dict[str, Any], ...] = (
    {
        "id": "doom3-bfg",
        "title": "DOOM 3 BFG Edition (id Software)",
        "repo": "https://github.com/id-Software/DOOM-3-BFG",
        "owner_repo": "id-Software/DOOM-3-BFG",
        "branch": "master",
        "license": "GPL-3.0",
        "large": True,
        "study": [
            "idLib vectors/matrices/quaternions as plain structs with SIMD variants",
            "idList / contiguous containers instead of pointer graphs",
            "renderer backend separated from game logic",
            "deterministic-ish math helpers used by both game and renderer",
        ],
        "steal": [
            "Keep hot data in arrays of structs the CPU can prefetch",
            "Put normalize/dot/cross in one math header and test them",
            "Keep game objects as ids + components, not deep virtual trees on the hot path",
        ],
        "do_not_copy": [
            "Doom branding, maps, textures, sounds",
            "verbatim renderer or tool code into a Playground game",
        ],
        "files": (
            "neo/idlib/math/Vector.h",
            "neo/idlib/math/Quat.h",
            "neo/idlib/math/Matrix.h",
            "neo/idlib/containers/List.h",
            "README.md",
        ),
    },
    {
        "id": "mindustry",
        "title": "Mindustry (Anuken)",
        "repo": "https://github.com/Anuken/Mindustry",
        "owner_repo": "Anuken/Mindustry",
        "branch": "master",
        "license": "GPL-3.0",
        "large": False,
        "study": [
            "Entity components (arc ECS-style) instead of God-objects",
            "NetClient / NetServer split: world is simulated, packets are deltas",
            "Headless server path — logic does not assume a renderer",
        ],
        "steal": [
            "Simulate on the server (or host), render on the client",
            "Keep unit state in packed fields that serialize cheaply",
            "Separate input intent from applied simulation step",
        ],
        "do_not_copy": [
            "Mindustry art, sfx, campaign maps, GPL sources into MIT games without a license plan",
        ],
        "files": (
            "README.md",
            "core/src/mindustry/core/NetClient.java",
            "core/src/mindustry/core/NetServer.java",
            "core/src/mindustry/net/Net.java",
        ),
    },
    {
        "id": "game-programming-patterns",
        "title": "Game Programming Patterns (Robert Nystrom)",
        "repo": "https://github.com/munificent/game-programming-patterns",
        "owner_repo": "munificent/game-programming-patterns",
        "branch": "master",
        "license": "CC-BY-NC-ND (book text) — study, do not republish",
        "large": False,
        "study": [
            "Data Locality — the DOD chapter: cache lines, SoA vs AoS",
            "Component, Object Pool, Flyweight, Spatial Partition",
            "Game Loop, Update Method, Event Queue, Command",
        ],
        "steal": [
            "Name the pattern in a comment and write original code",
            "Read data-locality before inventing an entity heap of pointers",
        ],
        "do_not_copy": [
            "Do not paste book markdown into the product repo or a shipped game",
            "Do not vendor the book as Daedalus knowledge verbatim",
        ],
        "files": (
            "README.md",
            "book/data-locality.markdown",
            "book/component.markdown",
            "book/object-pool.markdown",
            "book/game-loop.markdown",
            "book/event-queue.markdown",
        ),
    },
    {
        "id": "tinyrenderer",
        "title": "tinyrenderer (Dmitry V. Sokolov)",
        "repo": "https://github.com/ssloy/tinyrenderer",
        "owner_repo": "ssloy/tinyrenderer",
        "branch": "master",
        "license": "MIT (check repo LICENSE)",
        "large": False,
        "study": [
            "Software rasterizer taught lesson-by-lesson: barycentric, z-buffer, textures",
            "Tiny geometry helpers — vectors as arrays, no engine framework",
            "Projection as matrices you can print and test",
        ],
        "steal": [
            "Implement one pipeline stage, verify with math3d, then the next",
            "Keep lesson-sized files; do not start with a 4k-line Renderer.cpp",
        ],
        "do_not_copy": [
            "Do not drop the whole tutorial tree into a Playground slug as the game",
        ],
        "files": (
            "README.md",
            "geometry.h",
            "our_gl.h",
            "main.cpp",
        ),
    },
    {
        "id": "stb",
        "title": "stb single-header libraries (Sean Barrett)",
        "repo": "https://github.com/nothings/stb",
        "owner_repo": "nothings/stb",
        "branch": "master",
        "license": "MIT / public domain (per file)",
        "large": False,
        "study": [
            "stb_ds.h — stretchy buffers / hash maps as macros over contiguous memory",
            "stb_image.h — practical C API: one call, caller owns the buffer",
            "No virtual interfaces on the pixel hot path",
        ],
        "steal": [
            "Prefer a concrete buffer + length over hidden containers in C/C++ game code",
            "Document ownership: who allocates, who frees",
        ],
        "do_not_copy": [
            "Do not gulp 8k-line headers into the harness; vendor into a game only with license notes",
        ],
        "files": (
            "README.md",
            "stb_ds.h",
        ),
    },
    {
        "id": "glm",
        "title": "OpenGL Mathematics (GLM)",
        "repo": "https://github.com/g-truc/glm",
        "owner_repo": "g-truc/glm",
        "branch": "master",
        "license": "MIT / Happy Bunny (check repo)",
        "large": False,
        "study": [
            "GLSL-shaped types: vec3, mat4, quat, lookAt, perspective, mix/slerp",
            "Header-only; column-major like GLSL",
            "Default C++ math for a native engine or shader-side CPU code",
        ],
        "steal": [
            "Use GLM (or the engine's Vector3) instead of a home-grown vec class",
            "Still verify a suspicious SLERP or ray with math3d claimed",
        ],
        "do_not_copy": [
            "Do not vendor the whole GLM tree into the Daedalus harness",
        ],
        "files": (
            "readme.md",
            "manual.md",
        ),
    },
    {
        "id": "directxmath",
        "title": "DirectXMath (Microsoft)",
        "repo": "https://github.com/microsoft/DirectXMath",
        "owner_repo": "microsoft/DirectXMath",
        "branch": "main",
        "license": "MIT",
        "large": False,
        "study": [
            "XMVECTOR / XMMATRIX SIMD types, 16-byte alignment",
            "Use on Windows / Xbox / D3D — not as the Godot or Three.js default",
        ],
        "steal": [
            "Call XMVector3Dot / XMMatrixLookAtRH instead of scalar loops",
            "Keep hot data aligned; don't mix with GLM types in the same struct",
        ],
        "do_not_copy": [
            "Do not gulp Inc/ into the harness; vendor into a Win64 game with the license",
        ],
        "files": (
            "README.md",
        ),
    },
    {
        "id": "tracy",
        "title": "Tracy Profiler",
        "repo": "https://github.com/wolfpld/tracy",
        "owner_repo": "wolfpld/tracy",
        "branch": "master",
        "license": "BSD-3-Clause (check repo)",
        "large": False,
        "study": [
            "ZoneScoped instrumentation on C++ hot paths",
            "Export CSV/trace and parse with profiler kind=tracy",
            "RenderDoc is a separate owner-side GPU capture — not this repo",
        ],
        "steal": [
            "Instrument after a measured spike, not as decoration in a Vite racer",
            "Fix the heaviest named zones first",
        ],
        "do_not_copy": [
            "Do not ship the Tracy GUI into a Playground slug",
        ],
        "files": (
            "README.md",
        ),
    },
    {
        "id": "milo-path",
        "title": "A Study Path for Game Programmer (Milo Yip)",
        "repo": "https://github.com/miloyip/game-programmer",
        "owner_repo": "miloyip/game-programmer",
        "branch": "master",
        "license": "CC BY-SA (check repo)",
        "kind": "roadmap",
        "large": False,
        "study": [
            "Curriculum graph: math → physics → graphics → engine systems",
            "isbn.csv is titles and ISBNs — buy the books; do not ingest scans",
            "readme.md explains how the DOT/PDF posters were built",
        ],
        "steal": [
            "Use the path as a reading order, not as a dump into RAG",
            "When a formula shows up, verify it with math3d before C++",
        ],
        "do_not_copy": [
            "Do not fetch game-programmer.pdf / huge PNG posters into the harness",
            "Do not vendor book scans; isbn.csv is bibliography only",
        ],
        "files": (
            "readme.md",
            "isbn.csv",
        ),
    },
    {
        "id": "game-program-books",
        "title": "GameProgramBooks (ebook dump — refused)",
        "repo": "https://github.com/kurong00/GameProgramBooks",
        "owner_repo": "kurong00/GameProgramBooks",
        "branch": "master",
        "license": "not ingested — copyrighted ebooks",
        "kind": "books-refuse",
        "large": True,
        "no_clone": True,
        "no_open": True,
        "study": [
            "This GitHub tree is a collection of pirated textbooks aligned to Milo Yip's path",
            "Daedalus will not clone it, open PDFs, or index book text into RAG",
            "Legal study: milo-path isbn.csv + buy the titles; patterns: game-programming-patterns",
        ],
        "steal": [
            "Follow the Milo Yip order; implement original code after you read a legally obtained book",
        ],
        "do_not_copy": [
            "Never clone, fetch, or paste ebook PDFs into Daedalus, Playground, or RAG",
        ],
        "files": (),
        "refuse": (
            "kurong00/GameProgramBooks is a dump of copyrighted ebooks. "
            "Daedalus will not clone, open PDFs, or ingest book text. "
            "Use milo-path (isbn.csv) for titles, then buy the books. "
            "Named patterns: game-programming-patterns."
        ),
    },
    {
        "id": "cis-roadmap",
        "title": "Game Development Roadmap 2024 (CIS-Team)",
        "repo": "https://github.com/CIS-Team/Game-Development-Roadmap-2024",
        "owner_repo": "CIS-Team/Game-Development-Roadmap-2024",
        "branch": "main",
        "license": "check repo LICENSE",
        "kind": "roadmap",
        "large": False,
        "study": [
            "Modern stack notes: algorithms, Unity-oriented books, architecture pointers",
            "README.md only — chunk headings for RAG, do not vendor the file into daedalus/",
        ],
        "steal": [
            "Treat it as a checklist of topics, then write original systems in the Playground slug",
        ],
        "do_not_copy": [
            "Do not paste the whole README into a game or the harness",
        ],
        "files": (
            "README.md",
        ),
    },
    {
        "id": "awesome-engine",
        "title": "awesome-game-engine-dev (stevinz)",
        "repo": "https://github.com/stevinz/awesome-game-engine-dev",
        "owner_repo": "stevinz/awesome-game-engine-dev",
        "branch": "main",
        "license": "CC0 / check repo LICENSE",
        "kind": "architecture",
        "large": False,
        "study": [
            "Curated links for low-level core, audio, geometry, graphics, tools",
            "open README.md (truncated at 80k) then chunk by heading",
            "ARCHIVE.md holds retired links",
        ],
        "steal": [
            "Follow a link, study that project's structure, write original code",
            "Do not scrape the list into a Playground slug as content",
        ],
        "do_not_copy": [
            "Do not vendor the 140k README into daedalus/knowledge",
        ],
        "files": (
            "README.md",
            "ARCHIVE.md",
        ),
    },
    {
        "id": "engine-arch-samples",
        "title": "Game Engine Architecture Samples (Azurelol)",
        "repo": "https://github.com/Azurelol/GameEngineArchitectureSamples",
        "owner_repo": "Azurelol/GameEngineArchitectureSamples",
        "branch": "master",
        "license": "check repo (educational C++)",
        "kind": "architecture",
        "large": False,
        "study": [
            "Small C++ samples: events, ECS-ish architecture, factory, audio, serialization",
            "action=chunk splits class/struct with a Context comment for RAG",
            "Read SampleEventSystem headers before inventing a singleton EventManager",
        ],
        "steal": [
            "Name the pattern (event, component, factory) and write original types",
            "Keep samples in $DAEDALUS_DATA/canon/ — never copy them into a Playground game",
        ],
        "do_not_copy": [
            "Do not drop SampleZilchEngine or VS projects into a player-facing slug",
        ],
        "files": (
            "README.md",
            "SampleArchitecture/README.md",
            "SampleEventSystem/README.md",
            "SampleEventSystem/SampleEvent/Event.h",
            "SampleEventSystem/SampleEvent/Event.cpp",
            "SampleEventSystem/SampleEvent/EventDelegate.h",
            "SampleEventSystem/SampleEvent/EventInterface.h",
            "SampleEventSystem/SampleEvent/Component.h",
            "SampleEventSystem/SampleEvent/Entity.h",
            "SampleArchitecture/SampleECS/Main.cpp",
        ),
    },
    {
        "id": "unity-chop",
        "title": "Unity Open Project 1 — Chop Chop (architecture wiki)",
        "repo": "https://github.com/UnityTechnologies/open-project-1",
        "owner_repo": "UnityTechnologies/open-project-1",
        "branch": "main",
        "license": "Unity companion license / check repo",
        "kind": "architecture",
        "large": True,
        "study": [
            "Wiki: additive scenes, PersistentManagers, ScriptableObject Event Channels, SO-driven FSM",
            "open wiki/Game-architecture-overview.md then Event-system and State-Machine",
            "This cloud has no Unity Editor — scaffold is not Play; do not clone UOP1_Project into Playground",
        ],
        "steal": [
            "Event channels instead of a scene-wide singleton for cross-scene talk",
            "Data in ScriptableObjects; runtime classes execute; name the pattern in a comment",
        ],
        "do_not_copy": [
            "Chop Chop art, audio, characters, trademarks, or UOP1_Project scripts into a Daedalus game",
            "Do not treat Docs/ screenshots as a code dump",
        ],
        "files": (
            "README.md",
            "wiki/Game-architecture-overview.md",
            "wiki/Event-system.md",
            "wiki/State-Machine.md",
        ),
    },
)

_ID_INDEX = {str(row["id"]): row for row in CANON}
_MAX_FILE = 80_000
_TIMEOUT = 20.0
_CHUNK_LIMIT = 12
_CHUNK_CHARS = 3_500
_CODE_SUFFIX = {".h", ".hpp", ".c", ".cc", ".cpp", ".cs"}
_MD_SUFFIX = {".md", ".markdown"}
_CLASS_RE = re.compile(r"(?m)^(class|struct)\s+(\w+)\b")
_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+)$")
_FOLDER_CONTEXT: tuple[tuple[str, str], ...] = (
    ("SampleEventSystem", "Event System Pattern"),
    ("SampleArchitecture", "Engine Architecture / ECS"),
    ("SampleFactory", "Factory Pattern"),
    ("SampleActions", "Command / Action Pattern"),
    ("wiki/", "Unity Chop Chop architecture"),
    ("book/", "Game Programming Patterns (study, do not paste)"),
)


def canon_root() -> Path:
    from .paths import data_root
    path = data_root() / "canon"
    path.mkdir(parents=True, exist_ok=True)
    return path


def local_dir(entry_id: str) -> Path:
    safe = _safe_id(entry_id)
    return canon_root() / safe


def _safe_id(entry_id: str) -> str:
    raw = "".join(ch if ch.isalnum() or ch in "-_" else "" for ch in (entry_id or ""))
    if raw not in _ID_INDEX:
        raise ValueError(f"unknown canon id {entry_id}")
    return raw


def get(entry_id: str) -> dict[str, Any]:
    row = _ID_INDEX.get((entry_id or "").strip())
    if not row:
        raise ValueError(f"unknown canon id {entry_id}. Known: {', '.join(_ID_INDEX)}")
    return row


def list_canon() -> dict[str, Any]:
    rows = []
    for item in CANON:
        dest = local_dir(str(item["id"]))
        rows.append({
            "id": item["id"],
            "title": item["title"],
            "repo": item["repo"],
            "license": item["license"],
            "kind": item.get("kind") or "engine",
            "large": bool(item.get("large")),
            "no_clone": bool(item.get("no_clone")),
            "cloned": (dest / ".git").is_dir() or (dest / "README.md").is_file(),
            "files": list(item["files"]),
        })
    return {
        "ok": True,
        "canon": rows,
        "note": (
            "Study and write original code. Never copy assets, paste books, "
            "or clone GameProgramBooks / Chop Chop into a Playground game. "
            "action=chunk splits allowlisted C++/markdown for RAG."
        ),
    }


def study(entry_id: str) -> dict[str, Any]:
    item = get(entry_id)
    dest = local_dir(str(item["id"]))
    payload: dict[str, Any] = {
        "ok": True,
        "id": item["id"],
        "title": item["title"],
        "repo": item["repo"],
        "license": item["license"],
        "kind": item.get("kind") or "engine",
        "study": list(item["study"]),
        "steal": list(item["steal"]),
        "do_not_copy": list(item["do_not_copy"]),
        "files": list(item["files"]),
        "cloned": (dest / ".git").is_dir(),
        "no_clone": bool(item.get("no_clone")),
        "how": (
            "open a listed file (local clone, GitHub raw, or wiki/). "
            "chunk C++/markdown for RAG. math3d-check formulas. "
            "Write new code in the Playground slug."
        ),
    }
    if item.get("refuse"):
        payload["refuse"] = item["refuse"]
    return payload


def _allowed_file(item: dict[str, Any], rel: str) -> str:
    rel = (rel or "").replace("\\", "/").lstrip("/")
    if not rel or ".." in rel.split("/"):
        raise ValueError("invalid path")
    allowed = set(item["files"])
    if rel not in allowed:
        raise ValueError(f"{rel} is not on the allowlist for {item['id']}: {sorted(allowed)}")
    return rel


def _refuse_ingest(item: dict[str, Any]) -> dict[str, Any] | None:
    if item.get("no_open") or (item.get("no_clone") and not item.get("files")):
        return {
            "ok": False,
            "id": item["id"],
            "error": str(item.get("refuse") or "ingest refused for this canon id"),
        }
    return None


def folder_context(rel: str) -> str:
    path = (rel or "").replace("\\", "/")
    for prefix, label in _FOLDER_CONTEXT:
        if prefix in path:
            return label
    return Path(path).parent.as_posix() or Path(path).stem


def raw_url(item: dict[str, Any], rel: str) -> str:
    """GitHub raw URL. wiki/Foo.md maps to the repo wiki, not the git tree."""
    path = (rel or "").replace("\\", "/").lstrip("/")
    owner = item["owner_repo"]
    if path.startswith("wiki/"):
        page = path[5:]
        return f"https://raw.githubusercontent.com/wiki/{owner}/{page}"
    return f"https://raw.githubusercontent.com/{owner}/{item['branch']}/{path}"


def chunk_cpp(text: str, rel: str, *, context: str = "", limit: int = _CHUNK_LIMIT) -> list[dict[str, Any]]:
    """Split C/C++/C# by class/struct. Adds a Context comment for RAG metadata."""
    ctx = context or folder_context(rel)
    matches = list(_CLASS_RE.finditer(text or ""))
    if not matches:
        body = (text or "").strip()
        if not body:
            return []
        return [{
            "kind": "file",
            "name": Path(rel).stem,
            "context": ctx,
            "file": rel,
            "text": f"// Context: {ctx}\n{body[:_CHUNK_CHARS]}",
        }]
    out: list[dict[str, Any]] = []
    for i, match in enumerate(matches):
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = (text[start:end] or "").strip()
        if len(body) < 20:
            continue
        out.append({
            "kind": match.group(1),
            "name": match.group(2),
            "context": ctx,
            "file": rel,
            "text": f"// Context: {ctx}\n{body[:_CHUNK_CHARS]}",
        })
        if len(out) >= limit:
            break
    return out


def chunk_markdown(text: str, rel: str, *, context: str = "", limit: int = _CHUNK_LIMIT) -> list[dict[str, Any]]:
    """Split markdown on ATX headings for RAG (READMEs / wiki pages)."""
    ctx = context or folder_context(rel)
    sections: list[tuple[str, str]] = []
    title = Path(rel).stem
    buf: list[str] = []
    for line in (text or "").splitlines(keepends=True):
        heading = _HEADING_RE.match(line.rstrip("\r\n"))
        if heading and buf:
            sections.append((title, "".join(buf)))
            title = heading.group(2).strip()
            buf = [line]
        else:
            buf.append(line)
    if buf:
        sections.append((title, "".join(buf)))
    out: list[dict[str, Any]] = []
    for name, body in sections:
        body = body.strip()
        if len(body) < 40:
            continue
        out.append({
            "kind": "heading",
            "name": name[:120],
            "context": ctx,
            "file": rel,
            "text": f"<!-- Context: {ctx} / {name} -->\n{body[:_CHUNK_CHARS]}",
        })
        if len(out) >= limit:
            break
    return out


def _chunk_targets(item: dict[str, Any], rel: str) -> list[str]:
    files = [str(f) for f in item.get("files") or ()]
    if rel:
        return [_allowed_file(item, rel)]
    code = [f for f in files if Path(f).suffix.lower() in _CODE_SUFFIX]
    md = [f for f in files if Path(f).suffix.lower() in _MD_SUFFIX]
    if code:
        return code[:6]
    return md[:3]


def _offline() -> bool:
    try:
        from .offline import enabled
        return enabled()
    except Exception:
        return False


def _fetch_raw(item: dict[str, Any], rel: str) -> str:
    url = raw_url(item, rel)
    with httpx.Client(timeout=_TIMEOUT, follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
        text = resp.text
    if len(text) > _MAX_FILE:
        text = text[:_MAX_FILE] + "\n… [truncated]"
    return text


def open_file(entry_id: str, rel: str = "") -> dict[str, Any]:
    item = get(entry_id)
    blocked = _refuse_ingest(item)
    if blocked:
        return blocked
    if not item.get("files"):
        return {"ok": False, "id": item["id"], "error": "no allowlisted files"}
    path = _allowed_file(item, rel or item["files"][0])
    dest = local_dir(str(item["id"])) / path
    source = "local"
    if dest.is_file():
        text = dest.read_text(encoding="utf-8", errors="replace")
        if len(text) > _MAX_FILE:
            text = text[:_MAX_FILE] + "\n… [truncated]"
    else:
        if _offline():
            return {
                "ok": False,
                "error": "offline: file not cloned. action=study still works.",
                "id": item["id"],
                "path": path,
            }
        try:
            text = _fetch_raw(item, path)
            source = "github-wiki" if path.startswith("wiki/") else "github-raw"
        except Exception as exc:
            return {"ok": False, "error": f"fetch failed: {exc}"[:300], "id": item["id"], "path": path}
    return {
        "ok": True,
        "id": item["id"],
        "path": path,
        "source": source,
        "license": item["license"],
        "text": text,
        "do_not_copy": list(item["do_not_copy"]),
    }


def clone(entry_id: str, *, timeout: int = 180) -> dict[str, Any]:
    item = get(entry_id)
    if item.get("no_clone"):
        return {
            "ok": False,
            "id": item["id"],
            "error": str(item.get("refuse") or "clone refused for this canon id"),
        }
    if _offline():
        return {"ok": False, "error": "offline: clone disabled", "id": item["id"]}
    dest = local_dir(str(item["id"]))
    if (dest / ".git").is_dir():
        return {"ok": True, "id": item["id"], "path": str(dest), "already": True}
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    cmd = ["git", "clone", "--depth", "1", "--single-branch", item["repo"], str(dest)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "git clone timeout", "id": item["id"], "large": item.get("large")}
    if proc.returncode != 0:
        return {
            "ok": False,
            "id": item["id"],
            "error": ((proc.stderr or proc.stdout) or "git clone failed")[-800:],
        }
    return {"ok": True, "id": item["id"], "path": str(dest), "log": (proc.stdout or "")[-400:]}


def chunk(entry_id: str, rel: str = "", *, limit: int = _CHUNK_LIMIT) -> dict[str, Any]:
    """Fetch allowlisted files and split them for RAG (class/struct or markdown headings)."""
    item = get(entry_id)
    blocked = _refuse_ingest(item)
    if blocked:
        return blocked
    cap = max(1, min(int(limit or _CHUNK_LIMIT), 24))
    targets = _chunk_targets(item, rel)
    chunks: list[dict[str, Any]] = []
    remaining = cap
    for path in targets:
        opened = open_file(entry_id, path)
        if not opened.get("ok"):
            continue
        text = str(opened.get("text") or "")
        suffix = Path(path).suffix.lower()
        ctx = folder_context(path)
        if suffix in _CODE_SUFFIX:
            pieces = chunk_cpp(text, path, context=ctx, limit=remaining)
        else:
            pieces = chunk_markdown(text, path, context=ctx, limit=remaining)
        chunks.extend(pieces)
        remaining = cap - len(chunks)
        if remaining <= 0:
            break
    return {
        "ok": True,
        "id": item["id"],
        "chunks": chunks,
        "count": len(chunks),
        "note": "Context comments are RAG metadata. Write original code; do not paste chunks into a Playground game.",
        "do_not_copy": list(item["do_not_copy"]),
    }


def search_local(entry_id: str, query: str, *, limit: int = 20) -> dict[str, Any]:
    item = get(entry_id)
    if item.get("no_clone"):
        return {
            "ok": False,
            "id": item["id"],
            "error": str(item.get("refuse") or "search refused for this canon id"),
        }
    root = local_dir(str(item["id"]))
    if not root.is_dir():
        return {"ok": False, "error": "not cloned; call action=clone first", "id": item["id"]}
    needle = (query or "").strip()
    if len(needle) < 3:
        return {"ok": False, "error": "query too short"}
    rx = re.compile(re.escape(needle), re.I)
    hits: list[dict[str, Any]] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if ".git" in path.parts:
            continue
        if path.suffix.lower() not in {".h", ".hpp", ".c", ".cc", ".cpp", ".java", ".md", ".markdown", ".cs", ".gd", ".csv"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                hits.append({
                    "file": str(path.relative_to(root)).replace("\\", "/"),
                    "line": i,
                    "text": line.strip()[:200],
                })
                if len(hits) >= limit:
                    return {"ok": True, "id": item["id"], "hits": hits}
    return {"ok": True, "id": item["id"], "hits": hits}


def run_action(action: str = "list", **fields: Any) -> dict[str, Any]:
    act = (action or "list").strip().lower()
    entry_id = str(fields.get("id") or fields.get("name") or "")
    if act in ("list", "catalog", "status"):
        return list_canon()
    if act in ("study", "brief", "notes"):
        try:
            return study(entry_id)
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:300]}
    if act in ("open", "read", "file"):
        try:
            return open_file(entry_id, str(fields.get("path") or fields.get("file") or ""))
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:300]}
    if act == "clone":
        try:
            return clone(entry_id)
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:300]}
    if act in ("chunk", "chunks", "rag"):
        try:
            raw_limit = fields.get("limit")
            cap = int(raw_limit) if raw_limit not in (None, "") else _CHUNK_LIMIT
            return chunk(
                entry_id,
                str(fields.get("path") or fields.get("file") or ""),
                limit=cap,
            )
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:300]}
    if act in ("search", "grep"):
        try:
            return search_local(entry_id, str(fields.get("query") or fields.get("q") or ""))
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:300]}
    return {"ok": False, "error": f"unknown action {act}"}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        print(json.dumps(list_canon(), indent=2, ensure_ascii=False))
        return 0
    if args[0].startswith("{"):
        payload = json.loads(args[0])
        result = run_action(str(payload.pop("action", "list")), **payload)
    else:
        action = args[0]
        payload: dict[str, Any] = {}
        if len(args) > 1:
            payload["id"] = args[1]
        if len(args) > 2:
            payload["path" if action in ("open", "read", "chunk") else "query"] = args[2]
        result = run_action(action, **payload)
    print(json.dumps(result, ensure_ascii=False, indent=2)[:20000])
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
