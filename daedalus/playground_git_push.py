"""Unified Git remote push — GitLab (preferred) or GitHub."""
from __future__ import annotations

from typing import Any


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
) -> dict[str, Any]:
    """Push playground project to GitLab and/or GitHub.

    provider:
      auto   — GitLab if GITLAB_TOKEN, else GitHub if GITHUB_TOKEN
      gitlab — GitLab only
      github — GitHub only
    """
    prov = (provider or "auto").strip().lower()
    errors: list[str] = []

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
            )
            res["provider"] = "github"
            return res
        if prov == "github":
            raise RuntimeError(gh.github_error_hint("GITHUB_TOKEN not configured"))

    raise RuntimeError(
        "Няма конфигуриран Git remote. Задай GITLAB_TOKEN или GITHUB_TOKEN в Secrets."
    )


def remote_status() -> dict[str, Any]:
    from . import playground_gitlab as gl
    from . import playground_github as gh
    return {
        "gitlab": {"configured": gl.is_configured(), "host": gl.gitlab_host()},
        "github": {"configured": gh.is_configured()},
        "any_configured": gl.is_configured() or gh.is_configured(),
    }
