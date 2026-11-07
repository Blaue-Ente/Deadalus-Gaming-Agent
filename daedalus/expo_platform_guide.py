"""Expo / React Native platform guide — NativeBot-inspired rules for Playground.

Layered prompts: stack constraints, offline-first, host-owned preview, mock data.
"""
from __future__ import annotations

EXPO_BUILD_RULES = """
EXPO / REACT NATIVE RULES (NativeBot-aligned):
• Expo Router: routes in src/app/ — src/app/_layout.tsx is root, src/app/index.tsx is home.
• Stack: Expo SDK 51, npm, expo-router, React Query (@tanstack/react-query), Zustand.
• Styling: StyleSheet.create — НЕ HTML/CSS файлове, НЕ Tailwind className (освен ако NativeWind е вече в проекта).
• Icons: import от @/components/Icons (pre-built SVG) — не инсталирай lucide от нулата.
• Анимации: Animated от react-native. НЕ инсталирай react-native-reanimated или react-native-gesture-handler (crash в Expo Go).
• Компоненти: View, Text, Pressable, Image, FlatList, StyleSheet — НЕ div/span/button/img (lowercase HTML).
• ЗАБРАНЕНО в код: window, document, AudioContext, canvas.getContext('2d'), localStorage, vite, index.html#root.
• Звук: expo-av (Audio.Sound) — не Web Audio API.
• NO BACKEND DEFAULT: без Supabase/auth освен ако потребителят го иска — AsyncStorage + mock data.
• Mock data: 5–15 реалистични записа на първо зареждане; picsum.photos за placeholder снимки.
• Pressable пред TouchableOpacity; explicit useState<Type>; празен string в RN е text node — ползвай null.
• AGENT НЕ ПУСКА: expo start, expo export, eas build — Playground host ги управлява.
• След промени в package.json: npm install (host/autopilot ще го пусне).

━━━ ЗА ИГРИ С @shopify/react-native-skia ━━━
• @shopify/react-native-skia е ОФИЦИАЛНО ПОДДЪРЖАН Expo пакет — ОДОБРЕНО.
• <Canvas> от Skia е React Native компонент — НЕ е HTML canvas.
• useFrameCallback от Skia — ОДОБРЕНО за 60fps game loop (НЕ requestAnimationFrame директно).
• Skia компоненти (<Canvas>, <Circle>, <Path>, <Group>, <Rect>, <Text as SkText>) — ОДОБРЕНО.
• Skia useValue/useDerivedValue за shared values в game loop — ОДОБРЕНО.
• requestAnimationFrame директно (без Skia wrapper) — ЗАБРАНЕНО в Expo.
"""

EXPO_PLAN_ADDENDUM = """
━━━ EXPO BUILD GUIDE ━━━
Файлова структура:
  • app.json + package.json + babel.config.js
  • src/app/_layout.tsx — root Stack + QueryClientProvider
  • src/app/index.tsx — home / game screen entry
  • src/components/ — по ЕДИН компонент на етап (Hero.jsx, Enemy.jsx, Joystick.jsx)
  • src/lib/ — helpers, zustand stores
КРИТИЧНО за игри:
  • MAX 1-2 файла на етап, MAX ~200 реда на файл — НЕ монолитен GameArena.jsx
  • PanResponder за joystick — НЕ gesture-handler
  • Animated.loop или useFrameCallback (Skia) — НЕ requestAnimationFrame директно
  • uses_visuals=true за hero/enemy икони в assets/
"""

EXPO_GAME_PLAN_ADDENDUM = """
━━━ EXPO GAME PLAN (задължително гранулиране) ━━━

── Вариант A: Skia Canvas игра (@shopify/react-native-skia) ──
  1) src/game/entities.ts — типове (Position, Asteroid, Laser, Particle)
  2) src/game/GameEngine.tsx — <Canvas> + useFrameCallback (MAX ~200 реда; само loop + state)
  3) src/components/ShipRenderer.tsx — Path drawing в Skia (<Path path=..>)
  4) src/components/AsteroidRenderer.tsx — <Circle> или Path asteroid
  5) src/components/HUD.tsx — View/Text overlay (score, lives) ИЗВЪН Canvas
  6) src/app/index.tsx — свързва GameEngine + HUD

── Вариант B: View/Animated игра (без Skia) ──
  1) src/components/Hero.jsx (~150 реда)
  2) src/components/Enemy.jsx
  3) src/components/Joystick.jsx
  4) src/components/GameArena.jsx (само layout, вика Hero/Enemy/Joystick)
  5) src/app/game.tsx или index.tsx — свързва компонентите

НЕ събирай цялата игра в един файл.
"""


def expo_build_rules() -> str:
    return EXPO_BUILD_RULES.strip()


def autopilot_plan_addendum(kind: str, *, is_game: bool = False) -> str:
    if kind != "expo":
        return ""
    base = EXPO_PLAN_ADDENDUM
    if is_game:
        base += EXPO_GAME_PLAN_ADDENDUM
    return base


def expo_fix_addendum() -> str:
    """Extra hints when expo compile/smoke test fails."""
    return (
        "EXPO FIX HINTS:\n"
        "- Провери expo-router entry: package.json main = expo-router/entry\n"
        "- babel.config.js: expo-router/babel plugin + @ alias към ./src\n"
        "- Несъвместими peer deps → коригирай версии в package.json (без --legacy-peer-deps в кода)\n"
        "- Import paths: @/components/... от src/\n"
        "- Не ползвай web-only API; Image от react-native за assets/*.png\n"
    )


def brief_enrichment(kind: str, name: str = "", criteria: str = "") -> str:
    if kind != "expo":
        return ""
    return f"\n\n{EXPO_BUILD_RULES}\n"


def kind_detect_hint() -> str:
    return (
        "• EXPO: мобилно приложение, App Store/Play Store, React Native, touch UI, expo-av.\n"
    )
