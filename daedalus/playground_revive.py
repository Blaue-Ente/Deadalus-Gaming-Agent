"""Съживи от GitHub — import pipeline за Playground.

Фази:
  1. Оценка на съвместимост (pre/post clone)
  2. Clone + smart onboarding (deps, smoke test, repair)
  3. Publish loop (verify → publish, upstream diff, PR)
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from . import playground as pg
from . import playground_runner as runner
from . import playground_verify_fix as pvf

log = logging.getLogger(__name__)

# Kinds that revive well in cloud Playground
_GOOD_KINDS = {"static", "vite", "vite-react", "fastapi", "streamlit", "node", "expo"}
_HARD_KINDS = {"unity"}
_MONOREPO_MARKERS = (
    "turbo.json", "lerna.json", "pnpm-workspace.yaml", "nx.json",
    "rush.json", "go.work",
)


def _parse_github_url(url: str) -> tuple[str, str] | None:
    """Return (owner, repo) from a GitHub URL or owner/repo shorthand."""
    url = (url or "").strip().rstrip("/")
    if not url:
        return None
    if not url.startswith("http"):
        parts = url.split("/")
        if len(parts) >= 2:
            return parts[0], parts[1].removesuffix(".git")
        return None
    m = re.search(r"github\.com[:/]+([^/]+)/([^/.]+)", url)
    if m:
        return m.group(1), m.group(2)
    return None


def _dir_stats(root: Path) -> dict[str, Any]:
    """Cheap workspace stats for compatibility scoring."""
    if not root.exists():
        return {"file_count": 0, "total_bytes": 0, "has_package_json": False,
                "has_requirements": False, "monorepo_hints": []}
    file_count = 0
    total_bytes = 0
    has_pkg = False
    has_req = False
    monorepo_hints: list[str] = []
    try:
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            if any(x in p.parts for x in (".git", "node_modules", "__pycache__", ".venv")):
                continue
            file_count += 1
            try:
                total_bytes += p.stat().st_size
            except OSError:
                pass
            name = p.name.lower()
            if name == "package.json" and p.parent == root:
                has_pkg = True
            if name in ("requirements.txt", "pyproject.toml") and p.parent == root:
                has_req = True
            if name in _MONOREPO_MARKERS:
                monorepo_hints.append(p.name)
    except Exception as exc:
        log.debug("dir_stats failed: %s", exc)
    return {
        "file_count": file_count,
        "total_bytes": total_bytes,
        "has_package_json": has_pkg,
        "has_requirements": has_req,
        "monorepo_hints": monorepo_hints,
    }


def compatibility_report(
    root: Path | None = None,
    *,
    kind: str = "",
    repo_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assess whether a repo/project is suitable for cloud revive.

    Returns score 0–100, level (good/warning/hard), badges, warnings.
    """
    warnings: list[str] = []
    badges: list[str] = []
    repo_meta = repo_meta or {}

    if root and root.exists() and any(root.iterdir()):
        kind, run_cmd, entry = pg.detect_kind(root)
        stats = _dir_stats(root)
    else:
        stats = {"file_count": 0, "total_bytes": 0, "has_package_json": False,
                 "has_requirements": False, "monorepo_hints": []}
        run_cmd, entry = "", "index.html"
        if not kind:
            lang = (repo_meta.get("language") or "").lower()
            lang_map = {
                "javascript": "vite", "typescript": "vite-react",
                "python": "fastapi", "html": "static",
            }
            kind = lang_map.get(lang, "static")

    if kind in _GOOD_KINDS:
        badges.append(f"✅ {kind}")
    elif kind in _HARD_KINDS:
        badges.append(f"⚠️ {kind}")
        warnings.append(f"{kind} изисква локален editor — cloud preview е ограничен")
    else:
        badges.append(f"❓ {kind}")

    if stats["has_package_json"]:
        badges.append("📦 npm")
    if stats["has_requirements"]:
        badges.append("🐍 pip")

    if repo_meta.get("private"):
        badges.append("🔒 private")
    else:
        badges.append("🌐 public")

    if repo_meta.get("license"):
        badges.append(f"📄 {repo_meta['license']}")

    fc = stats["file_count"]
    if fc > 500:
        warnings.append(f"Голям repo (~{fc} файла) — monorepo може да е бавен")
    if stats["monorepo_hints"]:
        warnings.append(f"Monorepo маркери: {', '.join(stats['monorepo_hints'])}")
    if stats["total_bytes"] > 50_000_000:
        warnings.append("Repo > 50MB — clone може да отнеме време")

    if not stats["has_package_json"] and not stats["has_requirements"] and kind not in ("static",):
        warnings.append("Липсва package.json/requirements — entry point неясен")

    score = 70
    if kind in _GOOD_KINDS:
        score += 15
    if kind in _HARD_KINDS:
        score -= 30
    if stats["monorepo_hints"]:
        score -= 20
    if fc > 500:
        score -= 15
    if stats["has_package_json"] or stats["has_requirements"] or kind == "static":
        score += 10
    if repo_meta.get("private") and not os.environ.get("GITHUB_TOKEN"):
        score -= 25
        warnings.append("Private repo изисква GITHUB_TOKEN")
    score = max(0, min(100, score))

    if score >= 70:
        level = "good"
    elif score >= 40:
        level = "warning"
    else:
        level = "hard"

    return {
        "score": score,
        "level": level,
        "kind": kind,
        "run_cmd": run_cmd,
        "entry": entry,
        "badges": badges,
        "warnings": warnings,
        "stats": stats,
        "publishable": kind in ("static", "vite", "vite-react", "expo"),
    }


