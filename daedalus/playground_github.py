"""Дедал Playground — GitHub clone / create / push / pull request.

Използва ``GITHUB_TOKEN`` (Studio → Свързаност) + git CLI.

  clone_repo(url)        — издърпва repo в playground/projects/{slug}/ (съживяване)
  create_repo(name)      — създава нов repo през PyGithub
  push_project(slug,...) — git init/commit/push с token-authenticated URL
  create_pull_request    — PR към owner/repo
  create_upstream_pr     — push branch + PR към revived upstream

Всички write-операции са fail-closed: при липса на токен/права → ясна грешка,
не тих провал.
"""
from __future__ import annotations
from .paths import repo_root

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from . import playground as pg

log = logging.getLogger(__name__)

_GIT = "git"
_TIMEOUT = 180  # seconds for clone/push

# workspace root — four levels up from this file
_WORKSPACE_ROOT = repo_root()

DEFAULT_KERNEL_REPO  = "healthyaicreations-hue/Daedalus-Agent"
DEFAULT_KERNEL_PATHS = [
    "Daedalus/daedalus",
    "argos_data_nexus/modules/daedalus",
    "argos_data_nexus/templates/daedalus.html",
    "argos_data_nexus/templates/daedalus_playground.html",
    "argos_data_nexus/templates/bundles.html",
]


def _token() -> str:
    from .connectors_config import github_token
    return github_token()


def github_error_hint(exc: Exception | str) -> str:
    """Turn raw git/PyGithub errors into actionable Bulgarian hints."""
    msg = str(exc).lower()
    if "not configured" in msg or "github_token" in msg:
        return (
            "GITHUB_TOKEN не е конфигуриран. "
            "Отвори /admin/daedalus/connectors (Свързаност) и сложи GitHub PAT."
        )
    if "403" in msg or "forbidden" in msg:
        return (
            "GitHub отказа достъп (403). Токенът няма права за push/създаване на repos.\n\n"
            "Нужен е GITHUB_TOKEN с scope repo:\n"
            "• Classic PAT: github.com/settings/tokens → Generate → отметни repo\n"
            "• Fine-grained: Contents + Pull requests + Administration (Read/Write)\n"
            "• Studio → Свързаност → GitHub PAT\n\n"
            "След това: Playground → ⬆ GitHub (1 клик, празно repo име = нов repo)."
        )
    if "422" in msg or "already exists" in msg or "name already exists" in msg:
        return "Repo с това име вече съществува — посочи owner/repo или избери друго име."
    if "command not found" in msg and "git" in msg:
        return "git CLI липсва на сървъра — инсталирай git."
    if "nothing to commit" in msg:
        return "Няма промени за commit — добави файлове в проекта преди push."
    return str(exc)[:500]


def is_configured() -> bool:
    from .connectors_config import is_github_configured
    return is_github_configured()


def _gh_get(path: str, params: dict[str, Any] | None = None) -> Any:
    import httpx
    tok = _token()
    if not tok:
        raise RuntimeError("GITHUB_TOKEN not configured")
    r = httpx.get(
        f"https://api.github.com{path}",
        headers={
            "Authorization": f"Bearer {tok}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "daedalus",
        },
        params=params or {},
        timeout=20.0,
    )
    r.raise_for_status()
    return r.json()


def _license_name(raw: dict[str, Any] | None) -> str:
    lic = raw or {}
    return str(lic.get("spdx_id") or lic.get("name") or "")


def list_user_repos(limit: int = 30, *, page: int = 1) -> list[dict[str, Any]]:
    data = _gh_get(
        "/user/repos",
        {"type": "owner", "sort": "updated", "per_page": limit, "page": page},
    )
    out: list[dict[str, Any]] = []
    for r in data:
        out.append({
            "full_name": r.get("full_name", ""),
            "description": r.get("description") or "",
            "url": r.get("html_url", ""),
            "clone_url": r.get("clone_url", ""),
            "language": r.get("language") or "",
            "stars": r.get("stargazers_count", 0),
            "private": bool(r.get("private")),
            "default_branch": r.get("default_branch", "main"),
            "license": _license_name(r.get("license") if isinstance(r.get("license"), dict) else None),
            "size_kb": r.get("size", 0),
            "updated_at": r.get("updated_at") or "",
        })
    return out


