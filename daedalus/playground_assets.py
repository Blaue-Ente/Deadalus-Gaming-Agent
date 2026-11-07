"""Playground asset generation — plan, generate, and list project images.

Assets live under ``playground/projects/{slug}/assets/``. Metadata is stored in
``assets/_manifest.json`` (prompt, size, generated_at per file).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from .playground import project_dir, write_bytes, _safe_join

log = logging.getLogger(__name__)

MANIFEST_REL = "assets/_manifest.json"
STYLE_BIBLE_REL = "assets/_style_bible.json"
MAX_ASSETS_DEFAULT = 6
MAX_ASSETS_CAP = 32
DEFAULT_MODEL = "sdxl"
DEFAULT_SIZE_SPRITE = "512x512"
DEFAULT_SIZE_BG = "1024x768"

# Back-compat alias
MAX_ASSETS = MAX_ASSETS_DEFAULT

_LEVEL_PATH_RE = re.compile(r"level\s*(\d+)", re.I)
_ENTITY_STEM_RE = re.compile(r"(hero|enemy|player|boss|npc|sprite|tile|item|weapon|prop)", re.I)

_STYLE_BIBLE_SYS = (
    "Ти си art director. Връщаш САМО валиден JSON art bible за един проект.\n"
    'ФОРМАТ: {"style_name":"...","palette":["#hex",...],"style_tags":"pixel art, cozy...",'
    '"character_style":"...","background_style":"...","ui_style":"...",'
    '"prompt_prefix":"consistent english prefix for ALL image prompts in this project"}\n'
    "prompt_prefix трябва да е 1-2 изречения на АНГЛИЙСКИ — един и същ стил за всички assets."
)


def max_assets_cap() -> int:
    import os
    try:
        return max(MAX_ASSETS_DEFAULT, min(64, int(os.environ.get("DAEDALUS_MAX_ASSETS", str(MAX_ASSETS_CAP)))))
    except ValueError:
        return MAX_ASSETS_CAP


def compute_asset_budget(
    plan: dict | None,
    *,
    studio: bool = False,
    ultra: bool = False,
    is_game: bool = False,
) -> int:
    """Scale asset count with plan complexity — not a flat 6."""
    stages = (plan or {}).get("stages") or []
    cap = max_assets_cap()
    if not stages:
        return MAX_ASSETS_DEFAULT

    visual = sum(1 for s in stages if s.get("uses_visuals"))
    micro_visual = sum(1 for s in stages if s.get("micro") and s.get("uses_visuals"))
    entities = sum(1 for s in stages if s.get("fragment_kind") in ("entity", "level"))
    milestones = len((plan or {}).get("milestones") or [])

    inferred = infer_assets_from_stages(plan or {})
    budget = max(
        MAX_ASSETS_DEFAULT,
        len(inferred),
        visual + micro_visual,
        entities + (2 if is_game else 0),
    )
    if ultra:
        budget = max(budget, min(cap, 6 + len(stages) // 2))
    if studio:
        budget = max(budget, min(cap, 8 + milestones * 2))
    if is_game:
        budget = max(budget, 8)
    return min(cap, budget)


def _asset_sys_for_budget(budget: int) -> str:
    lo = min(3, budget)
    return (
        "Ти си art director. Връщаш САМО валиден JSON списък с изображения, нужни за проекта.\n"
        'ФОРМАТ: {"assets":[{"path":"assets/player.png","prompt":"detailed english image prompt","size":"512x512"}]}\n'
        f"ПРАВИЛА:\n- Генерирай между {lo} и {budget} изображения — ВСИЧКИ нужни за плана\n"
        "- За ВСЕКИ етап с uses_visuals=true — поне 1 asset (фон, спрайт, икона, UI)\n"
        "- За всяко ниво/Level — отделен фон assets/levels/levelN_bg.png\n"
        "- За всеки герой/враг/NPC/предмет — отделен спрайт assets/<name>.png\n"
        "- ВСИЧКИ prompts споделят ЕДИН визуален стил (виж STYLE BIBLE)\n"
        "- АКО проектът е ИГРА — player, enemies, backgrounds, items/icons\n"
        "- path: винаги в assets/ с .png разширение\n"
        "- prompt: на АНГЛИЙСКИ, детайлен; спрайтове: 'plain solid background'\n"
        "- size: 512x512 (спрайт/икона) или 1024x768 (фон)\n"
        "- Ако НЕ трябват изображения върни {\"assets\":[]}\n"
        "- ЗАПОЧНИ ДИРЕКТНО с { — никакъв друг текст"
    )


_ASSET_SYS = _asset_sys_for_budget(MAX_ASSETS_DEFAULT)

_JSON_RE = re.compile(r"\{[\s\S]+\}")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def filename_under_assets(filename: str) -> str:
    """Path *inside* ``assets/`` for ``generate_image`` writes.

    Dest dir is already ``<slug>/assets``. Writers often pass
    ``assets/keeper.png``, which used to write ``assets/assets/keeper.png``
    and raise ENOENT because the nested folder was never created.
    """
    raw = str(filename or "asset.png").replace("\\", "/").strip().lstrip("/")
    while raw.startswith("./"):
        raw = raw[2:]
    while raw.lower().startswith("assets/"):
        raw = raw[len("assets/") :]
    parts = [p for p in raw.split("/") if p and p not in (".", "..")]
    if not parts:
        parts = ["asset.png"]
    if len(parts) > 2:
        parts = parts[-2:]
    if "." not in parts[-1]:
        parts[-1] = parts[-1] + ".png"
    return "/".join(parts)


def normalize_asset_path(path: str) -> str:
    """Normalize to ``assets/foo.png`` under the project workspace."""
    apath = filename_under_assets(path or "")
    if not apath.lower().endswith(".png"):
        apath = apath.rsplit(".", 1)[0] + ".png"
    return f"assets/{apath}"


def assets_dir(slug: str) -> Path:
    return _safe_join(project_dir(slug), "assets")


def ensure_assets_dir(slug: str) -> Path:
    """Create ``assets/`` and seed ``_manifest.json`` if missing."""
    adir = assets_dir(slug)
    adir.mkdir(parents=True, exist_ok=True)
    manifest_path = adir / "_manifest.json"
    if not manifest_path.exists():
        manifest_path.write_text(
            json.dumps({"version": 1, "assets": {}}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return adir


def _manifest_path(slug: str) -> Path:
    return _safe_join(project_dir(slug), MANIFEST_REL)


def load_manifest(slug: str) -> dict[str, Any]:
    ensure_assets_dir(slug)
    path = _manifest_path(slug)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("version", 1)
            data.setdefault("assets", {})
            return data
    except Exception:
        pass
    return {"version": 1, "assets": {}}


def save_manifest(slug: str, manifest: dict[str, Any]) -> None:
    ensure_assets_dir(slug)
    path = _manifest_path(slug)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def _update_manifest_entry(
    slug: str,
    rel: str,
    *,
    prompt: str = "",
    size: str = "",
    bytes_written: int = 0,
) -> None:
    manifest = load_manifest(slug)
    entries = manifest.setdefault("assets", {})
    entries[rel] = {
        "prompt": prompt,
        "size": size,
        "bytes": bytes_written,
        "generated_at": _now_iso(),
    }
    save_manifest(slug, manifest)


def list_assets(slug: str) -> list[dict[str, Any]]:
    """List PNG/WebP/JPEG files under ``assets/`` with manifest metadata."""
    ensure_assets_dir(slug)
    manifest = load_manifest(slug)
    meta = manifest.get("assets") or {}
    adir = assets_dir(slug)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    for p in sorted(adir.rglob("*")):
        if not p.is_file():
            continue
        rel = str(p.relative_to(project_dir(slug)))
        if rel.endswith("_manifest.json") or rel.endswith(".gitkeep"):
            continue
        if p.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
            continue
        seen.add(rel)
        info = meta.get(rel) or {}
        try:
            size_bytes = p.stat().st_size
        except OSError:
            size_bytes = 0
        out.append({
            "path": rel,
            "bytes": size_bytes,
            "prompt": info.get("prompt", ""),
            "size": info.get("size", ""),
            "generated_at": info.get("generated_at", ""),
        })

    for rel, info in sorted(meta.items()):
        if rel in seen:
            continue
        out.append({
            "path": rel,
            "bytes": info.get("bytes", 0),
            "prompt": info.get("prompt", ""),
            "size": info.get("size", ""),
            "generated_at": info.get("generated_at", ""),
            "missing": True,
        })
    return out


def extract_json(text: str) -> dict | None:
    for candidate in (
        text,
        re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip(),
    ):
        try:
            return json.loads(candidate)
        except Exception:
            pass
    m = _JSON_RE.search(text)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    return None


def _extract_llm_text(res: dict) -> str:
    if not isinstance(res, dict):
        return ""
    for blk in (res.get("content") or []):
        t = blk.get("text") or ""
        if t:
            return t
    for ch in (res.get("choices") or []):
        t = ((ch.get("message") or {}).get("content") or "")
        if t:
            return t
    return res.get("reply") or ""


def style_bible_path(slug: str) -> Path:
    return _safe_join(project_dir(slug), STYLE_BIBLE_REL)


def load_style_bible(slug: str) -> dict[str, Any]:
    path = style_bible_path(slug)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_style_bible(slug: str, bible: dict[str, Any]) -> None:
    ensure_assets_dir(slug)
    style_bible_path(slug).write_text(
        json.dumps(bible, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    manifest = load_manifest(slug)
    manifest["style_bible"] = bible.get("style_name") or bible.get("prompt_prefix", "")[:80]
    save_manifest(slug, manifest)


def style_prompt_prefix(bible: dict[str, Any] | None) -> str:
    if not bible:
        return ""
    prefix = (bible.get("prompt_prefix") or "").strip()
    tags = (bible.get("style_tags") or "").strip()
    if prefix:
        return prefix
    if tags:
        return f"Art style: {tags}. Consistent palette and lighting."
    return ""


async def ensure_style_bible(
    *,
    slug: str,
    name: str,
    description: str,
    plan: dict | None = None,
    model: str = "alltoken:deepseek-v3.2",
    client_id: str = "",
) -> dict[str, Any]:
    """Create or load project-wide art direction for consistent assets."""
    existing = load_style_bible(slug)
    if existing.get("prompt_prefix"):
        return existing

    from . import daedalus_mesh as mesh

    visual_stages = [
        s for s in ((plan or {}).get("stages") or [])
        if s.get("uses_visuals") or s.get("fragment_kind") in ("entity", "level")
    ]
    stages_block = "\n".join(
        f"- {s.get('title', '?')}: {s.get('goal', '')[:120]}"
        for s in visual_stages[:20]
    )
    user = (
        f'ПРОЕКТ: "{name}"\nОПИСАНИЕ: {description}\n\n'
        f"ВИЗУАЛНИ ЕТАПИ:\n{stages_block or '(общ стил от описанието)'}\n\n"
        "Дефинирай ЕДИН стил за всички sprites, фонове и икони."
    )
    try:
        res = await mesh.route(
            messages=[
                {"role": "system", "content": _STYLE_BIBLE_SYS},
                {"role": "user", "content": user},
            ],
            model=model,
            intent="chat",
            max_tokens=1200,
            temperature=0.4,
            client_id=client_id or f"style-bible/{slug}",
        )
        data = extract_json(_extract_llm_text(res).strip()) or {}
    except Exception as exc:
        log.warning("style bible failed for %s: %s", slug, exc)
        data = {}

    if not data.get("prompt_prefix"):
        data.setdefault("style_name", name)
        data.setdefault("style_tags", "cohesive game art, soft lighting, readable silhouettes")
        data.setdefault(
            "prompt_prefix",
            f"Consistent art for {name}: same palette, line weight, and lighting across all assets.",
        )
    save_style_bible(slug, data)
    return data


def _asset_entry(path: str, prompt: str, *, size: str = "") -> dict[str, Any]:
    is_bg = "bg" in path.lower() or "background" in path.lower() or "/levels/" in path
    return {
        "path": path,
        "prompt": prompt,
        "size": size or (DEFAULT_SIZE_BG if is_bg else DEFAULT_SIZE_SPRITE),
    }


def infer_assets_from_stages(plan: dict, *, style_prefix: str = "") -> list[dict[str, Any]]:
    """Derive required PNG paths from plan stages (levels, entities, files)."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    prefix = f"{style_prefix}. " if style_prefix else ""

    def add(path: str, subject: str, *, size: str = "") -> None:
        try:
            norm = normalize_asset_path(path)
        except ValueError:
            return
        if norm in seen:
            return
        seen.add(norm)
        out.append(_asset_entry(norm, f"{prefix}{subject}", size=size))

    for s in (plan.get("stages") or []):
        svis = bool(
            s.get("uses_visuals")
            or s.get("fragment_kind") in ("entity", "level")
        )
        if not svis:
            continue
        title = s.get("title") or ""
        goal = s.get("goal") or ""

        for fpath in s.get("files") or []:
            low = fpath.replace("\\", "/").lower()
            if low.endswith(".png") or low.endswith(".webp"):
                stem = Path(low).stem.replace("_", " ")
                add(fpath, f"game asset {stem}, {goal[:80]}")
                continue
            lm = _LEVEL_PATH_RE.search(low) or _LEVEL_PATH_RE.search(title)
            if lm and ("level" in low or s.get("fragment_kind") == "level"):
                n = lm.group(1)
                add(
                    f"assets/levels/level{n}_bg.png",
                    f"level {n} background environment, {title[:60]}",
                    size=DEFAULT_SIZE_BG,
                )
            stem = Path(low).stem
            if _ENTITY_STEM_RE.search(stem):
                add(
                    f"assets/{stem}.png",
                    f"{stem} sprite character or object, plain solid background",
                )

        lm = _LEVEL_PATH_RE.search(title) or _LEVEL_PATH_RE.search(goal)
        if lm and s.get("fragment_kind") == "level":
            n = lm.group(1)
            add(
                f"assets/levels/level{n}_bg.png",
                f"level {n} background, {title[:80]}",
                size=DEFAULT_SIZE_BG,
            )

    return out


