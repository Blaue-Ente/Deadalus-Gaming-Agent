"""Playable M0 hover-racer scaffold — Vite + Three.js (WebGPU with WebGL fallback).

Used instead of the Phaser/vanilla hello template when the brief is F-Zero-class.
Files are complete, not TODOs: one ship, spline track, fail, visible boost pads.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from .arcade_craft import wants_racer_scaffold

log = logging.getLogger(__name__)

_PLACEHOLDER_MARKERS = (
    "Vite + Vanilla JS работи",
    "Кликни ме",
    "React 18 готов",
)

_BOOT_JS = """\
/** Renderer picker — FM1 ships WebGPU; WebGL must still boot in preview/CI. */
import * as THREE from 'three';

export async function createRenderer(canvas) {
  const size = () => {
    const w = Math.max(1, canvas.clientWidth || window.innerWidth);
    const h = Math.max(1, canvas.clientHeight || window.innerHeight);
    return { w, h };
  };
  if (globalThis.navigator?.gpu) {
    try {
      const { WebGPURenderer } = await import('three/webgpu');
      const renderer = new WebGPURenderer({ canvas, antialias: true });
      await renderer.init();
      const { w, h } = size();
      renderer.setSize(w, h, false);
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
      return { renderer, backend: 'webgpu', THREE };
    } catch (err) {
      console.warn('WebGPU init failed, using WebGL', err);
    }
  }
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false });
  const { w, h } = size();
  renderer.setSize(w, h, false);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.05;
  return { renderer, backend: 'webgl', THREE };
}
"""

_TRACK_JS = """\
/** Catmull-Rom race line + ribbon + chevrons + glowing boost pads. */
import * as THREE from 'three';

const HALF_WIDTH = 7.2;

