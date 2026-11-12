"""Game visual quality guards — block CSS-gradient hero placeholders.

Used after asset generation in autopilot: if PNGs exist in assets/, code must
reference them; gradient-only character cards are rejected before LLM review.
"""
from __future__ import annotations

import re
from typing import Any

from .playground_assets import list_assets

# File/content hints that this is character/sprite UI, not a generic button bg
_CHARACTER_CTX = re.compile(
    r"(hero|player|character|sprite|avatar|enemy|opponent|герой|враг|противник|"
    r"избери|choose|battle|битка|fighter|unit)",
    re.I,
)

_GRADIENT = re.compile(r"linear-gradient|radial-gradient|conic-gradient", re.I)

_ASSET_REF = re.compile(
    r"assets/[\w./-]+\.(?:png|jpg|jpeg|webp|gif)|"
    r"['\"]/assets/|"
    r"ASSET_PATHS|preloadImages|assetLoader|"
    r"load\.image\s*\(|load\.spritesheet\s*\(|this\.load\.|"
    r"add\.image\s*\(|add\.sprite\s*\(|textures\.|"
    r"import\s+.*from\s+['\"]phaser['\"]|Phaser\.Scene|extends\s+Phaser\.Scene|"
    r"from\s+['\"]three['\"]|from\s+['\"]three/webgpu['\"]|"
    r"WebGLRenderer|WebGPURenderer|GLTFLoader|\.glb\b|"
    r"drawImage\s*\(|"
    r"new\s+Image\s*\(|"
    r"<img\b|"
    r"Image\s+source\s*=|"
    r"require\s*\(\s*['\"].*\.(?:png|jpg|jpeg|webp)",
    re.I,
)

# Per-file stages (entities, helpers) don't load assets — scenes/boot do.
_ENTITY_STAGE_PATH = re.compile(
    r"(?:^|/)(?:entities|entity|lib|utils|helpers|systems|physics|components)/",
    re.I,
)
_ENTITY_FILE_NAME = re.compile(
    r"(?:Projectile|Player|Enemy|Tile|Block|Bullet|Mob|Entity|Input|Collision)\.",
    re.I,
)

# CSS files and HTML structure files (no inline JS) never reference game assets.
# Assets are loaded in JS/TS/Vue stages that come later in the plan.
_SCRIPT_EXTS = frozenset({"js", "ts", "jsx", "tsx", "vue"})

# Canvas 2D primitive drawing — valid visual rendering without PNG sprites.
# fillRect/arc/strokeRect/fillText are all legitimate ways to draw a Snake/Pong/Tetris.
_CANVAS_DRAW = re.compile(
    r"(?:ctx|context|this\.ctx|this\.context)\s*\.\s*"
    r"(?:fillRect|arc|strokeRect|clearRect|fillText|strokeText|"
    r"beginPath|moveTo|lineTo|quadraticCurveTo|bezierCurveTo|drawImage)\s*\(",
    re.I,
)
_THREE_DRAW = re.compile(
    r"WebGLRenderer|WebGPURenderer|new\s+THREE\.|from\s+['\"]three['\"]",
    re.I,
)

_GAME_KINDS = frozenset({"static", "vite", "vite-react"})


def list_image_asset_paths(slug: str) -> list[str]:
    """Return existing image paths under assets/ (excluding manifest)."""
    return [
        a["path"]
        for a in list_assets(slug)
        if not a.get("missing") and a.get("bytes", 0) > 0
    ]


def _should_skip_project_asset_check(files: dict[str, str]) -> bool:
    """Entity/helper-only stages don't need asset paths in every file."""
    if not files:
        return True
    paths = list(files.keys())
    if all(
        _ENTITY_STAGE_PATH.search(p) or _ENTITY_FILE_NAME.search(p)
        for p in paths
    ):
        return True
    return False