def _merge_asset_lists(*lists: list[dict[str, Any]], cap: int) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for lst in lists:
        for a in lst:
            p = a.get("path") or ""
            if not p or p in seen:
                continue
            seen.add(p)
            merged.append(a)
            if len(merged) >= cap:
                return merged
    return merged


async def plan_assets(
    *,
    name: str,
    slug: str,
    kind: str,
    description: str,
    research_notes: str = "",
    reference_notes: str = "",
    plan: dict | None = None,
    model: str = "alltoken:deepseek-v3.2",
    client_id: str = "",
    max_count: int | None = None,
    style_bible: dict[str, Any] | None = None,
    studio: bool = False,
    ultra: bool = False,
    is_game: bool = False,
) -> list[dict[str, Any]]:
    """LLM plans which images to generate for a project."""
    from . import daedalus_mesh as mesh

    budget = max_count or compute_asset_budget(
        plan, studio=studio, ultra=ultra, is_game=is_game,
    )
    prefix = style_prompt_prefix(style_bible or load_style_bible(slug))
    inferred = infer_assets_from_stages(plan or {}, style_prefix=prefix)

    ref_block = f"РЕФЕРЕНЦИЯ:\n{reference_notes}\n\n" if reference_notes else ""
    visual_stages = [
        s for s in ((plan or {}).get("stages") or [])
        if s.get("uses_visuals") or s.get("fragment_kind") in ("entity", "level")
    ]
    stages_block = "\n".join(
        f"  • [{s.get('id', '?')}] {s.get('title', '')} — files: {', '.join(s.get('files') or [])[:80]}"
        for s in visual_stages[:25]
    )
    bible_block = ""
    if style_bible or prefix:
        bible = style_bible or load_style_bible(slug)
        bible_block = (
            f"STYLE BIBLE (задължително в prompt_prefix на ВСЕКИ asset):\n"
            f"{json.dumps(bible, ensure_ascii=False)}\n\n"
        )

    asset_user = (
        f'ПРОЕКТ: "{name}" | Тип: {kind}\n\n'
        f"ОПИСАНИЕ: {description}\n\n"
        f"{ref_block}{bible_block}"
        f"БЮДЖЕТ: до {budget} изображения (минимум {min(budget, max(3, len(visual_stages)))})\n"
        f"ВИЗУАЛНИ ЕТАПИ ОТ ПЛАНА:\n{stages_block or '(няма маркирани)'}\n\n"
        f"ВЕЧЕ ИЗВЕДЕНИ ПЪТИЩА (задължително включи + допълни):\n"
        + "\n".join(f"  - {a['path']}" for a in inferred[:30])
        + "\n\n"
        f"ПРОУЧВАНЕ: {research_notes or '(няма)'}\n\n"
        f"ПЛАН JSON: {json.dumps(plan or {}, ensure_ascii=False)[:6000]}"
    )
    cid = client_id or f"playground-assets-plan/{slug}"
    manifest = None
    asset_sys = _asset_sys_for_budget(budget)
    for attempt in range(2):
        plan_model = model if attempt == 0 else "alltoken:deepseek-v3.2"
        try:
            ares = await mesh.route(
                messages=[
                    {"role": "system", "content": asset_sys},
                    {"role": "user", "content": asset_user},
                ],
                model=plan_model,
                intent="chat",
                max_tokens=2000,
                temperature=0.3,
                client_id=f"{cid}/a{attempt}",
            )
            manifest = extract_json(_extract_llm_text(ares).strip())
            if manifest and isinstance(manifest.get("assets"), list) and manifest["assets"]:
                break
            manifest = None
        except Exception as exc:
            log.warning("asset plan attempt %s failed for %s: %s", attempt + 1, slug, exc)
            manifest = None

    asset_list = (manifest or {}).get("assets") or []
    if not isinstance(asset_list, list):
        return []
    asset_list = (manifest or {}).get("assets") or []
    if not isinstance(asset_list, list):
        asset_list = []
    llm_out: list[dict[str, Any]] = []
    for a in asset_list[:budget]:
        if not isinstance(a, dict):
            continue
        apath = (a.get("path") or "").strip()
        aprompt = (a.get("prompt") or "").strip()
        if not apath or not aprompt:
            continue
        if prefix and prefix not in aprompt:
            aprompt = f"{prefix}. {aprompt}"
        try:
            apath = normalize_asset_path(apath)
        except ValueError:
            continue
        llm_out.append({
            "path": apath,
            "prompt": aprompt,
            "size": a.get("size") or DEFAULT_SIZE_SPRITE,
        })
    return _merge_asset_lists(inferred, llm_out, cap=budget)


