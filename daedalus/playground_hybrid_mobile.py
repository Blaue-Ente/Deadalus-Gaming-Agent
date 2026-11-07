"""Framework7 → Vite → PakePlus-iOS hybrid mobile pipeline."""
from __future__ import annotations

import json
from typing import Any

from . import playground as pg

HYBRID_README = "studio/hybrid/README.md"
PAKEPLUS_DOC = "studio/hybrid/PAKEPLUS_PIPELINE.md"
PAKEPLUS_CONFIG = "studio/hybrid/pakeplus.config.json"
BUILD_SCRIPT = "scripts/hybrid-build.sh"
EXPORT_MANIFEST = "studio/hybrid/export-manifest.json"


def _package_json(name: str, slug: str) -> str:
    return json.dumps({
        "name": slug or "daedalus-hybrid",
        "private": True,
        "version": "0.1.0",
        "type": "module",
        "scripts": {
            "dev": "vite",
            "build": "vite build",
            "preview": "vite preview",
            "hybrid:build": "bash scripts/hybrid-build.sh",
        },
        "dependencies": {
            "framework7": "^8.3.3",
            "framework7-icons": "^5.0.5",
        },
        "devDependencies": {
            "vite": "^5.4.0",
        },
    }, indent=2) + "\n"


def _vite_config() -> str:
    return """import { defineConfig } from 'vite';

export default defineConfig({
  base: './',
  build: {
    outDir: 'dist',
    assetsDir: 'assets',
    emptyOutDir: true,
  },
  server: { port: 5173, host: true },
});
"""


def _index_html(name: str) -> str:
    title = name or "Daedalus Hybrid"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
  <title>{title}</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/framework7@8/framework7-bundle.min.css" />
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/framework7-icons@5/css/framework7-icons.css" />
</head>
<body>
  <div id="app"></div>
  <script type="module" src="/src/main.js"></script>
</body>
</html>
"""


def _main_js(name: str) -> str:
    title = name or "Daedalus"
    return f"""import Framework7 from 'framework7/lite-bundle';
import Framework7DOM from 'framework7/lite/dom';

const app = new Framework7({{
  el: '#app',
  name: '{title}',
  theme: 'auto',
  routes: [
    {{
      path: '/',
      url: './pages/home.html',
    }},
  ],
}});

export default app;
"""


def _home_page(name: str) -> str:
    return f"""<div class="page" data-name="home">
  <div class="navbar">
    <div class="navbar-bg"></div>
    <div class="navbar-inner">
      <div class="title">{name or 'Hybrid'}</div>
    </div>
  </div>
  <div class="page-content">
    <div class="block block-strong inset">
      <p>Framework7 shell — Daedalus Studio hybrid mobile prototype.</p>
      <p class="text-color-gray">Build → <code>npm run hybrid:build</code> → PakePlus-iOS wrap.</p>
    </div>
    <div class="list links-list">
      <ul>
        <li><a href="#" class="item-link list-button">Play prototype</a></li>
      </ul>
    </div>
  </div>
</div>
"""


def _hybrid_readme(name: str, slug: str) -> str:
    return f"""# Hybrid Mobile — {name}

Framework7 + Vite prototype → PakePlus-iOS native shell.

## Quick start
```bash
cd hybrid   # or project root if files at top level
npm install
npm run dev
```

## Production build
```bash
npm run hybrid:build
# → dist/ ready for PakePlus-iOS
```

## Pipeline
1. **Daedalus** generates UI + game logic in `hybrid/` or `src/`
2. **Framework7** — mobile UI shell (any genre)
3. **Vite** — bundle to static `dist/`
4. **PakePlus-iOS** — wrap as `.ipa` (see `{PAKEPLUS_DOC}`)
5. **LiveContainer** / **ios-app-signer** — sideload for testing

Project slug: `{slug}`
"""


def _pakeplus_doc(name: str, slug: str, bundle_id: str) -> str:
    return f"""# PakePlus-iOS Pipeline

Wrap Daedalus **vite/dist** as a native iOS app.

