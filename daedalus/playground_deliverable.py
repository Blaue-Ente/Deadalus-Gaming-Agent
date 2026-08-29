"""Deliverable gate — job is not 'done' until a real preview/publish/mobile URL exists."""
from __future__ import annotations

import urllib.parse
from typing import Any

from . import playground as pg
from . import playground_runner as runner
from .playground_runtime import verify_unity_scaffold


def admin_preview_url(slug: str) -> str:
    return f"/admin/daedalus/playground/preview/{slug}/"


def apps_publish_url(slug: str) -> str:
    return f"/apps/{slug}/"


def _qr_for(url: str) -> str:
    if not url:
        return ""
    return (
        "https://api.qrserver.com/v1/create-qr-code/?size=220x220&data="
        + urllib.parse.quote(url, safe="")
    )


async def assess_deliverable(slug: str, kind: str, *, verified: bool) -> dict[str, Any]:
    """Return deliverable status + URLs for autopilot ``done`` payload."""
    preview_url = admin_preview_url(slug)
    publish_url = ""
    expo_url = runner.extract_expo_url(slug) or ""
    qr_url = _qr_for(expo_url)

    paths = [t["path"] for t in pg.read_tree(slug)]
    has_entry = any(p == "index.html" or p.endswith("/index.html") for p in paths)

    deliverable = False
    reason = ""

    if kind == "static":
        deliverable = has_entry
        reason = "" if deliverable else "липсва index.html"
    elif kind == "expo":
        # Web smoke OK and/or tunnel exp:// URL for Expo Go
        deliverable = bool(verified) or bool(expo_url)
        if not deliverable:
            reason = "expo не стартира (web) и няма exp:// URL в логовете"
    elif kind in ("vite", "vite-react"):
        deliverable = bool(verified)
        if not deliverable:
            reason = "build/smoke test не мина"
    elif kind in ("fastapi", "streamlit", "node"):
        deliverable = bool(verified)
        reason = "" if deliverable else "runtime smoke test не мина"
    elif kind == "godot":
        from .playground_godot_scaffold import verify_godot_scaffold
        proj = await pg.get_project(slug)
        gh_url = (proj or {}).get("github_repo") or ""
        gl_url = (proj or {}).get("gitlab_repo") or ""
        remote_url = gl_url or gh_url
        gv = verify_godot_scaffold(slug)
        deliverable = bool(gv.get("ok")) or bool(remote_url)
        if not deliverable:
            reason = gv.get("error") or "Godot scaffold непълен — липсват project.godot/.gd/.tscn"
        elif remote_url:
            preview_url = remote_url
    elif kind == "unity":
        proj = await pg.get_project(slug)
        gh_url = (proj or {}).get("github_repo") or ""
        gl_url = (proj or {}).get("gitlab_repo") or ""
        remote_url = gl_url or gh_url
        uv = verify_unity_scaffold(slug)
        deliverable = bool(uv.get("ok")) or bool(remote_url)
        if not deliverable:
            reason = uv.get("error") or "Unity scaffold непълен — липсват C#/manifest"
        elif remote_url:
            preview_url = remote_url
    else:
        deliverable = bool(verified) or (kind == "static" and has_entry)
        reason = "" if deliverable else "не е верифициран"

    # Check if already published to /apps/
    try:
        from . import playground_publish as pub

        if pub.app_dir(slug).is_dir() and any(pub.app_dir(slug).iterdir()):
            publish_url = apps_publish_url(slug)
            deliverable = True
            reason = ""
    except Exception:
        pass

    proj = await pg.get_project(slug) or {}
    return {
        "deliverable": deliverable,
        "deliverable_reason": reason,
        "preview_url": preview_url,
        "publish_url": publish_url,
        "expo_url": expo_url,
        "qr_url": qr_url,
        "github_url": proj.get("github_repo") or "",
        "gitlab_url": proj.get("gitlab_repo") or "",
        "remote_url": proj.get("gitlab_repo") or proj.get("github_repo") or "",
        "local_only": kind in ("unity", "godot"),
    }
