"""Shared helpers for playground stage builds (guided + autopilot)."""
from __future__ import annotations

import re
from typing import Any


def parse_build_files(text: str) -> dict[str, str]:
    """Parse ===FILE: path===\\n...\\n===END=== blocks into {path: content}."""
    files: dict[str, str] = {}
    pattern = re.compile(
        r"===FILE:\s*(.+?)\s*===\r?\n(.*?)\r?\n?===END===",
        re.DOTALL,
    )
    for m in pattern.finditer(text):
        path = m.group(1).strip().lstrip("/")
        if path:
            files[path] = m.group(2)
    if not files:
        stripped = text.strip()
        if "<!DOCTYPE" in stripped or "<html" in stripped.lower():
            start = stripped.lower().find("<!doctype")
            if start == -1:
                start = stripped.lower().find("<html")
            files["index.html"] = stripped[start:]
    return files


def file_preview(content: str, head: int = 4000, tail: int = 2000) -> str:
    if len(content) <= head + tail:
        return content
    omitted = len(content) - head - tail
    return content[:head] + f"\n...[{omitted} chars omitted]...\n" + content[-tail:]


def extract_partial(raw: str, fname: str) -> str:
    for pat in (
        rf"===\s*FILE\s*:\s*{re.escape(fname)}\s*===\s*\n(.*)",
        r"===\s*FILE\s*:[^\n]+\n(.*)",
        r"```[a-z]*\s*\n(.*)",
    ):
        m = re.search(pat, raw, re.DOTALL | re.IGNORECASE)
        if m:
            part = m.group(1)
            part = re.sub(r"\n?===\s*END\s*===.*$", "", part, flags=re.DOTALL)
            return part.rstrip()
    return raw.strip()


def extract_llm_text(res: dict[str, Any]) -> str:
    if not isinstance(res, dict):
        return ""
    for blk in res.get("content") or []:
        t = blk.get("text") or ""
        if t:
            return t
    for ch in res.get("choices") or []:
        t = (ch.get("message") or {}).get("content") or ""
        if t:
            return t
    return res.get("reply") or ""


def kind_forbidden_block(kind: str, *, is_game: bool = False) -> str:
    blocks = {
        "vite-react": (
            "ЗАБРАНЕНО: babel.config.js, metro.config.js, app.json (Expo), expo-*, "
            "react-native-*, StyleSheet.create, View/Text (RN). Само React+Vite+JSX."
        ),
        "vite": (
            "ЗАБРАНЕНО: JSX (.jsx), React, babel.config.js, expo-*. "
            "За ИГРИ: Phaser 3 + npm е ВАЛИДНО (import Phaser, Phaser.Scene, load.image)."
            if is_game else
            "ЗАБРАНЕНО: JSX (.jsx), React, babel.config.js, expo-*. Само Vanilla JS + Vite."
        ),
        "static": (
            "ЗАБРАНЕНО: npm пакети без bundler, babel.config.js, package.json. "
            "Само HTML+CSS+JS."
        ),
        "fastapi": "ЗАБРАНЕНО: React, Vite, Expo. Само Python FastAPI.",
        "node": "ЗАБРАНЕНО: Python, FastAPI, Expo. Само Node.js.",
        "expo": (
            "ЗАБРАНЕНО: HTML lowercase тагове (div/span/canvas), Vite, window/document, "
            "requestAnimationFrame директно, react-native-reanimated, gesture-handler. "
            "ОСНОВНО: View/Text/Pressable/StyleSheet + expo-router src/app/. "
            "ЗА ИГРИ: @shopify/react-native-skia (<Canvas>, <Circle>, <Path>, useFrameCallback) "
            "е ОФИЦИАЛЕН Expo пакет — ВАЛИДНО и ПРЕПОРЪЧИТЕЛНО."
        ),
        "streamlit": "ЗАБРАНЕНО: HTML templates, Flask, React. Само Streamlit.",
        "unity": (
            "ЗАБРАНЕНО: React, JSX, npm package.json main entry, Expo, HTML gameplay, "
            "САМО: C# under Assets/_Project/Code/, Unity APIs. "
            "След build: NEED_PLAYGROUND_PUSH (GitLab/GitHub) → Unity Hub локално."
        ),
    }
    return blocks.get(kind, "")


TRUNCATION_KEYWORDS = (
    "незатворен", "непълен", "incomplete", "unclosed", "truncat",
    "отрязан", "липсва затварящ", "token limit",
)
