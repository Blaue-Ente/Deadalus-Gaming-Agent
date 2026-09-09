"""Writer assist — kernel Phaser/Vite skeleton so weaker models still boot.

Mentor steers NEXT. This module *writes the first files* when the owner
asks for help: a Phaser 3 + Vite project that already paints a Menu on
``#game`` with ``Phaser.WEBGL`` and ``import Phaser from 'phaser'``.

The Vite Hello ``#app`` counter is the blank-screen trap. Kernel-scaffold
skips it. Racer scaffold still wins when the brief is F-Zero-class.

Toggle (highest wins):

1. ``DAEDALUS_WRITER_ASSIST=off|auto|kernel-scaffold`` — process lock
   (tests, live sims). ``0``/``false`` = off; ``1``/``true``/``on`` =
   kernel-scaffold.
2. KV ``adn:daedalus_writer_assist`` — Studio persist.
3. Default **off** until the owner promotes Auto after A/B sims.

Modes:

- ``off`` — today's Vite Hello (or racer).
- ``auto`` — Phaser seed when kind=vite *and* the brief mentions Phaser
  (name, description, or the current native-loop ask).
- ``kernel-scaffold`` — Phaser seed for every new ``kind=vite`` project
  that is not a racer.

``steered`` is accepted and currently aliases ``off`` (Mentor already
locks NEXT; a writer clamp is a later candidate).
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_DB_KEY = "adn:daedalus_writer_assist"
_DEFAULT_MODE = "off"
MODES = ("off", "auto", "kernel-scaffold")
_ALIASES = {
    "0": "off",
    "false": "off",
    "no": "off",
    "off": "off",
    "1": "kernel-scaffold",
    "true": "kernel-scaffold",
    "on": "kernel-scaffold",
    "yes": "kernel-scaffold",
    "kernel": "kernel-scaffold",
    "kernel-scaffold": "kernel-scaffold",
    "scaffold": "kernel-scaffold",
    "auto": "auto",
    "default": "auto",
    "steered": "off",
}
_MARKER = ".daedalus-writer-assist.json"
_PHASER_RE = re.compile(
    r"\bphaser\b|phaser\.game|phaser\.webgl|kind\s*=\s*vite.*scene",
    re.I,
)

_ask: contextvars.ContextVar[str] = contextvars.ContextVar(
    "writer_assist_ask", default=""
)


def set_ask(text: str) -> None:
    """Bind the current user ask so Auto can see Phaser even if playground_new
    ships an empty description."""
    _ask.set(str(text or ""))


def current_ask() -> str:
    return str(_ask.get() or "")


def normalize_mode(raw: Any) -> str:
    key = str(raw or "").strip().lower()
    if not key:
        return _DEFAULT_MODE
    return _ALIASES.get(key, _DEFAULT_MODE if key not in MODES else key)


def env_override() -> str | None:
    raw = (os.environ.get("DAEDALUS_WRITER_ASSIST") or "").strip()
    if not raw:
        return None
    return normalize_mode(raw)


def _kv_raw() -> Any:
    try:
        from . import kv
        return kv.get(_DB_KEY)
    except Exception:
        return None


def persisted_mode() -> str | None:
    raw = _kv_raw()
    if raw is None or str(raw).strip() == "":
        return None
    return normalize_mode(raw)


def mode() -> str:
    ov = env_override()
    if ov is not None:
        return ov
    stored = persisted_mode()
    return stored if stored is not None else _DEFAULT_MODE


def set_mode(flag: str) -> str:
    from . import kv
    nxt = normalize_mode(flag)
    kv.set(_DB_KEY, nxt)
    log.info("Daedalus writer assist set → %s", nxt)
    return mode()


def status() -> dict[str, Any]:
    ov = env_override()
    stored = persisted_mode()
    current = mode()
    return {
        "mode": current,
        "enabled": current != "off",
        "env_locked": ov is not None,
        "env_override": ov,
        "persisted": stored,
        "default": _DEFAULT_MODE,
        "modes": list(MODES),
        "steered_available": False,
    }


def apply_update(body: dict[str, Any] | None) -> dict[str, Any]:
    body = body or {}
    raw = body.get("mode")
    if raw is None or str(raw).strip() == "":
        raw = body.get("writer_assist")
    if (raw is None or str(raw).strip() == "") and body.get("enabled") is not None:
        raw = "kernel-scaffold" if body.get("enabled") else "off"
    if raw is not None and str(raw).strip() != "":
        set_mode(str(raw))
    return status()


def looks_like_phaser(
    *,
    kind: str = "",
    name: str = "",
    description: str = "",
    ask: str = "",
) -> bool:
    if str(kind or "").strip().lower() not in {"vite", "phaser"}:
        return False
    blob = " ".join((kind, name, description, ask or current_ask()))
    return bool(_PHASER_RE.search(blob))


def wants_phaser_seed(
    *,
    kind: str = "",
    name: str = "",
    description: str = "",
    ask: str = "",
) -> bool:
    k = str(kind or "").strip().lower()
    if k not in {"vite", "phaser"}:
        return False
    current = mode()
    if current == "off":
        return False
    if current == "kernel-scaffold":
        return True
    return looks_like_phaser(
        kind=k, name=name, description=description, ask=ask,
    )


def marker_path(pdir: Path) -> Path:
    return Path(pdir) / _MARKER


def project_seeded(pdir: Path | str) -> bool:
    return marker_path(Path(pdir)).is_file()


def seed_note(slug: str) -> str:
    try:
        from .playground import project_dir
        root = project_dir(slug)
    except Exception:
        return ""
    if not project_seeded(root):
        return ""
    return (
        "\nKernel Phaser skeleton is already on disk: index.html #game, "
        "src/main.js Phaser.WEBGL, src/scenes/Boot.js Menu.js Play.js, "
        "package.json has vite + phaser. Fill src/scenes/Play.js (WASD, "
        "orbs, HUD). Do not revert src/main.js to the Vite Hello / #app "
        "counter. Then npm install (no pipes) and npm run build."
    )


def maybe_seed_phaser_vite(
    pdir: Path,
    *,
    name: str,
    slug: str,
    description: str = "",
    kind: str = "vite",
) -> bool:
    """Write the Phaser/Vite skeleton. Return True if this replaced the Vite Hello path."""
    if not wants_phaser_seed(
        kind=kind, name=name, description=description,
    ):
        return False
    seed_phaser_vite(pdir, name=name, slug=slug, description=description)
    return True


def seed_phaser_vite(
    pdir: Path,
    *,
    name: str = "Game",
    slug: str = "",
    description: str = "",
) -> dict[str, str]:
    """Write a bootable Phaser 3 + Vite skeleton. Safe to call twice (skips existing)."""
    root = Path(pdir)
    root.mkdir(parents=True, exist_ok=True)
    title = (name or slug or "Game").strip() or "Game"
    slug = (slug or root.name or "game").strip()
    files = _skeleton_files(title=title, slug=slug, description=description)
    written: dict[str, str] = {}
    for rel, text in files.items():
        target = root / rel
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        written[rel] = text
    marker = marker_path(root)
    if not marker.exists():
        marker.write_text(
            json.dumps(
                {
                    "mode": mode(),
                    "kind": "phaser-vite",
                    "version": 1,
                    "name": title,
                    "slug": slug,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    log.info("writer assist seeded Phaser vite slug=%s files=%s", slug, sorted(written))
    return written


def _skeleton_files(*, title: str, slug: str, description: str) -> dict[str, str]:
    title_js = json.dumps(title, ensure_ascii=False)
    hint = (description or "").strip() or "One playable slice."
    hint_js = json.dumps(hint[:80], ensure_ascii=False)
    pkg_name = re.sub(r"[^a-z0-9-]+", "-", slug.lower()).strip("-") or "game"
    return {
        "index.html": _INDEX_HTML.format(title=_html(title)),
        "package.json": _PACKAGE_JSON.format(slug=pkg_name),
        "vite.config.js": _VITE_CONFIG,
        "src/main.js": _MAIN_JS,
        "src/scenes/Boot.js": _BOOT_JS,
        "src/scenes/Menu.js": _MENU_JS.format(title_js=title_js, hint_js=hint_js),
        "src/scenes/Play.js": _PLAY_JS,
    }


def _html(text: str) -> str:
    import html
    return html.escape(text or "", quote=True)


_INDEX_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{title}</title>
  <style>
    html, body {{ margin: 0; height: 100%; background: #0b1020; overflow: hidden; }}
    #game {{ width: 100%; height: 100%; }}
  </style>
</head>
<body>
  <div id="game"></div>
  <script type="module" src="/src/main.js"></script>
</body>
</html>
"""

