#!/usr/bin/env python3
"""Fail-closed secret scan for Daedalus CI. No docker.

Scans product trees and config files. Tests, the golden pack, and empty
``.env.example`` placeholders are out of scope. A missing scanner is a
failure — this file *is* the scanner.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TREES = ("daedalus", "app", "scripts", "ui", "packaging", "docker")
FILES = (
    ".gitlab-ci.yml",
    ".github/workflows/docker.yml",
    "pyproject.toml",
    "Dockerfile",
    "docker-compose.yml",
    ".env.example",
    "README.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "DEPLOY.md",
)
SKIP_DIR_NAMES = {".git", ".venv", "__pycache__", "node_modules", ".mypy_cache", ".pytest_cache"}
TEXT_SUFFIXES = {
    ".py", ".md", ".yml", ".yaml", ".toml", ".txt", ".sh", ".ps1", ".json",
    ".html", ".css", ".js", ".env", ".ini", ".cfg", ".example",
}

PATTERNS = (
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("pem_private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("gitlab_pat", re.compile(r"glpat-[0-9A-Za-z_-]{20,}")),
    ("github_pat", re.compile(r"ghp_[0-9A-Za-z]{36}")),
    ("github_fine_grained", re.compile(r"github_pat_[0-9A-Za-z_]{20,}")),
    ("openai_live_key", re.compile(r"sk-(?:proj-)?[A-Za-z0-9]{32,}")),
    ("openrouter_live_key", re.compile(r"sk-or-v1-[0-9a-f]{32,}")),
)

# Placeholder / documentation values that must not trip the gate.
ALLOW_SUBSTRINGS = (
    "YOUR_",
    "changeme",
    "example",
    "placeholder",
    "sk-or-should-not",
    "sk-test",
    "sk-or-preset",
    "sk-or-http",
    "sk-or-after",
)


def _iter_files() -> list[Path]:
    out: list[Path] = []
    for rel in FILES:
        path = ROOT / rel
        if path.is_file():
            out.append(path)
    for tree in TREES:
        base = ROOT / tree
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            if any(part in SKIP_DIR_NAMES for part in path.parts):
                continue
            if path.suffix.lower() not in TEXT_SUFFIXES and path.name not in {
                "Dockerfile", "Procfile",
            }:
                continue
            out.append(path)
    return out


def _allowed(line: str) -> bool:
    low = line.lower()
    return any(token in low for token in ALLOW_SUBSTRINGS)


def _env_example_assignment(path: Path, line: str) -> bool:
    if path.name != ".env.example":
        return False
    if "=" not in line or line.lstrip().startswith("#"):
        return False
    value = line.split("=", 1)[1].strip().strip("\"'")
    return len(value) < 24


def main() -> int:
    hits: list[str] = []
    for path in _iter_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rel = path.relative_to(ROOT).as_posix()
        for lineno, line in enumerate(text.splitlines(), 1):
            if _allowed(line) or _env_example_assignment(path, line):
                continue
            for name, pattern in PATTERNS:
                if pattern.search(line):
                    hits.append(f"{rel}:{lineno}: {name}")
    report = ROOT / "secret-scan.txt"
    if hits:
        body = "secret-scan failed\n" + "\n".join(hits) + "\n"
        report.write_text(body, encoding="utf-8")
        sys.stderr.write(body)
        return 1
    report.write_text("secret-scan ok\n", encoding="utf-8")
    print("secret-scan ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