async def assess_url(url: str) -> dict[str, Any]:
    """Pre-clone compatibility check using GitHub API metadata."""
    parsed = _parse_github_url(url)
    repo_meta: dict[str, Any] = {}
    if parsed:
        try:
            from .host import github
            ghc = github()
            if ghc.is_configured():
                repo_meta = ghc.get_repo(*parsed)
        except Exception as exc:
            log.debug("assess_url github meta: %s", exc)
    report = compatibility_report(None, repo_meta=repo_meta)
    report["url"] = url
    if parsed:
        report["full_name"] = f"{parsed[0]}/{parsed[1]}"
        report["default_branch"] = repo_meta.get("default_branch", "main")
    return report


async def onboard_project(
    slug: str,
    *,
    repair_on_fail: bool = True,
    route_fn: Any = None,
    build_model: str = "",
    combined_sys: str = "",
) -> dict[str, Any]:
    """Post-clone smart onboarding: smoke test + optional LLM repair."""
    proj = await pg.get_project(slug)
    if not proj:
        return {"ok": False, "error": "project not found"}

    smoke = await runner.smoke_test(slug)
    result: dict[str, Any] = {
        "ok": bool(smoke.get("ok")),
        "smoke": smoke,
        "repaired": False,
        "repair_files": [],
    }

    if result["ok"] or smoke.get("skipped"):
        await pg.update_project(slug, status="idle")
        return result

    if not repair_on_fail or not route_fn:
        result["error"] = smoke.get("error") or "smoke test failed"
        return result

    kind = proj.get("kind", "static")
    error = (smoke.get("error") or "\n".join((smoke.get("logs") or [])[-20:])).strip()
    if not combined_sys:
        combined_sys = (
            "Ти си Дедал — експерт по поправка на код. "
            "Върни САМО променените файлове в ===FILE: path=== формат."
        )
    if not build_model:
        build_model = os.environ.get("DAEDALUS_BUILD_MODEL", "anthropic/claude-sonnet-4")

    fix_files = await pvf.llm_fix_from_error(
        slug,
        kind=kind,
        error=error[:2500],
        combined_sys=combined_sys,
        build_model=build_model,
        route_fn=route_fn,
        client_id=f"revive-repair/{slug}",
        problem_label="REPAIR MODE: проектът не стартира след clone от GitHub.",
    )
    if fix_files:
        written = pvf.write_fix_files(slug, fix_files)
        result["repair_files"] = written
        result["repaired"] = bool(written)
        smoke2 = await runner.smoke_test(slug)
        result["smoke_after_repair"] = smoke2
        result["ok"] = bool(smoke2.get("ok") or smoke2.get("skipped"))
        if not result["ok"]:
            result["error"] = smoke2.get("error") or "repair did not fix smoke test"
    else:
        result["error"] = error or "repair produced no files"

    return result


