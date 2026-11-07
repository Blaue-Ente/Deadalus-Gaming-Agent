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
    ROOT / "assets" / "logo" / "README.md",
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
        "Daedalus.exe",
        "docs/INSTALL.md",
        "deadalus-agent-0-main.zip",
        "assets/logo/daedalus.svg",
    ):
        assert needle in text, f"README missing {needle!r}"


def test_pyproject_has_no_foreign_extra():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "argos-data-nexus" not in text
    assert 'adn = ' not in text


def test_logo_kit_exists_and_is_small():
    logo = ROOT / "assets" / "logo"
    svg = (logo / "daedalus.svg").read_text(encoding="utf-8")
    assert 'aria-label="Daedalus"' in svg
    assert "#c9a227" in svg
    avatar = logo / "daedalus-avatar.png"
    mark = ROOT / "app" / "static" / "daedalus-mark.png"
    ico = ROOT / "app" / "static" / "favicon.ico"
    assert avatar.is_file() and avatar.stat().st_size < 50_000
    assert mark.is_file() and mark.stat().st_size < 200_000
    assert ico.is_file()
    assert (ROOT / "ui" / "static" / "daedalus-mark.png").is_file()
    assert (ROOT / "packaging" / "windows" / "daedalus.ico").is_file()
