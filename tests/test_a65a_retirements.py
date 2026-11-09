"""A6.5a: the owner-signed retirements R2–R5 (2026-11-09) are carried out and stay carried out.

R2 outbox apply, R3 classic Playground buttons that only the old catch-all answered, R4 unmounted routers,
R5 ADN-only paths (Sonar search, NEED_MESH / mesh_call, POST /api/iyveris, discovery-to-plan via the mesh registry).
"""
from __future__ import annotations

import asyncio
import importlib
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
PLAYGROUND = ROOT / "ui" / "templates" / "daedalus_playground.html"

R3_SUFFIXES = (
    "ai-build", "revive-pipeline", "revive-repair", "upstream-diff", "eas-build", "delete-published",
    "/publish`", "/rebuild", "/unpublish", "assets/raw", "netlify-deploy", "studio/unity-export",
)
R3_FUNCTIONS = (
    "createAndRaw", "aiBuildStream", "reconnectBuild", "streamBuild", "maybeReconnectBuild", "switchToRawBuild",
    "stopBuild", "resumeBuild", "correctBuild", "rewriteBuild", "revivePipeline", "showUpstreamDiff", "easBuild",
    "deletePublishedProj", "publishProj", "rebuildProj", "unpublishProj", "assetThumbUrl", "netlifyDeploy",
    "exportUnityBridge",
)


@pytest.fixture
def standalone(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path / "projects"))
    from daedalus.paths import data_root, playground_projects_dir
    data_root.cache_clear()
    playground_projects_dir.cache_clear()
    yield tmp_path
    data_root.cache_clear()
    playground_projects_dir.cache_clear()


def _page() -> str:
    return PLAYGROUND.read_text(encoding="utf-8")


# ---- R3 -----------------------------------------------------------------------------------------

def test_classic_playground_no_longer_calls_any_r3_endpoint():
    page = _page()
    hits = [suf for suf in R3_SUFFIXES if suf in page]
    assert hits == [], hits


