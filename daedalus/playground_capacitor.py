"""Capacitor ship layer — same web game → iOS + Android.

Not a new Playground kind. Vite / vite-react / static keep their canvas loop.
Capacitor wraps ``dist/`` (or ``hybrid/dist``) in a native WebView.

Ionic UI is optional for **apps** (lists, settings, forms). It is not the
game loop — Phaser / Pixi / Three stay on canvas.

Honesty: Daedalus writes config + a local sync script. It does not run
Xcode, Android Studio, or ``npx cap add`` in the cloud. A Capacitor wrap
is not store-ready.

Do not wrap the Daedalus Studio process (FastAPI + SQLite + shell) in
Capacitor. Studio on Windows already uses WebView2.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from . import playground as pg

CAP_README = "studio/capacitor/README.md"
CAP_PIPELINE = "studio/capacitor/CAPACITOR_PIPELINE.md"
CAP_PLUGINS = "studio/capacitor/plugins.json"
CAP_EXPORT = "studio/capacitor/export-manifest.json"
CAP_SYNC = "scripts/capacitor-sync.sh"
CAP_BRIDGE = "src/native/daedalus-cap.js"
CAP_CONFIG_NAME = "capacitor.config.json"

WEB_SHIP_KINDS = frozenset({"static", "vite", "vite-react"})
ENGINE_KINDS = frozenset({"unity", "godot"})
EXPO_KIND = "expo"

CAPACITOR_CORE = "^7.4.0"
RECOMMENDED_PLUGINS: tuple[dict[str, str], ...] = (
    {"id": "@capacitor/app", "why": "App state, back button, URL open"},
    {"id": "@capacitor/haptics", "why": "Impact feedback on device; no-op on web"},
    {"id": "@capacitor/status-bar", "why": "Immersive overlay for games"},
    {"id": "@capacitor/splash-screen", "why": "Hide splash after first frame"},
    {"id": "@capacitor/keyboard", "why": "Resize / hide on play screens"},
    {"id": "@capacitor/preferences", "why": "Durable settings (better than localStorage on iOS)"},
    {"id": "@capacitor/share", "why": "Native share sheet"},
    {"id": "@capacitor/filesystem", "why": "Optional replay / save export"},
)

_VITE_BASE_HINT = re.compile(r"\bbase\s*:", re.I)


def ship_advice(kind: str) -> dict[str, Any]:
    """Which native path a Playground kind should take. Capacitor is not Expo."""
    k = (kind or "").strip()
    if k in WEB_SHIP_KINDS:
        return {
            "path": "capacitor",
            "rewrite": False,
            "platforms": ["web", "pwa", "android", "ios"],
            "ionic_ui": "optional-apps-only",
            "note": "Same canvas / Three.js. Wrap dist/. Do not rewrite to Expo or Ionic pages.",
        }
    if k == EXPO_KIND:
        return {
            "path": "eas",
            "rewrite": False,
            "platforms": ["ios", "android", "web"],
            "ionic_ui": "no",
            "note": "Already React Native. Do not add Capacitor on top.",
        }
    if k in ENGINE_KINDS:
        return {
            "path": "editor-export",
            "rewrite": False,
            "platforms": ["desktop", "android", "ios"],
            "ionic_ui": "no",
            "note": "Export from Godot / Unity Hub. Capacitor does not wrap .tscn or C#.",
        }
    return {
        "path": "none",
        "rewrite": False,
        "platforms": [],
        "ionic_ui": "no",
        "note": "This kind is a process (API / Streamlit / node), not a static WebView.",
    }


def cap_layout(root: Path | str) -> dict[str, str]:
    """Where Capacitor CLI should live relative to the project root."""
    root = Path(root)
    if (root / "hybrid" / "package.json").is_file():
        return {
            "workdir": "hybrid",
            "web_dir": "dist",
            "config_rel": "hybrid/capacitor.config.json",
            "package_rel": "hybrid/package.json",
            "bridge_rel": "hybrid/src/native/daedalus-cap.js",
        }
    if (root / "package.json").is_file() or (root / "vite.config.js").is_file() or (
        root / "vite.config.ts"
    ).is_file():
        return {
            "workdir": ".",
            "web_dir": "dist",
            "config_rel": CAP_CONFIG_NAME,
            "package_rel": "package.json",
            "bridge_rel": CAP_BRIDGE,
        }
    return {
        "workdir": ".",
        "web_dir": "www",
        "config_rel": CAP_CONFIG_NAME,
        "package_rel": "package.json",
        "bridge_rel": "www/native/daedalus-cap.js",
    }


def capacitor_config(*, app_id: str, app_name: str, web_dir: str) -> dict[str, Any]:
    return {
        "appId": app_id,
        "appName": app_name,
        "webDir": web_dir,
        "android": {"allowMixedContent": True},
        "plugins": {
            "SplashScreen": {"launchAutoHide": True, "launchShowDuration": 0},
            "StatusBar": {"style": "DARK"},
        },
    }


def plugins_manifest(*, with_ionic: bool = False) -> dict[str, Any]:
    return {
        "core": [
            "@capacitor/core",
            "@capacitor/cli",
            "@capacitor/android",
            "@capacitor/ios",
        ],
        "recommended": [dict(p) for p in RECOMMENDED_PLUGINS],
        "ionic_ui": {
            "optional": True,
            "enabled": bool(with_ionic),
            "when": "Apps with lists, forms, settings — never the 60fps game loop",
            "packages": ["@ionic/core"] if with_ionic else [],
            "docs": "https://ionicframework.com/",
        },
        "upstream": {
            "capacitor": "https://github.com/ionic-team/capacitor",
            "plugins": "https://github.com/ionic-team/capacitor-plugins",
            "ionic": "https://ionicframework.com/",
        },
        "notes": (
            "Install plugins locally with npm. Daedalus does not vendor Capacitor. "
            "Ionic UI is not required to ship a Vite/Phaser/Three game."
        ),
    }


def merge_package_json(
    existing: dict[str, Any] | None,
    *,
    slug: str,
    with_ionic: bool = False,
    at_root: bool = True,
) -> dict[str, Any]:
    data = dict(existing) if isinstance(existing, dict) else {}
    data.pop("_cap_root", None)
    data.setdefault("name", slug or "daedalus-app")
    data.setdefault("private", True)
    data.setdefault("version", "0.1.0")
    scripts = data.setdefault("scripts", {})
    if not isinstance(scripts, dict):
        scripts = {}
        data["scripts"] = scripts
    scripts["cap:sync"] = (
        "bash scripts/capacitor-sync.sh" if at_root else "bash ../scripts/capacitor-sync.sh"
    )
    scripts.setdefault("cap:open:android", "npx cap open android")
    scripts.setdefault("cap:open:ios", "npx cap open ios")
    dev = data.setdefault("devDependencies", {})
    if not isinstance(dev, dict):
        dev = {}
        data["devDependencies"] = dev
    deps = data.setdefault("dependencies", {})
    if not isinstance(deps, dict):
        deps = {}
        data["dependencies"] = deps
    dev.setdefault("@capacitor/cli", CAPACITOR_CORE)
    deps.setdefault("@capacitor/core", CAPACITOR_CORE)
    deps.setdefault("@capacitor/android", CAPACITOR_CORE)
    deps.setdefault("@capacitor/ios", CAPACITOR_CORE)
    for plug in RECOMMENDED_PLUGINS:
        deps.setdefault(plug["id"], CAPACITOR_CORE)
    if with_ionic:
        deps.setdefault("@ionic/core", "^8.4.0")
    return data


def _sync_script(workdir: str, web_dir: str) -> str:
    # workdir is relative to project root ('.' or 'hybrid')
    cd = 'cd "$ROOT"' if workdir in ("", ".") else f'cd "$ROOT/{workdir}"'
    www_copy = ""
    if web_dir == "www":
        www_copy = """
