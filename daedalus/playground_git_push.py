"""Unified Git remote push / clone / review — GitLab (preferred) or GitHub."""
from __future__ import annotations

from typing import Any


def detect_forge(url: str) -> str:
    """Return ``gitlab``, ``github``, or ``auto`` from a remote URL."""
    raw = (url or "").strip().lower()
    if not raw:
        return "auto"
    if "github.com" in raw:
        return "github"
    if "gitlab" in raw:
        return "gitlab"
    from .playground_gitlab import gitlab_host
    host = (gitlab_host() or "").strip().lower()
    if host and host not in ("github.com",) and host in raw:
        return "gitlab"
    return "auto"


async def push_project_remote(
    slug: str,
    *,
    provider: str = "auto",
    repo_full_name: str = "",
    create_if_missing: bool = True,
    private: bool = True,
    branch: str = "main",
    commit_msg: str = "",
    namespace: str = "",
    force: bool = False,
) -> dict[str, Any]:
    """Push playground project to GitLab and/or GitHub (force only when confirmed).

    provider:
      auto   — GitLab if GITLAB_TOKEN, else GitHub if GITHUB_TOKEN
      gitlab — GitLab only
      github — GitHub only
    """
    prov = (provider or "auto").strip().lower()
    if prov in ("auto", "gitlab"):
        from . import playground_gitlab as gl
        if gl.is_configured():
            return await gl.push_project(
                slug,
                repo_full_name=repo_full_name,
                create_if_missing=create_if_missing,
                private=private,
                branch=branch,
                commit_msg=commit_msg,
                namespace=namespace,
                force=force,
            )
        if prov == "gitlab":
            raise RuntimeError(gl.gitlab_error_hint("GITLAB_TOKEN not configured"))

    if prov in ("auto", "github"):
        from . import playground_github as gh
        if gh.is_configured():
            res = await gh.push_project(
                slug,
                repo_full_name=repo_full_name,
                create_if_missing=create_if_missing,
                private=private,
                branch=branch,
                commit_msg=commit_msg,
                force=force,
            )
            res["provider"] = "github"
            return res
        if prov == "github":
            raise RuntimeError(gh.github_error_hint("GITHUB_TOKEN not configured"))

    raise RuntimeError(
        "Няма конфигуриран Git remote. Задай GITLAB_TOKEN или GITHUB_TOKEN "
        "в Studio → Свързаност."
    )


def remote_status() -> dict[str, Any]:
    from . import playground_gitlab as gl
    from . import playground_github as gh
    return {
        "ok": True,
        "gitlab": {"configured": gl.is_configured(), "host": gl.gitlab_host()},
        "github": {"configured": gh.is_configured()},
        "any_configured": gl.is_configured() or gh.is_configured(),
    }


async def clone_project_remote(
    url: str,
    *,
    name: str = "",
    slug: str = "",
    branch: str = "",
    ref: str = "",
    source: str = "cloned",
    keep_git: bool | None = None,
    auto_onboard: bool = False,
) -> dict[str, Any]:
    """Clone GitHub or GitLab into a playground project."""
    forge = detect_forge(url)
    kwargs = dict(
        name=name, slug=slug, branch=branch, ref=ref, source=source,
        keep_git=keep_git, auto_onboard=auto_onboard,
    )
    if forge == "gitlab":
        from . import playground_gitlab as gl
        return await gl.clone_repo(url, **kwargs)
    from . import playground_github as gh
    return await gh.clone_repo(url, **kwargs)


def forge_error_hint(exc: Exception | str, *, provider: str = "auto") -> str:
    prov = (provider or "auto").strip().lower()
    if prov == "gitlab" or (prov == "auto" and "gitlab" in str(exc).lower()):
        from . import playground_gitlab as gl
        return gl.gitlab_error_hint(exc)
    from . import playground_github as gh
    return gh.github_error_hint(exc)


async def create_upstream_review(
    slug: str,
    *,
    title: str = "",
    body: str = "",
    base: str = "",
    branch_name: str = "",
    commit_msg: str = "",
) -> dict[str, Any]:
    """Push a feature branch and open a GitHub PR or GitLab MR against upstream."""
    from . import playground as pg
    from . import playground_github as gh
    from . import playground_gitlab as gl

    proj = await pg.get_project(slug)
    if not proj:
        raise ValueError(f"project '{slug}' not found")
    upstream = (
        (proj.get("upstream_url") or "")
        or (proj.get("gitlab_repo") or "")
        or (proj.get("github_repo") or "")
    )
    forge = detect_forge(upstream)
    if forge == "auto":
        gh_parsed = gh.parse_github_repo(upstream)
        gl_parsed = gl.parse_gitlab_repo(upstream)
        looks_gitlab = "gitlab" in (upstream or "").lower() or "://" in upstream or "@" in upstream
        if gl_parsed and (not gh_parsed or looks_gitlab):
            forge = "gitlab"
        elif gh_parsed:
            forge = "github"
        elif gl.is_configured() and not gh.is_configured():
            forge = "gitlab"
        else:
            forge = "github"
    kwargs = dict(
        title=title, body=body, base=base,
        branch_name=branch_name, commit_msg=commit_msg,
    )
    if forge == "gitlab":
        return await gl.create_upstream_mr(slug, **kwargs)
    return await gh.create_upstream_pr(slug, **kwargs)
