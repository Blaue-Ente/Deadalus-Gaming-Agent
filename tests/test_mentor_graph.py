"""Mentor tool graph, director guard, and pending corrections."""
from __future__ import annotations

import asyncio
import json

from daedalus.build_loop import BuildLoop
from daedalus.tool_router import route

from tests.test_mentor import BOOT_SCENE, LUMEN, PHASER_BOOT


def _vite_root(tmp_path, monkeypatch, *, auto_canvas: bool = False):
    from daedalus.paths import data_root

    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path))
    monkeypatch.setenv("DAEDALUS_MENTOR", "1")
    data_root.cache_clear()
    slug = "glass-orchard"
    root = tmp_path / slug
    (root / "src" / "scenes").mkdir(parents=True)
    (root / "node_modules" / "phaser").mkdir(parents=True)
    (root / "index.html").write_text(
        '<canvas id="game-canvas"></canvas><div id="game"></div>'
        '<script type="module" src="/src/main.js"></script>',
        encoding="utf-8",
    )
    main = (
        "import Phaser from 'phaser';\n"
        "import { Boot } from './scenes/Boot.js';\n"
        "new Phaser.Game({\n"
        "  type: Phaser.AUTO,\n"
        "  parent: 'game',\n"
        "  canvas: document.getElementById('game-canvas'),\n"
        "  scene: [Boot],\n"
        "});\n"
        if auto_canvas
        else PHASER_BOOT
    )
    (root / "src" / "main.js").write_text(main, encoding="utf-8")
    (root / "src" / "scenes" / "Boot.js").write_text(BOOT_SCENE, encoding="utf-8")
    (root / "package.json").write_text('{"dependencies":{"phaser":"^3.80"}}', encoding="utf-8")
    monkeypatch.setattr("daedalus.playground.project_dir", lambda s: root)
    monkeypatch.setattr("daedalus.mentor._rag_hits", lambda q: [])
    rt = route(LUMEN)
    loop = BuildLoop(rt, LUMEN, fix_rounds=3)
    loop.slug = slug
    loop.kind = "vite"
    loop.observe("write_file", {"slug": slug, "path": "src/main.js"}, "── write_file OK ──", ok=True)
    return loop


def test_vite_graph_includes_writer_recovery():
    from daedalus.mentor_graph import for_inspect, pick_recovery

    g = for_inspect("playground.vite", flags={})
    assert g["playbook"] == "playground.vite"
    assert "write_file" in g["tools"]
    assert "browser" in g["tools"]
    assert any("browser fail" in e for e in g["edges"])
    assert any("WEBGL" in s for s in g["standards"])
    rec = pick_recovery({"proof_failed": True, "needs_npm": True})
    assert rec is not None
    assert rec["next"] == "write_file"
    assert "browser" in rec["forbid"]


def test_inspect_digest_includes_graph(tmp_path, monkeypatch):
    from daedalus import mentor

    loop = _vite_root(tmp_path, monkeypatch)
    digest = mentor.inspect_digest(loop)
    assert digest["playbook"] == "playground.vite"
    graph = digest["graph"]
    assert "write_file" in graph["tools"]
    assert graph["flow"]
    assert digest["flags"]["has_phaser_boot"] is True


def test_build_user_prompt_includes_graph():
    from daedalus.mentor_graph import for_inspect
    from daedalus.mentor_helm import build_user_prompt

    inspect = {
        "kind": "vite",
        "playbook": "playground.vite",
        "scaffold": False,
        "loop_ok": True,
        "has_src_main": True,
        "has_index_html": True,
        "play_url": "",
        "proof_blank": False,
        "flags": {"proof_failed": True, "kernel_proof_ran": True},
        "last_fails": ["proof:Must set explicit renderType"],
        "native_hint": "",
        "graph": for_inspect("playground.vite", flags={"proof_failed": True}),
    }
    raw = build_user_prompt(inspect)
    body = json.loads(raw)
    assert body["graph"]["playbook"] == "playground.vite"
    assert body["graph"]["recovery"]["next"] == "write_file"
    assert "browser fail" in " ".join(body["graph"]["edges"])


def test_guard_clamps_browser_after_proof_fail():
    from daedalus.mentor_graph import for_inspect, guard_steer

    flags = {"proof_failed": True, "kernel_proof_ran": True}
    inspect = {
        "kind": "vite",
        "loop_ok": True,
        "flags": flags,
        "graph": for_inspect("playground.vite", flags=flags),
    }
    rules = {
        "action": "call_tool",
        "next_tool": "write_file",
        "next_args": {"path": "src/main.js"},
        "reason": "fix WEBGL",
        "forbid_tools": ["browser"],
    }
    parsed = {
        "action": "call_tool",
        "next_tool": "browser",
        "next_args": {"slug": "x"},
        "reason": "open it again",
        "forbid_tools": [],
    }
    out = guard_steer(parsed, inspect, rules)
    assert out["next_tool"] == "write_file"
    assert "browser" in out["forbid_tools"]


