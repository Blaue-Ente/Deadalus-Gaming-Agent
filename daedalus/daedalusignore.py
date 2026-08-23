"""Daedalus Ignore — Phase 17C.

Defines file patterns that Дедал should NEVER modify.
Checked in the File Confidence Gate before every NEED_PROPOSE_START.

Storage: data/daedalus/.daedalusignore (plain text, one pattern per line)
If the file doesn't exist, only built-in default patterns are applied.

Pattern syntax (fnmatch-style, also supports simple prefix/suffix matching):
  data/daedalus/chroma/**    — entire ChromaDB directory
  data/daedalus/backups/**   — backups directory  
  *.pyc                      — compiled Python files
  **/__pycache__/**          — Python cache
  .env                       — environment file

A blocked proposal returns a clear message with the reason.
"""
from __future__ import annotations
from .paths import data_root

import fnmatch
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_DATA_ROOT = data_root()  # paths
_IGNORE_FILE = _DATA_ROOT / ".daedalusignore"

# Default patterns — always active, even without a .daedalusignore file.
# These protect critical system data from accidental modification.
_DEFAULT_PATTERNS: list[str] = [
    "data/daedalus/chroma/**",
    "data/daedalus/backups/**",
    "data/daedalus/*.json",        # proposals, skills, settings — use proper tools
    "data/daedalus/.daedalusignore",  # meta: protect the ignore file itself
    "**/__pycache__/**",
    "*.pyc",
    "*.pyo",
    ".env",
    ".env.*",
    "*.key",
    "*.pem",
    "*.p12",
]

# Whitelist — always allow even if matched by a broad pattern
_WHITELIST: list[str] = [
    "data/daedalus/playbooks.json",  # Дедал may create custom playbooks
]


def _load_custom_patterns() -> list[str]:
    """Load custom patterns from .daedalusignore file."""
    try:
        if _IGNORE_FILE.exists():
            lines = _IGNORE_FILE.read_text("utf-8").splitlines()
            return [
                line.strip()
                for line in lines
                if line.strip() and not line.strip().startswith("#")
            ]
    except Exception as exc:
        log.debug("daedalusignore load failed (non-fatal): %s", exc)
    return []


def _matches(file_path: str, pattern: str) -> bool:
    """Check if file_path matches a pattern using fnmatch with ** support."""
    # Normalize path separators
    fp = file_path.replace("\\", "/").lstrip("/")
    pat = pattern.replace("\\", "/").lstrip("/")

    # Direct fnmatch
    if fnmatch.fnmatch(fp, pat):
        return True

    # Handle ** glob (recursive)
    if "**" in pat:
        # Replace ** with a wildcard that matches any path segment
        import re
        regex_pat = re.escape(pat).replace(r"\*\*", ".*").replace(r"\*", "[^/]*")
        import re as _re
        if _re.fullmatch(regex_pat, fp):
            return True

    # Prefix match (pattern is a directory prefix)
    if pat.endswith("/") and fp.startswith(pat):
        return True

    return False


def is_ignored(file_path: str) -> bool:
    """Return True if file_path matches any ignore pattern (and not whitelisted)."""
    if not file_path:
        return False

    # Whitelist check first
    for wl in _WHITELIST:
        if _matches(file_path, wl):
            return False

    all_patterns = _DEFAULT_PATTERNS + _load_custom_patterns()
    for pat in all_patterns:
        if _matches(file_path, pat):
            log.info("daedalusignore: blocked '%s' (pattern: '%s')", file_path, pat)
            return True
    return False


def get_ignore_reason(file_path: str) -> str | None:
    """Return the matched pattern if ignored, None otherwise."""
    if not file_path:
        return None
    for wl in _WHITELIST:
        if _matches(file_path, wl):
            return None
    for pat in _DEFAULT_PATTERNS + _load_custom_patterns():
        if _matches(file_path, pat):
            return pat
    return None


def format_blocked_message(file_path: str, pattern: str) -> str:
    """Return a clear block message for the agent."""
    return (
        f"━━━ TOOL RESULTS — .DAEDALUSIGNORE BLOCKED ━━━\n"
        f"File    : {file_path}\n"
        f"Pattern : {pattern}\n\n"
        f"Този файл е в .daedalusignore — НЕ го модифицирай директно.\n"
        f"Алтернативи:\n"
        f"  • Ако е data файл → използвай NEED_DATA_PROPOSAL\n"
        f"  • Ако е chroma/backup → системата управлява автоматично\n"
        f"  • Ако смяташ, че блокирането е грешно → NEED_INBOX_MSG до Николай"
    )


def ensure_ignore_file() -> None:
    """Create default .daedalusignore if it doesn't exist."""
    if not _IGNORE_FILE.exists():
        _DATA_ROOT.mkdir(parents=True, exist_ok=True)
        lines = [
            "# Daedalus Ignore File — Phase 17C",
            "# Един pattern на ред. # = коментар.",
            "# Default patterns са вградени в кода — тук само custom additions.",
            "",
            "# Пример: игнорирай конкретен чувствителен файл",
            "# secrets/production.json",
            "",
        ]
        _IGNORE_FILE.write_text("\n".join(lines), "utf-8")
        log.info("daedalusignore: created default .daedalusignore")


def list_patterns() -> dict[str, list[str]]:
    """Return all active patterns (for admin UI / debugging)."""
    return {
        "default": _DEFAULT_PATTERNS,
        "custom": _load_custom_patterns(),
        "whitelist": _WHITELIST,
    }
