"""Static checks for app/static/i18n.js: EN/BG key parity and canonical English defaults."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
I18N_PATH = ROOT / "app" / "static" / "i18n.js"


def _brace_inner_after_anchor(text: str, anchor: str) -> str:
    idx = text.index(anchor) + len(anchor)
    if text[idx] != "{":
        raise ValueError(f"expected '{{' after {anchor!r}")
    depth = 1
    i = idx + 1
    start = i
    while i < len(text) and depth:
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        i += 1
    if depth != 0:
        raise ValueError("unbalanced braces in i18n.js")
    return text[start : i - 1]


def _keys_in_locale_block(inner: str) -> set[str]:
    return set(re.findall(r'^\s+"([^"]+)":\s*', inner, flags=re.MULTILINE))


@pytest.fixture(scope="module")
def i18n_text() -> str:
    return I18N_PATH.read_text(encoding="utf-8")


def test_i18n_file_exists() -> None:
    assert I18N_PATH.is_file()


def test_i18n_en_bg_key_parity(i18n_text: str) -> None:
    en_inner = _brace_inner_after_anchor(i18n_text, "en: ")
    bg_inner = _brace_inner_after_anchor(i18n_text, "bg: ")
    en_keys = _keys_in_locale_block(en_inner)
    bg_keys = _keys_in_locale_block(bg_inner)
    only_en = sorted(en_keys - bg_keys)
    only_bg = sorted(bg_keys - en_keys)
    assert not only_en, f"keys only in en: {only_en[:40]}"
    assert not only_bg, f"keys only in bg: {only_bg[:40]}"


def _js_string_value(locale_inner: str, key: str) -> str:
    line_m = re.search(rf'"{re.escape(key)}":\s*"((?:[^"\\]|\\.)*)"', locale_inner)
    assert line_m, f"missing {key}"
    return line_m.group(1)


def test_i18n_studio_greeting_english_canonical(i18n_text: str) -> None:
    """Startup greeting: English source; Bulgarian is a translation (not shown for default EN)."""
    en_inner = _brace_inner_after_anchor(i18n_text, "en: ")
    val = _js_string_value(en_inner, "studio.greeting")
    assert "Hello" in val, val
    assert not re.search(r"[\u0400-\u04FF]", val), "en studio.greeting must not contain Cyrillic"


def test_i18n_studio_greeting_bulgarian_translation(i18n_text: str) -> None:
    bg_inner = _brace_inner_after_anchor(i18n_text, "bg: ")
    val = _js_string_value(bg_inner, "studio.greeting")
    assert re.search(r"[\u0400-\u04FF]", val), "bg studio.greeting should contain Bulgarian text"


def test_i18n_auth_owner_token_prompt(i18n_text: str) -> None:
    en_inner = _brace_inner_after_anchor(i18n_text, "en: ")
    bg_inner = _brace_inner_after_anchor(i18n_text, "bg: ")
    assert '"auth.ownerTokenPrompt"' in en_inner
    assert '"auth.ownerTokenPrompt"' in bg_inner
    en_val = _js_string_value(en_inner, "auth.ownerTokenPrompt")
    bg_val = _js_string_value(bg_inner, "auth.ownerTokenPrompt")
    assert "DAEDALUS_TOKEN" in en_val
    assert not re.search(r"[\u0400-\u04FF]", en_val)
    assert re.search(r"[\u0400-\u04FF]", bg_val)


def test_i18n_readlang_defaults_documented(i18n_text: str) -> None:
    assert 'localStorage.getItem(STORAGE_KEY)' in i18n_text
    assert 'return "en"' in i18n_text or "return 'en'" in i18n_text

