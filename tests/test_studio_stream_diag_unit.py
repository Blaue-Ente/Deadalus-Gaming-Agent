"""Unit tests for studio_stream_diag (no Playwright server)."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import tests.studio_stream_diag as stream_diag

_FAKE_TOKEN = "sentinel-not-a-real-token-00000000"


class _FakePage:
    def evaluate(self, script: str) -> dict:
        if "build-stop" in script:
            return {"buildStopHidden": True, "slug": "demo-game", "panel": ""}
        return {"slug": "demo-game", "falseFacts": ["pane-flight.on"], "buildCheckpointHidden": True}


class _FakeOwned:
    streams = ["http://127.0.0.1:9/guided/stream?job_id=job-live&token=" + _FAKE_TOKEN]
    actives = []
    answered = ["http://127.0.0.1/guided/active"]
    held_posts = []
    posts = []
    stops = []
    post_mode = "hold"


def test_oversized_stream_artifact_parses_unicode(tmp_path, monkeypatch):
    monkeypatch.setattr(stream_diag, "ARTIFACT_DIR", tmp_path)
    monkeypatch.setattr(stream_diag, "FILE_CAP", 800)
    monkeypatch.setattr(stream_diag, "_SEQ", 0)
    trace = stream_diag.Trace()
    for i in range(30):
        trace.add("console", text="row-" + str(i) + "🧭" * 30)
    stream_diag.record_failure(_FakePage(), trace, "cap-test", owned=_FakeOwned())
    raw = next(tmp_path.glob("*.json")).read_text(encoding="utf-8")
    assert len(raw.encode("utf-8")) <= 800
    data = json.loads(raw)
    assert data["label"] == "cap-test"
    assert _FAKE_TOKEN not in json.dumps(data)


def test_record_failure_adds_note_without_swallowing(tmp_path, monkeypatch):
    monkeypatch.setattr(stream_diag, "ARTIFACT_DIR", tmp_path)
    monkeypatch.setattr(stream_diag, "_SEQ", 0)
    trace = stream_diag.Trace()
    err = TimeoutError("wait_for_function deadline")
    try:
        raise err
    except TimeoutError:
        stream_diag.record_failure(_FakePage(), trace, "note-test", owned=_FakeOwned())
    notes = getattr(err, "__notes__", None) or []
    assert any("stream-diag artifact" in n for n in notes)


def test_record_failure_safe_when_write_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(stream_diag, "ARTIFACT_DIR", tmp_path)
    monkeypatch.setattr(stream_diag, "_SEQ", 0)
    real_write = Path.write_text
    art = str(tmp_path)

    def _fail_write(self, data, encoding=None, errors=None, newline=None):
        if str(self).startswith(art):
            raise OSError(28, "no space")
        return real_write(self, data, encoding=encoding, errors=errors, newline=newline)

    monkeypatch.setattr(Path, "write_text", _fail_write)
    trace = stream_diag.Trace()
    summary = stream_diag.record_failure(_FakePage(), trace, "write-fail", owned=None)
    assert "capture failed" in summary or "write" in summary.lower()


def test_prune_keeps_latest_three(tmp_path, monkeypatch):
    monkeypatch.setattr(stream_diag, "ARTIFACT_DIR", tmp_path)
    monkeypatch.setattr(stream_diag, "KEEP_ARTIFACTS", 3)
    base = 1_700_000_000
    for i in range(4):
        p = tmp_path / f"keep-{i}.json"
        p.write_text("{}", encoding="utf-8")
        os.utime(p, (base + i, base + i))
    stream_diag._prune()
    assert len(list(tmp_path.glob("*.json"))) == 3


def test_dump_writes_valid_json_on_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(stream_diag, "ARTIFACT_DIR", tmp_path)
    monkeypatch.setattr(stream_diag, "FILE_CAP", 700)
    monkeypatch.setattr(stream_diag, "_SEQ", 0)
    trace = stream_diag.Trace()
    for i in range(25):
        trace.add("request", method="GET", url=f"http://127.0.0.1/guided/stream?job_id=job-{i}")
    stream_diag.dump(_FakePage(), trace, "dump-cap")
    data = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert data["label"] == "dump-cap"