def _stage_has_executable_code(files: dict[str, str]) -> bool:
    """True if stage files contain JS/TS that is expected to load/draw assets.

    CSS-only and HTML-structure-only (no inline <script>) stages never reference
    game assets — they come in later JS stages. Only enforce the asset-reference
    guard when we have at least one executable file:
      • .js / .ts / .jsx / .tsx / .vue  → always executable
      • .html with an inline <script …> block  → monolithic game file, executable
    """
    for path, content in files.items():
        ext = path.rsplit(".", 1)[-1].lower()
        if ext in _SCRIPT_EXTS:
            return True
        if ext == "html" and "<script" in content:
            return True
    return False


def scan_game_visuals(
    kind: str,
    files: dict[str, str],
    *,
    slug: str = "",
    asset_paths: list[str] | None = None,
    is_game: bool = True,
    all_files: dict[str, str] | None = None,
    stage_uses_visuals: bool = True,
) -> list[dict[str, Any]]:
    """Return violations for placeholder graphics or missing asset usage.

    ``stage_uses_visuals`` mirrors the plan stage's ``uses_visuals`` flag.
    When False the stage is structural/logic-only (HTML skeleton, CSS, init JS)
    and asset refs are expected to arrive in a later visual stage — skip the
    asset-reference check entirely to avoid 15-retry loops on non-visual stages.
    """
    if not is_game or kind not in _GAME_KINDS:
        return []

    paths = asset_paths if asset_paths is not None else (
        list_image_asset_paths(slug) if slug else []
    )
    violations: list[dict[str, Any]] = []

    corpus: dict[str, str] = {}
    if all_files:
        corpus.update(all_files)
    corpus.update(files)
    combined = "\n".join(corpus.values())

    # Asset-reference check has THREE gates — all must pass to fire:
    # 1. stage_uses_visuals=True  — only visual stages need asset refs;
    #    structural/logic stages (uses_visuals=False) defer this check.
    # 2. _stage_has_executable_code — CSS-only and bare HTML-structure stages
    #    never reference assets; that comes in JS stages.
    # 3. canvas-primitive exemption — fillRect/arc/strokeRect are valid renderers;
    #    games like Snake draw directly without PNG sprites.
    if (
        paths
        and stage_uses_visuals
        and not _should_skip_project_asset_check(files)
        and _stage_has_executable_code(files)
    ):
        has_asset_ref = any(
            p in combined or p.split("/")[-1] in combined
            for p in paths
        ) or bool(_ASSET_REF.search(combined))
        if not has_asset_ref:
            # Canvas 2D primitive rendering (fillRect, arc, etc.) is a valid
            # alternative to sprite loading — do not require PNG asset refs
            # when the corpus already contains real canvas drawing calls.
            if not _CANVAS_DRAW.search(combined) and not _THREE_DRAW.search(combined):
                violations.append({
                    "path": "(project)",
                    "match": f"{len(paths)} assets",
                    "message": (
                        "There are generated assets/ but the code does not use them: "
                        "use load.image/add.image (Phaser), <img src=\"assets/...\">, "
                        "or canvas drawImage / assetLoader.js"
                    ),
                })

    for fpath, content in files.items():
        if not content or not _GRADIENT.search(content):
            continue
        ext = fpath.rsplit(".", 1)[-1].lower()
        if ext not in ("jsx", "js", "tsx", "ts", "html", "css", "vue"):
            continue
        if not _CHARACTER_CTX.search(content):
            continue
        if _ASSET_REF.search(content):
            continue
        violations.append({
            "path": fpath,
            "match": "linear-gradient",
            "message": (
                "CSS gradient placeholder for hero/enemy: use assets/*.png "
                "or a canvas sprite, not coloured squares"
            ),
        })

    return violations


def format_game_review_block(violations: list[dict[str, Any]]) -> str:
    if not violations:
        return ""
    detail = "; ".join(
        f"{v['path']}: {v['message']}" for v in violations[:6]
    )
    return (
        "РЕЗУЛТАТ: ПРОБЛЕМ\n"
        f"ПРОБЛЕМ: Визуално качество на играта — {detail}"
    )
