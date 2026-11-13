"""SubQ Snap in shadow mode (owner 2026-11-13): it judges beside Daedalus's own yes/no decisions and never decides."""
from __future__ import annotations

import json

import pytest

from daedalus import subq_judge


@pytest.fixture
def judge(monkeypatch, tmp_path):
    monkeypatch.setattr(subq_judge, "_path", lambda: tmp_path / "subq_shadow.jsonl")
    monkeypatch.setenv("SUBQ_API_KEY", "test-key")
    monkeypatch.delenv("DAEDALUS_SUBQ_SHADOW", raising=False)
    subq_judge._seen.clear()
    asked: list[tuple[str, list[str], str]] = []

    def fake_ask(state, names, *, api_key=""):
        asked.append((state, list(names), api_key))
        return {n: (0.99 if "frog" in state or "озвуч" in state else 0.01) for n in names}

    monkeypatch.setattr(subq_judge, "ask", fake_ask)
    return asked


def _rows(tmp_path):
    return [json.loads(x) for x in (tmp_path / "subq_shadow.jsonl").read_text().splitlines()]


def test_shadow_records_both_answers_and_agreement(judge, tmp_path):
    subq_judge.shadow("is_game", "a frog that jumps", True, wait=True)
    subq_judge.shadow("is_game", "fix the jump", False, wait=True)
    rows = _rows(tmp_path)
    assert [(r["current"], r["subq"], r["agree"]) for r in rows] == [(True, 0.99, True), (False, 0.01, True)]
    assert judge[0][2] == "test-key"


def test_the_same_text_is_judged_once_and_nothing_without_a_key(judge, monkeypatch, tmp_path):
    subq_judge.shadow("is_game", "a frog", True, wait=True)
    subq_judge.shadow("is_game", "a frog", True, wait=True)
    assert len(judge) == 1
    monkeypatch.delenv("SUBQ_API_KEY")
    assert subq_judge.shadow("is_game", "another frog", True) is None and len(judge) == 1
    monkeypatch.setenv("SUBQ_API_KEY", "k")
    monkeypatch.setenv("DAEDALUS_SUBQ_SHADOW", "0")
    assert subq_judge.shadow("is_game", "a third frog", True) is None and len(judge) == 1


def test_summary_counts_agreement_failures_and_disagreements(judge, monkeypatch, tmp_path):
    subq_judge.shadow("is_game", "a frog", True, wait=True)
    subq_judge.shadow("is_game", "a frog pond app", False, wait=True)           # SubQ says yes: a disagreement
    monkeypatch.setattr(subq_judge, "ask", lambda *a, **k: None)
    subq_judge.shadow("is_game", "offline", True, wait=True)
    q = subq_judge.summary()["questions"]["is_game"]
    assert (q["judged"], q["agree"], q["failed"], q["agreement"]) == (2, 1, 1, 0.5)
    assert q["disagreements"][0]["text"] == "a frog pond app"


def test_the_current_decisions_ask_subq_in_the_background_and_keep_their_answer(judge, monkeypatch, tmp_path):
    from daedalus import mentor_lead, paid_assets

    threads = []
    real = subq_judge.shadow
    monkeypatch.setattr(subq_judge, "shadow", lambda *a, **k: threads.append(real(*a, **k)))
    assert mentor_lead.is_game_build("", "make a frog game") is True
    assert mentor_lead.is_game_build("", "a budget tracker") is False
    token = paid_assets.set_turn_words("искам озвучаване на героя")
    paid_assets._TURN_WORDS.reset(token)
    for th in threads:
        if th is not None:
            th.join(5)
    got = {(r["question"], r["text"]): r["current"] for r in _rows(tmp_path)}
    assert got == {("is_game", "make a frog game"): True, ("is_game", "a budget tracker"): False,
                   ("wants_paid_assets", "искам озвучаване на героя"): True}


def test_ask_sends_the_documented_request(monkeypatch):
    sent = {}

    class Res:
        status_code = 200

        @staticmethod
        def json():
            return {"answers": {"is_game": {"type": "noul", "noul": 0.97}}}

    def post(url, json=None, timeout=None, headers=None):
        sent.update(url=url, body=json, auth=headers["Authorization"])
        return Res()

    import httpx
    monkeypatch.setattr(httpx, "post", post)
    assert subq_judge.ask("make a frog game", ["is_game", "unknown"], api_key="k") == {"is_game": 0.97}
    assert sent["url"].endswith("/v1/systemone") and sent["auth"] == "Bearer k"
    q = sent["body"]["questions"]["is_game"]
    assert sent["body"]["model"] == "subq-snap-1.2" and q["type"] == "noul" and set(q["criteria"]) == {"true", "false"}
    assert list(sent["body"]["questions"]) == ["is_game"]


def test_the_owner_route_reports_the_summary(judge, tmp_path):
    from fastapi.testclient import TestClient

    from app.main import app

    subq_judge.shadow("is_game", "a frog", True, wait=True)
    res = TestClient(app).get("/api/v1/harness/subq", headers={"Authorization": "Bearer pytest-owner-token"})
    assert res.status_code == 200 and res.json()["questions"]["is_game"]["judged"] == 1


def test_the_shadow_log_keeps_only_the_latest_rows(judge, monkeypatch, tmp_path):
    monkeypatch.setattr(subq_judge, "MAX_ROWS", 3)
    for i in range(12):
        subq_judge._record({"question": "is_game", "text": "x" * 500 + str(i), "current": True, "subq": 0.9,
                            "agree": True})
    rows = _rows(tmp_path)
    assert len(rows) <= 3 + 3 and rows[-1]["text"].endswith("11")
