"""Дедал Playground — publishing apps as internal ADN sections.

Публикуван app става раздел вътре в ADN, зареждан от менюто на
``/apps/{slug}/``. За да оцелее Replit redeploy (ephemeral FS), файловете
се zip-ват в **object storage** + ред в ``playground_published_apps``.

Поток:
  publish(slug)      — zip workspace → object storage → registry + materialize
                       (за vite kind: npm build → zip dist/ → kind="static")
  materialize_all()  — при startup: сваля всички enabled apps на локален диск
  app_dir(slug)      — локалната папка от която catch-all route serve-ва файлове
  unpublish(slug)    — маха от менюто (object-ът остава за rollback)
  list_published()   — за меню + admin

Само ``static`` и ``vite`` (след build) apps се serve-ват в prod (in-process файлове).
Python/Node apps се отбелязват, но изискват subprocess (само dev).
"""
from __future__ import annotations

import asyncio
import io
import logging
import zipfile
from pathlib import Path
from typing import Any

from . import playground as pg
from .paths import data_root, repo_root
from .playground_runtime import PlaygroundRuntime
from .proc import argv_for_command, terminate_process_tree

log = logging.getLogger(__name__)

ADN_ROOT = repo_root()
_OBJ_PREFIX = "playground/published"


def published_dir() -> Path:
    """Materialized /apps files. Docker volume when standalone; ADN path otherwise."""
    from .host import standalone_mode
    if standalone_mode():
        path = data_root() / "published_apps"
    else:
        path = repo_root() / "data" / "published_apps"
    path.mkdir(parents=True, exist_ok=True)
    return path


from .harness.context import legacy_paths as _legacy_paths  # noqa: E402

_legacy_paths(globals(), {"PUBLISHED_DIR": published_dir})  # resolved live, never frozen at import

_SKIP = {".git", "node_modules", "__pycache__", ".venv", ".cache"}


async def _pool():
    from .host import get_pool
    return await get_pool()


# ---------------------------------------------------------------------------
# Zip helpers
# ---------------------------------------------------------------------------

def excluded_secret_files(root: Path) -> list[str]:
    """Relative names of files a zip of ``root`` leaves out because they look secret."""
    from .secret_paths import is_secret_path

    out: list[str] = []
    for p in root.rglob("*"):
        if p.is_dir():
            continue
        rel = p.relative_to(root)
        if any(part in _SKIP for part in rel.parts):
            continue
        if is_secret_path(rel):
            out.append(rel.as_posix())
    return sorted(out)


# Daedalus's working notes about a project: kept in the project and its capsule, never shipped with the game
# (review 2026-11-12: the bible copies the owner's requests word for word; the baseline is internal state).
_NOT_PUBLISHED = {"GAME_BIBLE.md", "PROJECT_BIBLE.md", "studio/baseline.json"}


def _zip_dir(root: Path, *, for_publish: bool = False) -> bytes:
    from .secret_paths import is_secret_path

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in root.rglob("*"):
            if p.is_dir():
                continue
            rel = p.relative_to(root)
            if any(part in _SKIP for part in rel.parts):
                continue
            if is_secret_path(rel):
                continue
            if for_publish and rel.as_posix() in _NOT_PUBLISHED:
                continue
            zf.write(p, str(rel))
    return buf.getvalue()


def _unzip_to(data: bytes, dest: Path) -> None:
    import shutil
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for member in zf.namelist():
            target = (dest / member).resolve()
            if dest.resolve() not in target.parents and target != dest.resolve():
                continue
            if member.endswith("/"):
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, open(target, "wb") as out:
                out.write(src.read())


# ---------------------------------------------------------------------------
# Vite build step
# ---------------------------------------------------------------------------

def _static_plan(root: Path) -> dict | None:
    from .static_build import plan
    return plan(root)


async def _build_vite(root: Path) -> Path:
    """Same as visual proof / compile gate: ``daedalus.web_build.build_web``."""
    from .web_build import build_web

    return await build_web(root)