async def revive_pipeline(
    slug: str,
    *,
    publish: bool = True,
    push_github: bool = False,
    route_fn: Any = None,
    build_model: str = "",
) -> dict[str, Any]:
    """One-click: Verify → (optional Publish) → (optional Push)."""
    from . import playground_publish as pub
    from . import playground_github as gh

    proj = await pg.get_project(slug)
    if not proj:
        return {"ok": False, "error": "project not found", "steps": []}

    steps: list[dict[str, Any]] = []

    onboard = await onboard_project(
        slug, repair_on_fail=True, route_fn=route_fn, build_model=build_model,
    )
    steps.append({"step": "verify", **onboard})
    if not onboard.get("ok"):
        return {"ok": False, "error": onboard.get("error", "verify failed"), "steps": steps}

    if publish:
        try:
            pub_res = await pub.publish(slug)
            steps.append({"step": "publish", "ok": True, **pub_res})
        except Exception as exc:
            steps.append({"step": "publish", "ok": False, "error": str(exc)})
            return {"ok": False, "error": str(exc), "steps": steps}

    if push_github:
        try:
            push_res = await gh.push_project(slug)
            steps.append({"step": "push", "ok": True, **push_res})
        except Exception as exc:
            steps.append({"step": "push", "ok": False, "error": str(exc)})

    return {"ok": True, "steps": steps, "slug": slug}


async def upstream_diff(slug: str) -> dict[str, Any]:
    """Diff local workspace vs upstream SHA captured at clone."""
    proj = await pg.get_project(slug)
    if not proj:
        return {"ok": False, "error": "project not found"}

    upstream_sha = proj.get("upstream_sha") or ""
    root = pg.project_dir(slug)
    git_dir = root / ".git"

    if not upstream_sha and git_dir.exists():
        from .playground_github import _run as git_run
        code, out = await git_run(["git", "rev-parse", "HEAD"], cwd=root)
        if code == 0:
            upstream_sha = out.strip()

    if not upstream_sha:
        return {
            "ok": True,
            "has_upstream": False,
            "message": "Няма upstream baseline — clone-ът е без git history",
            "changed_files": [],
            "diff_stat": "",
        }

    if git_dir.exists():
        from .playground_github import _run as git_run
        code, stat = await git_run(
            ["git", "diff", "--stat", upstream_sha], cwd=root,
        )
        code2, names = await git_run(
            ["git", "diff", "--name-only", upstream_sha], cwd=root,
        )
        changed = [l.strip() for l in names.splitlines() if l.strip()] if code2 == 0 else []
        code3, log_out = await git_run(
            ["git", "log", "--oneline", f"{upstream_sha}..HEAD"], cwd=root,
        )
        commits = [l.strip() for l in log_out.splitlines() if l.strip()] if code3 == 0 else []
        return {
            "ok": True,
            "has_upstream": True,
            "upstream_sha": upstream_sha,
            "changed_files": changed,
            "diff_stat": stat.strip() if code == 0 else "",
            "commits": commits,
            "dirty": bool(changed),
        }

    # Fallback: compare file mtimes vs stored manifest
    manifest_path = root / ".daedalus" / "upstream_manifest.json"
    if manifest_path.exists():
        try:
            baseline = json.loads(manifest_path.read_text("utf-8"))
            current: list[str] = []
            for rel, _mtime in baseline.items():
                if not (root / rel).exists():
                    current.append(rel)
            return {
                "ok": True,
                "has_upstream": True,
                "upstream_sha": upstream_sha,
                "changed_files": current,
                "diff_stat": f"{len(current)} files changed (manifest mode)",
                "commits": [],
                "dirty": bool(current),
            }
        except Exception:
            pass

    return {"ok": True, "has_upstream": False, "changed_files": [], "diff_stat": ""}


def _write_upstream_manifest(root: Path) -> None:
    """Snapshot file list at clone for diff-without-git fallback."""
    manifest: dict[str, float] = {}
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if any(x in p.parts for x in (".git", "node_modules", "__pycache__")):
            continue
        try:
            manifest[str(p.relative_to(root))] = p.stat().st_mtime
        except OSError:
            pass
    meta_dir = root / ".daedalus"
    meta_dir.mkdir(parents=True, exist_ok=True)
    (meta_dir / "upstream_manifest.json").write_text(
        json.dumps(manifest, indent=0), encoding="utf-8",
    )
