---
name: capacitor-ship
description: Wrap a Vite/static Playground web game as iOS + Android with Capacitor — same canvas, not an Expo rewrite. Ionic UI is optional for apps only.
---

# SKILL: Capacitor ship

**When**: owner says Capacitor, Ionic, “same game on iOS and Android”, hybrid web wrap, or Playground kind is `vite` / `vite-react` / `static` and they want a store-direction binary without rewriting to React Native.

**How**:
1. Keep the existing kind. Do **not** switch to `expo`. The game loop stays Phaser / Pixi / Three / canvas.
2. `init_capacitor` writes `capacitor.config.json` (or `hybrid/capacitor.config.json`), `studio/capacitor/`, `src/native/daedalus-cap.js`, `scripts/capacitor-sync.sh`.
3. Owner builds locally: `bash scripts/capacitor-sync.sh`, then once `npx cap add android` / `npx cap add ios` (Android SDK / Xcode). Cloud does not run those.
4. Optional Ionic UI (`with_ionic`) is for lists, forms, settings — never sprites or `requestAnimationFrame`.
5. Expo slugs stay on EAS. Godot / Unity export from their Editors.
6. PakePlus-iOS remains an iOS-only fallback under the hybrid_mobile preset.

**Anti-patterns**:
- Do not make Ionic the game engine.
- Do not wrap Daedalus Studio (FastAPI + SQLite + shell) in Capacitor. Windows already uses WebView2.
- Do not claim store-ready because `capacitor.config.json` exists.
- Do not add Capacitor on Expo / Unity / Godot projects.
- Do not rewrite a working Vite game to React Native “so it can go mobile”.