function ribbonGeometry(curve, width, segments) {
  const half = width / 2;
  const positions = [];
  const uvs = [];
  const indices = [];
  const up = new THREE.Vector3(0, 1, 0);
  const side = new THREE.Vector3();
  for (let i = 0; i <= segments; i += 1) {
    const t = i / segments;
    const p = curve.getPointAt(t);
    const tangent = curve.getTangentAt(t);
    side.crossVectors(up, tangent).normalize();
    const a = p.clone().addScaledVector(side, half);
    const b = p.clone().addScaledVector(side, -half);
    positions.push(a.x, 0.04, a.z, b.x, 0.04, b.z);
    uvs.push(t * 24, 0, t * 24, 1);
    if (i < segments) {
      const base = i * 2;
      indices.push(base, base + 1, base + 2, base + 1, base + 3, base + 2);
    }
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geo.setAttribute('uv', new THREE.Float32BufferAttribute(uvs, 2));
  geo.setIndex(indices);
  geo.computeVertexNormals();
  return geo;
}

export function createTrack(scene) {
  const pts = [
    new THREE.Vector3(48, 0, 4),
    new THREE.Vector3(36, 0, 38),
    new THREE.Vector3(-6, 0, 52),
    new THREE.Vector3(-52, 0, 22),
    new THREE.Vector3(-44, 0, -26),
    new THREE.Vector3(4, 0, -50),
    new THREE.Vector3(46, 0, -28),
  ];
  const curve = new THREE.CatmullRomCurve3(pts, true, 'catmullrom', 0.28);
  const length = curve.getLength();
  const roadMat = new THREE.MeshStandardMaterial({
    color: 0x1e293b,
    metalness: 0.15,
    roughness: 0.55,
    emissive: 0x0b1220,
    emissiveIntensity: 0.4,
  });
  const road = new THREE.Mesh(ribbonGeometry(curve, HALF_WIDTH * 2, 160), roadMat);
  road.receiveShadow = true;
  scene.add(road);

  const edgeGeo = new THREE.TubeGeometry(curve, 180, 0.18, 6, true);
  const edgeMat = new THREE.MeshStandardMaterial({
    color: 0x22d3ee,
    emissive: 0x22d3ee,
    emissiveIntensity: 2.4,
    roughness: 0.3,
  });
  scene.add(new THREE.Mesh(edgeGeo, edgeMat));

  const chevrons = [];
  const chevronMat = new THREE.MeshStandardMaterial({
    color: 0x38bdf8,
    emissive: 0x38bdf8,
    emissiveIntensity: 3.2,
    roughness: 0.25,
  });
  for (let i = 0; i < 16; i += 1) {
    const t = (i + 0.5) / 16;
    const p = curve.getPointAt(t);
    const tangent = curve.getTangentAt(t);
    const mesh = new THREE.Mesh(new THREE.ConeGeometry(0.55, 1.6, 3), chevronMat);
    mesh.position.set(p.x, 0.35, p.z);
    mesh.lookAt(p.clone().add(tangent));
    mesh.rotateX(Math.PI / 2);
    scene.add(mesh);
    chevrons.push({ t, mesh });
  }

  const pads = [];
  const padMat = new THREE.MeshStandardMaterial({
    color: 0xf59e0b,
    emissive: 0xfbbf24,
    emissiveIntensity: 4.5,
    roughness: 0.2,
  });
  for (const t of [0.22, 0.63]) {
    const p = curve.getPointAt(t);
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(3.4, 0.12, 3.4), padMat);
    mesh.position.set(p.x, 0.08, p.z);
    scene.add(mesh);
    pads.push({ t, mesh, glow: true });
  }

  const up = new THREE.Vector3(0, 1, 0);
  function frame(t) {
    const u = ((t % 1) + 1) % 1;
    const position = curve.getPointAt(u);
    const tangent = curve.getTangentAt(u).normalize();
    const side = new THREE.Vector3().crossVectors(up, tangent).normalize();
    return { position, tangent, side };
  }

  function nearestOffset(worldPos) {
    let best = 0;
    let bestDist = Infinity;
    for (let i = 0; i < 80; i += 1) {
      const t = i / 80;
      const p = curve.getPointAt(t);
      const d = p.distanceToSquared(worldPos);
      if (d < bestDist) {
        bestDist = d;
        best = t;
      }
    }
    const f = frame(best);
    const rel = worldPos.clone().sub(f.position);
    return { t: best, offset: rel.dot(f.side), onTrack: Math.abs(rel.dot(f.side)) <= HALF_WIDTH };
  }

  return { curve, halfWidth: HALF_WIDTH, length, pads, chevrons, frame, nearestOffset };
}
"""

_VEHICLE_JS = """\
/** Hover craft + arcade handling. Integrity drains off-track; boost pads are risk/reward. */
import * as THREE from 'three';

function craftMesh() {
  const g = new THREE.Group();
  const hull = new THREE.Mesh(
    new THREE.BoxGeometry(1.55, 0.28, 2.9),
    new THREE.MeshStandardMaterial({
      color: 0x38bdf8,
      metalness: 0.72,
      roughness: 0.22,
      emissive: 0x0ea5e9,
      emissiveIntensity: 0.35,
    }),
  );
  hull.position.y = 0.2;
  const canopy = new THREE.Mesh(
    new THREE.SphereGeometry(0.42, 12, 8),
    new THREE.MeshStandardMaterial({
      color: 0xe0f2fe,
      metalness: 0.2,
      roughness: 0.1,
      emissive: 0x7dd3fc,
      emissiveIntensity: 0.8,
      transparent: true,
      opacity: 0.85,
    }),
  );
  canopy.position.set(0, 0.48, -0.15);
  const wingGeo = new THREE.BoxGeometry(2.6, 0.08, 0.7);
  const wingMat = new THREE.MeshStandardMaterial({
    color: 0x0f172a,
    metalness: 0.4,
    roughness: 0.4,
    emissive: 0x22d3ee,
    emissiveIntensity: 0.6,
  });
  const wing = new THREE.Mesh(wingGeo, wingMat);
  wing.position.set(0, 0.12, 0.35);
  const engine = new THREE.Mesh(
    new THREE.CylinderGeometry(0.18, 0.22, 0.5, 10),
    new THREE.MeshStandardMaterial({
      color: 0xf97316,
      emissive: 0xfb923c,
      emissiveIntensity: 3.2,
    }),
  );
  engine.rotation.x = Math.PI / 2;
  engine.position.set(0, 0.18, 1.35);
  g.add(hull, canopy, wing, engine);
  g.userData.engine = engine;
  return g;
}