_PACKAGE_JSON = """\
{{
  "name": "{slug}",
  "private": true,
  "version": "0.0.1",
  "type": "module",
  "scripts": {{
    "dev": "vite",
    "build": "vite build"
  }},
  "dependencies": {{
    "phaser": "^3.80.0"
  }},
  "devDependencies": {{
    "vite": "^5.0.0"
  }}
}}
"""

_VITE_CONFIG = """\
import { defineConfig } from 'vite';

export default defineConfig({
  base: './',
  build: { outDir: 'dist' },
});
"""

_MAIN_JS = """\
import Phaser from 'phaser';
import { Boot } from './scenes/Boot.js';
import { Menu } from './scenes/Menu.js';
import { Play } from './scenes/Play.js';

const game = new Phaser.Game({
  type: Phaser.WEBGL,
  parent: 'game',
  backgroundColor: '#0b1020',
  pixelArt: true,
  scale: {
    mode: Phaser.Scale.FIT,
    autoCenter: Phaser.Scale.CENTER_BOTH,
    width: 800,
    height: 600,
  },
  scene: [Boot, Menu, Play],
});

export default game;
"""

_BOOT_JS = """\
import Phaser from 'phaser';

export class Boot extends Phaser.Scene {
  constructor() {
    super('Boot');
  }

  preload() {}

  create() {
    this.scene.start('Menu');
  }
}
"""