async def ensure_stage_assets(
    slug: str,
    stage: dict[str, Any],
    *,
    name: str,
    kind: str,
    description: str,
    assets_built: list[str],
    style_bible: dict[str, Any] | None = None,
    model: str = "alltoken:deepseek-v3.2",
    image_model: str = DEFAULT_MODEL,
    client_id: str = "",
) -> list[str]:
    """Generate missing PNGs for one visual stage (ultra / guided checkpoint)."""
    if not stage.get("uses_visuals") and stage.get("fragment_kind") not in ("entity", "level"):
        return []

    bible = style_bible or load_style_bible(slug)
    prefix = style_prompt_prefix(bible)
    built_set = set(assets_built)
    adir = assets_dir(slug)

    needed = infer_assets_from_stages({"stages": [stage]}, style_prefix=prefix)
    if not needed:
        needed = await plan_assets(
            name=name,
            slug=slug,
            kind=kind,
            description=description,
            plan={"stages": [stage]},
            model=model,
            client_id=client_id,
            max_count=3,
            style_bible=bible,
        )

    new_paths: list[str] = []
    for a in needed:
        apath = a["path"]
        if apath in built_set:
            continue
        full = _safe_join(project_dir(slug), apath)
        if full.exists():
            built_set.add(apath)
            if apath not in assets_built:
                assets_built.append(apath)
            continue
        try:
            await generate_asset(
                slug,
                path=apath,
                prompt=a["prompt"],
                size=a.get("size") or DEFAULT_SIZE_SPRITE,
                model=image_model,
                client_id=client_id or f"stage-asset/{slug}",
                style_prefix=prefix,
            )
            new_paths.append(apath)
            built_set.add(apath)
        except Exception as exc:
            log.warning("stage asset %s failed: %s", apath, exc)
    return new_paths


