"""Sound and feel for every game, free: a Web Audio sound module Daedalus places in the project (gap 4a).

Owner, 2026-11-12 (plan approved): games get sound and a look by default, procedurally and at no cost; paid services
(ElevenLabs, image models) come later, with each user's own key. The-Labirinth has a score per world; Daedalus games
were silent.

- ``sfx.js`` (an ES module, no files, no network): ``sfx.jump()``, ``score()``, ``hit()``, ``lose()``, ``win()``,
  ``click()``, ``powerup()``, ``explode()``. It starts on the player's first key or click (browsers block sound
  before that) and ``M`` mutes. Daedalus writes it into a game project once and never overwrites the game's copy.
- ``GUIDANCE`` tells the writer to call it on the game's events and to draw its art in the bible's look.
- ``has_sound`` is the check the juice gate (mentor_lead) uses: a game that plays no sound at all is asked once.
"""
from __future__ import annotations

import re
from pathlib import Path

SFX_JS = r"""// sfx.js: game sounds made in the browser (Web Audio). No files, no network. Placed by Daedalus.
// Use: import { sfx } from './sfx.js';  sfx.jump(); sfx.score(); sfx.hit(); sfx.lose(); sfx.win();
// Sound starts after the player's first key or click (browsers require it). M mutes and unmutes.
let ctx = null;
let master = null;
let muted = false;
try { muted = localStorage.getItem('sfx.muted') === '1'; } catch (e) { /* private mode */ }

function audio() {
  if (ctx) return ctx;
  const AC = window.AudioContext || window.webkitAudioContext;
  if (!AC) return null;
  ctx = new AC();
  master = ctx.createGain();
  master.gain.value = muted ? 0 : 0.35;
  master.connect(ctx.destination);
  return ctx;
}

function unlock() {
  const a = audio();
  if (a && a.state === 'suspended') a.resume();
}
['keydown', 'pointerdown', 'touchstart'].forEach((t) => addEventListener(t, unlock, { passive: true }));
addEventListener('keydown', (e) => {
  if (e.key === 'm' || e.key === 'M') {
    muted = !muted;
    try { localStorage.setItem('sfx.muted', muted ? '1' : '0'); } catch (err) { /* private mode */ }
    if (master) master.gain.value = muted ? 0 : 0.35;
  }
});

function tone(type, from, to, dur, vol = 1, delay = 0) {
  const a = audio();
  if (!a || muted || a.state !== 'running') return;
  const t = a.currentTime + delay;
  const osc = a.createOscillator();
  const g = a.createGain();
  osc.type = type;
  osc.frequency.setValueAtTime(from, t);
  osc.frequency.exponentialRampToValueAtTime(Math.max(1, to), t + dur);
  g.gain.setValueAtTime(0.0001, t);
  g.gain.exponentialRampToValueAtTime(vol, t + 0.01);
  g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
  osc.connect(g).connect(master);
  osc.start(t);
  osc.stop(t + dur + 0.02);
}

function noise(dur, vol = 1, delay = 0) {
  const a = audio();
  if (!a || muted || a.state !== 'running') return;
  const t = a.currentTime + delay;
  const len = Math.max(1, Math.floor(a.sampleRate * dur));
  const buf = a.createBuffer(1, len, a.sampleRate);
  const data = buf.getChannelData(0);
  for (let i = 0; i < len; i++) data[i] = (Math.random() * 2 - 1) * (1 - i / len);
  const src = a.createBufferSource();
  const g = a.createGain();
  g.gain.value = vol;
  src.buffer = buf;
  src.connect(g).connect(master);
  src.start(t);
}

export const sfx = {
  jump: () => tone('square', 260, 620, 0.14, 0.5),
  score: () => { tone('square', 880, 880, 0.06, 0.4); tone('square', 1320, 1320, 0.09, 0.4, 0.06); },
  click: () => tone('triangle', 660, 520, 0.05, 0.4),
  powerup: () => [0, 1, 2, 3].forEach((i) => tone('square', 440 * (1 + i * 0.25), 440 * (1 + i * 0.25), 0.07, 0.35, i * 0.06)),
  hit: () => { tone('sawtooth', 220, 70, 0.18, 0.6); noise(0.08, 0.25); },
  explode: () => noise(0.45, 0.7),
  lose: () => [0, 1, 2].forEach((i) => tone('triangle', 440 / (1 + i * 0.35), 300 / (1 + i * 0.35), 0.22, 0.5, i * 0.18)),
  win: () => [523, 659, 784, 1047].forEach((f, i) => tone('square', f, f, 0.14, 0.4, i * 0.11)),
  get muted() { return muted; },
};
export default sfx;
"""

GUIDANCE = (
    "SOUND AND LOOK (every game has both, free): sfx.js is in the project (Daedalus placed it; keep it, do not "
    "rewrite it). Import it (import { sfx } from './sfx.js') and call it on the game's events: sfx.jump(), "
    "sfx.score(), sfx.hit(), sfx.lose(), sfx.win(), sfx.click() on menus, sfx.powerup(), sfx.explode(). Mention in "
    "the help text that M mutes. Draw the art in code (canvas paths or inline SVG) in the look the plan or bible "
    "names: a palette of 3-5 colours, shapes with an outline or a shadow, a background with depth (a gradient, "
    "parallax layers or a pattern), and a short effect when something happens (a flash, particles, a shake). No "
    "image files from the web."
)

_SOUND_CALL = re.compile(r"\bsfx\s*\.\s*\w+\s*\(|\bAudioContext\b|\bnew\s+Audio\s*\(|\bHowl\s*\(")
_CODE_FILES = ("*.js", "*.mjs", "*.ts", "*.tsx", "*.jsx", "*.html")
_SKIP_DIRS = {"node_modules", "dist", "build", ".git", ".daedalus", "tests", "test"}


def sfx_path(kind: str) -> str:
    """Where sfx.js goes: next to index.html for a static game, in src/ for a bundled one."""
    return "src/sfx.js" if kind in {"vite", "vite-react", "phaser"} else "sfx.js"


def ensure_sfx(root: Path, kind: str = "static") -> bool:
    """Place sfx.js in a game project when it has none. Never overwrites the game's copy. True if it wrote."""
    target = Path(root) / sfx_path(kind)
    if target.exists():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(SFX_JS, encoding="utf-8")
    return True


def has_sound(root: Path) -> bool:
    """Does the game's own code play any sound (sfx.js calls, Web Audio, an Audio element, Howler)? sfx.js itself
    does not count: placing the module is not using it."""
    root = Path(root)
    for pattern in _CODE_FILES:
        for path in root.rglob(pattern):
            rel = path.relative_to(root)
            if path.name == "sfx.js" or any(part in _SKIP_DIRS for part in rel.parts[:-1]):
                continue
            try:
                if _SOUND_CALL.search(path.read_text(encoding="utf-8", errors="replace")):
                    return True
            except OSError:
                continue
    return False
