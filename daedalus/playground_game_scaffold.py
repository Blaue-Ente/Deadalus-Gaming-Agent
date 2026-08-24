"""Game-specific scaffold files — Phaser boot, asset loader, generate-asset helper.

Inspired by Vibra Code expo-template patterns: pre-baked tooling so autopilot
builds reference real sprites instead of CSS gradient placeholders.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .playground import project_dir, read_file, write_file

log = logging.getLogger(__name__)

_GENERATE_ASSET_JS = """\
#!/usr/bin/env node
/**
 * Generate a PNG asset via ADN Playground API (Vibra-style CLI).
 * Usage: node scripts/generate-asset.js --prompt "pixel hero" --output assets/player.png
 *
 * Requires ADN admin session cookie or PLAYGROUND_ASSET_TOKEN env (future).
 * During autopilot, assets are generated server-side — this script is for manual/agent use.
 */
import { parseArgs } from 'node:util';
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';

const { values } = parseArgs({
  options: {
    prompt: { type: 'string' },
    output: { type: 'string' },
    size: { type: 'string', default: '512x512' },
    base: { type: 'string', default: process.env.PLAYGROUND_API_BASE || '' },
    slug: { type: 'string', default: process.env.PLAYGROUND_SLUG || '' },
  },
});

const prompt = values.prompt;
const output = values.output;
if (!prompt || !output) {
  console.error('Usage: --prompt "..." --output assets/foo.png [--size 512x512] [--base URL] [--slug name]');
  process.exit(1);
}

const rel = output.startsWith('assets/') ? output : `assets/${output}`;
const outPath = resolve(process.cwd(), rel);
mkdirSync(dirname(outPath), { recursive: true });

const base = (values.base || '').replace(/\\/$/, '');
const slug = values.slug;
if (!base || !slug) {
  console.error('Set PLAYGROUND_API_BASE + PLAYGROUND_SLUG or pass --base and --slug');
  process.exit(1);
}

const url = `${base}/daedalus/playground/api/projects/${slug}/assets/generate`;
const res = await fetch(url, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  credentials: 'include',
  body: JSON.stringify({ path: rel, prompt, size: values.size }),
});

if (!res.ok) {
  const err = await res.text();
  console.error('Generate failed:', res.status, err.slice(0, 200));
  process.exit(1);
}

const data = await res.json();
console.log('OK', rel, data.bytes || 0, 'bytes');
"""

_ASSET_LOADER_JS = """\
/** Preload game sprites from assets/ — use with Phaser or canvas. */
export const ASSET_PATHS = {
  player: '/assets/player.png',
  enemy: '/assets/enemy.png',
  background: '/assets/background.png',
};

export function preloadImages(sceneOrCallbacks) {
  const load = sceneOrCallbacks?.load ?? sceneOrCallbacks;
  if (!load?.image) {
    return ASSET_PATHS;
  }
  Object.entries(ASSET_PATHS).forEach(([key, path]) => {
    if (path) load.image(key, path);
  });
  return ASSET_PATHS;
}

export function drawSprite(ctx, img, x, y, w = 64, h = 64) {
  if (img?.complete && img.naturalWidth) {
    ctx.drawImage(img, x, y, w, h);
  }
}
"""

_PHASER_BOOT_JS = """\
import Phaser from 'phaser';
import { preloadImages } from './game/assetLoader.js';

class BootScene extends Phaser.Scene {
  constructor() { super('Boot'); }
  preload() {
    preloadImages(this);
  }
  create() {
    this.scene.start('Game');
  }
}

class GameScene extends Phaser.Scene {
  constructor() { super('Game'); }
  create() {
    const bg = this.add.image(400, 300, 'background').setDisplaySize(800, 600);
    bg.setDepth(0);
    const player = this.add.image(200, 300, 'player').setDisplaySize(64, 64);
    player.setDepth(1);
    this.add.text(16, 16, 'Phaser + assets/', { fontSize: '18px', color: '#fff' });
  }
}

export function startGame(parent = 'app') {
  return new Phaser.Game({
    type: Phaser.AUTO,
    width: 800,
    height: 600,
    parent,
    backgroundColor: '#0f172a',
    scene: [BootScene, GameScene],
  });
}
"""

_GAME_CANVAS_JSX = """\
import { useEffect, useRef } from 'react';

const SPRITES = {
  player: '/assets/player.png',
  enemy: '/assets/enemy.png',
  background: '/assets/background.png',
};

function loadImage(src) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = reject;
    img.src = src;
  });
}