_MENU_JS = """\
import Phaser from 'phaser';

export class Menu extends Phaser.Scene {{
  constructor() {{
    super('Menu');
  }}

  create() {{
    const {{ width, height }} = this.scale;
    this.cameras.main.setBackgroundColor('#0b1020');
    this.add.text(width / 2, height / 2 - 48, {title_js}, {{
      fontFamily: 'Georgia, serif',
      fontSize: '36px',
      color: '#e8f0ff',
    }}).setOrigin(0.5);
    this.add.text(width / 2, height / 2 + 8, {hint_js}, {{
      fontFamily: 'system-ui, sans-serif',
      fontSize: '14px',
      color: '#8aa0c8',
    }}).setOrigin(0.5);
    this.add.text(width / 2, height / 2 + 48, 'Enter — begin', {{
      fontFamily: 'system-ui, sans-serif',
      fontSize: '16px',
      color: '#c9a227',
    }}).setOrigin(0.5);
    this.input.keyboard.once('keydown-ENTER', () => this.scene.start('Play'));
    this.input.once('pointerdown', () => this.scene.start('Play'));
  }}
}}
"""

_PLAY_JS = """\
import Phaser from 'phaser';

export class Play extends Phaser.Scene {
  constructor() {
    super('Play');
  }

  create() {
    this.cameras.main.setBackgroundColor('#12182a');
    this.add.text(24, 24, 'Play — fill this scene', {
      fontFamily: 'system-ui, sans-serif',
      fontSize: '18px',
      color: '#dce7ff',
    });
  }
}
"""
