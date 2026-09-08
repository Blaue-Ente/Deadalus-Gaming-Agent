"""Unified Playground runtime — command resolution, env, HTTP probes, preview paths.

Single source of truth for kind-specific runner behaviour shared by
``playground_runner``, ``playground_proxy``, ``playground_browser``, tests,
and the standalone workshop adapter.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from . import playground as pg
from .capability_registry import BROWSER_VISUAL_KINDS

# Chromium visual kinds. Godot uses the engine runner, not this tuple.
VISUAL_KINDS = tuple(sorted(BROWSER_VISUAL_KINDS))

# Kinds prepared locally (Unity Hub / Xcode) — never start a dev subprocess in cloud.
LOCAL_ONLY_KINDS = ("unity", "godot")

UNITY_LOCAL_ONLY_MSG = (
    "Unity Editor не работи в ADN cloud. Подготви C# проекта и го качи в GitHub/GitLab — "
    "отвори в Unity Hub на локална машина."
)

GODOT_LOCAL_ONLY_MSG = (
    "Godot Editor не работи в ADN cloud. Отвори папката в Godot 4.3 локално. "
    "Ако `godot` е в PATH: godot --headless --path . --quit"
)

ADMIN_PREVIEW_PREFIX = "/admin/daedalus/playground/preview"
APPS_PREFIX = "/apps"

_ENV_ALLOW = {
    "PATH", "HOME", "LANG", "LC_ALL", "TERM", "TZ", "TMPDIR", "USER",
    "PYTHONUNBUFFERED", "PYTHONDONTWRITEBYTECODE",
    "NODE_ENV", "NPM_CONFIG_CACHE", "NIX_PATH",
    "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_DATA_DIRS",
    "npm_config_prefix", "NPM_CONFIG_PREFIX",
}

# HTTP smoke probe config per process kind: (paths, optional body substrings).
HTTP_PROBE_CONFIG: dict[str, tuple[list[str], tuple[str, ...]]] = {
    "fastapi": (["/docs", "/openapi.json", "/redoc", "/"], ("swagger", "openapi", "fastapi", "redoc")),
    "streamlit": (["/"], ("streamlit", "stapp", "data-testid")),
    "node": (["/health", "/api/health", "/api/", "/"], ()),
    "expo": (["/", "/index.bundle"], ("expo", "react", "bundle", "root")),
}


def is_local_only_kind(kind: str) -> bool:
    return (kind or "").strip() in LOCAL_ONLY_KINDS


def local_only_kind_message(kind: str) -> str:
    k = (kind or "").strip()
    if k == "unity":
        return UNITY_LOCAL_ONLY_MSG
    if k == "godot":
        return GODOT_LOCAL_ONLY_MSG
    return f"Платформата {kind} не се стартира в cloud sandbox."


def verify_unity_scaffold(slug: str, paths: list[str] | None = None) -> dict[str, Any]:
    """Check Unity project files without running the Editor."""
    root = pg.project_dir(slug)
    if paths is None:
        paths = [t["path"] for t in pg.read_tree(slug)]
    issues: list[str] = []
    if "Packages/manifest.json" not in paths:
        issues.append("липсва Packages/manifest.json")
    cs = [p for p in paths if p.endswith(".cs")]
    if not cs:
        issues.append("няма C# файлове в Assets/")
    if not (root / "studio" / "preview" / "index.html").is_file():
        if "studio/preview/index.html" not in paths:
            issues.append("липсва studio/preview/index.html")
    ok = not issues
    return {
        "ok": ok,
        "issues": issues,
        "error": "; ".join(issues),
        "cs_count": len(cs),
        "file_count": len(paths),
    }


class PlaygroundRuntime:
    """Facade for playground subprocess lifecycle helpers."""

    @staticmethod
    def admin_preview_base(slug: str) -> str:
        return f"{ADMIN_PREVIEW_PREFIX}/{slug}"

    @staticmethod
    def apps_base(slug: str) -> str:
        return f"{APPS_PREFIX}/{slug}"

    @staticmethod
    def child_env(port: int) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if k in _ENV_ALLOW}
        env["PORT"] = str(port)
        env["PYTHONUNBUFFERED"] = "1"
        env.setdefault("CI", "1")
        env.setdefault("EXPO_NO_TELEMETRY", "1")
        home = env.get("HOME", os.path.expanduser("~"))
        env.setdefault("XDG_CONFIG_HOME", f"{home}/.config")
        env.setdefault("XDG_CACHE_HOME", f"{home}/.cache")
        env.setdefault("XDG_DATA_HOME", f"{home}/.local/share")
        env.setdefault("BROWSER", "none")
        return env

    @staticmethod
    def resolve_cmd(
        proj: dict[str, Any],
        port: int,
        *,
        tunnel: bool = False,
        preview_base: str = "",
    ) -> str:
        """Build the concrete shell command for this project kind."""
        kind = proj.get("kind", "static")
        run_cmd = (proj.get("run_cmd") or "").strip()
        root = pg.project_dir(proj["slug"])

        if kind == "fastapi":
            main = run_cmd.split()[1].split(":")[0] if ":" in run_cmd else "main"
            for cand in ("main", "app", "server"):
                if (root / f"{cand}.py").exists():
                    main = cand
                    break
            return f"python -m uvicorn {main}:app --host 0.0.0.0 --port {port}"

        if kind == "streamlit":
            target = "streamlit_app.py"
            for cand in ("streamlit_app.py", "app.py", "main.py"):
                if (root / cand).exists():
                    target = cand
                    break
            base = (
                f"python -m streamlit run {target} --server.headless true "
                f"--server.port {port} --server.address 0.0.0.0 "
                f"--browser.gatherUsageStats false"
            )
            if preview_base:
                path = preview_base.rstrip("/")
                base += (
                    f" --server.baseUrlPath={path}"
                    " --server.enableCORS=false"
                    " --server.enableXsrfProtection=false"
                )
            return base

        if kind in ("vite", "vite-react"):
            return f"npx --yes vite --host 0.0.0.0 --port {port} --base ./ --no-open"

        if kind == "node":
            for cand in ("index.js", "server.js", "app.js", "main.js"):
                if (root / cand).exists():
                    return f"node {cand}"
            return run_cmd or "node index.js"

        if kind == "expo":
            if tunnel:
                return "npx expo start --tunnel --non-interactive"
            return (
                f"npx expo start --web --port {port} --non-interactive "
                f"--no-dev-client"
            )

        return ""

    @staticmethod
    def http_probe_paths(kind: str) -> tuple[list[str], tuple[str, ...]]:
        return HTTP_PROBE_CONFIG.get(kind, ([], ()))

    @staticmethod
    async def http_probe(
        port: int,
        paths: list[str],
        *,
        body_hints: tuple[str, ...] = (),
        timeout: float = 8.0,
    ) -> tuple[bool, str, str]:
        """HTTP health check. Returns (ok, hit_path, error_summary)."""
        import httpx

        errors: list[str] = []
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as cx:
            for path in paths:
                try:
                    r = await cx.get(f"http://127.0.0.1:{port}{path}")
                    body = (r.text or "")[:12000]
                    if r.status_code >= 500:
                        errors.append(f"{path}: HTTP {r.status_code}")
                        continue
                    if body_hints and not any(h.lower() in body.lower() for h in body_hints):
                        errors.append(
                            f"{path}: липсва очаквано съдържание ({', '.join(body_hints[:2])})"
                        )
                        continue
                    return True, path, ""
                except Exception as exc:
                    errors.append(f"{path}: {exc}")
        return False, "", "; ".join(errors[:4])

    @staticmethod
    def project_root(slug: str) -> Path:
        return pg.project_dir(slug)

    @staticmethod
    def preview_static_root(slug: str, kind: str) -> Path | None:
        """Filesystem root for admin preview when no dev subprocess is running.

        • static            → project workspace (always).
        • vite/vite-react/expo → ``dist/`` when ``dist/index.html`` exists.
        • other kinds       → None (UI should prompt Run / start subprocess).
        """
        root = pg.project_dir(slug)
        k = (kind or "static").strip()
        if k == "static":
            return root
        if k in ("vite", "vite-react", "expo"):
            dist = root / "dist"
            if dist.is_dir() and (dist / "index.html").is_file():
                return dist
            return None
        if k == "unity":
            preview = root / "studio" / "preview"
            if preview.is_dir() and (preview / "index.html").is_file():
                return preview
            if (root / "README.md").is_file():
                return root
            return None
        return None