async def generate_asset(
    slug: str,
    *,
    path: str,
    prompt: str,
    size: str = DEFAULT_SIZE_SPRITE,
    model: str = DEFAULT_MODEL,
    client_id: str = "",
    style_prefix: str = "",
) -> dict[str, Any]:
    """Generate one image and save it into the project workspace."""
    import base64

    import httpx

    from . import daedalus_mesh as mesh

    apath = normalize_asset_path(path)
    aprompt = (prompt or "").strip()
    if not aprompt:
        raise ValueError("empty prompt")
    if style_prefix and style_prefix not in aprompt:
        aprompt = f"{style_prefix}. {aprompt}"

    img = await mesh.generate_image(
        prompt=aprompt,
        model=mesh.resolve_image_model(model),
        size=size or DEFAULT_SIZE_SPRITE,
        n=1,
        image_type="png",
        client_id=client_id or f"playground-img/{slug}",
        max_wait=90.0,
    )
    first = (img.get("images") or [{}])[0]
    url = first.get("url", "")
    b64 = first.get("b64", "")
    if url.startswith("data:"):
        _, _, data = url.partition(",")
        raw = base64.b64decode(data)
    elif b64:
        raw = base64.b64decode(b64)
    elif url:
        async with httpx.AsyncClient(timeout=60.0) as http:
            resp = await http.get(url)
            resp.raise_for_status()
            raw = resp.content
    else:
        raise RuntimeError("няма URL от генератора")

    from .playground_asset_verify import ensure_png_bytes

    raw = ensure_png_bytes(raw)
    nbytes = write_bytes(slug, apath, raw)
    _update_manifest_entry(slug, apath, prompt=aprompt, size=size, bytes_written=nbytes)
    return {
        "path": apath,
        "bytes": nbytes,
        "prompt": aprompt,
        "size": size,
        "url": url,
    }