def test_guard_ships_when_proof_painted():
    from daedalus.mentor_graph import for_inspect, guard_steer

    flags = {"kernel_proof_ran": True, "entry_boots": True}
    inspect = {
        "kind": "vite",
        "loop_ok": True,
        "flags": flags,
        "graph": for_inspect("playground.vite", flags=flags),
    }
    rules = {"action": "ship", "next_tool": None, "next_args": {}, "reason": "done", "forbid_tools": []}
    parsed = {
        "action": "call_tool",
        "next_tool": "run_command",
        "next_args": {"command": "npm run build"},
        "reason": "build again",
        "forbid_tools": [],
    }
    out = guard_steer(parsed, inspect, rules)
    assert out["action"] == "ship"
    assert out["next_tool"] is None


def test_consult_clamps_browser_after_proof_fail(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path))
    monkeypatch.setenv("DAEDALUS_MENTOR_LLM", "1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    from daedalus.paths import data_root
    data_root.cache_clear()
    from daedalus import mentor_helm
    from daedalus.mentor_graph import for_inspect

    mentor_helm.set_choice({"choice_id": "or-claude-sonnet"})

    async def fake(_provider, _model, _user):
        return json.dumps({
            "action": "call_tool",
            "next_tool": "browser",
            "next_args": {"slug": "glass-orchard"},
            "reason": "Look again.",
            "forbid_tools": [],
            "blockers": [],
        })

    monkeypatch.setattr("daedalus.mentor_helm._complete_director", fake)
    flags = {"proof_failed": True, "kernel_proof_ran": True}
    inspect = {
        "kind": "vite",
        "scaffold": False,
        "loop_ok": True,
        "has_src_main": True,
        "has_index_html": True,
        "play_url": "",
        "proof_blank": False,
        "flags": flags,
        "last_fails": ["proof:renderType"],
        "native_hint": "",
        "graph": for_inspect("playground.vite", flags=flags),
    }
    rules = {
        "ok": True,
        "action": "call_tool",
        "next_tool": "write_file",
        "next_args": {"path": "src/main.js"},
        "reason": "WEBGL",
        "forbid_tools": ["browser"],
        "blockers": [],
    }
    out = asyncio.run(mentor_helm.consult(inspect, rules))
    assert out["next_tool"] == "write_file"
    assert "browser" in out["forbid_tools"]


def test_record_outcome_queues_correction(tmp_path, monkeypatch):
    from daedalus import mentor
    from daedalus.learned_skills import list_pending, pending_corrections

    loop = _vite_root(tmp_path, monkeypatch, auto_canvas=True)
    loop.kernel_proof_ran = True
    loop.proof_ok = False
    loop.proof_errors = ["Must set explicit renderType in custom environment"]
    loop.stop_reason = "round budget exhausted"
    out = mentor.record_outcome(loop)
    assert out is not None
    assert out["kind"] == "correction"
    assert out["next_tool"] == "write_file"
    rows = pending_corrections()
    assert rows
    rec = rows[0]
    assert rec["origin"] == "mentor_correction"
    assert rec["extra"]["next_tool"] == "write_file"
    blob = (rec["extra"].get("kernel_hint") or "") + " " + (rec.get("body") or "")
    assert "write_file" in blob
    assert "renderType" in blob or "Chromium" in blob or "WEBGL" in blob
    assert mentor.record_outcome(loop) == out
    assert len(list_pending()) == 1


def test_mentor_status_lists_pending_corrections(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from daedalus.learned_skills import queue_correction
    from daedalus.paths import data_root

    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path))
    monkeypatch.delenv("DAEDALUS_MENTOR", raising=False)
    data_root.cache_clear()
    queue_correction(
        kind="vite",
        playbook="playground.vite",
        errors=["proof:renderType"],
        next_tool="write_file",
        hint="set type: Phaser.WEBGL",
    )
    client = TestClient(app)
    got = client.get("/api/v1/daedalus/mentor")
    assert got.status_code == 200
    body = got.json()
    assert body["pending_corrections"] >= 1
    assert body["corrections"][0]["next_tool"] == "write_file"


def test_canvas_ok_summary_does_not_claim_zero_dom_text():
    from daedalus.playground_browser import paint_summary

    ok = paint_summary(
        reachable=True,
        dark_frame=False,
        blank_screen=False,
        has_fatal=False,
        has_canvas=True,
        page_errors=[],
        console_errors=[],
        failed_requests=[],
        dom_text_len=0,
    )
    assert "Canvas/WebGL painted" in ok
    assert "0" not in ok or "DOM text" in ok
    assert "видим текст" not in ok
    html = paint_summary(
        reachable=True,
        dark_frame=False,
        blank_screen=False,
        has_fatal=False,
        has_canvas=False,
        page_errors=[],
        console_errors=[],
        failed_requests=[],
        dom_text_len=12,
    )
    assert "12" in html
