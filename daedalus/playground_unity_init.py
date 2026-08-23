"""Unity project init — scaffold, template attach, integrations doc."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from . import playground as pg
from .playground_integration_catalog import (
    UNITY_TEMPLATES,
    generate_integrations_doc,
    integrations_for_preset,
)
from .playground_unity_scaffold import write_unity_scaffold

log = logging.getLogger(__name__)

_INTEGRATIONS_PATH = "studio/integrations/INTEGRATIONS.md"
_IOS_BUILD_PATH = "studio/ios/BUILD.md"
_UNITY_SUBMENU_PATH = "studio/unity/SUBMENU.md"


def _ios_build_doc() -> str:
    return """# iOS Build Pipeline (Daedalus Studio)

## Paths

| Tool | Repo | When |
|------|------|------|
| Unity iOS build | Local Xcode | After Unity Android/PC slice works |
| PakePlus-iOS | github.com/Sjj1024/PakePlus-iOS | Wrap web/Expo export as iOS app |
| ios-app-signer | github.com/DanTheMan827/ios-app-signer | Re-sign IPA (macOS) |
| LiveContainer | github.com/LiveContainer/LiveContainer | Sideload test builds |
| Firebase iOS SDK | github.com/firebase/firebase-ios-sdk | Auth, analytics, crashlytics |

## Unity → iOS
1. Unity Hub → iOS Build Support module
2. Player Settings → Bundle ID, signing team (Xcode)
3. Build → open Xcode project → Archive → TestFlight

## Web prototype → iOS (hybrid)
1. Daedalus `expo` or `vite` vertical slice
2. PakePlus-iOS wrap
3. Optional: Framework7 UI in vite prototype first

## Firebase
Add `GoogleService-Info.plist` locally — never commit secrets.
See `studio/integrations/firebase.md` (generated per project).
"""


def _unity_submenu_doc(template_id: str) -> str:
    tpl = UNITY_TEMPLATES.get(template_id, UNITY_TEMPLATES["daedalus_minimal"])
    return f"""# Unity Submenu (Daedalus)

Active template: **{tpl.get('label', template_id)}**

## Actions (Playground UI)
1. **Init Unity** — scaffold Packages/, Assets/_Project/, studio/
2. **Attach template** — shallow clone external framework
3. **Studio build** — C# codegen with milestones
4. **Export bridge** — manifest + stubs for import
5. **Integrations doc** — Firebase, iOS tools, Framework7

## Open project
Unity Hub → Open → this folder

## Misnamed repos (ignore for games)
- ThrowTheSwitch/Unity = Catch2 C++ tests, NOT Unity3D
- unitycontainer/unity = PHP DI container
"""


async def _shallow_clone(repo_url: str, dest: Path) -> None:
    import os
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    url = repo_url
    if token and "github.com" in repo_url:
        url = repo_url.replace("https://github.com/", f"https://x-access-token:{token}@github.com/")
    proc = await asyncio.create_subprocess_exec(
        "git", "clone", "--depth", "1", url, str(dest),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, err = await asyncio.wait_for(proc.communicate(), timeout=180)
    if proc.returncode != 0:
        raise RuntimeError((err or b"").decode("utf-8", errors="replace")[:400])


async def init_unity_project(
    slug: str,
    *,
    template_id: str = "daedalus_minimal",
    studio_preset: str = "custom",
    name: str = "",
) -> dict[str, Any]:
    """Scaffold Unity project + studio integration docs."""
    proj = await pg.get_project(slug)
    if not proj:
        raise ValueError("project not found")

    pdir = pg.project_dir(slug)
    pname = name or proj.get("name", slug)
    desc = proj.get("description", "")

    written = write_unity_scaffold(pdir, pname, slug, desc)

    tpl = UNITY_TEMPLATES.get(template_id, UNITY_TEMPLATES["daedalus_minimal"])
    integration_ids = list(tpl.get("integrations") or integrations_for_preset(studio_preset))

    from .playground_firebase_config import configure_firebase, normalize_firebase_config

    fb_cfg = normalize_firebase_config({}, slug=slug, name=pname)
    if "firebase_ios_sdk" in integration_ids:
        await configure_firebase(slug, fb_cfg)

    docs = {
        _INTEGRATIONS_PATH: generate_integrations_doc(integration_ids),
        _IOS_BUILD_PATH: _ios_build_doc(),
        _UNITY_SUBMENU_PATH: _unity_submenu_doc(template_id),
    }
    for path, content in docs.items():
        target = pdir / path
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            written.append(path)

    cloned = ""
    repo = tpl.get("repo")
    clone_into = tpl.get("clone_into")
    if repo and clone_into and template_id != "daedalus_minimal":
        dest = pdir / clone_into
        if not dest.exists():
            try:
                await _shallow_clone(repo, dest)
                cloned = clone_into
                written.append(clone_into + "/")
            except Exception as exc:
                log.warning("unity template clone failed: %s", exc)
                cloned = f"FAILED: {exc}"

    from .playground_unity_build import generate_unity_build_scripts

    try:
        build_result = await generate_unity_build_scripts(slug, name=pname)
        written.extend(build_result.get("written") or [])
    except Exception as exc:
        log.warning("unity build scripts: %s", exc)

    await pg.update_project(slug, kind="unity", entry="studio/preview/index.html")

    return {
        "ok": True,
        "written": written,
        "template_id": template_id,
        "cloned": cloned,
        "integrations": integration_ids,
        "preview": "studio/preview/index.html",
    }


async def attach_unity_template(slug: str, template_id: str) -> dict[str, Any]:
    tpl = UNITY_TEMPLATES.get(template_id)
    if not tpl or not tpl.get("repo"):
        raise ValueError(f"unknown or non-cloneable template: {template_id}")
    pdir = pg.project_dir(slug)
    dest = pdir / tpl["clone_into"]
    if dest.exists():
        return {"ok": True, "cloned": str(tpl["clone_into"]), "status": "already_present"}
    await _shallow_clone(tpl["repo"], dest)
    doc_path = _INTEGRATIONS_PATH
    ids = list(tpl.get("integrations") or [])
    pg.write_file(slug, doc_path, generate_integrations_doc(ids))
    return {"ok": True, "cloned": tpl["clone_into"], "integrations": ids}
