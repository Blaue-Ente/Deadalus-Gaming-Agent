"""Live publish — one button: play here, then a public https link.

Practical order (auto):
  1. Always materialize ``/apps/{slug}/`` (this machine, instant).
  2. Netlify with ``NETLIFY_AUTH_TOKEN`` — durable ``*.netlify.app`` in seconds.
  3. GitLab Pages with ``GITLAB_TOKEN`` — durable ``*.gitlab.io`` (CI ~1–2 min).
  4. Netlify anonymous — instant URL, claim within 1 hour or it vanishes.
  5. Local only if offline or no npx/git host.

Static / vite / expo-web only. FastAPI/Unity stay local (or Git push).
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from .capability_registry import allows_static_live_publish

log = logging.getLogger(__name__)
PAGES_CI = """pages:
  stage: deploy
  script:
    - test -f public/index.html
  artifacts:
    paths:
      - public
  rules:
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH
"""


def _data_file() -> Path:
    from .paths import data_root
    path = data_root() / "live_sites.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def load_sites() -> dict[str, Any]:
    path = _data_file()
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                sites = data.get("sites")
                if isinstance(sites, dict):
                    return data
    except Exception:
        pass
    return {"sites": {}}


def save_sites(data: dict[str, Any]) -> None:
    _data_file().write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def record_site(slug: str, row: dict[str, Any]) -> dict[str, Any]:
    data = load_sites()
    sites = data.setdefault("sites", {})
    prev = sites.get(slug) or {}
    merged = {**prev, **row, "slug": slug, "updated_at": time.time()}
    sites[slug] = merged
    save_sites(data)
    return merged


def get_site(slug: str) -> dict[str, Any] | None:
    row = (load_sites().get("sites") or {}).get(slug)
    return dict(row) if isinstance(row, dict) else None


def netlify_token() -> str:
    return (
        os.environ.get("NETLIFY_AUTH_TOKEN")
        or os.environ.get("NETLIFY_TOKEN")
        or ""
    ).strip()


def _npx() -> str | None:
    name = "npx.cmd" if os.name == "nt" else "npx"
    return shutil.which(name) or shutil.which("npx")


def parse_json_blob(text: str) -> dict[str, Any]:
    """Netlify CLI prints logs then a JSON object — take the last object."""
    start = text.rfind("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def extract_https(text: str) -> str | None:
    found = re.findall(r"https://[a-zA-Z0-9._/-]+\.(?:netlify\.app|gitlab\.io)[a-zA-Z0-9._/-]*", text or "")
    return found[-1].rstrip(").,") if found else None


def pages_url(namespace: str, project: str, host: str = "gitlab.com") -> str:
    ns = (namespace or "").strip().split("/")[-1].lower()
    proj = (project or "").strip().split("/")[-1]
    if host and host not in ("gitlab.com", "www.gitlab.com"):
        # self-managed: https://pages.example.com/ns/proj
        return f"https://{ns}.{host}/{proj}/"
    return f"https://{ns}.gitlab.io/{proj}/"


def pick_chain(requested: str = "auto") -> list[str]:
    from .offline import enabled as offline_on

    want = (requested or "auto").strip().lower()
    if offline_on():
        return ["local"]
    if want not in ("", "auto"):
        return [want]
    chain: list[str] = []
    if netlify_token():                    # the token path is the HTTP API: no Node/npx needed (review 2026-11-11)
        chain.append("netlify")
    try:
        from .playground_gitlab import is_configured
        if is_configured():
            chain.append("gitlab_pages")
    except Exception:
        pass
    if _npx() and "netlify_anon" not in chain:
        chain.append("netlify_anon")
    return chain


# Backends that create an unclaimed public site or a public GitLab project. They run only after the
# owner explicitly confirms (confirm_public=True); "auto" never picks them on its own.
CONFIRM_REQUIRED_BACKENDS = frozenset({"netlify_anon", "gitlab_pages"})


def effective_chain(requested: str = "auto", confirm_public: bool = False) -> list[str]:
    """``pick_chain`` minus confirm-only backends the owner has not confirmed."""
    chain = pick_chain(requested)
    if confirm_public:
        return chain
    return [name for name in chain if name not in CONFIRM_REQUIRED_BACKENDS]


def capabilities() -> dict[str, Any]:
    from .offline import enabled as offline_on
    from .playground_git_push import remote_status

    git = remote_status()
    return {
        "ok": True,
        "offline": offline_on(),
        "npx": bool(_npx()),
        "netlify_token": bool(netlify_token()),
        "gitlab": git.get("gitlab") or {},
        "chain": pick_chain("auto"),
        "sites": list((load_sites().get("sites") or {}).values()),
    }


async def ensure_local(slug: str) -> dict[str, Any]:
    """Build (if vite/expo) and materialize /apps/{slug}/."""
    from . import playground as pg
    from . import playground_publish as pub

    proj = await pg.get_project(slug)
    if not proj:
        return {"ok": False, "error": f"project '{slug}' not found"}
    kind = (proj.get("kind") or "static").lower()
    from .static_build import plan as _static_plan
    static_node = kind == "node" and _static_plan(pg.project_dir(slug)) is not None
    if not allows_static_live_publish(kind) and not static_node:
        return {
            "ok": False,
            "static": False,
            "kind": kind,
            "error": (
                f"{kind} is not a static live-publish target. "
                "Godot/Expo/Capacitor need a hashed immutable artifact; "
                "web preview does not replace device_smoke."
            ),
        }
    published = await pub.publish(slug, icon="🎮")
    dest = pub.app_dir(slug)
    index = dest / "index.html"
    if not index.is_file():
        src = pg.project_dir(slug) / "index.html"
        if src.is_file():
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, index)
    if not index.is_file():
        return {
            "ok": False,
            "error": "no index.html after publishing: the game is not static",
            "published": published,
        }
    return {
        "ok": True,
        "static": True,
        "kind": published.get("kind") or kind,
        "dir": dest,
        "local_url": f"/apps/{slug}/",
        "published": published,
    }


def _stage_pages(static_dir: Path, slug: str) -> Path:
    from .paths import data_root

    dest = data_root() / "live_export" / slug
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    public = dest / "public"
    shutil.copytree(static_dir, public, ignore=shutil.ignore_patterns(".git", "node_modules"))
    (dest / ".gitlab-ci.yml").write_text(PAGES_CI, encoding="utf-8")
    (dest / "README.md").write_text(
        f"# {slug}\n\nPublished by Дедал. GitLab Pages serves ``public/``.\n",
        encoding="utf-8",
    )
    return dest


async def _run(args: list[str], *, cwd: Path | None = None, timeout: int = 180,
               extra_env: dict[str, str] | None = None) -> tuple[int, str]:
    import asyncio

    env = dict(os.environ)
    if extra_env:
        env.update(extra_env)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["CI"] = "1"
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=str(cwd) if cwd else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=env,
    )
    try:
        raw, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return 124, f"timeout after {timeout}s"
    text = (raw or b"").decode("utf-8", errors="replace")
    tok = netlify_token()
    if tok:
        text = text.replace(tok, "***")
    return proc.returncode or 0, text


NETLIFY_API = "https://api.netlify.com/api/v1"


def _zip_static(static_dir: Path) -> bytes:
    """The published folder as a zip, without secret files (same rule as the capsule)."""
    import io
    import zipfile

    from .secret_paths import is_secret_path

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(static_dir.rglob("*")):
            rel = path.relative_to(static_dir)
            if path.is_file() and not path.is_symlink() and not is_secret_path(rel):
                zf.write(path, rel.as_posix())
    return buf.getvalue()


def _api_error(resp: Any) -> str:
    try:
        body = resp.json()
        msg = body.get("message") or body.get("error") or str(body)
    except Exception:
        msg = (resp.text or "")[:200]
    return f"HTTP {resp.status_code}: {str(msg)[:200]}"


async def deploy_netlify_api(static_dir: Path, slug: str, *, poll_s: float = 90.0) -> dict[str, Any]:
    """Deploy with the owner's token through the Netlify API: create (or reuse) the site, upload a zip, wait for it.

    Found live (2026-11-11): netlify-cli in non-interactive mode stopped at "No teams available" on an account that has
    a team, and it needed the token in its argv (``--auth``), where any process listing shows it. The API path needs no
    CLI download and keeps the token in a request header only."""
    import asyncio

    import httpx

    token = netlify_token()
    if not token:
        raise RuntimeError("NETLIFY_AUTH_TOKEN is missing")
    headers = {"Authorization": f"Bearer {token}", "User-Agent": "daedalus-live-publish"}
    prev = get_site(slug) or {}
    site_id = prev.get("site_id") if prev.get("backend") == "netlify" else None
    async with httpx.AsyncClient(base_url=NETLIFY_API, timeout=120) as client:
        site: dict[str, Any] = {}
        if site_id:
            r = await client.get(f"/sites/{site_id}", headers=headers)
            site = r.json() if r.status_code == 200 else {}
            site_id = site.get("id")
        if not site_id:
            r = await client.post("/sites", headers=headers, json={})
            if r.status_code >= 300:
                raise RuntimeError(f"netlify could not create a site: {_api_error(r)}")
            site = r.json()
            site_id = site.get("id")
        r = await client.post(f"/sites/{site_id}/deploys", headers={**headers, "Content-Type": "application/zip"},
                              content=_zip_static(static_dir))
        if r.status_code >= 300:
            raise RuntimeError(f"netlify deploy upload failed: {_api_error(r)}")
        deploy = r.json()
        state = str(deploy.get("state") or "")
        waited = 0.0
        while state not in ("ready", "error") and waited < poll_s:
            await asyncio.sleep(2.0)
            waited += 2.0
            r = await client.get(f"/deploys/{deploy.get('id')}", headers=headers)
            if r.status_code == 200:
                deploy = r.json()
                state = str(deploy.get("state") or "")
        if state == "error":
            raise RuntimeError(f"netlify deploy failed: {str(deploy.get('error_message') or 'error')[:200]}")
    url = site.get("ssl_url") or site.get("url") or deploy.get("ssl_url") or deploy.get("deploy_ssl_url")
    if not url:
        raise RuntimeError("netlify did not return a URL")
    return {"ok": True, "backend": "netlify", "url": url, "site_id": site_id,
            "note": "The public link is ready." if state == "ready" else f"Uploaded; Netlify is still processing ({state})."}


async def deploy_netlify(static_dir: Path, slug: str, *, anonymous: bool = False) -> dict[str, Any]:
    if not anonymous:
        return await deploy_netlify_api(static_dir, slug)
    npx = _npx()
    if not npx:
        raise RuntimeError("npx is missing: install Node.js to upload to Netlify")
    # Anonymous preview only: the owner-token path returned above (the API; never a token in argv).
    args = [npx, "-y", "netlify-cli", "deploy", "--dir", str(static_dir), "--json", "--allow-anonymous"]
    extra: dict[str, str] = {}
    prev = get_site(slug) or {}
    code, out = await _run(args, timeout=180, extra_env=extra)
    blob = parse_json_blob(out)
    url = (
        blob.get("url")
        or blob.get("ssl_url")
        or blob.get("deploy_url")
        or extract_https(out)
    )
    if code != 0 and not url:
        raise RuntimeError(f"netlify deploy failed: {out[-500:]}")
    if not url:
        raise RuntimeError(f"netlify returned no URL: {out[-400:]}")
    return {
        "ok": True,
        "backend": "netlify_anon" if anonymous else "netlify",
        "url": url,
        "site_id": blob.get("site_id") or prev.get("site_id"),
        "expires": "1h claim" if anonymous else None,
        "log_tail": out[-240:],
    }


async def deploy_gitlab_pages(static_dir: Path, slug: str) -> dict[str, Any]:
    from . import playground_gitlab as gl

    if not gl.is_configured():
        raise RuntimeError(gl.gitlab_error_hint("GITLAB_TOKEN not configured"))
    stage = _stage_pages(static_dir, slug)
    ns = gl.default_namespace()
    if not ns:
        ns = await _gitlab_username()
    project_path = f"daedalus-{slug}"
    full_name = f"{ns}/{project_path}" if ns else project_path
    created = None
    try:
        created = await gl.create_project(
            project_path,
            private=False,
            description=f"Дедал live / {slug}",
            namespace=ns,
        )
        full_name = created.get("full_name") or full_name
    except Exception as extra:
        log.info("live: gitlab project may already exist (%s)", extra)

    repo_url = f"https://{gl.gitlab_host()}/{full_name}.git"
    auth = gl._auth_url(repo_url)
    steps = [
        ["git", "init", "-q"],
        ["git", "-c", "user.email=daedalus@local", "-c", "user.name=Дедал", "checkout", "-B", "main"],
        ["git", "add", "-A"],
        ["git", "-c", "user.email=daedalus@local", "-c", "user.name=Дедал",
         "commit", "-q", "-m", f"Дедал live {slug}"],
    ]
    for step in steps:
        code, out = await gl._run(step, cwd=stage)
        if code != 0 and "nothing to commit" not in out.lower():
            raise RuntimeError(gl.gitlab_error_hint(f"git {' '.join(step[:2])} failed: {out[-300:]}"))
    code, out = await gl._run(["git", "remote", "add", "origin", auth], cwd=stage)
    if code != 0:
        await gl._run(["git", "remote", "set-url", "origin", auth], cwd=stage)
    code, out = await gl._run(["git", "push", "-q", "-f", "-u", "origin", "main"], cwd=stage)
    if code != 0:
        raise RuntimeError(gl.gitlab_error_hint(f"git push failed: {out[-400:]}"))

    ns_part = full_name.split("/")[0]
    proj_part = full_name.split("/")[-1]
    url = pages_url(ns_part, proj_part, gl.gitlab_host())
    return {
        "ok": True,
        "backend": "gitlab_pages",
        "url": url,
        "repo": full_name,
        "html_url": created.get("html_url") if created else f"https://{gl.gitlab_host()}/{full_name}",
        "note": "GitLab Pages is usually live 1–2 minutes later (CI).",
        "created": created,
    }


async def _gitlab_username() -> str:
    import httpx
    from . import playground_gitlab as gl

    token = os.environ.get("GITLAB_TOKEN") or os.environ.get("GitLabToken") or ""
    async with httpx.AsyncClient(timeout=15.0) as cx:
        r = await cx.get(
            f"https://{gl.gitlab_host()}/api/v4/user",
            headers={"PRIVATE-TOKEN": token},
        )
    if r.status_code >= 400:
        raise RuntimeError(gl.gitlab_error_hint(f"GitLab /user failed: {r.text[:200]}"))
    return str((r.json() or {}).get("username") or "").strip()


async def publish_live(
    slug: str,
    *,
    backend: str = "auto",
    local_fn: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
    deploy_fn: Callable[..., Awaitable[dict[str, Any]]] | None = None,
    confirm_public: bool = False,
) -> dict[str, Any]:
    """Local /apps/ plus the first public backend that works.

    Anonymous Netlify and GitLab Pages need ``confirm_public=True``; asking for one
    explicitly without it is refused before anything is built or sent.
    """
    asked = (backend or "auto").strip().lower()
    if asked in CONFIRM_REQUIRED_BACKENDS and not confirm_public:
        return {
            "ok": False,
            "error": "confirm_required",
            "backend": asked,
            "detail": f"{asked} publishes publicly; confirm with confirm_public=true.",
        }
    ensure = local_fn or ensure_local
    local = await ensure(slug)
    if not local.get("ok"):
        return {"ok": False, **local}

    result: dict[str, Any] = {
        "ok": True,
        "slug": slug,
        "local_url": local.get("local_url"),
        "url": None,
        "backend": "local",
        "chain": effective_chain(backend, confirm_public),
        "note": "The game is at /apps/ on this machine.",
    }
    static_dir = local.get("dir")
    if isinstance(static_dir, str):
        static_dir = Path(static_dir)
    if backend.strip().lower() == "local" or not static_dir:
        record_site(slug, result)
        return result

    errors: list[str] = []
    skipped = [n for n in pick_chain(backend) if n in CONFIRM_REQUIRED_BACKENDS] if not confirm_public else []
    for name in effective_chain(backend, confirm_public):
        if name == "local":
            continue
        try:
            if deploy_fn:
                remote = await deploy_fn(name, static_dir, slug)
            elif name == "netlify":
                remote = await deploy_netlify(static_dir, slug, anonymous=False)
            elif name == "netlify_anon":
                remote = await deploy_netlify(static_dir, slug, anonymous=True)
            elif name == "gitlab_pages":
                remote = await deploy_gitlab_pages(static_dir, slug)
            else:
                errors.append(f"unknown backend {name}")
                continue
        except Exception as extra:
            log.warning("live: %s failed: %s", name, extra)
            errors.append(f"{name}: {extra}")
            continue
        result.update({
            "url": remote.get("url"),
            "backend": remote.get("backend") or name,
            "site_id": remote.get("site_id"),
            "repo": remote.get("repo"),
            "expires": remote.get("expires"),
            "note": remote.get("note") or "The public link is ready.",
            "html_url": remote.get("html_url"),
        })
        record_site(slug, {k: v for k, v in result.items() if k != "chain"})
        if errors:
            result["fallback_errors"] = errors
        return result

    result["ok"] = True
    if skipped:
        result["needs_confirm"] = skipped
    if errors:
        # A tried backend failed: say why, instead of asking for a token the owner already gave (found live).
        result["note"] = "Локално е готово. Публичното качване не мина: " + "; ".join(e[:200] for e in errors[:3])
    else:
        result["note"] = (
            "Локално е готово. За публичен линк сложи NETLIFY_AUTH_TOKEN "
            "(https://app.netlify.com/user/applications#personal-access-tokens) "
            "или GITLAB_TOKEN, или инсталирай Node.js (npx) за едночасов Netlify preview."
        )
    if errors:
        result["errors"] = errors
    record_site(slug, {k: v for k, v in result.items() if k != "chain"})
    return result
