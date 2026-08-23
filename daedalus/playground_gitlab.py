"""Дедал Playground — GitLab create / push.

Използва ``GITLAB_TOKEN`` (или ``GitLabToken``) + git CLI + GitLab REST API v4.

  create_project(name)   — създава нов project в namespace (GITLAB_NAMESPACE)
  push_project(slug,...) — git init/commit/push с oauth2 token URL
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path
from typing import Any

from . import playground as pg

log = logging.getLogger(__name__)

_GIT = "git"
_TIMEOUT = 180


def gitlab_host() -> str:
    return os.environ.get("GITLAB_HOST", "gitlab.com").strip().rstrip("/")


def _host() -> str:
    return gitlab_host()


def _api_base() -> str:
    return f"https://{_host()}/api/v4"


def _token() -> str:
    return (
        os.environ.get("GITLAB_TOKEN", "").strip()
        or os.environ.get("GitLabToken", "").strip()
    )


def _default_namespace() -> str:
    return os.environ.get("GITLAB_NAMESPACE", "").strip()


def default_namespace() -> str:
    return _default_namespace()


def gitlab_error_hint(exc: Exception | str) -> str:
    msg = str(exc).lower()
    if "not configured" in msg or "gitlab_token" in msg:
        return (
            "GITLAB_TOKEN не е конфигуриран. "
            "Задай GITLAB_TOKEN (или GitLabToken) с права api + write_repository."
        )
    if "403" in msg or "forbidden" in msg:
        return "GitLab отказа достъп (403). Токенът трябва scope api + write_repository."
    if "401" in msg or "unauthorized" in msg:
        return "GitLab 401 — невалиден или изтекъл GITLAB_TOKEN."
    if "already exists" in msg or "has already been taken" in msg:
        return "Project с това име вече съществува — посочи group/project или друго име."
    if "command not found" in msg and "git" in msg:
        return "git CLI липсва на сървъра — инсталирай git."
    if "nothing to commit" in msg:
        return "Няма промени за commit — добави файлове в проекта преди push."
    return str(exc)[:500]


def is_configured() -> bool:
    return bool(_token())


def _auth_url(repo_url: str) -> str:
    """Inject oauth2 token into https GitLab URL for push."""
    tok = _token()
    host = re.escape(_host())
    m = re.match(rf"https://{host}/(.+)", repo_url.strip())
    if not m or not tok:
        return repo_url
    return f"https://oauth2:{tok}@{_host()}/{m.group(1)}"


def _scrub_secrets(text: str) -> str:
    tok = _token()
    if tok:
        text = text.replace(tok, "***")
    return text


async def _run(args: list[str], *, cwd: Path | None = None, timeout: int = _TIMEOUT) -> tuple[int, str]:
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    proc = await asyncio.create_subprocess_exec(
        *args, cwd=str(cwd) if cwd else None,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        env=env,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return 124, f"timeout after {timeout}s"
    return proc.returncode or 0, _scrub_secrets((out or b"").decode("utf-8", errors="replace"))


async def _resolve_namespace_id(namespace_path: str) -> int | None:
    """Resolve GitLab group/user namespace path → numeric id."""
    if not namespace_path:
        return None
    import httpx

    headers = {"PRIVATE-TOKEN": _token()}
    async with httpx.AsyncClient(timeout=15.0) as cx:
        for endpoint in (f"/groups/{namespace_path}", f"/namespaces/{namespace_path}"):
            try:
                r = await cx.get(f"{_api_base()}{endpoint}", headers=headers)
                if r.status_code == 200:
                    data = r.json()
                    return int(data.get("id") or 0) or None
            except Exception:
                continue
        r = await cx.get(
            f"{_api_base()}/namespaces",
            headers=headers,
            params={"search": namespace_path},
        )
        if r.status_code != 200:
            return None
        for item in r.json() or []:
            if (item.get("path") or "").lower() == namespace_path.lower():
                return int(item.get("id") or 0) or None
            if (item.get("full_path") or "").lower() == namespace_path.lower():
                return int(item.get("id") or 0) or None
    return None


async def create_project(
    name: str,
    *,
    private: bool = True,
    description: str = "",
    namespace: str = "",
) -> dict[str, Any]:
    """Create a new GitLab project under namespace (group) or user."""
    if not is_configured():
        raise RuntimeError(gitlab_error_hint("GITLAB_TOKEN not configured"))
    import httpx

    slug = pg.slugify(name) or name
    ns_path = (namespace or _default_namespace()).strip()
    payload: dict[str, Any] = {
        "name": name,
        "path": slug,
        "visibility": "private" if private else "public",
        "description": description or "Built with Дедал (ADN Playground)",
        "initialize_with_readme": False,
    }
    ns_id = await _resolve_namespace_id(ns_path) if ns_path else None
    if ns_id:
        payload["namespace_id"] = ns_id

    headers = {"PRIVATE-TOKEN": _token()}
    async with httpx.AsyncClient(timeout=30.0) as cx:
        r = await cx.post(f"{_api_base()}/projects", headers=headers, data=payload)
    if r.status_code >= 400:
        raise RuntimeError(gitlab_error_hint(f"GitLab create project failed: {r.text[:400]}"))
    data = r.json()
    path_with_ns = data.get("path_with_namespace") or ""
    web_url = data.get("web_url") or f"https://{_host()}/{path_with_ns}"
    return {
        "full_name": path_with_ns,
        "html_url": web_url,
        "clone_url": data.get("http_url_to_repo") or f"https://{_host()}/{path_with_ns}.git",
        "private": private,
    }


async def push_project(
    slug: str,
    *,
    repo_full_name: str = "",
    create_if_missing: bool = True,
    private: bool = True,
    branch: str = "main",
    commit_msg: str = "",
    namespace: str = "",
) -> dict[str, Any]:
    """Init git in project dir and push to GitLab."""
    if not is_configured():
        raise RuntimeError(gitlab_error_hint("GITLAB_TOKEN not configured — cannot push"))
    proj = await pg.get_project(slug)
    if not proj:
        raise ValueError(f"project '{slug}' not found")
    root = pg.project_dir(proj["slug"])
    if not root.exists() or not any(root.iterdir()):
        raise ValueError(f"project '{slug}' workspace is empty")

    created = None
    if not repo_full_name:
        if not create_if_missing:
            raise ValueError("repo_full_name required when create_if_missing=False")
        created = await create_project(
            proj["slug"],
            private=private,
            description=proj.get("description", ""),
            namespace=namespace or _default_namespace(),
        )
        repo_full_name = created["full_name"]

    repo_url = f"https://{_host()}/{repo_full_name}.git"
    auth = _auth_url(repo_url)
    msg = commit_msg or f"Дедал: publish {proj['name']}"

    gi = root / ".gitignore"
    if not gi.exists():
        gi.write_text(
            "node_modules/\n__pycache__/\n.venv/\ndist/\n.cache/\nLibrary/\nTemp/\nLogs/\n",
            encoding="utf-8",
        )

    steps = [
        [_GIT, "init", "-q"],
        [_GIT, "-c", "user.email=daedalus@argos.nexus",
         "-c", "user.name=Дедал", "checkout", "-B", branch],
        [_GIT, "add", "-A"],
    ]
    log_out: list[str] = []
    for step in steps:
        code, out = await _run(step, cwd=root)
        if code != 0:
            raise RuntimeError(gitlab_error_hint(f"git step {' '.join(step[:2])} failed: {out[-400:]}"))
        if out.strip():
            log_out.append(out.strip())

    code, out = await _run(
        [_GIT, "-c", "user.email=daedalus@argos.nexus",
         "-c", "user.name=Дедал", "commit", "-q", "-m", msg],
        cwd=root,
    )
    if code != 0:
        if "nothing to commit" in out.lower():
            code2, _out2 = await _run([_GIT, "rev-parse", "--verify", "HEAD"], cwd=root)
            if code2 != 0:
                raise RuntimeError(gitlab_error_hint("nothing to commit — проектът е празен"))
        else:
            raise RuntimeError(gitlab_error_hint(f"git commit failed: {out[-400:]}"))
    elif out.strip():
        log_out.append(out.strip())

    for step in (
        ([_GIT, "remote", "add", "origin", auth],),
        ([_GIT, "push", "-q", "-f", "-u", "origin", branch],),
    ):
        for sub in step:
            code, out = await _run(sub, cwd=root)
            if code != 0 and sub[1] == "remote":
                code, out = await _run([_GIT, "remote", "set-url", "origin", auth], cwd=root)
            if code != 0:
                raise RuntimeError(gitlab_error_hint(f"git {sub[1]} failed: {out[-400:]}"))
            if out.strip():
                log_out.append(out.strip())

    html_url = f"https://{_host()}/{repo_full_name}"
    await pg.update_project(slug, gitlab_repo=html_url, status="pushed")
    log.info("Playground: pushed %s → GitLab %s", slug, repo_full_name)
    return {
        "ok": True,
        "provider": "gitlab",
        "repo": repo_full_name,
        "html_url": html_url,
        "created": created,
        "branch": branch,
    }