if [[ ! -d www ]]; then mkdir -p www; fi
# Static projects: copy playable files into webDir, never node_modules.
for f in index.html style.css game.js; do
  [[ -f "$ROOT/$f" ]] && cp "$ROOT/$f" www/
done
[[ -d "$ROOT/assets" ]] && cp -R "$ROOT/assets" www/ || true
[[ -d "$ROOT/src" ]] && cp -R "$ROOT/src" www/ || true
"""
    return f"""#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
{cd}
{www_copy}
if [[ -f package.json ]]; then
  if command -v npm >/dev/null 2>&1; then
    npm install
    if npm run --silent build >/dev/null 2>&1; then
      npm run build
    elif [[ -f package.json ]] && grep -q '"build"' package.json; then
      npm run build
    fi
  else
    echo "npm not found — install Node 20+ locally, then re-run."
    exit 1
  fi
fi

WEB_DIR="{web_dir}"
if [[ ! -d "$WEB_DIR" ]] || [[ -z "$(ls -A "$WEB_DIR" 2>/dev/null || true)" ]]; then
  echo "webDir $WEB_DIR is empty. Build the web game first (npm run build), then re-run."
  echo "Daedalus does not produce a store binary in the cloud."
  exit 1
fi

if [[ ! -d android && ! -d ios ]]; then
  echo "First local step (not run by Daedalus):"
  echo "  npx cap add android     # needs Android SDK / Studio"
  echo "  npx cap add ios         # macOS + Xcode"
  echo "Then: bash scripts/capacitor-sync.sh   # npx cap sync"
  exit 0
