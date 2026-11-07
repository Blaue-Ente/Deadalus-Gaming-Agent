"""Smart file context for Playground chat — prioritize source over assets."""
from __future__ import annotations

import json
from typing import Any

from . import playground as pg

_SKIP_CONTENT_SUFFIX = (
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico", ".woff", ".woff2",
    ".mp3", ".mp4", ".zip",
)
_SOURCE_EXTS = (".jsx", ".js", ".tsx", ".ts", ".vue", ".html", ".css", ".json")
_MAX_CONTENT_FILES = 28
_MAX_CHARS_CODE = 5000
_MAX_CHARS_DOC = 2000


def _score_path(path: str) -> tuple[int, str]:
    """Lower sort key = higher priority."""
    low = path.replace("\\", "/").lower()
    if low.endswith(_SKIP_CONTENT_SUFFIX):
        return (900, low)
    if low == "package.json":
        return (0, low)
    if low.startswith("vite.config"):
        return (1, low)
    if low in ("index.html", "main.jsx", "main.tsx", "app.jsx", "app.tsx"):
        return (2, low)
    if low.startswith("src/") and low.endswith(_SOURCE_EXTS):
        return (10, low)
    if low.startswith("hybrid/") and low.endswith(_SOURCE_EXTS):
        return (12, low)
    if low.startswith("assets/_") or low.endswith("_manifest.json") or low.endswith("_style_bible.json"):
        return (40, low)
    if low.startswith("studio/") and low.endswith(".md"):
        return (45, low)
    if "registry" in low and low.endswith(".json"):
        return (46, low)
    if low.startswith("assets/"):
        return (800, low)
    if low.endswith(_SOURCE_EXTS):
        return (30, low)
    return (60, low)


def diagnose_project(
    *,
    kind: str,
    tree: list[dict[str, Any]],
    coding_mode: str = "lite",
) -> str:
    """Human-readable project health check for chat."""
    paths = {t["path"] for t in tree}
    issues: list[str] = []
    warnings: list[str] = []

    src_code = [
        p for p in paths
        if p.startswith(("src/", "hybrid/src/"))
        and p.endswith((".js", ".jsx", ".ts", ".tsx", ".vue"))
    ]
    assets_png = [p for p in paths if p.startswith("assets/") and p.lower().endswith(".png")]

    if kind in ("vite", "vite-react", "node"):
        if "package.json" not in paths:
            issues.append("липсва package.json — проектът не е scaffold-нат като Vite/Node app")
        if not src_code:
            issues.append("липсва src/ с .jsx/.js компоненти — няма приложен код за build")
        if "vite.config.js" not in paths and "vite.config.ts" not in paths and kind.startswith("vite"):
            warnings.append("липсва vite.config — dev/build няма да тръгне")

    if kind == "expo" and "app.json" not in paths and "package.json" not in paths:
        issues.append("липсва Expo scaffold (package.json / app.json)")

    if kind == "unity":
        if "Packages/manifest.json" not in paths:
            warnings.append("липсва Unity Packages/manifest.json")
        cs = [p for p in paths if p.endswith(".cs")]
        if not cs:
            warnings.append("няма C# файлове — Unity codegen не е записан")

    if kind == "godot":
        if "project.godot" not in paths:
            issues.append("липсва project.godot")
        if not any(p.endswith(".gd") for p in paths):
            warnings.append("няма GDScript (.gd)")
        if not any(p.endswith(".tscn") for p in paths):
            warnings.append("няма сцена (.tscn)")

    if assets_png and not src_code and kind in ("vite", "vite-react", "static"):
        issues.append(
            f"има {len(assets_png)} PNG assets, но няма source code — Autopilot е спрял след Phase 3 (assets) "
            "или Phase 4 (build) не е записал файлове"
        )

    if not paths:
        issues.append("проектът е празен — няма файлове на диска")

    lines = ["ДИАГНОСТИКА НА ПРОЕКТА:"]
    lines.append(f"- kind={kind}, coding_mode={coding_mode}")
    lines.append(f"- файлове: {len(paths)} | src code: {len(src_code)} | PNG assets: {len(assets_png)}")
    if issues:
        lines.append("КРИТИЧНО:")
        lines.extend(f"  • {x}" for x in issues)
    if warnings:
        lines.append("ВНИМАНИЕ:")
        lines.extend(f"  • {x}" for x in warnings)
    if not issues and not warnings:
        lines.append("  • структурата изглежда пълна на пръв поглед")
    return "\n".join(lines)