export function createVehicle(track) {
  const mesh = craftMesh();
  const start = track.frame(0);
  mesh.position.copy(start.position);
  mesh.position.y = 0.7;
  return {
    mesh,
    t: 0,
    offset: 0,
    speed: 0,
    integrity: 100,
    boost: 0,
    failed: false,
    lastPad: -1,
  };
}

export function resetVehicle(v, track) {
  v.t = 0;
  v.offset = 0;
  v.speed = 0;
  v.integrity = 100;
  v.boost = 0;
  v.failed = false;
  v.lastPad = -1;
  const f = track.frame(0);
  v.mesh.position.copy(f.position);
  v.mesh.position.y = 0.7;
}

export function stepVehicle(v, track, input, dt) {
  if (v.failed) return;
  const throttle = input.throttle ? 1 : 0;
  const brake = input.brake ? 1 : 0;
  const steer = THREE.MathUtils.clamp(input.steer || 0, -1, 1);
  const drag = 0.62;
  const accel = throttle * 26 - brake * 34 - v.speed * drag;
  const boostPush = v.boost > 0 ? 22 : 0;
  v.speed = THREE.MathUtils.clamp(v.speed + (accel + boostPush) * dt, -6, 78);
  v.offset += steer * (7.5 + Math.abs(v.speed) * 0.1) * dt;
  const offTrack = Math.abs(v.offset) > track.halfWidth;
  if (offTrack) {
    v.integrity -= 32 * dt;
    v.speed *= 1 - 1.4 * dt;
  }
  v.boost = Math.max(0, v.boost - dt);
  track.pads.forEach((pad, idx) => {
    const along = Math.abs(((v.t - pad.t + 0.5) % 1) - 0.5);
    if (along < 0.018 && Math.abs(v.offset) < 3.2 && v.lastPad !== idx) {
      v.boost = 1.35;
      v.lastPad = idx;
    }
  });
  if (v.integrity <= 0) {
    v.integrity = 0;
    v.failed = true;
    v.speed = 0;
    return;
  }
  const dist = v.speed * dt;
  v.t = ((v.t + dist / track.length) % 1 + 1) % 1;
  const f = track.frame(v.t);
  const pos = f.position.clone().addScaledVector(f.side, v.offset);
  pos.y = 0.62 + Math.sin(performance.now() / 160) * 0.07;
  v.mesh.position.copy(pos);
  const look = pos.clone().add(f.tangent);
  v.mesh.lookAt(look);
  const engine = v.mesh.userData.engine;
  if (engine?.material) {
    engine.material.emissiveIntensity = 2.2 + Math.abs(v.speed) * 0.05 + (v.boost > 0 ? 2 : 0);
  }
}
"""

_WORLD_JS = """\
/** Sparse procedural city — crisp boxes, not photogrammetry. */
import * as THREE from 'three';

export function createWorld(scene) {
  scene.background = new THREE.Color(0x020617);
  scene.fog = new THREE.Fog(0x020617, 40, 160);
  scene.add(new THREE.HemisphereLight(0x94a3b8, 0x020617, 0.7));
  const sun = new THREE.DirectionalLight(0xbae6fd, 1.35);
  sun.position.set(30, 50, 12);
  scene.add(sun);
  const ground = new THREE.Mesh(
    new THREE.CircleGeometry(120, 48),
    new THREE.MeshStandardMaterial({ color: 0x020617, roughness: 1, metalness: 0 }),
  );
  ground.rotation.x = -Math.PI / 2;
  ground.position.y = -0.02;
  scene.add(ground);

  const geo = new THREE.BoxGeometry(1, 1, 1);
  const mat = new THREE.MeshStandardMaterial({
    color: 0x0f172a,
    roughness: 0.55,
    metalness: 0.2,
    emissive: 0x1d4ed8,
    emissiveIntensity: 0.25,
  });
  const dummy = new THREE.Object3D();
  const count = 70;
  const mesh = new THREE.InstancedMesh(geo, mat, count);
  for (let i = 0; i < count; i += 1) {
    const a = (i / count) * Math.PI * 2;
    const r = 62 + (i % 5) * 6;
    dummy.position.set(Math.cos(a) * r, 0, Math.sin(a) * r);
    const h = 6 + (i % 9) * 3.4;
    dummy.scale.set(3.2 + (i % 3), h, 3.2 + ((i * 3) % 4));
    dummy.position.y = h / 2;
    dummy.updateMatrix();
    mesh.setMatrixAt(i, dummy.matrix);
  }
  scene.add(mesh);
}
"""

_MAIN_JS = """\
/**
 * M0 vertical slice — one ship, one camera, one closed spline, fail, visible boost.
 * Extend this. Do not replace with Phaser or pixel-art templates.
 */