fi

npx cap sync
echo "Synced $WEB_DIR → native projects. Open with: npm run cap:open:android | cap:open:ios"
echo "Not store-ready until you sign, test on a device, and pass store review."
"""


def _bridge_js() -> str:
    return """/** Optional Capacitor helpers. Safe on web: every call no-ops without native plugins. */

export async function isNative() {
  try {
    const { Capacitor } = await import('@capacitor/core');
    return Capacitor.isNativePlatform();
  } catch {
    return false;
  }
}

export async function hapticLight() {
  try {
    const { Haptics, ImpactStyle } = await import('@capacitor/haptics');
    await Haptics.impact({ style: ImpactStyle.Light });
  } catch {
    /* browser / missing plugin */
  }
}

export async function hideSplash() {
  try {
    const { SplashScreen } = await import('@capacitor/splash-screen');
    await SplashScreen.hide();
  } catch {
    /* browser */
  }
}
"""


def _readme(name: str, slug: str, layout: dict[str, str], *, with_ionic: bool) -> str:
    ionic_line = (
        "Ionic UI (`@ionic/core`) is **on** for this project — use it for menus/settings, "
        "not for sprites or the rAF loop.\n"
        if with_ionic
        else "Ionic UI is **off**. Add it later only for app chrome (lists/forms), never the game loop.\n"
    )
    return f"""# Capacitor ship — {name}

Same web game, three surfaces: browser / PWA, Android, iOS.

{ionic_line}
## Why Capacitor (not a new kind)