async def _build_expo(root: Path) -> Path:
    """Run npm install + expo export --platform web.

    Returns the ``dist/`` Path (Expo web export output). Raises RuntimeError on failure.
    """
    async def _run(*cmd: str, timeout: float = 600) -> tuple[int, str]:
        env = PlaygroundRuntime.child_env(0)
        argv = argv_for_command(cmd[0], *cmd[1:], env=env) if cmd else []
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=str(root), env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            raw, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            await terminate_process_tree(proc, timeout=12.0)
            return -1, f"timeout after {timeout}s"
        text = raw.decode("utf-8", errors="replace") if raw else ""
        return (proc.returncode or 0), text

    pkg = root / "package.json"
    if not pkg.exists():
        raise RuntimeError("no package.json found — is this an expo project?")

    log.info("Playground expo build: npm install in %s", root)
    rc, out = await _run("npm", "install", "--no-audit", "--no-fund",
                         "--legacy-peer-deps", timeout=600)
    if rc != 0:
        raise RuntimeError(f"npm install failed (rc={rc}):\n{out[-2000:]}")

    log.info("Playground expo build: expo export --platform web in %s", root)
    rc, out = await _run("npx", "expo", "export", "--platform", "web", timeout=300)
    if rc != 0:
        raise RuntimeError(f"expo export failed (rc={rc}):\n{out[-2000:]}")

    # Expo exports to dist/ by default
    dist = root / "dist"
    if not dist.exists() or not any(dist.iterdir()):
        raise RuntimeError(
            f"expo export exited 0 but dist/ is missing/empty.\nOutput:\n{out[-1000:]}"
        )
    log.info("Playground expo build: dist/ ready (%d files)",
             sum(1 for _ in dist.rglob("*") if _.is_file()))
    return dist


# ---------------------------------------------------------------------------
# Publish
# ---------------------------------------------------------------------------

