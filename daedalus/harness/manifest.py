"""Hand-written part of the capability registry: what cannot be derived from code.

Everything derivable (tools, routes) is generated in ``registry``. This file only names the
Studio panels, settings and workflows, the routers to introspect, and per-item overrides.
``status`` values: implemented | partial | stub | retired | planned (aspirational, not built).
"""
from __future__ import annotations

# (module, attribute) of every APIRouter mounted in app/main.py. The app's own @app routes are
# added from the app instance. Introspecting the routers (not app.routes) keeps this stable
# across FastAPI versions, which wrap included routers differently.
ROUTER_SOURCES: tuple[tuple[str, str], ...] = (
    ("app.playground_api", "router"),
    ("daedalus.playground_router", "router"),
    ("app.admin_compat", "router"),
    ("app.harness_api", "router"),
    ("app.harness_core_api", "router"),
    ("daedalus.skill_catalog", "catalog_router"),
)

# The catch-all that answers every unknown /admin/daedalus/* call with a 404 stub.
CATCH_ALL_PATH = "/admin/daedalus/{rest:path}"

# Path prefix -> surface label. First match wins.
SURFACES: tuple[tuple[str, str], ...] = (
    ("/api/v1/harness/", "harness-core"),
    ("/api/v1/", "api-v1"),
    ("/admin/", "legacy-admin"),
    ("/apps/", "published-apps"),
)

# Route -> status overrides ("METHOD path").
ROUTE_STATUS: dict[str, str] = {
    # Needs the legacy ADN host to apply; always fails standalone (outbox.py).
    "POST /admin/daedalus/outbox/{item_id}/apply": "partial",
}

# Tool cost class. Anything not listed is "none" (local, no model or network call).
TOOL_COST: dict[str, str] = {
    "web_search": "external", "web_fetch": "external", "web_read": "external",
    "generate_image": "llm", "spawn_subagent": "llm", "dissect_game": "llm",
    "playtest": "llm", "mirofish": "external", "mcp_call": "external",
    "git_forge": "external", "postgres_query": "external", "browser": "external",
    "play_lab": "external", "assets": "unknown",
}

# Highest proof level a tool can reach on its own (see spec 3.7).
TOOL_PROOF: dict[str, int] = {"browser": 2, "playtest": 2, "engine": 1, "game_lint": 0, "world": 0}

# Studio panels, header settings and workflow steps. id -> (title, status, notes).
PANELS: dict[str, tuple[str, str, str]] = {
    "panel:rail": ("Projects and files rail", "implemented", "studio.html left rail"),
    "panel:chat": ("Chat", "implemented", "chat jobs"),
    "panel:build": ("Build", "implemented", "brainstorm, GDD, guided/autopilot"),
    "panel:preview": ("Preview and publish", "implemented", "run, stop, preview, publish, capsule, git menu"),
    "panel:world": ("World / tools rail", "implemented", "right rail: shell, flight, brain, world"),
    "panel:settings": ("Settings", "implemented", "brain readout, git provider, connectors, secrets"),
    "panel:review/proposals": ("Review: proposals", "implemented", ""),
    "panel:review/skills": ("Review: skills", "implemented", ""),
    "panel:review/bundles": ("Review: bundles", "implemented", ""),
    "panel:review/permissions": ("Review: permissions", "implemented", ""),
    "panel:review/outbox": ("Review: outbox", "implemented", ""),
    "panel:review/blockers": ("Review: blockers", "implemented", "list + resolve only"),
    "panel:review/planner": ("Review: planner", "implemented", "list, auto, approve, delete only"),
    "panel:review/discoveries": ("Review: discoveries", "implemented", "approve, reject only"),
    "panel:review/skill-creator": ("Review: skill creator", "implemented", ""),
    "panel:review/reference-scout": ("Review: reference scout", "implemented", ""),
    # Target homes for legacy-only capabilities (built in A6; aspirational today).
    "panel:review/memory": ("Review: memory", "planned", "A6.2"),
    "panel:review/radar-journal": ("Review: radar and journal", "planned", "A6.2, after the MR126 hold"),
    "panel:settings/worker": ("Settings: workers and providers", "planned", "A6.1"),
    "panel:settings/connections": ("Settings: connection tests, MCP, durability", "planned", "A6.1"),
    "panel:settings/kernel": ("Settings: kernel push", "planned", "A6.1 (R1 kept)"),
    "panel:settings/auto-apply": ("Settings: auto-apply and upgrade scan", "planned", "A6.1 (R6 kept)"),
    "panel:settings/intents": ("Settings: per-intent models", "planned", "A2 routes, A6.1 UI"),
    "panel:settings/engines": ("Settings: engine binaries and proof levels", "planned", "A5, A6.1"),
    "panel:chat/attachments": ("Chat: documents and project chat", "planned", "A6.4"),
    "panel:build/controls": ("Build: autopilot controls, Unity, presets, dissect", "planned", "A6.3"),
    "panel:preview/apps": ("Preview: app data editor and tunnel", "planned", "A6.3"),
    "panel:rail/git": ("Project git menu: browse and status", "planned", "A6.3"),
}

SETTINGS: dict[str, tuple[str, str]] = {
    "setting:mentor": ("Mentor on/off, model, token cap", "implemented"),
    "setting:writer_assist": ("Writer assist", "implemented"),
    "setting:run_budget": ("Run budget", "implemented"),
    "setting:effort": ("Effort (client state)", "implemented"),
    "setting:language": ("Language EN/BG (client state)", "implemented"),
    "setting:brain": ("Brain provider and model", "partial"),
    "setting:intents": ("Per-intent model overrides", "planned"),
}

WORKFLOWS: dict[str, tuple[str, str]] = {
    "workflow:idea-to-gdd": ("Brainstorm and GDD", "implemented"),
    "workflow:build-guided": ("Guided build", "implemented"),
    "workflow:build-autopilot": ("Autopilot build", "implemented"),
    "workflow:build-full-auto": ("Full auto M0 to M8", "partial"),
    "workflow:preview-run": ("Run, stop, preview", "implemented"),
    "workflow:verify": ("Verify gates, browser and gameplay proof", "implemented"),
    "workflow:native-playtest": ("Native rendered playtest with real input", "partial"),  # Godot on Linux (A5a); Unity/Windows pending
    "workflow:publish": ("Publish local, live, capsule", "implemented"),
    "workflow:git": ("Git push, PR/MR, clone, revive", "implemented"),
}
