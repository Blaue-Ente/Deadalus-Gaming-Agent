---
name: phaser_vite
description: Phaser 3 + Vite browser game. Boot from src/main.js with new Phaser.Game. Never leave the Vite #app scaffold if index.html has a canvas.
---

# SKILL: Phaser 3 + Vite

**When**: Playground `kind=vite` (or vite-react) and the slice is a 2D web game. Not Godot. Not a single-file canvas arcade unless the owner asked for snake/tetris/pong.

**Entry contract**

- `package.json` lists `phaser` ^3.80 and `vite`. `npm install` then `vite build` (or the Playground runner) before claiming Play.
- `index.html` has a mount (`<canvas id="…">` or `<div id="game">`). Do not ship an empty `#app` hero.
- **`src/main.js` must boot the game.** `new Phaser.Game({ type: Phaser.WEBGL, parent: 'game', scene: [Boot, Menu, Play] })` (or Pixi `ticker`). **Do not use `Phaser.AUTO` with a pre-created `<canvas>`** — Vite ESM treats that as a custom environment and throws `Must set explicit renderType`. Prefer `parent: 'game'` only. Overwrite the Vite scaffold (`#app`.innerHTML / "Hello Vite") if `index.html` already has a canvas or `#game`. Leaving the scaffold is a **blank screen**.
- Every scene file that mentions `Phaser` must `import Phaser from 'phaser'`. A missing import compiles in Vite and then dies as one uncaught in Chromium.
- Scenes live under `src/scenes/`. Boot may generate canvas textures when `generate_image` fails — do not retry the same image call twice.
- Reference real `assets/*.png` via `this.load.image` / `this.add.image`. Procedural Boot textures are an honest prototype, not store art.

**Loop**

Phaser's `new Phaser.Game` **is** the animation loop. Do not also require `requestAnimationFrame` in `src/main.js`.

**Verify**

Kernel visual proof after writes. `production({action: pipeline, slug})` walks web QA. A compile is not Play. A blank Chromium frame is not done.
