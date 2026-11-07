"""Expo autopilot quality guards — file size, truncation, web patterns in RN code."""
from __future__ import annotations

import re
from typing import Any

# ~250 lines × ~40 chars — reject monster components before LLM review loop
MAX_EXPO_FILE_CHARS = 7500
MAX_EXPO_FILE_LINES = 280

_TRUNCATION_MARKERS = re.compile(
    r"(?:Haptics\.\s*$|"
    r"impactAsync\s*\(\s*$|"
    r"//\s*Super charge[^\\n]*$)",
    re.MULTILINE | re.IGNORECASE,
)

# (regex, message, flags) — flags default to re.IGNORECASE when omitted.
# HTML element check uses flags=0 (case-sensitive): JSX convention is that
# HTML tags are lowercase (<div>, <canvas>) while RN components are PascalCase
# (<Canvas> from @shopify/react-native-skia, <Image> from react-native, etc.).
_EXPO_WEB_IN_RN: list[tuple[str, str] | tuple[str, str, int]] = [
    (r"\brequestAnimationFrame\s*\(",
     "requestAnimationFrame — ползвай useFrameCallback (Skia) или setInterval"),
    (r"PanGestureHandler|GestureHandlerRootView",
     "react-native-gesture-handler — ползвай PanResponder от react-native"),
    (r"from\s+['\"]react-native-gesture-handler",
     "gesture-handler е забранен в Expo Go — PanResponder"),
    (r"from\s+['\"]react-native-reanimated",
     "reanimated е забранен в Expo Go — Animated от react-native"),
    (r"transform:\s*['\"][^'\"]*(?:rotateX|rotateY|perspective|rotateZ)",
     "CSS transform string — RN използва transform: [{ rotate: '45deg' }] масив"),
    (r"linear-gradient|radial-gradient",
     "CSS gradient — StyleSheet solid colors или Image background"),
    # Case-sensitive: <div>/<canvas> (lowercase HTML) is invalid in RN,
    # but <Canvas> (PascalCase from @shopify/react-native-skia) is valid.
    (r"<(div|span|p|button|img|canvas|section|header|footer)\b",
     "HTML елемент — View, Text, Pressable, Image",
     0),
]


def _brace_balance(content: str) -> int:
    """Rough brace balance (ignores strings). Negative = likely truncated."""
    depth = 0
    for ch in content:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
    return depth


def scan_expo_file(path: str, content: str) -> list[dict[str, Any]]:
    violations: list[dict[str, Any]] = []
    if not content:
        return violations

    n_chars = len(content)
    n_lines = content.count("\n") + 1
    if n_chars > MAX_EXPO_FILE_CHARS:
        violations.append({
            "path": path,
            "match": f"{n_chars} chars",
            "message": (
                f"Файлът е твърде голям ({n_chars} chars) — раздели на по-малки компоненти "
                f"(max ~{MAX_EXPO_FILE_LINES} реда)"
            ),
        })
    if n_lines > MAX_EXPO_FILE_LINES:
        violations.append({
            "path": path,
            "match": f"{n_lines} lines",
            "message": f"Твърде много редове ({n_lines}) — опрости или раздели файла",
        })

    tail = content.rstrip()[-400:]
    if _TRUNCATION_MARKERS.search(tail):
        violations.append({
            "path": path,
            "match": "truncated tail",
            "message": "Кодът изглежда отрязан в края — завърши функцията/блока",
        })
    elif _brace_balance(content) != 0:
        violations.append({
            "path": path,
            "match": "unclosed {",
            "message": "Незатворени {{ — вероятно truncated файл",
        })

    for item in _EXPO_WEB_IN_RN:
        rx, msg = item[0], item[1]
        flags = item[2] if len(item) > 2 else re.IGNORECASE  # type: ignore[misc]
        m = re.search(rx, content, flags)
        if m:
            violations.append({
                "path": path,
                "match": m.group(0)[:60],
                "message": msg,
            })

    return violations


def scan_expo_files(files: dict[str, str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for path, content in files.items():
        out.extend(scan_expo_file(path, content or ""))
    return out


def format_expo_review_block(violations: list[dict[str, Any]]) -> str:
    if not violations:
        return ""
    detail = "; ".join(
        f"{v['path']}: {v['message']}" for v in violations[:6]
    )
    return f"РЕЗУЛТАТ: ПРОБЛЕМ\nПРОБЛЕМ: Expo quality — {detail}"


def expo_review_system_addendum() -> str:
    """Extra rules so LLM reviewer stops rejecting valid RN components."""
    return (
        "EXPO REVIEW — ЗАДЪЛЖИТЕЛНО:\n"
        "• View, Text, Image, Animated, Pressable, TouchableOpacity, FlatList, StyleSheet, "
        "PanResponder — ВАЛИДНИ и ОЧАКВАНИ за Expo. НЕ ги отхвърляй.\n"
        "• require('../assets/hero.png') или require('../../assets/x.png') — ВАЛИДНО за спрайтове.\n"
        "• useNativeDriver: true на transform/opacity Animated — ВАЛИДНО.\n"
        "━━━ @shopify/react-native-skia — ОФИЦИАЛЕН EXPO ПАКЕТ (ЗАДЪЛЖИТЕЛНО) ━━━\n"
        "• import { Canvas, Circle, Path, Group, Rect, Line } from '@shopify/react-native-skia' — ОДОБРЕНО.\n"
        "• import { useFrameCallback, useValue, useDerivedValue } from '@shopify/react-native-skia' — ОДОБРЕНО.\n"
        "• <Canvas style={{flex:1}}> е React Native компонент, НЕ HTML <canvas> — ОДОБРЕНО.\n"
        "• <Circle>, <Path>, <Group>, <Rect> вътре в <Canvas> — ОДОБРЕНО (Skia JSX, не HTML/SVG).\n"
        "• useFrameCallback(() => { /* game loop */ }) — ОДОБРЕНО за 60fps (замества rAF).\n"
        "• НЕ отхвърляй код само защото съдържа 'Canvas', 'Skia', '@shopify/react-native-skia'.\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "• Отхвърли САМО: truncated код, HTML lowercase тагове (<div>/<span>/<canvas>/<img>), "
        "requestAnimationFrame директно (не useFrameCallback), "
        "gesture-handler/reanimated, CSS gradient strings, window/document/AudioContext, "
        "файл >280 реда незавършен.\n"
        "• Ако файлът е синтактично завършен и ползва RN + Skia компоненти → ОДОБРЕН.\n"
    )
