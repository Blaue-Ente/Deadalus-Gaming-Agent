"""Capability-first kernel — adapters, skip=fail, no crosswalk. No live runners."""
from __future__ import annotations

from pathlib import Path

from daedalus.capability import (
    acquire_write,
    adn_publish_block,
    clear_write_locks,
    polish_allowed,
    refuse_crosswalk,
    refuse_parallel_writers,
    release_write,
    resolve_target,
    run_pipeline,
    stale_hashes,
)
from daedalus.mobile_first import export_release as mobile_export
from daedalus.subagents import tools_for_role
from daedalus.tool_router import match_playbook, route
from daedalus.web_first import run_pipeline as web_run


def _snake_html() -> str:
    return (
        "<!doctype html><canvas id=c></canvas><script>\n"
        "const c=document.getElementById('c');\n"
        "const x=c.getContext('2d');\n"
        "addEventListener('keydown', e => { if (e.key==='r') location.reload(); });\n"
        "let dead=false;\n"
        "function loop(){ requestAnimationFrame(loop); if(dead){ /* game over */ } }\n"
        "loop();\n"
        "</script>\n"
    )


def test_resolve_target_godot_never_web():
    assert resolve_target("godot", "html canvas phaser") == "godot"
    assert resolve_target("static", "Godot 4 GDScript") == "godot"
    assert resolve_target("static", "snake canvas") == "web"
    assert resolve_target("static", "snake on iOS with Capacitor") == "capacitor"
    assert resolve_target("expo", "todo app") == "expo"
    assert resolve_target("unity", "fps") == "unity"


def test_refuse_crosswalk():
    godot_web = refuse_crosswalk("godot", "web")
    assert godot_web and godot_web["ok"] is False
    assert "no_crosswalk" in godot_web["blockers"]
    expo_web = refuse_crosswalk("expo", "web")
    assert expo_web and expo_web["ok"] is False
    unity = refuse_crosswalk("unity", "godot")
    assert unity and unity.get("honest_gap") == "unity_local"


