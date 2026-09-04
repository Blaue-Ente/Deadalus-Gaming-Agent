"""Curated open-source game canon — study well-written engines, never clone them into a product.

Five references the agent may read (clone on demand under ``$DAEDALUS_DATA/canon/``):

* id-Software/DOOM-3-BFG — idLib math, SIMD, game physics layout
* Anuken/Mindustry — lockstep-ish net, entity comps, Java game loop
* munificent/game-programming-patterns — named patterns (study, do not paste the book)
* ssloy/tinyrenderer — software rasterizer taught in small files
* nothings/stb — single-header C, data-oriented helpers

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
)

_ID_INDEX = {str(row["id"]): row for row in CANON}
_MAX_FILE = 80_000
_TIMEOUT = 20.0


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
            "large": bool(item.get("large")),
            "cloned": (dest / ".git").is_dir() or (dest / "README.md").is_file(),
            "files": list(item["files"]),
        })
    return {
        "ok": True,
        "canon": rows,
        "note": "Study and write original code. Never copy assets or paste the book into a game.",
    }


def study(entry_id: str) -> dict[str, Any]:
    item = get(entry_id)
    dest = local_dir(str(item["id"]))
    return {
        "ok": True,
        "id": item["id"],
        "title": item["title"],
        "repo": item["repo"],
        "license": item["license"],
        "study": list(item["study"]),
        "steal": list(item["steal"]),
        "do_not_copy": list(item["do_not_copy"]),
        "files": list(item["files"]),
        "cloned": (dest / ".git").is_dir(),
        "how": (
            "open a listed file (local clone or GitHub raw). "
            "math3d-check formulas. Write new code in the Playground slug."
        ),
    }


def _allowed_file(item: dict[str, Any], rel: str) -> str:
    rel = (rel or "").replace("\\", "/").lstrip("/")
    if not rel or ".." in rel.split("/"):
        raise ValueError("invalid path")
    allowed = set(item["files"])
    if rel not in allowed:
        raise ValueError(f"{rel} is not on the allowlist for {item['id']}: {sorted(allowed)}")
    return rel


def _offline() -> bool:
    try:
        from .offline import enabled
        return enabled()
    except Exception:
        return False


def _fetch_raw(item: dict[str, Any], rel: str) -> str:
    url = f"https://raw.githubusercontent.com/{item['owner_repo']}/{item['branch']}/{rel}"
    with httpx.Client(timeout=_TIMEOUT, follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
        text = resp.text
    if len(text) > _MAX_FILE:
        text = text[:_MAX_FILE] + "\n… [truncated]"
    return text


def open_file(entry_id: str, rel: str = "") -> dict[str, Any]:
    item = get(entry_id)
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
            source = "github-raw"
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


def search_local(entry_id: str, query: str, *, limit: int = 20) -> dict[str, Any]:
    item = get(entry_id)
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
        if path.suffix.lower() not in {".h", ".hpp", ".c", ".cc", ".cpp", ".java", ".md", ".markdown", ".cs", ".gd"}:
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
            payload["path" if action in ("open", "read") else "query"] = args[2]
        result = run_action(action, **payload)
    print(json.dumps(result, ensure_ascii=False, indent=2)[:20000])
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