## Prerequisites
- macOS with Xcode (for final IPA)
- [PakePlus-iOS](https://github.com/Sjj1024/PakePlus-iOS)
- Built `dist/` folder (`npm run hybrid:build`)

## Steps
1. Clone PakePlus-iOS locally (not in cloud sandbox)
2. Point web root to this project's `hybrid/dist/` (or `dist/`)
3. Set app name: **{name}**
4. Set bundle ID: `{bundle_id}`
5. Build IPA per PakePlus README
6. Optional: **LiveContainer** sideload for device test
7. Optional: **ios-app-signer** to re-sign with dev cert

## Config
See `{PAKEPLUS_CONFIG}` for Daedalus-generated defaults.

## Firebase (optional)
If using Firebase JS in prototype, add config in `src/firebase.js` — never commit secrets.
For native Firebase, prefer Unity path or native PakePlus plugins.
"""


def _pakeplus_config(name: str, slug: str, bundle_id: str) -> dict[str, Any]:
    return {
        "app_name": name,
        "slug": slug,
        "bundle_id": bundle_id,
        "web_root": "hybrid/dist",
        "entry": "index.html",
        "pakeplus_repo": "https://github.com/Sjj1024/PakePlus-iOS",
        "framework7_repo": "https://github.com/framework7io/framework7",
        "notes": "Edit locally on Mac before PakePlus build. Daedalus does not run PakePlus in cloud.",
    }


def _build_script() -> str:
    return """#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HYBRID="$ROOT/hybrid"
if [[ ! -f "$HYBRID/package.json" ]]; then
  echo "hybrid/package.json not found — run hybrid-mobile init first"
  exit 1
fi
cd "$HYBRID"
npm install
npm run build
echo "✅ dist/ ready at $HYBRID/dist"
echo "Next: PakePlus-iOS → see studio/hybrid/PAKEPLUS_PIPELINE.md"
"""


def hybrid_scaffold_files(name: str, slug: str, *, bundle_id: str = "") -> dict[str, str]:
    bundle = bundle_id or f"com.daedalus.{slug}"
    return {
        "hybrid/package.json": _package_json(name, slug),
        "hybrid/vite.config.js": _vite_config(),
        "hybrid/index.html": _index_html(name),
        "hybrid/src/main.js": _main_js(name),
        "hybrid/src/pages/home.html": _home_page(name),
        HYBRID_README: _hybrid_readme(name, slug),
        PAKEPLUS_DOC: _pakeplus_doc(name, slug, bundle),
        PAKEPLUS_CONFIG: json.dumps(_pakeplus_config(name, slug, bundle), indent=2) + "\n",
        BUILD_SCRIPT: _build_script(),
    }


async def init_hybrid_mobile(
    slug: str,
    *,
    bundle_id: str = "",
    name: str = "",
) -> dict[str, Any]:
    proj = await pg.get_project(slug)
    if not proj:
        raise ValueError("project not found")
    pname = name or proj.get("name", slug)
    bundle = bundle_id or f"com.daedalus.{slug}"
    files = hybrid_scaffold_files(pname, slug, bundle_id=bundle)
    written: list[str] = []
    for path, content in files.items():
        target = pg.project_dir(slug) / path
        if not target.exists():
            pg.write_file(slug, path, content)
            written.append(path)
    if BUILD_SCRIPT in written or not (pg.project_dir(slug) / BUILD_SCRIPT).exists():
        script_path = pg.project_dir(slug) / BUILD_SCRIPT
        if script_path.exists():
            script_path.chmod(0o755)
    await pg.update_project(slug, kind="vite", entry="hybrid/index.html")
    return {
        "ok": True,
        "written": written,
        "bundle_id": bundle,
        "preview": "hybrid/index.html",
        "build_cmd": "npm run hybrid:build",
    }


async def export_hybrid_ios(slug: str) -> dict[str, Any]:
    """Prepare export manifest after dist/ exists (build runs locally)."""
    proj = await pg.get_project(slug)
    if not proj:
        raise ValueError("project not found")
    pdir = pg.project_dir(slug)
    dist = pdir / "hybrid" / "dist"
    has_dist = dist.exists() and any(dist.iterdir()) if dist.exists() else False
    try:
        pake_cfg = json.loads(pg.read_file(slug, PAKEPLUS_CONFIG))
    except Exception:
        pake_cfg = _pakeplus_config(proj.get("name", slug), slug, f"com.daedalus.{slug}")
    manifest = {
        "slug": slug,
        "app_name": proj.get("name", slug),
        "dist_path": "hybrid/dist",
        "dist_ready": has_dist,
        "pakeplus": pake_cfg,
        "next_steps": [
            "Run: npm run hybrid:build (in hybrid/)",
            "Open PakePlus-iOS on Mac",
            "Point to hybrid/dist",
            "Build IPA → LiveContainer or TestFlight",
        ],
    }
    pg.write_file(slug, EXPORT_MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return {"ok": True, "manifest": manifest, "written": [EXPORT_MANIFEST]}
