"""Unit tests for failure-only radar/journal diagnostics (no Playwright server)."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import tests.studio_radar_journal_diag as rjdiag

_FAKE_TOKEN = "sentinel-not-a-real-token-00000000"


class _FakePage:
    def evaluate(self, _script: str) -> dict:
        return {
            "radarStatus": "ok",
            "journalStatus": "ж" * 400,
        }


def test_oversized_artifact_stays_valid_json_unicode(tmp_path, monkeypatch):
    monkeypatch.setattr(rjdiag, "ARTIFACT_DIR", tmp_path)
    monkeypatch.setattr(rjdiag, "FILE_CAP", 900)
    monkeypatch.setattr(rjdiag, "_SEQ", 0)
    trace = rjdiag.Trace()
    for i in range(40):
        trace.add("console", text=f"line-{i}-" + "🧭" * 40)
    summary = rjdiag.record_failure(
        _FakePage(),
        trace,
        "oversized-test",
        calls=[{"kind": "fetch", "method": "GET", "raw": None}] * 20,
        held_len=3,
        discoveries=[f"/admin/daedalus/discoveries/{i}?token={_FAKE_TOKEN}" for i in range(30)],
        leaked=[f"http://127.0.0.1/x?api_key={_FAKE_TOKEN}"],
    )
    assert "artifact" in summary
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    raw = files[0].read_text(encoding="utf-8")
    assert len(raw.encode("utf-8")) <= 900
    data = json.loads(raw)
    assert data.get("label") == "oversized-test"
    blob = json.dumps(data)
    assert _FAKE_TOKEN not in blob
    assert "api_key=" not in blob or "[redacted]" in blob


def test_record_failure_preserves_exception_chain_note(tmp_path, monkeypatch):
    monkeypatch.setattr(rjdiag, "ARTIFACT_DIR", tmp_path)
    monkeypatch.setattr(rjdiag, "_SEQ", 0)
    trace = rjdiag.Trace()
    err = ValueError("synthetic assert for diag")
    try:
        raise err
    except ValueError:
        rjdiag.record_failure(_FakePage(), trace, "chain-test", held_len=1)
    notes = getattr(err, "__notes__", None) or []
    assert any("radar-journal diag artifact" in n for n in notes)


def test_write_failure_still_adds_note(tmp_path, monkeypatch):
    monkeypatch.setattr(rjdiag, "ARTIFACT_DIR", tmp_path)
    monkeypatch.setattr(rjdiag, "_SEQ", 0)
    trace = rjdiag.Trace()
    real_write = Path.write_text
    art = str(tmp_path)

    def _selective_write(self, data, encoding=None, errors=None, newline=None):
        if str(self).startswith(art):
            raise OSError(28, "no space left on device")
        return real_write(self, data, encoding=encoding, errors=errors, newline=newline)

    monkeypatch.setattr(Path, "write_text", _selective_write)
    err = RuntimeError("parent failure")
    try:
        raise err
    except RuntimeError:
        summary = rjdiag.record_failure(_FakePage(), trace, "write-fail", held_len=0)
    assert "write failed" in summary
    notes = getattr(err, "__notes__", None) or []
    assert any("write failed" in n for n in notes)


def test_prune_retains_three_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(rjdiag, "ARTIFACT_DIR", tmp_path)
    monkeypatch.setattr(rjdiag, "KEEP_ARTIFACTS", 3)
    base = 1_700_000_000
    for i in range(4):
        p = tmp_path / f"old-{i}.json"
        p.write_text("{}", encoding="utf-8")
        os.utime(p, (base + i, base + i))
    rjdiag._prune()
    assert len(list(tmp_path.glob("*.json"))) == 3
    remaining = sorted(p.name for p in tmp_path.glob("*.json"))
    assert remaining == ["old-1.json", "old-2.json", "old-3.json"]


def test_redact_paths_and_dom_fields():
    trace = rjdiag.Trace()
    trace.add(
        "stub",
        method="POST",
        url=f"http://127.0.0.1/admin/daedalus/radar/scan?token={_FAKE_TOKEN}",
        body="ignored",
    )
    row = trace.events[0]
    assert row["url"] == "/admin/daedalus/radar/scan"
    assert "body" not in row
    frame = rjdiag.frame_facts(_FakePage())
    assert isinstance(frame, dict)
    assert _FAKE_TOKEN not in json.dumps(frame)