async def diagnose_project_async(slug: str, proj: dict[str, Any]) -> str:
    kind = proj.get("kind", "static")
    coding_mode = proj.get("coding_mode", "lite")
    tree = pg.read_tree(slug)
    paths = {t["path"] for t in tree}
    base = diagnose_project(kind=kind, tree=tree, coding_mode=coding_mode)

    extra: list[str] = []
    for reg in ("src/registry/entities.json", "src/registry/levels.json"):
        if reg not in paths:
            continue
        try:
            data = json.loads(pg.read_file(slug, reg, max_bytes=8000))
            key = "entities" if "entities" in reg else "levels"
            bucket = data.get(key) or {}
            if isinstance(bucket, dict) and len(bucket) == 0:
                extra.append(f"  • {reg} е празен ({{}}) — registry не е попълнен от orchestrator")
        except Exception:
            extra.append(f"  • {reg} не може да се прочете")

    plan = await pg.load_autopilot_plan(slug)
    if plan:
        stages = plan.get("stages") or []
        done = sum(1 for s in stages if s.get("status") == "done")
        extra.append(f"  • autopilot план: {done}/{len(stages)} етапа done")
        if done < len(stages):
            extra.append("  • build не е завършен — пусни Autopilot отново или „Контролирано строене“")

    if extra:
        return base + "\n" + "\n".join(extra)
    return base


_SENSITIVE_PARTS = (".env", "secret", "credential", "password", "api_key", "token", ".pem")


def _is_sensitive(path: str) -> bool:
    low = path.lower()
    return any(p in low for p in _SENSITIVE_PARTS)


def build_chat_file_context(slug: str, *, max_files: int = _MAX_CONTENT_FILES) -> tuple[str, str]:
    """Return (file_tree_summary, prioritized_file_contents)."""
    tree = pg.read_tree(slug)
    if not tree:
        return "(няма файлове)", ""

    sorted_paths = sorted(tree, key=lambda t: _score_path(t["path"]))
    tree_lines = [f"  {t['path']} ({t.get('size', 0)} B)" for t in sorted_paths[:120]]
    if len(tree) > 120:
        tree_lines.append(f"  … + още {len(tree) - 120} файла")
    tree_summary = "ПЪЛЕН FILE TREE:\n" + "\n".join(tree_lines)

    content_parts: list[str] = []
    sent = 0
    for ent in sorted_paths:
        if sent >= max_files:
            break
        path = ent["path"]
        if _is_sensitive(path):
            continue
        score, _ = _score_path(path)
        if score >= 900:
            continue
        try:
            cap = _MAX_CHARS_DOC if path.endswith(".md") else _MAX_CHARS_CODE
            content = pg.read_file(slug, path, max_bytes=cap + 500)[:cap]
        except Exception:
            continue
        content_parts.append(f"\n\n--- {path} ---\n{content}")
        sent += 1

    # PNG list without binary content
    pngs = [t["path"] for t in tree if t["path"].lower().endswith(".png")]
    if pngs:
        content_parts.append(
            f"\n\n--- assets/ (PNG списък, без binary) ---\n"
            + "\n".join(f"  • {p}" for p in pngs[:40])
            + (f"\n  … +{len(pngs)-40} още" if len(pngs) > 40 else "")
        )

    return tree_summary, "".join(content_parts)