def list_repo_branches(owner: str, repo: str, limit: int = 30) -> list[dict[str, Any]]:
    data = _gh_get(f"/repos/{owner}/{repo}/branches", {"per_page": limit})
    return [
        {
            "name": b.get("name", ""),
            "sha": ((b.get("commit") or {}).get("sha") or "")[:12],
            "protected": bool(b.get("protected")),
        }
        for b in data[:limit]
    ]


def list_repo_tags(owner: str, repo: str, limit: int = 20) -> list[dict[str, Any]]:
    data = _gh_get(f"/repos/{owner}/{repo}/tags", {"per_page": limit})
    return [
        {"name": t.get("name", ""), "sha": ((t.get("commit") or {}).get("sha") or "")[:12]}
        for t in data[:limit]
    ]


def get_repo(owner: str, repo: str) -> dict[str, Any]:
    r = _gh_get(f"/repos/{owner}/{repo}")
    return {
        "full_name": r.get("full_name", ""),
        "description": r.get("description") or "",
        "url": r.get("html_url", ""),
        "clone_url": r.get("clone_url", ""),
        "language": r.get("language") or "",
        "stars": r.get("stargazers_count", 0),
        "forks": r.get("forks_count", 0),
        "open_issues": r.get("open_issues_count", 0),
        "default_branch": r.get("default_branch", "main"),
        "private": bool(r.get("private")),
        "license": _license_name(r.get("license") if isinstance(r.get("license"), dict) else None),
        "size_kb": r.get("size", 0),
    }


def get_authenticated_user() -> dict[str, Any]:
    u = _gh_get("/user")
    return {
        "login": u.get("login", ""),
        "name": u.get("name"),
        "email": u.get("email"),
        "bio": u.get("bio"),
        "public_repos": u.get("public_repos", 0),
        "followers": u.get("followers", 0),
        "url": u.get("html_url", ""),
    }


def search_repos(query: str, limit: int = 5) -> list[dict[str, Any]]:
    data = _gh_get("/search/repositories", {"q": query, "sort": "stars", "order": "desc", "per_page": limit})
    items = data.get("items") if isinstance(data, dict) else data
    out: list[dict[str, Any]] = []
    for r in (items or [])[:limit]:
        out.append({
            "full_name": r.get("full_name", ""),
            "description": r.get("description") or "",
            "url": r.get("html_url", ""),
            "language": r.get("language") or "",
            "stars": r.get("stargazers_count", 0),
        })
    return out


def list_repo_issues(owner: str, repo: str, state: str = "open", limit: int = 10) -> list[dict[str, Any]]:
    data = _gh_get(f"/repos/{owner}/{repo}/issues", {"state": state, "per_page": limit})
    out: list[dict[str, Any]] = []
    for i in data[:limit]:
        if i.get("pull_request"):
            continue
        out.append({
            "number": i.get("number"),
            "title": i.get("title", ""),
            "state": i.get("state", ""),
            "url": i.get("html_url", ""),
            "created_at": i.get("created_at") or "",
            "labels": [lb.get("name") for lb in (i.get("labels") or []) if isinstance(lb, dict)],
        })
    return out


def create_issue(
    owner: str,
    repo: str,
    title: str,
    *,
    body: str = "",
    labels: list[str] | None = None,
) -> dict[str, Any]:
    import httpx
    tok = _token()
    if not tok:
        raise RuntimeError("GITHUB_TOKEN not configured")
    payload: dict[str, Any] = {"title": title, "body": body}
    if labels:
        payload["labels"] = labels
    r = httpx.post(
        f"https://api.github.com/repos/{owner}/{repo}/issues",
        headers={
            "Authorization": f"Bearer {tok}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "daedalus",
        },
        json=payload,
        timeout=20.0,
    )
    r.raise_for_status()
    issue = r.json()
    return {
        "number": issue.get("number"),
        "title": issue.get("title", ""),
        "url": issue.get("html_url", ""),
        "html_url": issue.get("html_url", ""),
        "state": issue.get("state", ""),
    }