def test_web_plan_fails_without_chromium(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("daedalus.web_first.chromium_bin", lambda: "")
    monkeypatch.setattr("daedalus.playground.project_dir", lambda slug: tmp_path)
    (tmp_path / "index.html").write_text(_snake_html(), encoding="utf-8")
    pipe = web_run("snake", root=tmp_path, kind="static", through="build")
    assert pipe["target"] == "web"
    assert pipe["stages"]["plan"]["ok"] is False
    assert pipe["stages"]["plan"]["skipped"] is True
    assert "chromium_missing" in pipe["stages"]["plan"]["blockers"]
    assert pipe["stages"]["build"]["ok"] is True
    assert pipe["ok"] is False
    evidence = tmp_path / "studio" / "capability_evidence.json"
    assert evidence.is_file()
    assert pipe["verdict"] == "BLOCKED"


def test_web_publish_without_export_fails(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("daedalus.web_first.chromium_bin", lambda: "")
    monkeypatch.setattr("daedalus.playground.project_dir", lambda slug: tmp_path)
    (tmp_path / "index.html").write_text(_snake_html(), encoding="utf-8")
    pipe = web_run("snake", root=tmp_path, kind="static", through="publish")
    pub = pipe["stages"]["publish"]
    assert pub["ok"] is False
    assert "no_immutable_artifact" in pub["blockers"] or "gates_not_green" in str(
        pipe["stages"]["export"]
    )


def test_mobile_fails_without_emulator(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "daedalus.mobile_first.device_probe",
        lambda: {
            "ok": False, "skipped": True, "adb": "", "xcrun": "",
            "emulator": "", "android_home": "", "devices": [], "ios_sim": False,
            "error": "No emulator or device",
        },
    )
    monkeypatch.setattr("daedalus.playground.project_dir", lambda slug: tmp_path)
    (tmp_path / "index.html").write_text(_snake_html(), encoding="utf-8")
    pipe = run_pipeline("app", kind="static", through="runtime", text="iOS Capacitor wrap")
    assert pipe["target"] == "capacitor"
    assert pipe["ok"] is False
    assert pipe["stages"]["runtime"]["skipped"] is True
    assert "emulator_missing" in str(pipe["stages"]["runtime"]["blockers"])


def test_capacitor_skip_is_not_export_success(tmp_path: Path):
    (tmp_path / "index.html").write_text(_snake_html(), encoding="utf-8")
    row = mobile_export(tmp_path, slug="wrap", kind="static", flavor="capacitor")
    assert row["ok"] is False
    assert row["skipped"] is True
    expo = mobile_export(tmp_path, slug="rn", kind="expo", flavor="expo")
    assert expo["ok"] is False
    assert "expo_web_is_not_native" in expo["blockers"]


def test_stale_hashes(tmp_path: Path):
    (tmp_path / "index.html").write_text("a", encoding="utf-8")
    from daedalus.capability import file_hash
    hashes = {"index.html": file_hash(tmp_path, "index.html")}
    assert stale_hashes(tmp_path, hashes) is False
    (tmp_path / "index.html").write_text("changed", encoding="utf-8")
    assert stale_hashes(tmp_path, hashes) is True


def test_adn_publish_block_only_when_evidence_blocked(tmp_path: Path):
    assert adn_publish_block(tmp_path) == ""
    (tmp_path / "studio").mkdir()
    (tmp_path / "studio" / "capability_evidence.json").write_text(
        '{"verdict":"BLOCKED","error":"runtime skipped"}', encoding="utf-8",
    )
    assert "BLOCKED" in adn_publish_block(tmp_path) or "runtime" in adn_publish_block(tmp_path)


def test_roles_verifier_does_not_write():
    assert "write_file" not in tools_for_role("verify")
    assert "write_file" not in tools_for_role("verifier")
    assert "write_file" not in tools_for_role("planner")
    assert "write_file" in tools_for_role("builder")
    assert "write_file" in tools_for_role("specialist")
    assert "write_file" not in tools_for_role("explorer")


def test_exclusive_writers_and_polish(tmp_path: Path):
    clear_write_locks()
    a = acquire_write("slug", holder="builder:1", files=["index.html"])
    assert a["ok"] is True
    b = acquire_write("slug", holder="coder:2", files=["index.html"])
    assert b["ok"] is False
    release_write("slug", "builder:1")
    c = acquire_write("slug", holder="coder:2", files=["index.html"])
    assert c["ok"] is True
    clear_write_locks()
    refused = refuse_parallel_writers([
        {"slug": "g", "role": "coder", "task": "a"},
        {"slug": "g", "role": "coder", "task": "b"},
    ])
    assert refused[1].get("_refuse")
    assert polish_allowed(tmp_path) is False
    (tmp_path / "studio").mkdir()
    (tmp_path / "studio" / "capability_evidence.json").write_text(
        '{"stages":{"visual":{"ok":false}}}', encoding="utf-8",
    )
    assert polish_allowed(tmp_path) is True
    clear_write_locks()


def test_arcade_stays_static_not_godot():
    pb = match_playbook("направи змия")
    assert pb.id == "playground.arcade"
    assert pb.kind == "static"
    rt = route("Направи нова Playground игра snake: HTML canvas, стрелки")
    assert rt.playbook.id == "playground.arcade"
    assert rt.kind == "static"


def test_godot_on_ios_stays_godot():
    assert resolve_target("godot", "export to iOS") == "godot"
    pb = match_playbook("Направи Godot 4 игра: CharacterBody2D, scenes/main.tscn, GDScript on iOS")
    assert "godot" in pb.id
    assert pb.kind == "godot"


def test_mobile_playbook_for_expo():
    pb = match_playbook("Expo React Native todo on iOS")
    assert "mobile" in pb.id
    ids = {p.id for p in __import__("daedalus.task_playbooks", fromlist=["PLAYBOOKS"]).PLAYBOOKS}
    assert "playground.mobile" in ids


def test_unity_pipeline_is_honest_gap(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("daedalus.playground.project_dir", lambda slug: tmp_path)
    (tmp_path / "Assets").mkdir()
    pipe = run_pipeline("u", kind="unity", through="plan")
    assert pipe["ok"] is False
    assert pipe.get("honest_gap") == "unity_local"