import * as THREE from 'three';
import { createRenderer } from './racer/boot.js';
import { createTrack } from './racer/track.js';
import { createVehicle, stepVehicle, resetVehicle } from './racer/vehicle.js';
import { createWorld } from './racer/world.js';

const canvas = document.getElementById('game');
const hudSpeed = document.getElementById('hud-speed');
const hudIntegrity = document.getElementById('hud-integrity');
const hudBoost = document.getElementById('hud-boost');
const hudBackend = document.getElementById('hud-backend');
const failEl = document.getElementById('fail');
const hintEl = document.getElementById('hint');

const keys = new Set();
const pointer = { down: false, x: 0.5, y: 0.5 };
const input = { throttle: false, brake: false, steer: 0 };

window.addEventListener('keydown', (e) => {
  keys.add(e.code);
  if (['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Space'].includes(e.code)) {
    e.preventDefault();
  }
  resumeAudio();
});
window.addEventListener('keyup', (e) => keys.delete(e.code));
function bindPointer(el) {
  const set = (ev) => {
    const r = el.getBoundingClientRect();
    const src = ev.touches ? ev.touches[0] : ev;
    pointer.x = (src.clientX - r.left) / r.width;
    pointer.y = (src.clientY - r.top) / r.height;
  };
  el.addEventListener('pointerdown', (ev) => { pointer.down = true; set(ev); resumeAudio(); });
  el.addEventListener('pointermove', (ev) => { if (pointer.down) set(ev); });
  el.addEventListener('pointerup', () => { pointer.down = false; });
  el.addEventListener('pointercancel', () => { pointer.down = false; });
}
bindPointer(canvas);

let audio = null;
function resumeAudio() {
  if (!audio) {
    try {
      const ctx = new AudioContext();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = 'sawtooth';
      osc.frequency.value = 72;
      gain.gain.value = 0.03;
      osc.connect(gain).connect(ctx.destination);
      osc.start();
      audio = { ctx, osc, gain };
    } catch {
      audio = { ctx: null };
    }
  }
  audio.ctx?.resume?.();
}

function readInput() {
  input.throttle = keys.has('KeyW') || keys.has('ArrowUp') || (pointer.down && pointer.x > 0.58);
  input.brake = keys.has('KeyS') || keys.has('ArrowDown') || keys.has('Space');
  let steer = 0;
  if (keys.has('KeyA') || keys.has('ArrowLeft')) steer -= 1;
  if (keys.has('KeyD') || keys.has('ArrowRight')) steer += 1;
  if (pointer.down && pointer.x < 0.42) steer += (pointer.x - 0.22) / 0.22;
  input.steer = THREE.MathUtils.clamp(steer, -1, 1);
}

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(62, 1, 0.1, 240);
camera.position.set(0, 8, 16);

