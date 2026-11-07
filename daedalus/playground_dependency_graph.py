"""Stage dependency graph for parallel ultra orchestration."""
from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

SCAFFOLD_NAMES = frozenset({
    "package.json", "vite.config.js", "vite.config.ts", "index.html",
    "app.json", "babel.config.js", "requirements.txt", "streamlit_app.py",
    "main.py", "index.js",
})

FRAGMENT_ORDER: dict[str, int] = {
    "scaffold": 0,
    "file": 1,
    "system": 2,
    "entity": 3,
    "level": 4,
}


def _basename(path: str) -> str:
    return PurePosixPath(path.replace("\\", "/")).name


def _is_scaffold_stage(stage: dict[str, Any]) -> bool:
    files = stage.get("files") or []
    if stage.get("fragment_kind") == "scaffold":
        return True
    return any(_basename(f) in SCAFFOLD_NAMES for f in files)


def infer_depends_on(stage: dict[str, Any], all_stages: list[dict[str, Any]]) -> list[int]:
    """Return stage ids that must complete before this stage."""
    explicit = stage.get("depends_on") or []
    out: list[int] = []
    for x in explicit:
        try:
            out.append(int(x))
        except (TypeError, ValueError):
            pass
    if out:
        return sorted(set(out))

    sid = stage.get("id")
    my_files = stage.get("files") or []
    my_kind = str(stage.get("fragment_kind") or "file")
    my_order = FRAGMENT_ORDER.get(my_kind, 2)
    my_scaffold = _is_scaffold_stage(stage)

    for other in all_stages:
        oid = other.get("id")
        if oid is None or oid == sid:
            continue
        other_files = other.get("files") or []
        other_kind = str(other.get("fragment_kind") or "file")
        other_order = FRAGMENT_ORDER.get(other_kind, 2)
        other_scaffold = _is_scaffold_stage(other)

        if other_scaffold and not my_scaffold:
            out.append(int(oid))
            continue
        if other_order < my_order:
            out.append(int(oid))
            continue
        # entry/main depends on scaffold only — handled above
        for mf in my_files:
            for of in other_files:
                if mf == of:
                    continue
                # same directory entity before level
                if "/levels/" in mf and "/levels/" not in of and other_kind == "entity":
                    out.append(int(oid))

    return sorted(set(out))


def build_dependency_map(stages: list[dict[str, Any]]) -> dict[int, list[int]]:
    deps: dict[int, list[int]] = {}
    for s in stages:
        sid = s.get("id")
        if sid is None:
            continue
        deps[int(sid)] = infer_depends_on(s, stages)
    return deps


def annotate_stages(stages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach depends_on to each stage if missing."""
    out: list[dict[str, Any]] = []
    for s in stages:
        st = dict(s)
        if not st.get("depends_on"):
            st["depends_on"] = infer_depends_on(st, stages)
        out.append(st)
    return out


def ready_stage_indices(
    stages: list[dict[str, Any]],
    done_ids: set[int],
    *,
    skip_done: bool = True,
) -> list[int]:
    """Indices of stages whose dependencies are all in done_ids."""
    deps = build_dependency_map(stages)
    ready: list[int] = []
    for i, stage in enumerate(stages):
        sid = int(stage.get("id", i + 1))
        if skip_done and (stage.get("status") in ("done", "skipped") or sid in done_ids):
            continue
        needs = deps.get(sid, [])
        if all(int(d) in done_ids for d in needs):
            ready.append(i)
    return ready


def next_parallel_batch(
    stages: list[dict[str, Any]],
    done_ids: set[int],
    *,
    max_parallel: int = 3,
) -> list[int]:
    """Up to max_parallel ready stage indices."""
    ready = ready_stage_indices(stages, done_ids)
    return ready[: max(1, max_parallel)]