ProgressCb = Callable[[str, dict[str, Any]], Awaitable[None] | None]


async def auto_pack_assets(
    slug: str,
    *,
    name: str = "",
    kind: str = "static",
    description: str = "",
    research_notes: str = "",
    reference_notes: str = "",
    plan: dict | None = None,
    model: str = "alltoken:deepseek-v3.2",
    image_model: str = DEFAULT_MODEL,
    client_id: str = "",
    on_progress: ProgressCb | None = None,
    studio: bool = False,
    ultra: bool = False,
    is_game: bool = False,
) -> dict[str, Any]:
    """Plan + batch-generate assets from a game/project description."""
    ensure_assets_dir(slug)
    proj = await _get_project_name(slug, name)
    bible = await ensure_style_bible(
        slug=slug,
        name=proj,
        description=description,
        plan=plan,
        model=model,
        client_id=client_id or f"autopack/{slug}",
    )
    prefix = style_prompt_prefix(bible)
    asset_list = await plan_assets(
        name=proj,
        slug=slug,
        kind=kind,
        description=description,
        research_notes=research_notes,
        reference_notes=reference_notes,
        plan=plan,
        model=model,
        client_id=client_id or f"autopack/{slug}",
        style_bible=bible,
        studio=studio,
        ultra=ultra,
        is_game=is_game,
    )

    built: list[str] = []
    errors: list[dict[str, str]] = []

    if on_progress:
        maybe = on_progress("plan", {"count": len(asset_list), "assets": asset_list})
        if maybe and hasattr(maybe, "__await__"):
            await maybe

    for a in asset_list:
        apath = a["path"]
        if on_progress:
            maybe = on_progress("start", {"path": apath, "prompt": a["prompt"][:80]})
            if maybe and hasattr(maybe, "__await__"):
                await maybe
        try:
            result = await generate_asset(
                slug,
                path=apath,
                prompt=a["prompt"],
                size=a.get("size") or DEFAULT_SIZE_SPRITE,
                model=image_model,
                client_id=f"{client_id or 'autopack'}/{slug}",
                style_prefix=prefix,
            )
            built.append(apath)
            if on_progress:
                maybe = on_progress("ok", {"path": apath, "bytes": result["bytes"]})
                if maybe and hasattr(maybe, "__await__"):
                    await maybe
        except Exception as exc:
            errors.append({"path": apath, "error": str(exc)[:200]})
            if on_progress:
                maybe = on_progress("err", {"path": apath, "message": str(exc)[:120]})
                if maybe and hasattr(maybe, "__await__"):
                    await maybe

    return {
        "planned": len(asset_list),
        "built": built,
        "errors": errors,
        "assets": list_assets(slug),
        "style_bible": bible.get("style_name") or "",
    }


async def _get_project_name(slug: str, fallback: str) -> str:
    if fallback:
        return fallback
    from . import playground as pg
    proj = await pg.get_project(slug)
    return (proj or {}).get("name") or slug


def format_asset_summary(built: list[str]) -> str:
    """Prompt block for build phases after assets are generated."""
    if built:
        return (
            "\n🎨 ГОТОВИ ASSETS (вече генерирани и записани в проекта — реферирай ги ДИРЕКТНО като локални пътища):\n"
            + "\n".join(f"  • {p}" for p in built)
            + "\nЗАДЪЛЖИТЕЛНО: НЕ ползвай placeholder/external URL изображения — реферирай горните локални файлове.\n"
        )
    return (
        "\n⚠️ ВНИМАНИЕ: Няма генерирани PNG assets в проекта.\n"
        "ЗАДЪЛЖИТЕЛНО за герои/врагове: <img src=\"assets/player.png\"> или canvas drawImage.\n"
        "ЗАБРАНЕНО: CSS linear-gradient/radial-gradient като цветни квадрати за герой/враг.\n"
        "Фоновете може да са CSS — спрайтовете НЕ.\n"
    )