async function boot() {
  const { renderer, backend } = await createRenderer(canvas);
  hudBackend.textContent = backend === 'webgpu' ? 'WebGPU' : 'WebGL fallback';
  createWorld(scene);
  const track = createTrack(scene);
  const vehicle = createVehicle(track);
  scene.add(vehicle.mesh);

  function resize() {
    const w = canvas.clientWidth || window.innerWidth;
    const h = canvas.clientHeight || window.innerHeight;
    camera.aspect = w / Math.max(1, h);
    camera.updateProjectionMatrix();
    renderer.setSize(w, h, false);
  }
  window.addEventListener('resize', resize);
  resize();

  function restart() {
    resetVehicle(vehicle, track);
    failEl.hidden = true;
  }

  window.addEventListener('keydown', (e) => {
    if (e.code === 'KeyR') restart();
  });
  failEl.addEventListener('click', restart);

  const clock = new THREE.Clock();
  const camPos = new THREE.Vector3();
  const lookAt = new THREE.Vector3();

  function tick() {
    const dt = Math.min(0.033, clock.getDelta());
    readInput();
    stepVehicle(vehicle, track, input, dt);
    const f = track.frame(vehicle.t);
    camPos.copy(vehicle.mesh.position).addScaledVector(f.tangent, -11).add(new THREE.Vector3(0, 4.2, 0));
    camera.position.lerp(camPos, 1 - Math.pow(0.0008, dt));
    lookAt.copy(vehicle.mesh.position).addScaledVector(f.tangent, 8);
    camera.lookAt(lookAt);
    if (audio?.osc) {
      audio.osc.frequency.value = 64 + Math.abs(vehicle.speed) * 2.4;
      audio.gain.gain.value = vehicle.failed ? 0 : 0.025 + Math.abs(vehicle.speed) * 0.0004;
    }
    hudSpeed.textContent = String(Math.round(Math.abs(vehicle.speed)));
    hudIntegrity.textContent = String(Math.round(vehicle.integrity));
    hudBoost.textContent = vehicle.boost > 0 ? 'TURBO' : '—';
    hudIntegrity.classList.toggle('danger', vehicle.integrity < 35 || Math.abs(vehicle.offset) > track.halfWidth);
    failEl.hidden = !vehicle.failed;
    hintEl.hidden = vehicle.speed > 4;
    window.__DAEDALUS_STATE__ = {
      speed: vehicle.speed,
      integrity: vehicle.integrity,
      failed: vehicle.failed,
      boost: vehicle.boost,
      t: vehicle.t,
      offset: vehicle.offset,
    };
    window.__GAME__ = window.__DAEDALUS_STATE__;
    renderer.render(scene, camera);
    requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
}

boot().catch((err) => {
  console.error(err);
  hudBackend.textContent = 'boot failed';
});
"""

_INDEX_HTML = """\
<!DOCTYPE html>
<html lang="bg">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no" />
  <meta name="apple-mobile-web-app-capable" content="yes" />
  <meta name="mobile-web-app-capable" content="yes" />
  <title>__NAME__</title>
  <style>
    * { box-sizing: border-box; }
    html, body { margin: 0; height: 100%; background: #020617; overflow: hidden; touch-action: none; }
    #game { width: 100%; height: 100%; display: block; }
    #hud {
      position: fixed; inset: 0; pointer-events: none;
      font-family: ui-monospace, "Cascadia Code", monospace;
      color: #e2e8f0; text-shadow: 0 1px 8px #020617;
    }
    .bar { position: absolute; top: 12px; left: 14px; display: flex; gap: 16px; font-size: 13px; letter-spacing: .08em; }
    .bar b { color: #22d3ee; }
    .danger { color: #fb7185 !important; }
    #hint { position: absolute; bottom: 18px; left: 50%; transform: translateX(-50%);
            font-size: 12px; color: #94a3b8; text-align: center; white-space: nowrap; }
    #fail {
      position: absolute; inset: 0; display: grid; place-items: center;
      background: rgba(2,6,23,.72); pointer-events: auto; cursor: pointer;
    }
    #fail[hidden] { display: none !important; }
    #fail strong { font-size: 28px; letter-spacing: .2em; color: #fb7185; }
    #fail span { display: block; margin-top: 8px; color: #94a3b8; font-size: 13px; }
    #rotate {
      display: none; position: fixed; inset: 0; background: #020617; color: #e2e8f0;
      place-items: center; text-align: center; padding: 2rem; z-index: 5;
    }
    @media (orientation: portrait) and (max-width: 900px) {
      #rotate { display: grid; }
    }
  </style>
</head>
<body>
  <canvas id="game"></canvas>
  <div id="hud">
    <div class="bar">
      <div>СКОРОСТ <b id="hud-speed">0</b></div>
      <div>ЦЯЛОСТ <b id="hud-integrity">100</b></div>
      <div>BOOST <b id="hud-boost">—</b></div>
      <div id="hud-backend">…</div>
    </div>
    <div id="hint">WASD / стрелки · газ има цена · светещите pads са turbo</div>
    <div id="fail" hidden>
      <div>
        <strong>ПРОВАЛ</strong>
        <span>Integrity 0 — off-track. R или тап за рестарт</span>
      </div>
    </div>
  </div>
  <div id="rotate">Обърни телефона — landscape, както FM1.</div>
  <script type="module" src="/src/main.js"></script>
</body>
</html>
"""

_VITE_CONFIG = """\
import { defineConfig } from 'vite'

export default defineConfig({
  base: './',
  server: { host: true },
})
"""

_README = """\
# __NAME__

M0 vertical slice (arcade craft / Formula Minus One path):

- One hover ship (procedural mesh, not a cube hero)
- One Catmull-Rom track + wayfinding chevrons
- Fail: integrity drains off-track
- Visible glowing boost pads
- Keyboard + touch; landscape lock on phones

Do **not** replace this with Phaser or pixel-art templates. Extend handling,
rivals, and HUD in later milestones. Hero GLB (Tripo/Meshy) can swap the craft
mesh later — keep `VehicleFactory` stats in `src/racer/vehicle.js`.
"""


def racer_package_json(slug: str) -> str:
    return json.dumps(
        {
            "name": slug or "racer",
            "private": True,
            "version": "0.0.1",
            "type": "module",
            "scripts": {
                "dev": "vite",
                "build": "vite build",
                "preview": "vite preview",
            },
            "dependencies": {"three": "^0.170.0"},
            "devDependencies": {"vite": "^5.4.0"},
        },
        indent=2,
    ) + "\n"


def racer_scaffold_files(name: str, slug: str, description: str = "") -> dict[str, str]:
    title = (name or slug or "Racer").strip()
    from .playtest_scenario import default_scenario, dumps_scenario

    files = {
        "index.html": _INDEX_HTML.replace("__NAME__", title),
        "vite.config.js": _VITE_CONFIG,
        "package.json": racer_package_json(slug),
        "src/main.js": _MAIN_JS,
        "src/racer/boot.js": _BOOT_JS,
        "src/racer/track.js": _TRACK_JS,
        "src/racer/vehicle.js": _VEHICLE_JS,
        "src/racer/world.js": _WORLD_JS,
        "playtest/scenario.json": dumps_scenario(default_scenario("vite", racer=True)),
        "README.md": _README.replace("__NAME__", title),
    }
    if description:
        files["README.md"] += f"\nBrief: {description.strip()[:400]}\n"
    return files


def _is_placeholder(path: Path) -> bool:
    if not path.is_file():
        return True
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return any(m in text for m in _PLACEHOLDER_MARKERS)


def write_racer_scaffold(
    pdir: Path,
    name: str,
    slug: str,
    description: str = "",
    *,
    overwrite: bool = False,
) -> list[str]:
    """Write M0 racer files. Returns relative paths written."""
    pdir = Path(pdir)
    pdir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for rel, content in racer_scaffold_files(name, slug, description).items():
        target = pdir / rel
        if target.exists() and not overwrite and not _is_placeholder(target):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(rel)
    if written:
        log.info("racer scaffold: %s → %s", slug, written)
    return written


def maybe_write_racer_scaffold(
    pdir: Path,
    kind: str,
    name: str,
    slug: str,
    description: str,
    *,
    studio_preset: str = "",
) -> list[str]:
    if kind != "vite":
        return []
    if studio_preset == "racing" or wants_racer_scaffold(name, description):
        return write_racer_scaffold(pdir, name, slug, description, overwrite=True)
    return []
