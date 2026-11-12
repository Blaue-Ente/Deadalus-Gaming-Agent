"""Дедал Playground — GitLab clone / create / push / merge request.

Използва ``GITLAB_TOKEN`` (или ``GitLabToken``) + git CLI + GitLab REST API v4.

  clone_repo(url)        — издърпва project в playground/projects/{slug}/
  create_project(name)   — създава нов project в namespace (GITLAB_NAMESPACE)
  push_project(slug,...) — git init/commit/push с oauth2 token URL
  create_merge_request   — MR към path_with_namespace
  create_upstream_mr     — push branch + MR към revived upstream
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

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
            "Отвори /admin/daedalus/connectors (Свързаност) и сложи токен "
            "с права api + write_repository."
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
    """Return the clean https URL. The token is sent via ``git_auth.auth_env`` in ``_run``,
    never embedded in the URL, argv or ``.git/config``."""
    return repo_url.strip()


def _scrub_secrets(text: str) -> str:
    from .git_auth import scrub

    return scrub(text, _token(), "oauth2")


async def _run(args: list[str], *, cwd: Path | None = None, timeout: int = _TIMEOUT,
               anonymous: bool = False) -> tuple[int, str]:
    from .git_auth import GIT_TOO_OLD, auth_env, git_supports_env_config

    if _token() and not anonymous and not git_supports_env_config():
        return 1, GIT_TOO_OLD
    env = auth_env(_host(), "oauth2", "" if anonymous else _token())
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
    force: bool = False,
) -> dict[str, Any]:
    """Init git in project dir and push to GitLab (non-force unless ``force``)."""
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
                raise RuntimeError(gitlab_error_hint("nothing to commit: the project is empty"))
        else:
            raise RuntimeError(gitlab_error_hint(f"git commit failed: {out[-400:]}"))
    elif out.strip():
        log_out.append(out.strip())

    for step in (
        ([_GIT, "remote", "add", "origin", auth],),
        ([_GIT, "push", "-q", "-f", "-u", "origin", branch] if force
         else [_GIT, "push", "-q", "-u", "origin", branch],),
    ):
        for sub in step:
            code, out = await _run(sub, cwd=root)
            if code != 0 and sub[1] == "remote":
                code, out = await _run([_GIT, "remote", "set-url", "origin", auth], cwd=root)
            if code != 0:
                if sub[1] == "push":
                    from .git_auth import raise_if_rejected
                    raise_if_rejected(out, force=force)
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


def parse_gitlab_repo(url: str, *, host: str = "") -> tuple[str, str] | None:
    """Return (path_with_namespace, https_clone_url) or None."""
    raw = (url or "").strip()
    if not raw:
        return None
    h = (host or gitlab_host()).strip().rstrip("/")
    m = re.match(r"https?://([^/]+)/(.+?)(?:\.git)?/?$", raw, re.I)
    if m:
        found_host, ns = m.group(1), m.group(2).strip("/")
        if not ns:
            return None
        if "gitlab" in found_host.lower() or found_host.lower() == h.lower():
            return ns, f"https://{found_host}/{ns}.git"
        return None
    m = re.match(r"git@([^:]+):(.+?)(?:\.git)?/?$", raw, re.I)
    if m:
        found_host, ns = m.group(1), m.group(2).strip("/")
        if not ns or "github.com" in found_host.lower():
            return None
        return ns, f"https://{found_host}/{ns}.git"
    # gitlab.com/group/project without a scheme — first segment has a dot, so
    # it is a host, not a namespace (owner/repo stays GitHub shorthand).
    m = re.match(r"^([^/\s]+\.[^/\s]+)/(.+?)(?:\.git)?/?$", raw)
    if m:
        found_host, ns = m.group(1), m.group(2).strip("/")
        if not ns:
            return None
        if "gitlab" in found_host.lower() or found_host.lower() == h.lower():
            return ns, f"https://{found_host}/{ns}.git"
        return None
    if re.match(r"^[\w-]+(?:/[\w.-]+)+$", raw) and "github.com" not in raw.lower():
        ns = raw.strip("/")
        return ns, f"https://{h}/{ns}.git"
    return None


def _encoded_project(path_with_ns: str) -> str:
    return quote(path_with_ns.strip("/"), safe="")


def _gitlab_headers() -> dict[str, str]:
    tok = _token()
    if not tok:
        raise RuntimeError(gitlab_error_hint("GITLAB_TOKEN not configured"))
    return {"PRIVATE-TOKEN": tok}


def list_projects(limit: int = 30) -> list[dict[str, Any]]:
    import httpx

    if not is_configured():
        raise RuntimeError(gitlab_error_hint("GITLAB_TOKEN not configured"))
    r = httpx.get(
        f"{_api_base()}/projects",
        headers=_gitlab_headers(),
        params={
            "membership": True,
            "simple": True,
            "order_by": "last_activity_at",
            "per_page": min(max(limit, 1), 100),
        },
        timeout=20.0,
    )
    r.raise_for_status()
    out: list[dict[str, Any]] = []
    for p in r.json() or []:
        path = p.get("path_with_namespace") or ""
        out.append({
            "full_name": path,
            "description": p.get("description") or "",
            "url": p.get("web_url") or "",
            "clone_url": p.get("http_url_to_repo") or (f"https://{_host()}/{path}.git" if path else ""),
            "language": "",
            "stars": p.get("star_count", 0),
            "private": (p.get("visibility") or "") != "public",
            "default_branch": p.get("default_branch") or "main",
            "updated_at": p.get("last_activity_at") or "",
        })
    return out


def get_project_meta(path_with_ns: str) -> dict[str, Any]:
    import httpx

    r = httpx.get(
        f"{_api_base()}/projects/{_encoded_project(path_with_ns)}",
        headers=_gitlab_headers(),
        timeout=20.0,
    )
    r.raise_for_status()
    p = r.json()
    path = p.get("path_with_namespace") or path_with_ns
    return {
        "full_name": path,
        "url": p.get("web_url") or "",
        "clone_url": p.get("http_url_to_repo") or "",
        "default_branch": p.get("default_branch") or "main",
        "private": (p.get("visibility") or "") != "public",
        "description": p.get("description") or "",
    }


def create_merge_request(
    path_with_ns: str,
    *,
    source_branch: str,
    target_branch: str = "main",
    title: str = "",
    description: str = "",
) -> dict[str, Any]:
    """Open a GitLab merge request. Reuse an open MR for the same source branch."""
    import httpx

    if not is_configured():
        raise RuntimeError(gitlab_error_hint("GITLAB_TOKEN not configured"))
    ns = (path_with_ns or "").strip("/")
    src = (source_branch or "").strip()
    tgt = (target_branch or "main").strip() or "main"
    title = (title or "").strip() or f"Дедал: {src}"
    if not ns or not src:
        raise ValueError("project path and source_branch are required")
    headers = _gitlab_headers()
    encoded = _encoded_project(ns)
    payload = {
        "source_branch": src,
        "target_branch": tgt,
        "title": title,
        "description": description or "",
    }
    r = httpx.post(
        f"{_api_base()}/projects/{encoded}/merge_requests",
        headers=headers,
        json=payload,
        timeout=20.0,
    )
    if r.status_code in (200, 201):
        mr = r.json()
        url = mr.get("web_url") or ""
        return {
            "ok": True,
            "number": mr.get("iid"),
            "title": mr.get("title", ""),
            "url": url,
            "html_url": url,
            "state": mr.get("state", ""),
            "head": src,
            "base": tgt,
            "existed": False,
        }
    if r.status_code in (409, 422):
        listed = httpx.get(
            f"{_api_base()}/projects/{encoded}/merge_requests",
            headers=headers,
            params={"state": "opened", "source_branch": src},
            timeout=20.0,
        )
        if listed.status_code == 200:
            items = listed.json() or []
            if items:
                mr = items[0]
                url = mr.get("web_url") or ""
                return {
                    "ok": True,
                    "number": mr.get("iid"),
                    "title": mr.get("title", ""),
                    "url": url,
                    "html_url": url,
                    "state": mr.get("state", ""),
                    "head": src,
                    "base": tgt,
                    "existed": True,
                }
        raise RuntimeError(gitlab_error_hint(f"GitLab MR {r.status_code}: {r.text[:400]}"))
    raise RuntimeError(gitlab_error_hint(f"GitLab MR failed: HTTP {r.status_code} {r.text[:400]}"))


def _repo_slug_from_url(url: str) -> str:
    parsed = parse_gitlab_repo(url)
    if parsed:
        return pg.slugify(parsed[0].rsplit("/", 1)[-1])
    return pg.slugify(url.rsplit("/", 1)[-1])


async def clone_repo(
    repo_url: str,
    *,
    name: str = "",
    slug: str = "",
    branch: str = "",
    ref: str = "",
    source: str = "cloned",
    keep_git: bool | None = None,
    auto_onboard: bool = False,
) -> dict[str, Any]:
    """Clone a GitLab project into a new playground project."""
    from .playground_revive import compatibility_report, _write_upstream_manifest

    repo_url = (repo_url or "").strip()
    parsed = parse_gitlab_repo(repo_url)
    if not parsed:
        raise ValueError("not a GitLab URL — use https://gitlab.com/group/project")
    ns, clone_https = parsed
    clean_url = f"https://{gitlab_host()}/{ns}" if "://" not in repo_url else re.sub(
        r"https://[^@]*@", "https://", clone_https
    ).removesuffix(".git")

    slug = pg.slugify(slug or name or _repo_slug_from_url(repo_url))
    dest = pg.project_dir(slug)
    if dest.exists() and any(dest.iterdir()):
        raise ValueError(f"project '{slug}' already exists — choose another name")
    dest.parent.mkdir(parents=True, exist_ok=True)

    clone_args = [_GIT, "clone", "--depth", "1"]
    checkout_ref = ref or branch
    if checkout_ref:
        clone_args += ["--branch", checkout_ref]
    clone_args += [_auth_url(clone_https), str(dest)]

    code, out = await _run(clone_args)
    if code != 0 and _token():
        from .git_auth import auth_failed
        if auth_failed(out):
            # A refused token (expired, wrong scope, placeholder) must not block a public repo: retry once without it.
            import shutil
            shutil.rmtree(dest, ignore_errors=True)
            code, out = await _run(clone_args, anonymous=True)
    if code != 0:
        raise RuntimeError(gitlab_error_hint(f"git clone failed: {out[-500:]}"))

    upstream_sha = ""
    gitdir = dest / ".git"
    if gitdir.exists():
        code_sha, sha_out = await _run([_GIT, "rev-parse", "HEAD"], cwd=dest)
        if code_sha == 0:
            upstream_sha = sha_out.strip()

    if source == "revived":
        keep = True if keep_git is None else keep_git
    else:
        keep = False if keep_git is None else keep_git

    if not keep and gitdir.exists():
        import shutil
        shutil.rmtree(gitdir, ignore_errors=True)
        _write_upstream_manifest(dest)
    elif keep and upstream_sha:
        _write_upstream_manifest(dest)

    compat = compatibility_report(dest)
    proj_source = source if source in pg.VALID_SOURCES else "cloned"

    proj = await pg.create_project(
        name or slug, source=proj_source, slug=slug,
        description=f"Revived from {clean_url}" if proj_source == "revived"
        else f"Cloned from {clean_url}",
    )
    await pg.update_project(
        slug,
        gitlab_repo=clean_url,
        upstream_url=clean_url,
        cloned_branch=branch or checkout_ref or "",
        cloned_ref=checkout_ref or "",
        upstream_sha=upstream_sha,
        compat_score=float(compat.get("score", 0)),
        compat_detail=json.dumps(compat, ensure_ascii=False),
    )
    proj = await pg.get_project(slug) or proj

    if auto_onboard:
        from . import playground_runner as runner
        smoke = await runner.smoke_test(slug)
        proj["_onboard"] = smoke

    log.info("Playground: cloned GitLab %s → project %s (source=%s)", clean_url, slug, proj_source)
    return proj


async def push_upstream_branch(
    slug: str,
    *,
    branch_name: str = "",
    commit_msg: str = "",
) -> dict[str, Any]:
    """Push local changes to a branch on the GitLab upstream project."""
    if not is_configured():
        raise RuntimeError(gitlab_error_hint("GITLAB_TOKEN not configured"))
    proj = await pg.get_project(slug)
    if not proj:
        raise ValueError(f"project '{slug}' not found")
    upstream = proj.get("upstream_url") or proj.get("gitlab_repo") or ""
    parsed = parse_gitlab_repo(upstream)
    if not parsed:
        raise ValueError("no GitLab upstream URL — clone/revive from GitLab first")
    ns, clone_https = parsed
    root = pg.project_dir(slug)
    if not root.exists():
        raise ValueError("workspace missing")

    branch = branch_name or f"daedalus-revive-{slug}"
    msg = commit_msg or f"Дедал: revive improvements for {proj.get('name', slug)}"
    auth = _auth_url(clone_https)

    gi = root / ".gitignore"
    if not gi.exists():
        gi.write_text(
            "node_modules/\n__pycache__/\n.venv/\ndist/\n.cache/\n.daedalus/\n",
            encoding="utf-8",
        )

    if not (root / ".git").exists():
        steps = [
            [_GIT, "init", "-q"],
            [_GIT, "-c", "user.email=daedalus@argos.nexus",
             "-c", "user.name=Дедал", "checkout", "-B", branch],
            [_GIT, "add", "-A"],
            [_GIT, "-c", "user.email=daedalus@argos.nexus",
             "-c", "user.name=Дедал", "commit", "-q", "-m", msg],
            [_GIT, "remote", "add", "origin", auth],
            [_GIT, "push", "-q", "-u", "origin", branch],
        ]
    else:
        steps = [
            [_GIT, "-c", "user.email=daedalus@argos.nexus",
             "-c", "user.name=Дедал", "checkout", "-B", branch],
            [_GIT, "add", "-A"],
            [_GIT, "-c", "user.email=daedalus@argos.nexus",
             "-c", "user.name=Дедал", "commit", "-q", "-m", msg],
            [_GIT, "remote", "set-url", "origin", auth],
            [_GIT, "push", "-q", "-u", "origin", branch],
        ]

    for step in steps:
        code, out = await _run(step, cwd=root)
        if code != 0 and step[1] == "remote":
            code, out = await _run([_GIT, "remote", "add", "origin", auth], cwd=root)
        if code != 0 and "nothing to commit" in out.lower():
            code2, _ = await _run([_GIT, "rev-parse", "--verify", "HEAD"], cwd=root)
            if code2 != 0:
                raise RuntimeError(gitlab_error_hint("nothing to commit"))
            continue
        if code != 0:
            raise RuntimeError(gitlab_error_hint(f"git {step[1]} failed: {out[-400:]}"))

    return {
        "ok": True,
        "owner": ns.rsplit("/", 1)[0] if "/" in ns else "",
        "repo": ns.rsplit("/", 1)[-1],
        "full_name": ns,
        "branch": branch,
        "html_url": f"https://{_host()}/{ns}",
    }


async def create_upstream_mr(
    slug: str,
    *,
    title: str = "",
    body: str = "",
    base: str = "",
    branch_name: str = "",
    commit_msg: str = "",
) -> dict[str, Any]:
    """Push a revive branch and open a GitLab merge request."""
    pushed = await push_upstream_branch(
        slug, branch_name=branch_name, commit_msg=commit_msg,
    )
    ns = str(pushed.get("full_name") or "")
    head = str(pushed.get("branch") or "")
    if not ns or not head:
        raise RuntimeError("upstream push did not return project/branch")
    target = (base or "").strip()
    if not target:
        try:
            target = str(get_project_meta(ns).get("default_branch") or "main")
        except Exception:
            target = "main"
    if head == target:
        raise RuntimeError(
            f"head branch {head!r} is the default branch — cannot open an MR onto itself"
        )
    proj = await pg.get_project(slug)
    name = (proj or {}).get("name") or slug
    mr = create_merge_request(
        ns,
        source_branch=head,
        target_branch=target,
        title=title or f"Дедал: {name}",
        description=body or f"Промени от Дедал върху `{slug}`.\n",
    )
    return {"ok": True, "pr": mr, "mr": mr, "branch": head, "repo": ns}