def test_classic_playground_has_no_r3_function_and_no_button_calls_a_missing_function():
    page = _page()
    for name in R3_FUNCTIONS:
        assert not re.search(rf"\bfunction {name}\(", page), name
        assert f"{name}(" not in page, name
    defined = set(re.findall(r"\bfunction\s+([A-Za-z_$][\w$]*)\s*\(", page))
    defined |= set(re.findall(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=", page))
    called = set(re.findall(r'onclick="([A-Za-z_$][\w$]*)\(', page))
    known_globals = {"event", "alert", "confirm", "DaedalusUI", "window", "document"}
    assert sorted(called - defined - known_globals) == []


def test_classic_playground_raw_mode_elements_are_gone_and_build_modes_stay():
    page = _page()
    for el in ('id="tab-raw"', 'id="bd-body"', 'id="bd-cmd"', 'id="bd-foot"', 'id="plan-unity-export-btn"'):
        assert el not in page, el
    for mode in ("'auto'", "'chat'", "'plan'"):
        assert f"setBuildMode({mode})" in page
    assert "$('bd-foot')" not in page and "isRaw" not in page


def test_an_idea_goes_to_project_chat_instead_of_the_missing_raw_build():
    body = _page().split("function applySuggestion(idx) {", 1)[1].split("\n}\n", 1)[0]
    assert "openChatModal(slug, name || slug, text)" in body


def test_r3_routes_are_still_unserved(standalone):
    from app.main import app
    client = TestClient(app)
    base = "/admin/daedalus/playground/api/projects/demo"
    for method, tail in (("post", "/ai-build"), ("post", "/publish"), ("post", "/unpublish"), ("post", "/rebuild"),
                         ("post", "/delete-published"), ("post", "/netlify-deploy"), ("get", "/upstream-diff"),
                         ("get", "/assets/raw?path=a.png"), ("post", "/studio/unity-export"), ("post", "/eas-build"),
                         ("post", "/revive-pipeline"), ("post", "/revive-repair")):
        res = getattr(client, method)(base + tail, json={}) if method == "post" else client.get(base + tail)
        assert res.status_code == 404, (tail, res.status_code)


# ---- R4 -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("mod", ["daedalus.builder_router", "daedalus.knowledge_books"])
def test_unmounted_routers_are_deleted(mod):
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(mod)


# ---- R5 -----------------------------------------------------------------------------------------

def test_need_search_uses_the_kept_ddg_path_not_the_old_sonar_host():
    src = (ROOT / "daedalus" / "agent.py").read_text(encoding="utf-8")
    assert "smart_search" not in src and 'mode="sonar"' not in src
    assert "web_q = hit.web_q or search_q" in src          # NEED_SEARCH runs the one NEED_WEB search
    assert "            if search_q:\n" not in src           # no second, duplicate search branch


def test_mesh_call_tool_and_agent_method_are_gone():
    import daedalus.agent as agent
    import daedalus.tools as tools
    assert not hasattr(tools, "mesh_call_tool")
    assert not hasattr(agent.Daedalus if hasattr(agent, "Daedalus") else object, "mesh_call")
    assert not hasattr(agent, "_format_mesh_call_result")
    assert "mesh_call_tool" not in (ROOT / "daedalus" / "agent.py").read_text(encoding="utf-8")


def test_a_stray_need_mesh_marker_is_answered_as_retired_and_calls_nothing():
    src = (ROOT / "daedalus" / "agent.py").read_text(encoding="utf-8")
    block = src.split("            if mesh_marker:\n", 1)[1].split("            if skill_q:\n", 1)[0]
    assert '"error": "retired"' in block and "Nothing was called" in block
    assert "await " not in block


def test_prompts_and_navigator_no_longer_teach_need_mesh():
    from daedalus.identity import build_persona
    from daedalus.identity_standalone import DAEDALUS_STANDALONE_PREAMBLE
    from daedalus.navigator_vocab import TOOLS_AVAILABLE
    from daedalus import playbooks
    persona = build_persona()
    assert "<NEED_MESH:" not in persona and "MESH PROTOCOL" not in persona
    assert "X-Iyveris-Mesh-Key" not in DAEDALUS_STANDALONE_PREAMBLE
    assert "NEED_MESH" not in TOOLS_AVAILABLE
    assert playbooks.get("mesh-integration") is None
    assert not (ROOT / "daedalus" / "skills" / "mesh_call.md").exists()


def test_probing_another_service_is_honestly_unavailable():
    from daedalus import probe
    res = asyncio.run(probe.probe_service("stock-argos", "file_tree"))
    assert res == {"ok": False, "error": "mesh_retired", "slug": "stock-argos"}


def test_approving_a_discovery_schedules_no_plan(monkeypatch):
    import daedalus.agent as agent_mod

    class Col:
        def get(self, ids):
            return {"ids": ids, "metadatas": [{"title": "t", "url": "u", "kind": "discovery"}], "documents": ["d"]}

    class Mem:
        discoveries_col = Col()
        lessons: list = []

        def set_discovery_status(self, did, status):
            return status == "approved"

        def capture_lesson(self, **kw):
            self.lessons.append(kw)

    import daedalus.plans as plans_mod

    async def _boom(*a, **k):
        raise AssertionError("approve must not generate a plan")

    monkeypatch.setattr(plans_mod, "generate_plan", _boom)
    self_ = type("D", (), {})()
    self_.memory = Mem()
    cls = next(v for v in vars(agent_mod).values() if isinstance(v, type) and hasattr(v, "approve_discovery"))
    res = cls.approve_discovery(self_, "disc-1")
    assert res == {"discovery_id": "disc-1", "approved": True, "plan_id": None}
    assert Mem.lessons and not hasattr(cls, "_schedule_plan_generation")


def test_regenerate_plan_no_longer_reads_the_old_mesh_registry():
    for path in (ROOT / "daedalus" / "harness" / "planner_actions.py", ROOT / "app" / "admin_compat.py"):
        assert "argos_data_nexus import mesh" not in path.read_text(encoding="utf-8"), path


def test_parity_records_every_signed_retirement_as_carried_out():
    from daedalus.harness.parity import PARITY_ROWS
    by_id = {r.id: r for r in PARITY_ROWS}
    assert by_id["retire-outbox-apply"].disposition == "retired:R2"
    assert by_id["retire-dead-buttons"].disposition == "retired:R3" and len(by_id["retire-dead-buttons"].routes) == 15
    assert by_id["retire-unmounted-routers"].disposition == "retired:R4"
    assert by_id["retire-adn-only"].disposition == "retired:R5"
    assert by_id["retire-adn-only"].routes == ("POST /api/iyveris",)
    assert not [r.id for r in PARITY_ROWS if r.disposition.startswith("retire:")]