def _gh_headers() -> dict[str, str]:
    tok = _token()
    if not tok:
        raise RuntimeError("GITHUB_TOKEN not configured")
    return {
        "Authorization": f"Bearer {tok}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "daedalus",
    }


def parse_github_repo(url: str) -> tuple[str, str] | None:
    """Return (owner, repo) from a GitHub URL or owner/repo slug."""
    raw = (url or "").strip()
    if not raw:
        return None
    m = re.search(r"github\.com[:/]+([^/]+)/([^/.]+)", raw, re.I)
    if m:
        return m.group(1), m.group(2)
    if re.match(r"^[^/\s]+/[^/\s]+$", raw) and "gitlab" not in raw.lower():
        owner, repo = raw.split("/", 1)
        return owner, repo
    return None


def create_pull_request(
    owner: str,
    repo: str,
    *,
    title: str,
    head: str,
    base: str = "main",
    body: str = "",
    draft: bool = False,
) -> dict[str, Any]:
    """Open a GitHub pull request. If one already exists for head, return it."""
    import httpx

    if not is_configured():
        raise RuntimeError(github_error_hint("GITHUB_TOKEN not configured"))
    owner = (owner or "").strip()
    repo = (repo or "").strip()
    head = (head or "").strip()
    base = (base or "main").strip() or "main"
    title = (title or "").strip() or f"Дедал: {head}"
    if not owner or not repo or not head:
        raise ValueError("owner, repo and head branch are required")
    headers = _gh_headers()
    payload = {
        "title": title,
        "head": head,
        "base": base,
        "body": body or "",
        "draft": bool(draft),
    }
    r = httpx.post(
        f"https://api.github.com/repos/{owner}/{repo}/pulls",
        headers=headers,
        json=payload,
        timeout=20.0,
    )
    if r.status_code in (200, 201):
        pr = r.json()
        return {
            "ok": True,
            "number": pr.get("number"),
            "title": pr.get("title", ""),
            "url": pr.get("html_url", ""),
            "html_url": pr.get("html_url", ""),
            "state": pr.get("state", ""),
            "head": head,
            "base": base,
            "existed": False,
        }
    if r.status_code == 422:
        listed = httpx.get(
            f"https://api.github.com/repos/{owner}/{repo}/pulls",
            headers=headers,
            params={"state": "open", "head": f"{owner}:{head}"},
            timeout=20.0,
        )
        if listed.status_code == 200:
            items = listed.json() or []
            if items:
                pr = items[0]
                return {
                    "ok": True,
                    "number": pr.get("number"),
                    "title": pr.get("title", ""),
                    "url": pr.get("html_url", ""),
                    "html_url": pr.get("html_url", ""),
                    "state": pr.get("state", ""),
                    "head": head,
                    "base": base,
                    "existed": True,
                }
        raise RuntimeError(github_error_hint(f"GitHub PR 422: {r.text[:400]}"))
    raise RuntimeError(github_error_hint(f"GitHub PR failed: HTTP {r.status_code} {r.text[:400]}"))


async def create_upstream_pr(
    slug: str,
    *,
    title: str = "",
    body: str = "",
    base: str = "",
    branch_name: str = "",
    commit_msg: str = "",
) -> dict[str, Any]:
    """Push a revive branch and open a PR against the upstream default branch."""
    pushed = await push_upstream_branch(
        slug, branch_name=branch_name, commit_msg=commit_msg,
    )
    owner = str(pushed.get("owner") or "")
    repo = str(pushed.get("repo") or "")
    head = str(pushed.get("branch") or "")
    if not owner or not repo or not head:
        raise RuntimeError("upstream push did not return owner/repo/branch")
    target = (base or "").strip()
    if not target:
        try:
            target = str(get_repo(owner, repo).get("default_branch") or "main")
        except Exception:
            target = "main"
    if head == target:
        raise RuntimeError(
            f"head branch {head!r} is the default branch — cannot open a PR onto itself"
        )
    proj = await pg.get_project(slug)
    name = (proj or {}).get("name") or slug
    pr = create_pull_request(
        owner,
        repo,
        title=title or f"Дедал: {name}",
        head=head,
        base=target,
        body=body or f"Промени от Дедал върху `{slug}`.\n",
    )
    return {"ok": True, "pr": pr, "branch": head, "repo": f"{owner}/{repo}"}


