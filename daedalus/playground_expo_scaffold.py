"""Rich Expo scaffold — Expo Router + Query + Zustand + Icons (NativeBot-inspired)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .playground import project_dir

log = logging.getLogger(__name__)

_ICONS_TSX = '''\
import Svg, { Path } from "react-native-svg";

type IconProps = { size?: number; color?: string };

export function HomeIcon({ size = 24, color = "#fff" }: IconProps) {
  return (
    <Svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={2}>
      <Path d="M3 9l9-7 9 7v11a2 2 0 01-2 2H5a2 2 0 01-2-2z" />
      <Path d="M9 22V12h6v10" />
    </Svg>
  );
}

export function UserIcon({ size = 24, color = "#fff" }: IconProps) {
  return (
    <Svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={2}>
      <Path d="M20 21v-2a4 4 0 00-4-4H8a4 4 0 00-4 4v2" />
      <Path d="M12 11a4 4 0 100-8 4 4 0 000 8z" />
    </Svg>
  );
}

export function PlusIcon({ size = 24, color = "#fff" }: IconProps) {
  return (
    <Svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={2}>
      <Path d="M12 5v14M5 12h14" />
    </Svg>
  );
}
'''

EXPO_SCAFFOLD: dict[str, str] = {
    "app.json": """\
{{
  "expo": {{
    "name": "{name}",
    "slug": "{slug}",
    "scheme": "{slug}",
    "version": "1.0.0",
    "orientation": "portrait",
    "userInterfaceStyle": "dark",
    "platforms": ["ios", "android", "web"],
    "web": {{ "bundler": "metro" }},
    "plugins": ["expo-router"]
  }}
}}
""",
    "package.json": """\
{{
  "name": "{slug}",
  "version": "1.0.0",
  "main": "expo-router/entry",
  "scripts": {{
    "start": "expo start",
    "web": "expo start --web",
    "build:web": "expo export --platform web"
  }},
  "dependencies": {{
    "expo": "~51.0.0",
    "expo-constants": "~16.0.2",
    "expo-linking": "~6.3.1",
    "expo-router": "~3.5.23",
    "expo-status-bar": "~1.12.1",
    "react": "18.2.0",
    "react-dom": "18.2.0",
    "react-native": "0.74.5",
    "react-native-safe-area-context": "4.10.5",
    "react-native-screens": "3.31.1",
    "react-native-web": "~0.19.13",
    "react-native-svg": "15.2.0",
    "@tanstack/react-query": "^5.62.0",
    "zustand": "^5.0.0"
  }},
  "devDependencies": {{
    "@babel/core": "^7.24.0",
    "@types/react": "~18.2.79",
    "babel-preset-expo": "~11.0.0",
    "babel-plugin-module-resolver": "^5.0.2",
    "typescript": "~5.3.0"
  }}
}}
""",
    "babel.config.js": """\
module.exports = function (api) {
  api.cache(true);
  return {
    presets: ['babel-preset-expo'],
    plugins: [
      ['module-resolver', { alias: { '@': './src' } }],
      'expo-router/babel',
    ],
  };
};
""",
    "tsconfig.json": """\
{
  "extends": "expo/tsconfig.base",
  "compilerOptions": {
    "strict": true,
    "paths": { "@/*": ["./src/*"] }
  },
  "include": ["**/*.ts", "**/*.tsx", ".expo/types/**/*.ts", "expo-env.d.ts"]
}
""",
    "src/app/_layout.tsx": """\
import { Stack } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { View } from 'react-native';

const queryClient = new QueryClient();

export default function RootLayout() {
  return (
    <QueryClientProvider client={queryClient}>
      <View style={{ flex: 1, backgroundColor: '#0A0A0A' }}>
        <StatusBar style="light" />
        <Stack screenOptions={{ headerShown: false }} />
      </View>
    </QueryClientProvider>
  );
}
""",
    "src/app/index.tsx": """\
import { useState } from 'react';
import { View, Text, Pressable, FlatList, StyleSheet } from 'react-native';
import { HomeIcon } from '@/components/Icons';

const MOCK_ITEMS = [
  { id: '1', title: 'Welcome', subtitle: '{name}' },
  { id: '2', title: 'Ready to build', subtitle: '{description}' },
  { id: '3', title: 'Expo Router', subtitle: 'src/app/index.tsx' },
];

