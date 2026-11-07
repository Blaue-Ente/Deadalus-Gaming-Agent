"""Git / object-storage connector status and non-secret preferences.

Secrets are set in Studio → Свързаност ($DAEDALUS_DATA/secrets.env) or process
env. This module reads env, tests connections, and stores UI prefs.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

log = logging.getLogger(__name__)

_DB_KEY = "adn:daedalus_connectors_config"


def github_token() -> str:
    return (
        os.environ.get("GITHUB_TOKEN", "").strip()
        or os.environ.get("GITHUB_INTEGRATION_TOKEN", "").strip()
    )


def github_token_source() -> str:
    if os.environ.get("GITHUB_TOKEN", "").strip():
        return "GITHUB_TOKEN"
    if os.environ.get("GITHUB_INTEGRATION_TOKEN", "").strip():
        return "GITHUB_INTEGRATION_TOKEN"
    return "none"


def is_github_configured() -> bool:
    return bool(github_token())


def probe_github_can_create_repo(tok: str) -> tuple[bool, str]:
    """Detect write/create-repo permission (works for fine-grained PATs without scope header)."""
    try:
        import httpx

        r = httpx.post(
            "https://api.github.com/user/repos",
            headers={
                "Authorization": f"Bearer {tok}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={"name": ""},
            timeout=10.0,
        )
        if r.status_code == 403:
            msg = (r.json() or {}).get("message", "403 Forbidden")
            return False, msg
        if r.status_code == 401:
            return False, "invalid or expired token (401)"
        if r.status_code == 422:
            return True, "write access confirmed"
        if r.status_code < 300:
            return True, "write access confirmed"
        return False, f"unexpected HTTP {r.status_code}"
    except Exception as exc:
        return False, str(exc)[:200]


_GITHUB_PUSH_SETUP_HINT = (
    "Push изисква PAT с права за запис (read-only integration не стига).\n"
    "1) github.com → Settings → Developer settings → Personal access tokens\n"
    "2) Classic token → отметни scope: repo (или fine-grained: Contents + Pull requests + Administration Read/Write)\n"
    "3) Studio → Свързаност → GitHub PAT\n"
    "4) Playground → ⬆ GitHub (1 клик)"
)


def _prefs_path():
    from .paths import data_root
    return data_root() / "connectors.json"


def _read_db() -> dict:
    try:
        from .kv import replit_required as _replit_required
        db = _replit_required()
        raw = db.get(_DB_KEY)
        if raw:
            return json.loads(raw) if isinstance(raw, str) else dict(raw)
    except Exception:
        pass
    try:
        path = _prefs_path()
        if path.is_file():
            return json.loads(path.read_text("utf-8"))
    except Exception:
        pass
    return {}


def _write_db(data: dict) -> None:
    try:
        from .kv import replit_required as _replit_required
        db = _replit_required()
        db[_DB_KEY] = json.dumps(data)
        return
    except Exception:
        pass
    try:
        path = _prefs_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as exc:
        log.warning("connectors_config db write failed: %s", exc)


def get_prefs() -> dict[str, Any]:
    stored = _read_db()
    return {
        "preferred_git_provider": stored.get("preferred_git_provider", "auto"),
        "default_github_branch": stored.get("default_github_branch", "main"),
        "auto_backup_on_revive": stored.get("auto_backup_on_revive", True),
        "auto_backup_on_publish": stored.get("auto_backup_on_publish", True),
    }


def set_prefs(**fields: Any) -> dict[str, Any]:
    allowed = {
        "preferred_git_provider", "default_github_branch",
        "auto_backup_on_revive", "auto_backup_on_publish",
    }
    stored = _read_db()
    for k, v in fields.items():
        if k in allowed:
            stored[k] = v
    _write_db(stored)
    return get_prefs()


async def test_github_connection() -> dict[str, Any]:
    """Verify token works against GitHub API."""
    tok = github_token()
    if not tok:
        return {
            "ok": False,
            "code": "not_configured",
            "configured": False,
            "source": "none",
            "error": "GITHUB_TOKEN не е зададен",
            "hint": (
                "Отвори Studio → Свързаност и сложи GITHUB_TOKEN "
                "(classic PAT с scope repo, или fine-grained Contents + Pull requests)."
            ),
        }
    try:
        import httpx
        r = httpx.get(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {tok}",
                "Accept": "application/vnd.github+json",
            },
            timeout=10.0,
        )
        if r.status_code == 401:
            return {
                "ok": False,
                "code": "invalid_token",
                "configured": True,
                "source": github_token_source(),
                "error": "Токенът е невалиден или изтекъл (401)",
                "hint": "Обнови GITHUB_TOKEN в Studio → Свързаност.",
            }
        if r.status_code == 403:
            return {
                "ok": False,
                "code": "forbidden",
                "configured": True,
                "source": github_token_source(),
                "error": "Достъпът е отказан (403) — липсва scope repo",
                "can_push": False,
                "hint": _GITHUB_PUSH_SETUP_HINT,
            }
        if r.status_code != 200:
            return {
                "ok": False,
                "code": "api_error",
                "configured": True,
                "source": github_token_source(),
                "error": f"GitHub API {r.status_code}",
            }
        user = r.json()
        scopes = r.headers.get("x-oauth-scopes", "")
        if scopes:
            can_push = "repo" in scopes or "public_repo" in scopes
            push_detail = scopes or "classic scopes"
        else:
            can_push, push_detail = probe_github_can_create_repo(tok)
        return {
            "ok": True,
            "code": "ok",
            "configured": True,
            "source": github_token_source(),
            "login": user.get("login"),
            "name": user.get("name") or user.get("login"),
            "public_repos": user.get("public_repos", 0),
            "scopes": scopes,
            "can_push": can_push,
            "push_detail": push_detail,
            "html_url": user.get("html_url"),
            "hint": None if can_push else _GITHUB_PUSH_SETUP_HINT,
        }
    except Exception as exc:
        return {
            "ok": False,
            "code": "network",
            "configured": True,
            "source": github_token_source(),
            "error": str(exc)[:300],
        }


async def test_gitlab_connection() -> dict[str, Any]:
    from . import playground_gitlab as gl
    if not gl.is_configured():
        return {
            "ok": False,
            "code": "not_configured",
            "configured": False,
            "error": "GITLAB_TOKEN не е зададен",
            "hint": "Добави GITLAB_TOKEN в Studio → Свързаност (scope api, write_repository).",
        }
    try:
        import httpx
        r = httpx.get(
            f"https://{gl.gitlab_host()}/api/v4/user",
            headers={"PRIVATE-TOKEN": gl._token()},
            timeout=10.0,
        )
        if r.status_code in (401, 403):
            return {"ok": False, "code": "invalid_token" if r.status_code == 401 else "forbidden",
                    "configured": True, "error": f"GitLab API {r.status_code}"}
        if r.status_code != 200:
            return {"ok": False, "code": "api_error", "configured": True, "error": f"GitLab API {r.status_code}"}
        u = r.json()
        return {
            "ok": True,
            "code": "ok",
            "configured": True,
            "username": u.get("username"),
            "host": gl.gitlab_host(),
            "namespace": gl.default_namespace(),
        }
    except Exception as exc:
        return {"ok": False, "code": "network", "configured": True, "error": str(exc)[:300]}


def storage_status() -> dict[str, Any]:
    try:
        from .host import storage as _storage
        configured = _storage().is_configured()
    except Exception:
        configured = False
    bucket = os.environ.get("DEFAULT_OBJECT_STORAGE_BUCKET_ID", "")
    return {
        "configured": configured,
        "bucket_set": bool(bucket),
        "hint": (
            "Object Storage е активен — published apps и workspace backups оцеляват при redeploy."
            if configured
            else "Object Storage не е конфигуриран — публикувай в /apps/ или задай DEFAULT_OBJECT_STORAGE_BUCKET_ID."
        ),
    }


async def full_status() -> dict[str, Any]:
    from . import playground_github as gh
    from . import playground_gitlab as gl

    gh_test = await test_github_connection()
    gl_test = await test_gitlab_connection()
    storage = storage_status()
    prefs = get_prefs()

    return {
        "github": {
            "configured": gh.is_configured(),
            "source": github_token_source(),
            **{k: v for k, v in gh_test.items() if k != "configured"},
        },
        "gitlab": {
            "configured": gl.is_configured(),
            "host": gl.gitlab_host(),
            "namespace": gl.default_namespace(),
            **{k: v for k, v in gl_test.items() if k not in ("configured",)},
        },
        "git": {
            "any_configured": gh.is_configured() or gl.is_configured(),
            "preferred_provider": prefs.get("preferred_git_provider", "auto"),
        },
        "storage": storage,
        "prefs": prefs,
        "setup": {
            "github": [
                "Studio → Свързаност → GitHub PAT (classic, scope repo)",
                "Fine-grained: Contents + Pull requests + Administration Read/Write",
                "За private repos: задължително repo scope",
                "Ключът се пише в $DAEDALUS_DATA/secrets.env, не в git",
            ],
            "durability": [
                "Публикувай в /apps/ — най-надеждно (zip в object storage)",
                "Непубликувани проекти: backup при Autopilot/Chat/Revive",
                "Ръчен backup: Playground → ⋮ → Backup workspace",
                "При redeploy: published apps се възстановяват автоматично",
            ],
        },
    }