def _auth_url(repo_url: str) -> str:
    """Return the clean https URL. The token is sent via ``git_auth.auth_env`` in ``_run``,
    never embedded in the URL, argv or ``.git/config``."""
    return repo_url.strip()


def _repo_slug_from_url(url: str) -> str:
    m = re.search(r"github\.com[:/]+([^/]+)/([^/.]+)", url.strip())
    if m:
        return pg.slugify(m.group(2))
    return pg.slugify(url.rsplit("/", 1)[-1])


async def _run(args: list[str], *, cwd: Path | None = None, timeout: int = _TIMEOUT) -> tuple[int, str]:
    """Run a git command, scrubbing the token from any output."""
    from .git_auth import auth_env

    env = auth_env("github.com", "x-access-token", _token())
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
    from .git_auth import scrub

    text = scrub((out or b"").decode("utf-8", errors="replace"), _token(), "x-access-token")
    return proc.returncode or 0, text


# ---------------------------------------------------------------------------
# Clone (pull a repo to revive with Дедал)
# ---------------------------------------------------------------------------

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
    """Clone a GitHub repo into a new playground project. Returns project dict.

    branch/ref — optional checkout target (branch name or tag).
    keep_git — retain .git for upstream diff (default True for revived).
    auto_onboard — run deps install + smoke test after clone.
    """
    from .playground_revive import compatibility_report, _write_upstream_manifest

    repo_url = repo_url.strip()
    if not repo_url.startswith("http"):
        repo_url = f"https://github.com/{repo_url}"
    clean_url = re.sub(r"https://[^@]*@", "https://", repo_url)

    slug = pg.slugify(slug or name or _repo_slug_from_url(repo_url))
    dest = pg.project_dir(slug)
    if dest.exists() and any(dest.iterdir()):
        raise ValueError(f"project '{slug}' already exists — choose another name")
    dest.parent.mkdir(parents=True, exist_ok=True)

    clone_args = [_GIT, "clone", "--depth", "1"]
    checkout_ref = ref or branch
    if checkout_ref:
        clone_args += ["--branch", checkout_ref]
    clone_args += [_auth_url(repo_url), str(dest)]

    code, out = await _run(clone_args)
    if code != 0:
        raise RuntimeError(f"git clone failed: {out[-500:]}")

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
        github_repo=clean_url,
        description=f"Revived from {clean_url}" if proj_source == "revived"
        else f"Cloned from {repo_url}",
    )

    await pg.update_project(
        slug,
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

    log.info("Playground: cloned %s → project %s (source=%s)", repo_url, slug, proj_source)
    return proj


# ---------------------------------------------------------------------------
# Create repo (PyGithub)
# ---------------------------------------------------------------------------

def create_repo(name: str, *, private: bool = True, description: str = "") -> dict[str, Any]:
    """Create a new GitHub repo under the authenticated user."""
    if not is_configured():
        raise RuntimeError(github_error_hint("GITHUB_TOKEN not configured"))
    from github import Github, Auth  # type: ignore
    gh = Github(auth=Auth.Token(_token()))
    user = gh.get_user()
    repo = user.create_repo(
        name=pg.slugify(name) or name,
        private=private,
        description=description or "Built with Дедал (ADN Playground)",
        auto_init=False,
    )
    return {
        "full_name": repo.full_name,
        "html_url": repo.html_url,
        "clone_url": repo.clone_url,
        "private": repo.private,
    }


# ---------------------------------------------------------------------------
# Push project → GitHub
# ---------------------------------------------------------------------------

async def push_project(
    slug: str,
    *,
    repo_full_name: str = "",
    create_if_missing: bool = True,
    private: bool = True,
    branch: str = "main",
    commit_msg: str = "",
    force: bool = False,
) -> dict[str, Any]:
    """Init git in the project dir and push to GitHub (non-force unless ``force``).

    repo_full_name: 'owner/repo'. If empty and create_if_missing, a repo named
    after the slug is created under the authenticated user.
    """
    if not is_configured():
        raise RuntimeError(github_error_hint("GITHUB_TOKEN not configured — cannot push"))
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
        created = create_repo(proj["slug"], private=private,
                              description=proj.get("description", ""))
        repo_full_name = created["full_name"]

    repo_url = f"https://github.com/{repo_full_name}.git"
    auth = _auth_url(repo_url)
    msg = commit_msg or f"Дедал: publish {proj['name']}"

    # ensure a .gitignore exists to avoid pushing junk
    gi = root / ".gitignore"
    if not gi.exists():
        gi.write_text("node_modules/\n__pycache__/\n.venv/\ndist/\n.cache/\n",
                      encoding="utf-8")

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
            raise RuntimeError(github_error_hint(f"git step {' '.join(step[:2])} failed: {out[-400:]}"))
        if out.strip():
            log_out.append(out.strip())

    code, out = await _run(
        [_GIT, "-c", "user.email=daedalus@argos.nexus",
         "-c", "user.name=Дедал", "commit", "-q", "-m", msg],
        cwd=root,
    )
    if code != 0:
        if "nothing to commit" in out.lower():
            # Allow push of prior commits; if none exist, fail clearly
            code2, out2 = await _run([_GIT, "rev-parse", "--verify", "HEAD"], cwd=root)
            if code2 != 0:
                raise RuntimeError(github_error_hint("nothing to commit — проектът е празен"))
        else:
            raise RuntimeError(github_error_hint(f"git commit failed: {out[-400:]}"))
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
                raise RuntimeError(github_error_hint(f"git {sub[1]} failed: {out[-400:]}"))
            if out.strip():
                log_out.append(out.strip())

    await pg.update_project(
        slug, github_repo=f"https://github.com/{repo_full_name}", status="pushed"
    )
    log.info("Playground: pushed %s → %s", slug, repo_full_name)
    return {
        "ok": True,
        "repo": repo_full_name,
        "html_url": f"https://github.com/{repo_full_name}",
        "created": created,
        "branch": branch,
    }


async def push_upstream_branch(
    slug: str,
    *,
    branch_name: str = "",
    commit_msg: str = "",
) -> dict[str, Any]:
    """Push local changes to a branch on the upstream repo (for PR workflow)."""
    if not is_configured():
        raise RuntimeError(github_error_hint("GITHUB_TOKEN not configured"))
    proj = await pg.get_project(slug)
    if not proj:
        raise ValueError(f"project '{slug}' not found")
    upstream = proj.get("upstream_url") or proj.get("github_repo") or ""
    if not upstream:
        raise ValueError("no upstream URL — project was not revived from GitHub")

    parsed = re.search(r"github\.com/([^/]+)/([^/.]+)", upstream)
    if not parsed:
        raise ValueError(f"invalid upstream URL: {upstream}")
    owner, repo = parsed.group(1), parsed.group(2)

    root = pg.project_dir(slug)
    if not root.exists():
        raise ValueError("workspace missing")

    branch = branch_name or f"daedalus-revive-{slug}"
    msg = commit_msg or f"Дедал: revive improvements for {proj.get('name', slug)}"
    auth = _auth_url(f"https://github.com/{owner}/{repo}.git")

    gi = root / ".gitignore"
    if not gi.exists():
        gi.write_text("node_modules/\n__pycache__/\n.venv/\ndist/\n.cache/\n.daedalus/\n",
                      encoding="utf-8")

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
                raise RuntimeError(github_error_hint("nothing to commit"))
            continue
        if code != 0:
            raise RuntimeError(github_error_hint(f"git {step[1]} failed: {out[-400:]}"))

    return {
        "ok": True,
        "owner": owner,
        "repo": repo,
        "branch": branch,
        "full_name": f"{owner}/{repo}",
    }


# ---------------------------------------------------------------------------
# Push Дедал kernel (sparse export of Дедал-relevant files → dedicated repo)
# ---------------------------------------------------------------------------

async def push_kernel(
    *,
    commit_msg: str = "",
    repo_full_name: str = DEFAULT_KERNEL_REPO,
    paths: list[str] | None = None,
    branch: str = "main",
    force: bool = False,
) -> dict[str, Any]:
    """Export only Дедал-relevant paths and push to the kernel repo.

    Creates a temporary directory, copies the selected paths (preserving
    relative structure from workspace root), then does a fresh git init and a
    push. The history is always fresh, so updating an existing kernel repo needs
    ``force=True``, which the owner must confirm (a plain push is rejected).
    """
    import shutil
    import tempfile
    import datetime as _dt

    if not is_configured():
        raise RuntimeError(github_error_hint("GITHUB_TOKEN not configured — cannot push kernel"))

    workspace = _WORKSPACE_ROOT
    selected = paths or DEFAULT_KERNEL_PATHS

    with tempfile.TemporaryDirectory(prefix="daedalus-kernel-") as tmpdir:
        tmp = Path(tmpdir)
        copied: list[str] = []

        for rel_path in selected:
            src = workspace / rel_path
            if not src.exists():
                log.debug("kernel push: skipping missing path %s", rel_path)
                continue
            dst = tmp / rel_path
            if src.is_dir():
                shutil.copytree(src, dst, dirs_exist_ok=True)
                copied.append(f"{rel_path}/")
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                copied.append(rel_path)

        if not copied:
            raise ValueError("no files matched — verify kernel paths in config")

        now = _dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        # manifest
        (tmp / "KERNEL_MANIFEST.md").write_text(
            f"# Daedalus Agent Kernel\n\n"
            f"Exported: {now}\n"
            f"Files / dirs: {len(copied)}\n\n"
            + "".join(f"- `{p}`\n" for p in copied),
            encoding="utf-8",
        )
        # .gitignore
        (tmp / ".gitignore").write_text(
            "__pycache__/\n*.pyc\n.cache/\nchroma/\n*.zip\n*.log\n",
            encoding="utf-8",
        )

        msg = commit_msg or f"Дедал kernel · {now}"
        auth = _auth_url(f"https://github.com/{repo_full_name}.git")

        steps = [
            [_GIT, "init", "-q"],
            [_GIT, "-c", "user.email=daedalus@argos.nexus",
             "-c", "user.name=Дедал", "checkout", "-B", branch],
            [_GIT, "add", "-A"],
            [_GIT, "-c", "user.email=daedalus@argos.nexus",
             "-c", "user.name=Дедал", "commit", "-q", "-m", msg],
            [_GIT, "remote", "add", "origin", auth],
            ([_GIT, "push", "-q", "-f", "-u", "origin", branch] if force
             else [_GIT, "push", "-q", "-u", "origin", branch]),
        ]

        log_lines: list[str] = []
        for step in steps:
            code, out = await _run(step, cwd=tmp)
            if code != 0 and step[1] == "remote":
                code, out = await _run(
                    [_GIT, "remote", "set-url", "origin", auth], cwd=tmp
                )
            if code != 0:
                if step[1] == "push":
                    from .git_auth import raise_if_rejected
                    raise_if_rejected(out, force=force)
                raise RuntimeError(f"git {step[1]} failed: {out[-400:]}")
            if out.strip():
                log_lines.append(out.strip())

        log.info("Kernel push → %s (%d paths)", repo_full_name, len(copied))
        return {
            "ok": True,
            "repo": repo_full_name,
            "html_url": f"https://github.com/{repo_full_name}",
            "files_exported": copied,
            "commit_msg": msg,
            "log": "\n".join(log_lines),
        }