async def publish(
    slug: str,
    *,
    icon: str = "🎮",
    menu_order: int = 100,
    name: str = "",
    description: str = "",
) -> dict[str, Any]:
    """Zip a project workspace → object storage → register as menu section.

    For ``vite`` projects: runs ``npm run build`` first and zips ``dist/``
    instead of the full workspace. The published record stores kind=``static``.
    """
    proj = await pg.get_project(slug)
    if not proj:
        raise ValueError(f"project '{slug}' not found")
    slug = proj["slug"]
    root = pg.project_dir(slug)
    if not root.exists() or not any(root.iterdir()):
        raise ValueError(f"project '{slug}' workspace is empty — nothing to publish")

    kind = proj.get("kind", "static")
    entry = proj.get("entry") or "index.html"

    from .capability_registry import is_workspace_path, platform_for_kind

    platform = platform_for_kind(str(kind))
    if platform in {"expo", "capacitor"}:
        raise RuntimeError("web preview does not replace device_smoke")

    try:
        from .capability import adn_publish_block
        blocked = adn_publish_block(root, kind)
        if blocked:
            raise RuntimeError(blocked)
    except RuntimeError:
        raise
    except Exception:
        log.debug("capability ADN publish gate skipped", exc_info=True)

    # Vite / Phaser (Vite+Phaser): build first, publish dist/ as static
    publish_root = root
    if kind in ("vite", "vite-react", "phaser"):
        from . import static_build as _sb
        sb_plan = _static_plan(root)
        if sb_plan and (_sb.fresh_output(root) is not None or sb_plan["manager"] != "npm"):
            # A revived repo: reuse the build verify made, and install with the repo's own manager (live
            # 2026-11-12: npm install crashed on The-Labirinth's pnpm node_modules).
            dist = await _sb.build(root)
            from .web_build import stage_web_assets      # what build_web does after a build (review 2026-11-12)
            stage_web_assets(root, dist)
        else:
            dist = await _build_vite(root)
        publish_root = dist
        kind = "static"
        entry = "index.html"
    elif kind == "node" and _static_plan(root):
        # A web app that builds to static files (CRA, Vue CLI, webpack, Parcel, ...): publish its build output, never
        # the raw source (found 2026-11-11 while reviving GitHub repos).
        from .static_build import build as _static_build
        publish_root = await _static_build(root)
        kind = "static"
        entry = "index.html"
    elif kind == "godot":
        from .godot_first import run_pipeline
        pipe = await asyncio.to_thread(run_pipeline, slug, through="publish")
        pub = (pipe.get("stages") or {}).get("publish") or {}
        if not pub.get("ok"):
            raise RuntimeError(
                pub.get("error")
                or "Godot publish requires a green Stage 7 export. Preview HTML is not a release."
            )
        artifact = Path(str(pub.get("artifact") or ""))
        if not artifact.is_file():
            raise RuntimeError("Godot publish has no immutable Stage 7 artifact")
        if is_workspace_path(root, artifact):
            raise RuntimeError("Publish uses the last verified artifact, not the workspace folder.")
        publish_root = artifact.parent
        entry = artifact.name

    data = _zip_dir(publish_root, for_publish=True)
    object_path = f"{_OBJ_PREFIX}/{slug}.zip"

    stored = False
    try:
        from .host import storage
        svc = storage()
        is_configured = svc.is_configured
        upload_bytes = svc.upload_bytes
        if is_configured():
            upload_bytes(data, object_path, content_type="application/zip")
            stored = True
    except Exception as exc:
        log.warning("Playground publish: object storage upload failed (%s) — local only", exc)

    _unzip_to(data, app_dir(slug))

    pool = await _pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO playground_published_apps
              (slug, name, description, kind, entry, object_path, icon, menu_order,
               enabled, project_id, size_bytes, published_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,TRUE,$9,$10,NOW())
            ON CONFLICT (slug) DO UPDATE SET
              name=EXCLUDED.name, description=EXCLUDED.description,
              kind=EXCLUDED.kind, entry=EXCLUDED.entry,
              object_path=EXCLUDED.object_path, icon=EXCLUDED.icon,
              menu_order=EXCLUDED.menu_order, enabled=TRUE,
              size_bytes=EXCLUDED.size_bytes, published_at=NOW()
            """,
            slug, name or proj["name"], description or proj.get("description", ""),
            kind, entry, object_path if stored else "", icon, menu_order,
            proj["id"], len(data),
        )
    await pg.update_project(slug, status="published")
    log.info("Playground: published %s (%d bytes, durable=%s, kind=%s)", slug, len(data), stored, kind)
    return {
        "ok": True, "slug": slug, "kind": kind, "entry": entry,
        "size_bytes": len(data), "durable": stored,
        "url": f"/apps/{slug}/",
        "built_from": (
            "godot_export" if proj.get("kind") == "godot"
            else "dist" if proj.get("kind") in ("vite", "vite-react", "phaser", "expo")
            else "workspace"
        ),
    }


async def unpublish(slug: str) -> bool:
    pool = await _pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE playground_published_apps SET enabled=FALSE WHERE slug=$1", slug
        )
    import shutil
    d = app_dir(slug)
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
    log.info("Playground: unpublished %s", slug)
    return True


async def delete_published(slug: str) -> bool:
    """Fully remove a published app — deletes DB record + local files."""
    import shutil
    pool = await _pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM playground_published_apps WHERE slug=$1", slug
        )
    d = app_dir(slug)
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
    log.info("Playground: deleted published app %s", slug)
    return True


# ---------------------------------------------------------------------------
# Serving helpers
# ---------------------------------------------------------------------------

def app_dir(slug: str) -> Path:
    return published_dir() / pg.slugify(slug)


async def list_published(*, enabled_only: bool = True) -> list[dict[str, Any]]:
    pool = await _pool()
    async with pool.acquire() as conn:
        if enabled_only:
            rows = await conn.fetch(
                "SELECT * FROM playground_published_apps WHERE enabled=TRUE "
                "ORDER BY menu_order, name"
            )
        else:
            rows = await conn.fetch(
                "SELECT * FROM playground_published_apps ORDER BY menu_order, name"
            )
    return [dict(r) for r in rows]


async def get_published(slug: str) -> dict[str, Any] | None:
    pool = await _pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM playground_published_apps WHERE slug=$1 AND enabled=TRUE",
            slug,
        )
    return dict(row) if row else None


async def materialize_all() -> int:
    """On startup: download every enabled published app to local disk."""
    apps = await list_published(enabled_only=True)
    n = 0
    for a in apps:
        slug = a["slug"]
        dest = app_dir(slug)
        if dest.exists() and any(dest.iterdir()):
            n += 1
            continue
        obj = a.get("object_path") or ""
        if not obj:
            continue
        try:
            from .host import storage
            data = storage().download_bytes(obj)
            _unzip_to(data, dest)
            n += 1
            log.info("Playground: materialized %s from object storage", slug)
        except Exception as exc:
            log.warning("Playground: materialize %s failed: %s", slug, exc)
    if apps:
        log.info("Playground: materialized %d/%d published apps", n, len(apps))
    return n
