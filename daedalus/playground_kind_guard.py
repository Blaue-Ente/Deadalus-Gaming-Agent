"""Static platform guards for Playground autopilot builds.

Catches web-only APIs in Expo (and cross-kind mistakes) before/without LLM review.
"""
from __future__ import annotations

import re
from typing import Any

# (regex, human-readable violation[, re flags])
# When flags is omitted, re.IGNORECASE is used.
# HTML element check uses flags=0 (case-sensitive): JSX convention is that
# HTML tags are lowercase (<div>, <canvas>) while RN/Skia components are
# PascalCase (<Canvas>, <Image>, <View>, <Text>, <Pressable>).
_EXPO_FORBIDDEN: list[tuple[str, str] | tuple[str, str, int]] = [
    (r"\bwindow\.(AudioContext|webkitAudioContext|addEventListener|localStorage|requestAnimationFrame)\b",
     "window.* API — използвай React Native / expo-av / Animated, не browser globals"),
    (r"\b(document|localStorage|sessionStorage)\b",
     "DOM/storage API — AsyncStorage от @react-native-async-storage/async-storage"),
    (r"\bAudioContext\b",
     "Web Audio API — expo-av (Audio.Sound) или expo-haptics за feedback"),
    (r"\bwebAudioContext\b",
     "webAudioContext — expo-av, не Web Audio"),
    (r"getElementById|querySelector",
     "DOM селектори — само React Native View/Text компоненти"),
    (r"canvas\.getContext|getContext\(['\"]2d",
     "HTML Canvas 2D — @shopify/react-native-skia <Canvas>, expo-gl, или чист RN layout"),
    (r"requestAnimationFrame",
     "requestAnimationFrame — useFrameCallback (Skia) или Animated.loop / setInterval"),
    (r"PanGestureHandler|GestureHandlerRootView",
     "gesture-handler — PanResponder от react-native"),
    (r"from\s+['\"]react-native-gesture-handler",
     "gesture-handler забранен в Expo Go"),
    (r"from\s+['\"]react-native-reanimated",
     "reanimated забранен в Expo Go — Animated от react-native"),
    # Case-sensitive (flags=0): <div>/<canvas> lowercase = HTML (invalid in RN),
    # but <Canvas> PascalCase = @shopify/react-native-skia component (valid).
    (r"<(div|span|p|button|img|canvas|section|header|footer)\b",
     "HTML елемент — View, Text, TouchableOpacity, Image",
     0),
    (r"\bvite\.config",
     "Vite config — Expo проект, не Vite"),
    (r"ReactDOM\.createRoot",
     "ReactDOM web entry — Expo App.jsx + registerRootComponent"),
]

_UNITY_FORBIDDEN: list[tuple[str, str]] = [
    (r"import\s+React|from\s+['\"]react", "React — Unity uGUI/UI Toolkit only"),
    (r"<div\b|<span\b", "HTML elements — Unity UI components"),
    (r"expo-router|react-native", "React Native — Unity C# project"),
    (r"def\s+\w+\(.*\):", "Python in gameplay file — use C#"),
]

_KIND_SCANNERS: dict[str, list[tuple[str, str]]] = {
    "expo": _EXPO_FORBIDDEN,
    "unity": _UNITY_FORBIDDEN,
}

def scan_file(kind: str, path: str, content: str) -> list[dict[str, Any]]:
    """Return list of violations for one file."""
    patterns = _KIND_SCANNERS.get(kind) or []
    if not patterns or not content:
        return []
    out: list[dict[str, Any]] = []
    for item in patterns:
        rx, msg = item[0], item[1]
        flags = item[2] if len(item) > 2 else re.IGNORECASE  # type: ignore[misc]
        m = re.search(rx, content, flags)
        if m:
            out.append({
                "path": path,
                "match": m.group(0)[:80],
                "message": msg,
            })
    return out


def scan_files(kind: str, files: dict[str, str]) -> list[dict[str, Any]]:
    """Scan all files for platform violations."""
    violations: list[dict[str, Any]] = []
    for path, content in files.items():
        violations.extend(scan_file(kind, path, content or ""))
    return violations


def format_review_block(violations: list[dict[str, Any]]) -> str:
    """Structured review text matching autopilot parser expectations."""
    if not violations:
        return ""
    # group by path
    by_path: dict[str, list[str]] = {}
    for v in violations[:8]:
        by_path.setdefault(v["path"], []).append(f"{v['match']!r} → {v['message']}")
    detail = "; ".join(
        f"{p}: " + " | ".join(msgs)
        for p, msgs in by_path.items()
    )
    return f"РЕЗУЛТАТ: ПРОБЛЕМ\nПРОБЛЕМ: Кодът нарушава платформата — {detail}"
