"""A4: paths resolve live (no import-time roots), old names still work, settings.json has one writer."""
from __future__ import annotations

import itertools
import json
import subprocess
import sys
import threading
from pathlib import Path

from a0_support import a0_tmp  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent

MODULES = [
    "daedalus.playground", "daedalus.playground_publish", "daedalus.self_improve", "daedalus.auto_apply",
    "daedalus.skill_acquisition", "daedalus.task_state", "daedalus.playbooks", "daedalus.document_generator",
]

_PROBE = r'''
import importlib, json, os, sys
sys.path.insert(0, os.environ["DX_ROOT"])
order = sys.argv[1].split(",")
for name in order:                      # import BEFORE the data/playground env is set
    importlib.import_module(name)
os.environ["DAEDALUS_DATA"] = sys.argv[2]
os.environ["DAEDALUS_PLAYGROUND"] = sys.argv[3]
from daedalus.paths import data_root, playground_projects_dir
data_root.cache_clear(); playground_projects_dir.cache_clear()
from daedalus import playground, playground_publish, self_improve, task_state, playbooks, document_generator, skill_acquisition, auto_apply
out = {
  "projects": str(playground.PROJECTS_DIR),
  "published": str(playground_publish.PUBLISHED_DIR),
  "proposals": str(self_improve.proposals_dir()),
  "backups": str(self_improve.backups_dir()),
  "state": str(task_state._state_file()),
  "playbooks": str(playbooks._custom_file()),
  "documents": str(document_generator.documents_dir()),
  "skills": str(skill_acquisition.skills_log()),
  "settings": str(auto_apply._SETTINGS_FILE),
}
print(json.dumps(out))
'''


def _run(order, data, projects):
    cp = subprocess.run(
        [sys.executable, "-I", "-c", _PROBE, ",".join(order), str(data), str(projects)],
        capture_output=True, text=True, cwd=str(ROOT), env={"DX_ROOT": str(ROOT), "PATH": "/usr/bin:/bin", "DAEDALUS_STANDALONE": "1", "HOME": str(data)},
    )
    assert cp.returncode == 0, cp.stderr[-800:]
    return json.loads(cp.stdout.strip().splitlines()[-1])


def test_no_second_root_under_any_import_order(tmp_path):
    data, projects = tmp_path / "d", tmp_path / "p"
    orders = [MODULES, list(reversed(MODULES)), MODULES[3:] + MODULES[:3]]
    results = [_run(o, data, projects) for o in orders]
    assert results[0] == results[1] == results[2]
    r = results[0]
    assert r["projects"] == str(projects)
    for key in ("published", "proposals", "backups", "state", "playbooks", "documents", "skills", "settings"):
        assert r[key].startswith(str(data)) or r[key].startswith(str(projects)), (key, r[key])


def test_import_creates_nothing(tmp_path):
    data = tmp_path / "never"
    cp = subprocess.run(
        [sys.executable, "-I", "-c", "import os, sys; sys.path.insert(0, os.environ['DX_ROOT']); import " + ",".join(MODULES)],
        capture_output=True, text=True, cwd=str(ROOT),
        env={"DX_ROOT": str(ROOT), "PATH": "/usr/bin:/bin", "DAEDALUS_STANDALONE": "1",
             "DAEDALUS_DATA": str(data), "DAEDALUS_PLAYGROUND": str(tmp_path / "pp"), "HOME": str(tmp_path)},
    )
    assert cp.returncode == 0, cp.stderr[-600:]
    assert not data.exists(), "an import-time mkdir came back"


def test_legacy_names_equal_live_values_and_file_names_unchanged(a0_tmp):
    from daedalus import (auto_apply, document_generator, playbooks, playground, playground_publish,
                          self_improve, skill_acquisition, task_state)
    from daedalus.paths import data_root, playground_projects_dir

    assert playground.PROJECTS_DIR == playground_projects_dir()
    assert playground_publish.PUBLISHED_DIR == playground_publish.published_dir()
    assert self_improve.PROPOSALS_DIR == self_improve.proposals_dir()
    assert self_improve.BACKUPS_DIR == self_improve.backups_dir()
    assert self_improve.SKILLS_FILE == self_improve.skills_file()
    assert self_improve.AUDIT_LOG == self_improve.audit_log()
    assert skill_acquisition.SKILLS_LOG == data_root() / "acquired_skills.json"
    assert task_state._STATE_FILE == data_root() / "task-state.json"
    assert playbooks._CUSTOM_FILE == data_root() / "playbooks.json"
    assert document_generator.DOCUMENTS_DIR == data_root() / "documents"
    assert auto_apply._SETTINGS_FILE == data_root() / "settings.json"
    for mod, name in ((self_improve, "NOPE"), (task_state, "NOPE")):
        try:
            getattr(mod, name)
        except AttributeError:
            pass
        else:  # pragma: no cover
            raise AssertionError("unknown names must still raise")


def test_paths_follow_a_changed_root_without_reimport(a0_tmp, monkeypatch, tmp_path):
    from daedalus import playground, task_state
    from daedalus.paths import data_root, playground_projects_dir

    other = tmp_path / "second"
    monkeypatch.setenv("DAEDALUS_DATA", str(other / "d"))
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(other / "p"))
    data_root.cache_clear(); playground_projects_dir.cache_clear()
    assert str(task_state._state_file()).startswith(str(other / "d"))
    assert playground.PROJECTS_DIR == other / "p"


def test_settings_json_has_one_writer(a0_tmp):
    """Evolution and auto-apply flip different keys concurrently; neither may erase the other (lost update)."""
    from daedalus import auto_apply, evolution, settings_file

    stop = threading.Event()

    def flip_evolution():
        while not stop.is_set():
            settings_file.update({"evolution_enabled": True, "evolution_interval_s": 3600})

    t = threading.Thread(target=flip_evolution)
    t.start()
    try:
        for _ in range(60):
            auto_apply.set_auto_apply_enabled(True)
    finally:
        stop.set(); t.join()
    data = json.loads(settings_file.path().read_text("utf-8"))
    assert data.get("auto_apply_enabled") is True and data.get("evolution_enabled") is True
    assert evolution._load_settings().get("evolution_interval_s") == 3600


def test_settings_file_atomic_and_tolerates_corruption(a0_tmp):
    from daedalus import settings_file

    settings_file.path().parent.mkdir(parents=True, exist_ok=True)
    settings_file.path().write_text("{not json", "utf-8")
    assert settings_file.load() == {}
    merged = settings_file.update({"a": 1})
    assert merged == {"a": 1}
    assert json.loads(settings_file.path().read_text("utf-8")) == {"a": 1}
    assert not list(settings_file.path().parent.glob("*.tmp"))