- Playground kind stays `{layout.get("kind", "vite")}`. Phaser / Pixi / Three keep the canvas.
- [Capacitor](https://github.com/ionic-team/capacitor) is a native WebView + plugins.
- [capacitor-plugins](https://github.com/ionic-team/capacitor-plugins) — haptics, status bar, filesystem, share.
- Expo is a **rewrite** to React Native / Skia. Use Expo when you want RN, not when you already have a web game.
- Framework7 + PakePlus-iOS remains an iOS-only fallback. Capacitor covers **Android and iOS**.

## What Daedalus does

Writes `{layout["config_rel"]}`, this folder, a sync script, and an optional JS bridge.
Does **not** run Xcode, Android Studio, or `npx cap add` in the cloud.

## Local pipeline

```bash
# from the playground project
bash scripts/capacitor-sync.sh
# first time, on your machine:
cd {layout["workdir"] if layout["workdir"] != "." else "."}
npx cap add android
npx cap add ios          # macOS
bash ../../scripts/capacitor-sync.sh   # or scripts/ from repo root
npm run cap:open:android
```

Vite builds must use `base: './'` so assets load inside the WebView.

## Anti-patterns

- Do not rebuild the game in Ionic pages / Angular.
- Do not wrap Daedalus Studio (the Python agent) in Capacitor.
- Do not claim store-ready because `capacitor.config.json` exists.
- Do not add Capacitor on an Expo, Unity, or Godot slug.

Project slug: `{slug}`
"""


def _pipeline_doc(name: str, bundle_id: str, layout: dict[str, str]) -> str:
    return f"""# Capacitor pipeline — {name}

Bundle ID: `{bundle_id}`
webDir: `{layout["web_dir"]}` (relative to `{layout["workdir"]}`)

## Prerequisites (owner machine)

- Node 20+
- Android: Android Studio / SDK, `ANDROID_HOME`
- iOS: macOS, Xcode, CocoaPods
- Apple / Google developer accounts for store upload

## Steps

1. Web game plays in the browser (Playground preview / `npm run dev`).
2. `npm run build` produces `{layout["web_dir"]}`.
3. `npx cap add android` and/or `npx cap add ios` **once**.
4. `bash scripts/capacitor-sync.sh` → `npx cap sync`.
5. Open the native IDE, run on a device, then sign.

## Plugins

See `studio/capacitor/plugins.json`. Install with npm; do not vendor copies into git as minified blobs.

## PakePlus

PakePlus-iOS is still valid as a thin iOS wrap if you are not ready for Capacitor.
Prefer Capacitor when you need Android + official plugins.
"""


def vite_needs_relative_base(vite_config: str) -> bool:
    if not (vite_config or "").strip():
        return True
    return _VITE_BASE_HINT.search(vite_config) is None


def capacitor_scaffold_files(
    name: str,
    slug: str,
    *,
    bundle_id: str = "",
    layout: dict[str, str],
    with_ionic: bool = False,
    kind: str = "vite",
) -> dict[str, str]:
    bundle = bundle_id or f"com.daedalus.{slug}"
    cfg = capacitor_config(app_id=bundle, app_name=name or slug, web_dir=layout["web_dir"])
    layout_doc = dict(layout)
    layout_doc["kind"] = kind
    files = {
        layout["config_rel"]: json.dumps(cfg, indent=2) + "\n",
        CAP_README: _readme(name, slug, layout_doc, with_ionic=with_ionic),
        CAP_PIPELINE: _pipeline_doc(name, bundle, layout),
        CAP_PLUGINS: json.dumps(plugins_manifest(with_ionic=with_ionic), indent=2) + "\n",
        CAP_SYNC: _sync_script(layout["workdir"], layout["web_dir"]),
        layout["bridge_rel"]: _bridge_js(),
    }
    return files


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _merge_existing_package(root: Path, layout: dict[str, str], slug: str, with_ionic: bool) -> str:
    pkg_path = root / layout["package_rel"]
    existing = _load_json(pkg_path)
    at_root = layout["workdir"] in ("", ".")
    merged = merge_package_json(existing, slug=slug, with_ionic=with_ionic, at_root=at_root)
    return json.dumps(merged, indent=2) + "\n"


async def init_capacitor(
    slug: str,
    *,
    bundle_id: str = "",
    name: str = "",
    with_ionic: bool = False,
) -> dict[str, Any]:
    proj = await pg.get_project(slug)
    if not proj:
        raise ValueError("project not found")
    kind = str(proj.get("kind") or "vite")
    advice = ship_advice(kind)
    if advice["path"] != "capacitor":
        return {
            "ok": True,
            "skipped": True,
            "reason": advice["note"],
            "ship": advice,
            "written": [],
        }
    pname = name or proj.get("name", slug)
    bundle = bundle_id or f"com.daedalus.{slug}"
    root = pg.project_dir(slug)
    layout = cap_layout(root)
    files = capacitor_scaffold_files(
        pname, slug, bundle_id=bundle, layout=layout, with_ionic=with_ionic, kind=kind,
    )
    pkg_text = _merge_existing_package(root, layout, slug, with_ionic)
    if pkg_text:
        files[layout["package_rel"]] = pkg_text

    written: list[str] = []
    for path, content in files.items():
        target = root / path
        if path == layout["package_rel"]:
            pg.write_file(slug, path, content)
            written.append(path)
            continue
        if target.exists():
            continue
        pg.write_file(slug, path, content)
        written.append(path)

    script_path = root / CAP_SYNC
    if script_path.is_file():
        try:
            script_path.chmod(0o755)
        except OSError:
            pass

    vite_cfg = ""
    for candidate in ("vite.config.js", "vite.config.ts", "hybrid/vite.config.js"):
        p = root / candidate
        if p.is_file():
            vite_cfg = p.read_text(encoding="utf-8")
            break

    return {
        "ok": True,
        "skipped": False,
        "written": written,
        "bundle_id": bundle,
        "layout": layout,
        "ship": advice,
        "with_ionic": bool(with_ionic),
        "vite_needs_relative_base": vite_needs_relative_base(vite_cfg) if vite_cfg else False,
        "build_cmd": "bash scripts/capacitor-sync.sh",
        "note": "Local Android SDK / Xcode required. Not store-ready.",
    }


def _dist_ready(root: Path, layout: dict[str, str]) -> bool:
    dist = (root / layout["workdir"] / layout["web_dir"]).resolve()
    try:
        return dist.is_dir() and any(dist.iterdir())
    except OSError:
        return False


async def export_capacitor(slug: str) -> dict[str, Any]:
    """Write an honest export manifest. Does not build IPA/AAB."""
    proj = await pg.get_project(slug)
    if not proj:
        raise ValueError("project not found")
    kind = str(proj.get("kind") or "")
    advice = ship_advice(kind)
    if advice["path"] != "capacitor":
        manifest = {
            "slug": slug,
            "kind": kind,
            "ship": advice,
            "skipped": True,
            "store_ready": False,
            "dist_ready": False,
            "next_steps": [advice["note"]],
        }
        pg.write_file(slug, CAP_EXPORT, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        return {"ok": True, "manifest": manifest, "written": [CAP_EXPORT]}
    root = pg.project_dir(slug)
    layout = cap_layout(root)
    has_dist = _dist_ready(root, layout)
    has_android = (root / layout["workdir"] / "android").is_dir()
    has_ios = (root / layout["workdir"] / "ios").is_dir()
    try:
        cfg = json.loads(pg.read_file(slug, layout["config_rel"]))
    except Exception:
        cfg = capacitor_config(
            app_id=f"com.daedalus.{slug}",
            app_name=str(proj.get("name") or slug),
            web_dir=layout["web_dir"],
        )
    vite_cfg = ""
    for candidate in ("vite.config.js", "vite.config.ts", "hybrid/vite.config.js"):
        p = root / candidate
        if p.is_file():
            vite_cfg = p.read_text(encoding="utf-8")
            break
    manifest = {
        "slug": slug,
        "app_name": proj.get("name", slug),
        "kind": kind,
        "ship": advice,
        "web_dir": layout["web_dir"],
        "workdir": layout["workdir"],
        "dist_ready": has_dist,
        "native_projects": {"android": has_android, "ios": has_ios},
        "capacitor": cfg,
        "store_ready": False,
        "vite_needs_relative_base": vite_needs_relative_base(vite_cfg) if vite_cfg else False,
        "next_steps": [
            "Play the web build in the browser first.",
            "Run: bash scripts/capacitor-sync.sh",
            "First time: npx cap add android / npx cap add ios (local SDKs).",
            "Open Android Studio or Xcode; run on a device; then sign.",
            "Capacitor config is not an App Store / Play listing.",
        ],
    }
    pg.write_file(slug, CAP_EXPORT, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return {"ok": True, "manifest": manifest, "written": [CAP_EXPORT]}
