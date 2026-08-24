"""Public GitLab docs describe Daedalus only."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Words that belong to other products — they must not appear in the landing docs.
_FORBIDDEN = (
    "ArgosDataNexus",
    "argos_data_nexus",
    "argos-data-nexus",
    "MeshRouter",
    "Hermes Agent",
    "Hermes-class",
)

_WORD_ADN = r"\bADN\b"
_WORD_HERMES = r"\bHermes\b"
_WORD_IYVERIS = r"\bIyveris\b"

_PUBLIC = (
    ROOT / "README.md",
    ROOT / "ARCHITECTURE.md",
    ROOT / "DEPLOY.md",
    ROOT / "AGENTS.md",
    ROOT / "CONTRIBUTING.md",
    ROOT / "SECURITY.md",
    ROOT / "CODE_OF_CONDUCT.md",
    ROOT / "CHANGELOG.md",
    ROOT / "docs" / "ENV.md",
    ROOT / "docs" / "INSTALL.md",
)


def test_landing_docs_exist():
    for path in _PUBLIC:
        assert path.is_file(), f"missing {path.name}"
    assert (ROOT / "LICENSE").is_file()
    assert (ROOT / ".env.example").is_file()
    assert (ROOT / ".gitlab" / "issue_templates" / "Bug.md").is_file()
    assert (ROOT / ".gitlab" / "merge_request_templates" / "Default.md").is_file()


def test_landing_docs_do_not_name_other_products():
    import re

    for path in _PUBLIC:
        text = path.read_text(encoding="utf-8")
        for needle in _FORBIDDEN:
            assert needle not in text, f"{path.name} mentions {needle!r}"
        assert re.search(_WORD_ADN, text) is None, f"{path.name} mentions ADN"
        assert re.search(_WORD_HERMES, text) is None, f"{path.name} mentions Hermes"
        assert re.search(_WORD_IYVERIS, text) is None, f"{path.name} mentions Iyveris"


def test_readme_covers_install_and_features():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    for needle in (
        "Дедал",
        "pip install -e .",
        "DAEDALUS_STANDALONE=1",
        "docker compose",
        "install.ps1",
        "godot",
        "pytest",
        "8787",
        "deadalus-agent-0-main.zip",
        "docs/INSTALL.md",
    ):
        assert needle in text, f"README missing {needle!r}"


def test_readme_says_no_frozen_exe():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Няма замразен" in text or "няма замразен" in text
    assert "Python 3.11" in text


def test_pyproject_has_no_foreign_extra():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "argos-data-nexus" not in text
    assert 'adn = ' not in text