export default function GameCanvas() {
  const canvasRef = useRef(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return undefined;
    const ctx = canvas.getContext('2d');
    let raf = 0;
    let images = {};

    (async () => {
      try {
        const entries = await Promise.all(
          Object.entries(SPRITES).map(async ([k, src]) => [k, await loadImage(src)])
        );
        images = Object.fromEntries(entries);
      } catch (e) {
        console.warn('Asset load failed', e);
      }

      const loop = () => {
        ctx.fillStyle = '#0f172a';
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        if (images.background) {
          ctx.drawImage(images.background, 0, 0, canvas.width, canvas.height);
        }
        if (images.player) {
          ctx.drawImage(images.player, 120, 200, 64, 64);
        }
        if (images.enemy) {
          ctx.drawImage(images.enemy, 520, 200, 64, 64);
        }
        raf = requestAnimationFrame(loop);
      };
      loop();
    })();

    return () => cancelAnimationFrame(raf);
  }, []);

  return (
    <canvas
      ref={canvasRef}
      width={800}
      height={500}
      style={{ width: '100%', maxWidth: 800, borderRadius: 12, display: 'block' }}
    />
  );
}
"""

_README_ASSETS = """\
# Game assets (Дедал Playground)

PNG sprites live in `assets/`. Autopilot generates them before the UI build.

**Rules (Vibra-style):**
- Heroes/enemies MUST use `assets/*.png` — never CSS gradient squares.
- Verify after generation: files exist and are >1KB valid PNG.
- Web: `<img src="/assets/player.png">` or canvas `drawImage`.
- React: put gameplay in `GameCanvas.jsx` (see `src/components/`).

Manual generation:
```bash
node scripts/generate-asset.js --prompt "pixel arena hero" --output assets/player.png \\
  --base https://your-adn-host --slug YOUR_PROJECT_SLUG
```
"""


def _merge_phaser_dep(pkg_path: Path) -> None:
    if not pkg_path.exists():
        return
    try:
        data = json.loads(pkg_path.read_text(encoding="utf-8"))
    except Exception:
        return
    deps = data.setdefault("dependencies", {})
    if "phaser" not in deps:
        deps["phaser"] = "^3.80.0"
        pkg_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def ensure_game_scaffold(slug: str, kind: str) -> list[str]:
    """Write game tooling files if missing. Returns paths written."""
    if kind not in ("static", "vite", "vite-react"):
        return []

    root = project_dir(slug)
    try:
        from .arcade_craft import wants_racer_scaffold
        blob = ""
        readme = root / "README.md"
        pkg = root / "package.json"
        if readme.exists():
            blob += readme.read_text(encoding="utf-8", errors="replace")[:800]
        if (root / "src/racer/vehicle.js").exists() or wants_racer_scaffold(slug, blob):
            return []
        if pkg.exists() and '"three"' in pkg.read_text(encoding="utf-8", errors="replace"):
            return []
    except Exception:
        pass
    written: list[str] = []

    def _write(rel: str, content: str) -> None:
        target = root / rel
        if target.exists():
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(rel)

    _write("scripts/generate-asset.js", _GENERATE_ASSET_JS)
    _write("assets/README.md", _README_ASSETS)

    if kind == "vite":
        _write("src/game/assetLoader.js", _ASSET_LOADER_JS)
        _write("src/game/phaserBoot.js", _PHASER_BOOT_JS)
        pkg = root / "package.json"
        if pkg.exists():
            _merge_phaser_dep(pkg)
            if "src/game/phaserBoot.js" not in written:
                written.append("package.json (phaser dep)")

    if kind == "vite-react":
        _write("src/game/assetLoader.js", _ASSET_LOADER_JS)
        _write("src/components/GameCanvas.jsx", _GAME_CANVAS_JSX)

    if kind == "static":
        _write("game/assetLoader.js", _ASSET_LOADER_JS)

    if written:
        log.info("game scaffold: %s → %s", slug, written)
    return written


def patch_vite_main_for_phaser(slug: str) -> bool:
    """If main.js still shows placeholder HTML, suggest Phaser boot (non-destructive)."""
    for rel in ("src/main.js", "main.js"):
        try:
            content = read_file(slug, rel, max_bytes=8000)
        except Exception:
            continue
        if "phaserBoot" in content or "startGame" in content:
            return False
        if "innerHTML" not in content and "Vite + Vanilla" not in content:
            return False
        new_content = """\
import { startGame } from './game/phaserBoot.js';

startGame('app');
"""
        write_file(slug, rel, new_content)
        return True
    return False