export default function Home() {
  const [count, setCount] = useState<number>(0);
  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <HomeIcon size={28} color="#38bdf8" />
        <Text style={styles.title}>{name}</Text>
        <Text style={styles.desc}>{description}</Text>
      </View>
      <FlatList
        data={MOCK_ITEMS}
        keyExtractor={(item) => item.id}
        renderItem={({ item }) => (
          <View style={styles.card}>
            <Text style={styles.cardTitle}>{{item.title}}</Text>
            <Text style={styles.cardSub}>{{item.subtitle}}</Text>
          </View>
        )}
        style={{ flex: 1, width: '100%' }}
      />
      <Pressable style={styles.btn} onPress={() => setCount((c) => c + 1)}>
        <Text style={styles.btnText}>Tap · {{count}}</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#0A0A0A', padding: 24, alignItems: 'center' },
  header: { alignItems: 'center', marginBottom: 16 },
  title: { fontSize: 26, fontWeight: '700', color: '#38bdf8', marginTop: 8 },
  desc: { fontSize: 14, color: '#94a3b8', textAlign: 'center', marginTop: 4 },
  card: {
    backgroundColor: '#141414', borderRadius: 12, padding: 16, marginBottom: 10,
    borderWidth: 1, borderColor: '#262626', width: '100%',
  },
  cardTitle: { color: '#fff', fontSize: 16, fontWeight: '600' },
  cardSub: { color: '#888', fontSize: 13, marginTop: 4 },
  btn: {
    backgroundColor: '#0ea5e9', paddingHorizontal: 28, paddingVertical: 14,
    borderRadius: 12, marginTop: 12,
  },
  btnText: { color: '#fff', fontSize: 16, fontWeight: '600' },
});
""",
    "src/components/Icons.tsx": _ICONS_TSX,
    "expo-env.d.ts": """\
/// <reference types="expo/types" />
""",
    "metro.config.js": """\
const { getDefaultConfig } = require('expo/metro-config');

const config = getDefaultConfig(__dirname);
module.exports = config;
""",
    "src/lib/store.ts": """\
import { create } from 'zustand';

type AppState = {
  ready: boolean;
  setReady: (v: boolean) => void;
};

export const useAppStore = create<AppState>((set) => ({
  ready: true,
  setReady: (v) => set({ ready: v }),
}));
""",
}


def _apply_template_ctx(content: str, name: str, slug: str, description: str) -> str:
    """Substitute only known placeholders — JSX ``{expr}`` stays intact."""
    desc = description or f"Project: {name}"
    return (
        content.replace("{name}", name)
        .replace("{slug}", slug)
        .replace("{description}", desc)
    )


def ensure_expo_router_scaffold(
    slug: str,
    *,
    name: str = "",
    description: str = "",
) -> list[str]:
    """Write missing Expo Router files into an existing project (non-destructive)."""
    root = project_dir(slug)
    pname = name or slug
    written: list[str] = []
    for rel, raw in EXPO_SCAFFOLD.items():
        target = root / rel
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if rel.endswith(".json"):
            try:
                text = raw.format(name=pname, slug=slug, description=description or f"Project: {pname}")
            except (KeyError, ValueError):
                text = _apply_template_ctx(raw, pname, slug, description)
        else:
            text = _apply_template_ctx(raw, pname, slug, description)
        target.write_text(text, encoding="utf-8")
        written.append(rel)
    if written:
        log.info("expo scaffold patched %s: %s", slug, written)
    return written


# Required for babel + expo-router; merged into existing package.json on repair.
_EXPO_REQUIRED_DEPS: dict[str, str] = {
    "expo-constants": "~16.0.2",
    "expo-linking": "~6.3.1",
}
_EXPO_REQUIRED_DEV_DEPS: dict[str, str] = {
    "babel-preset-expo": "~11.0.0",
    "@types/react": "~18.2.79",
}


def repair_expo_package_json(root: Path) -> list[str]:
    """Patch missing Expo deps in an existing package.json (non-destructive)."""
    pkg_path = root / "package.json"
    if not pkg_path.exists():
        return []
    try:
        data = json.loads(pkg_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    changed: list[str] = []
    deps = data.setdefault("dependencies", {})
    dev_deps = data.setdefault("devDependencies", {})
    for name, ver in _EXPO_REQUIRED_DEPS.items():
        if name not in deps:
            deps[name] = ver
            changed.append(f"dep:{name}")
    for name, ver in _EXPO_REQUIRED_DEV_DEPS.items():
        if name not in dev_deps:
            dev_deps[name] = ver
            changed.append(f"dev:{name}")
    if (data.get("main") or "") != "expo-router/entry" and "expo-router" in json.dumps(deps):
        data["main"] = "expo-router/entry"
        changed.append("main:expo-router/entry")
    if changed:
        pkg_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        log.info("expo package.json repaired: %s", changed)
    for rel in ("expo-env.d.ts", "metro.config.js"):
        if rel not in EXPO_SCAFFOLD:
            continue
        target = root / rel
        if not target.exists():
            target.write_text(EXPO_SCAFFOLD[rel], encoding="utf-8")
            changed.append(rel)
    return changed


def is_expo_router_project(root: Path) -> bool:
    pkg = root / "package.json"
    if not pkg.exists():
        return False
    try:
        data = json.loads(pkg.read_text(encoding="utf-8"))
        main = (data.get("main") or "")
        return "expo-router" in main or (root / "src/app").is_dir()
    except Exception:
        return (root / "src/app").is_dir()
