"""First steps (owner 2026-11-13): the machine check, keys by name, the SubQ switch, abilities, Studio and CLI."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from daedalus import onboarding

ROOT = Path(__file__).resolve().parents[1]
I18N = (ROOT / "app" / "static" / "i18n.js").read_text(encoding="utf-8")


def _strings(lang: str) -> set[str]:
    start = I18N.index("    bg: {") if lang == "bg" else I18N.index("    en: {")
    end = I18N.index("    bg: {") if lang == "en" else len(I18N)
    return set(re.findall(r'^\s+"([\w.]+)":', I18N[start:end], re.M))


@pytest.fixture
def data(monkeypatch, tmp_path):
    from daedalus import settings_file
    monkeypatch.setattr(settings_file, "path", lambda: tmp_path / "settings.json")
    monkeypatch.delenv("DAEDALUS_SUBQ_SHADOW", raising=False)
    return tmp_path


def test_the_report_names_every_check_key_and_ability(data):
    rep = onboarding.report()
    ids = {i["id"] for i in rep["items"]}
    assert {"python", "packages", "brain", "pillow", "browser", "git", "node", "ollama", "godot", "unity",
            "disk"} <= ids
    python = next(i for i in rep["items"] if i["id"] == "python")
    assert python["ok"] and python["level"] == "required" and python["minimum"] == "3.11"
    assert {k["name"] for k in rep["keys"]} >= {"NVIDIA_API_KEY", "SUBQ_API_KEY", "ELEVENLABS_API_KEY"}
    assert {a["id"] for a in rep["abilities"]} >= {"web_games", "godot", "judge", "free_sound_art"}
    assert rep["start"]["command"] == "daedalus serve" and rep["start"]["check"] == "daedalus doctor"
    assert all(i["install"] for i in rep["items"])


def test_keys_are_reported_by_name_never_by_value(data, monkeypatch):
    monkeypatch.setenv("SUBQ_API_KEY", "sk-secret-value-123")
    rep = onboarding.report()
    assert "sk-secret-value-123" not in json.dumps(rep)
    assert next(k for k in rep["keys"] if k["name"] == "SUBQ_API_KEY")["set"] is True
    assert next(a for a in rep["abilities"] if a["id"] == "judge")["ready"] is True


def test_a_missing_required_item_makes_it_not_ready_and_the_abilities_say_what_they_need(data, monkeypatch):
    for k in onboarding.BRAIN_KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(onboarding, "_brain", lambda: onboarding._item("brain", "required", False))
    monkeypatch.setattr(onboarding, "_godot", lambda: onboarding._item("godot", "optional", False))
    rep = onboarding.report()
    assert rep["ready"] is False
    ab = {a["id"]: a for a in rep["abilities"]}
    assert ab["web_games"]["needs"] == ["brain"] and ab["godot"]["needs"] == ["godot"]
    assert ab["free_sound_art"]["ready"] is True


def test_a_broken_check_is_reported_never_raised(data, monkeypatch):
    def boom():
        raise RuntimeError("no")
    monkeypatch.setattr(onboarding, "_unity", boom)
    rep = onboarding.report()
    assert any(i["ok"] is False and "check failed" in i["detail"] for i in rep["items"])


def test_seen_and_auto_open(data, monkeypatch):
    monkeypatch.delenv("DAEDALUS_ONBOARDING_AUTO", raising=False)
    assert onboarding.seen() is False and onboarding.auto_open() is True
    onboarding.mark_seen()
    assert onboarding.seen() is True
    monkeypatch.setenv("DAEDALUS_ONBOARDING_AUTO", "0")
    assert onboarding.auto_open() is False


def test_the_subq_switch_is_a_setting_the_environment_can_pin(data, monkeypatch):
    from daedalus import subq_judge
    from daedalus.env_lock import EnvLockedError
    from daedalus.harness import config_view

    monkeypatch.setenv("SUBQ_API_KEY", "k")
    assert subq_judge.switch_on() is True and subq_judge.enabled() is True
    row = config_view.set_value("subq.shadow", False)
    assert row["effective"] is False and row["source"] == "saved"
    assert subq_judge.enabled() is False and onboarding.subq_switch()["on"] is False
    assert subq_judge.shadow("is_game", "a frog game", True) is None          # off: nothing is asked
    monkeypatch.setenv("DAEDALUS_SUBQ_SHADOW", "1")
    assert subq_judge.switch_on() is True and onboarding.subq_switch()["env_locked"] is True
    with pytest.raises(EnvLockedError):
        config_view.set_value("subq.shadow", False)


def test_every_text_the_dialog_asks_for_exists_in_english_and_bulgarian(data):
    rep = onboarding.report()
    need = {"studio.setup", "settings.subqShadow", "onb.title", "onb.intro", "onb.ready", "onb.notReady",
            "onb.subq.text", "onb.subq.label", "onb.subq.stats", "onb.needs", "onb.needImageKey"}
    need |= {f"onb.item.{i['id']}" for i in rep["items"]} | {f"onb.why.{i['id']}" for i in rep["items"]}
    need |= {f"onb.level.{lvl}" for lvl in ("required", "recommended", "optional")}
    need |= {f"onb.ab.{a['id']}" for a in rep["abilities"]} | {f"onb.abd.{a['id']}" for a in rep["abilities"]}
    need |= {f"onb.use.{k['use']}" for k in rep["keys"]}
    need |= {f"onb.subq.q.{q}" for q in ("is_game", "wants_paid_assets")}
    need |= {f"onb.how.{i}" for i in ("brain", "unity", "disk")}       # steps in words, translated
    js = (ROOT / "app" / "static" / "studio-onboarding.js").read_text(encoding="utf-8")
    need |= {k for k in re.findall(r'dx\.t\("(onb\.[\w.]+)"', js) if not k.endswith(".")}
    assert need - _strings("en") == set()
    assert need - _strings("bg") == set()


def test_the_routes_are_the_owners(data):
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    assert client.get("/api/v1/onboarding?quick=1").json()["seen"] is False
    rep = client.get("/api/v1/onboarding").json()
    assert rep["ok"] and rep["items"]
    assert client.post("/api/v1/onboarding/seen").json()["ok"] is True
    assert client.get("/api/v1/onboarding?quick=1").json()["seen"] is True


def test_the_onboarding_needs_the_owner(raw_http, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    res = TestClient(app).get("/api/v1/onboarding", headers={"X-Forwarded-For": "203.0.113.9"})
    assert res.status_code in (401, 403)


def test_daedalus_doctor_prints_the_check_and_exits_by_readiness():
    out = subprocess.run([sys.executable, "-m", "app.cli", "doctor"], cwd=str(ROOT), capture_output=True,
                         text=True, timeout=120)
    assert "Daedalus check on" in out.stdout and "abilities:" in out.stdout and "daedalus serve" in out.stdout
    assert out.returncode == (0 if "(ready)" in out.stdout else 1)
