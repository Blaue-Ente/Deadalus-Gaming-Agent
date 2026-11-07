"""No-loss parity table: every legacy function and where it must live in the unified Studio.

A row is *resolved* when its ``home`` exists in the registry and every route it names is
actually served. Dispositions: ``in_studio`` (already reachable from Studio ``/``),
``port:A6.x`` (legacy-only today; must move before its old page is redirected),
``held:MR126`` (blocked on the radar/journal hold), ``retire:Rn`` (owner-approved retirement).
Removing or redirecting a legacy page is only allowed once every row naming it is ``in_studio``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

DISPOSITIONS_PREFIX = ("in_studio", "port:", "held:", "retire:")


@dataclass(frozen=True)
class ParityRow:
    id: str
    title: str
    legacy: tuple[str, ...]          # classic chat / playground / game studio / connectors / ...
    home: str | None                 # registry panel/setting id (None only for retire rows)
    routes: tuple[str, ...]          # "METHOD path" that must exist in the route table
    disposition: str

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "title": self.title, "legacy": list(self.legacy), "home": self.home,
                "routes": list(self.routes), "disposition": self.disposition}


def _r(id: str, title: str, legacy: str, home: str | None, routes: str, disposition: str) -> ParityRow:
    return ParityRow(id, title, tuple(x for x in legacy.split(",") if x), home,
                     tuple(x.strip() for x in routes.split(";") if x.strip()), disposition)


_A = "/admin/daedalus"
_PG = "/admin/daedalus/playground/api"
_GS = "/admin/daedalus/game-studio/api"

PARITY_ROWS: tuple[ParityRow, ...] = (
    # ---- already reachable from Studio ------------------------------------------------------
    _r("projects", "Project list, open, create, delete", "playground,game-studio", "panel:rail",
       "GET /api/v1/playground/projects; POST /api/v1/playground/projects; DELETE /api/v1/playground/projects/{slug}", "in_studio"),
    _r("chat-jobs", "Chat jobs with cancel and stream", "classic", "panel:chat",
       "POST /api/v1/daedalus/chat/jobs; GET /api/v1/daedalus/chat/jobs/{job_id}; GET /api/v1/daedalus/chat/jobs/{job_id}/stream", "in_studio"),
    _r("header-controls", "Mentor, writer assist, run budget, effort", "classic,hub", "setting:mentor",
       "GET /api/v1/daedalus/mentor; POST /api/v1/daedalus/mentor; GET /api/v1/daedalus/writer-assist; POST /api/v1/daedalus/writer-assist; "
       "GET /api/v1/daedalus/run-budget; POST /api/v1/daedalus/run-budget; GET /api/v1/daedalus/effort; POST /api/v1/daedalus/effort", "in_studio"),
    _r("brainstorm-gdd-start", "Brainstorm, GDD, start", "game-studio,playground", "panel:build",
       f"POST {_GS}/brainstorm; POST {_GS}/gdd; POST {_GS}/start; GET {_GS}/catalog", "in_studio"),
    _r("gates-verify-milestones", "Gates, verify, milestones", "game-studio", "panel:build",
       f"POST {_GS}/projects/{{slug}}/gates; POST {_GS}/projects/{{slug}}/verify; POST {_GS}/projects/{{slug}}/milestones/advance", "in_studio"),
    _r("guided-build", "Guided build start, continue, stop, stream", "game-studio,playground", "panel:build",
       f"POST {_PG}/projects/{{slug}}/guided/start; POST {_PG}/projects/{{slug}}/guided/continue; POST {_PG}/projects/{{slug}}/guided/stop; "
       f"GET {_PG}/projects/{{slug}}/guided/active; GET {_PG}/projects/{{slug}}/guided/stream", "in_studio"),
    _r("autopilot-core", "Autopilot start, stop, stream", "playground", "panel:build",
       "POST /api/v1/playground/projects/{slug}/autopilot; GET /api/v1/playground/projects/{slug}/autopilot/stream; "
       f"POST {_PG}/projects/{{slug}}/autopilot/stop", "in_studio"),
    _r("run-preview", "Run, stop, logs, preview", "playground", "panel:preview",
       f"POST {_PG}/projects/{{slug}}/run; POST {_PG}/projects/{{slug}}/stop; GET {_PG}/projects/{{slug}}/logs; "
       "GET /api/v1/playground/projects/{slug}/preview/{path:path}", "in_studio"),
    _r("publish", "Publish local, live, capsule", "playground", "panel:preview",
       "POST /api/v1/playground/projects/{slug}/publish-local; POST /api/v1/playground/projects/{slug}/live; "
       "GET /api/v1/playground/projects/{slug}/capsule", "in_studio"),
    _r("git-core", "Push, PR/MR, clone, revive", "playground", "panel:preview",
       f"POST {_PG}/projects/{{slug}}/push; POST {_PG}/projects/{{slug}}/create-upstream-pr; POST {_PG}/clone; POST {_PG}/revive", "in_studio"),
    _r("proposals", "Proposals approve, reject, rollback", "classic,proposals", "panel:review/proposals",
       f"GET {_A}/proposals/api; POST {_A}/proposals/{{proposal_id}}/approve; POST {_A}/proposals/{{proposal_id}}/reject; "
       f"POST {_A}/proposals/{{proposal_id}}/rollback", "in_studio"),
    _r("skills-toggle", "Skills toggle", "classic,skills", "panel:review/skills",
       f"GET {_A}/skills/api; POST {_A}/skills/{{skill_name}}/toggle", "in_studio"),
    _r("bundles", "Bundles apply, reject", "classic,bundles", "panel:review/bundles",
       f"GET {_A}/bundles/api; POST {_A}/bundles/{{bundle_id}}/apply; POST {_A}/bundles/{{bundle_id}}/reject", "in_studio"),
    _r("permissions-basic", "Permissions grant, revoke", "classic", "panel:review/permissions",
       f"GET {_A}/permissions; POST {_A}/permissions/grant; POST {_A}/permissions/{{perm_class}}/revoke", "in_studio"),
    _r("outbox-basic", "Outbox list, dismiss", "classic", "panel:review/outbox",
       f"GET {_A}/outbox/api; POST {_A}/outbox/{{item_id}}/dismiss", "in_studio"),
    _r("blockers-basic", "Blockers list, resolve", "classic", "panel:review/blockers",
       f"GET {_A}/blockers; POST {_A}/blockers/{{blocker_id}}/resolve", "in_studio"),
    _r("planner-basic", "Planner list, auto, approve, delete", "classic", "panel:review/planner",
       f"GET {_A}/planner/plans; POST {_A}/planner/plans/auto; POST {_A}/planner/plans/{{plan_id}}/approve; DELETE {_A}/planner/plans/{{plan_id}}", "in_studio"),
    _r("discoveries-basic", "Discoveries list, approve, reject", "classic", "panel:review/discoveries",
       f"GET {_A}/discoveries; POST {_A}/discoveries/{{discovery_id}}/approve; POST {_A}/discoveries/{{discovery_id}}/reject", "in_studio"),
    _r("skill-creator", "Skill creator start, refine, generate", "playground", "panel:review/skill-creator",
       f"POST {_PG}/skill-creator/start; POST {_PG}/skill-creator/refine; POST {_PG}/skill-creator/generate", "in_studio"),
    _r("reference-scout", "Reference scout", "playground", "panel:review/reference-scout",
       f"POST {_PG}/reference-scout", "in_studio"),
    _r("settings-connectors", "Connector config and write-only secrets", "connectors", "panel:settings",
       f"GET {_A}/connectors/config; POST {_A}/connectors/config; GET {_A}/connectors/overview; POST {_A}/connectors/secrets", "in_studio"),
    # ---- legacy-only today: must be ported before the old pages are redirected --------------
    _r("chat-upload-doc", "Upload a document into the chat", "classic", "panel:chat/attachments",
       f"POST {_A}/upload-doc", "port:A6.4"),
    _r("chat-project", "Project chat, apply, diff preview", "playground", "panel:chat/attachments",
       f"POST {_PG}/projects/{{slug}}/chat; POST {_PG}/projects/{{slug}}/chat/apply; POST {_PG}/projects/{{slug}}/diff-preview", "port:A6.4"),
    _r("chat-classic-blocking", "Classic blocking chat", "classic", "panel:chat/attachments",
       f"POST {_A}/chat", "port:A6.4"),
    _r("settings-workers", "Worker config, LLM config, provider test", "classic", "panel:settings/worker",
       f"GET {_A}/worker-config; GET {_A}/llm-config; POST {_A}/llm-config; POST {_A}/test-provider", "port:A6.1"),
    _r("settings-connection-tests", "GitHub/GitLab tests, MCP toggle, durability", "connectors", "panel:settings/connections",
       f"POST {_A}/connectors/test-github; POST {_A}/connectors/test-gitlab; POST {_A}/connectors/mcp; GET {_PG}/durability/status", "port:A6.1"),
    _r("settings-kernel", "Kernel config and push (re-pointed to this repo)", "classic", "panel:settings/kernel",
       f"GET {_A}/kernel/config; POST {_A}/kernel/config; POST {_A}/kernel/push", "port:A6.1"),
    _r("settings-auto-apply", "Auto-apply and upgrade-pool scan", "classic", "panel:settings/auto-apply",
       f"GET {_A}/settings/auto-apply; POST {_A}/settings/auto-apply; POST {_A}/upgrade-pool-scan", "port:A6.1"),
    _r("build-autopilot-controls", "Autopilot pause, resume, resume-info, clear plan, plan create, expand", "playground", "panel:build/controls",
       f"POST {_PG}/projects/{{slug}}/autopilot/pause; POST {_PG}/projects/{{slug}}/autopilot/resume; GET {_PG}/projects/{{slug}}/autopilot/resume-info; "
       f"POST {_PG}/projects/{{slug}}/autopilot/clear-plan; POST {_PG}/projects/{{slug}}/plan/create; POST {_PG}/projects/{{slug}}/plan/expand-ultra", "port:A6.3"),
    _r("build-unity", "Unity init, templates, build scripts, studio presets", "playground,game-studio", "panel:build/controls",
       f"POST {_PG}/projects/{{slug}}/unity/init; POST {_PG}/projects/{{slug}}/unity/attach-template; "
       f"POST {_PG}/projects/{{slug}}/unity/generate-build-scripts; GET {_PG}/unity/templates; GET {_PG}/studio/presets", "port:A6.3"),
    _r("build-dissect-lab", "Dissect a reference and manage labs", "playground,game-studio", "panel:build/controls",
       f"POST {_PG}/dissect; POST {_PG}/projects/{{slug}}/dissect; GET {_PG}/labs; POST {_PG}/labs", "port:A6.3"),
    _r("build-full-auto", "Full auto M0 to M8 as a server-side run", "game-studio", "panel:build/controls",
       f"POST {_GS}/projects/{{slug}}/build/guided", "port:A6.3"),
    _r("git-browse", "GitHub repos/branches, compat check, GitLab projects, git status", "playground", "panel:rail/git",
       f"GET {_PG}/github/repos; GET {_PG}/github/repos/{{owner}}/{{repo}}/branches; POST {_PG}/github/compat-check; "
       f"GET {_PG}/gitlab/projects; GET {_PG}/git/status", "port:A6.3"),
    _r("preview-app-data", "Per-app data editor", "playground", "panel:preview/apps",
       "GET /apps/{slug}/db/; POST /apps/{slug}/db/{key:path}; DELETE /apps/{slug}/db/{key:path}", "port:A6.3"),
    _r("skill-creator-gist", "Publish a skill as a gist (confirm-gated)", "playground", "panel:review/skill-creator",
       f"POST {_PG}/skill-creator/github-gist", "port:A6.3"),
    _r("review-permissions-bulk", "Permissions grant-all and renew-expired", "classic", "panel:review/permissions",
       f"POST {_A}/permissions/grant-all; POST {_A}/permissions/renew-expired", "port:A6.2"),
    _r("review-blockers-actions", "Blockers auto-fix, remediate, grant-recommended", "classic", "panel:review/blockers",
       f"POST {_A}/blockers/auto-fix-all; POST {_A}/blockers/grant-recommended; POST {_A}/blockers/{{blocker_id}}/auto-fix; "
       f"POST {_A}/blockers/{{blocker_id}}/remediate", "port:A6.2"),
    _r("review-planner-extras", "Planner manual create, steps, mark, override, pause, resume, progress, run-background", "classic",
       "panel:review/planner",
       f"POST {_A}/planner/plans; POST {_A}/planner/plans/{{plan_id}}/steps; POST {_A}/planner/plans/{{plan_id}}/steps/{{step_id}}/mark; "
       f"POST {_A}/planner/steps/{{step_id}}/override; POST {_A}/planner/plans/{{plan_id}}/pause; POST {_A}/planner/plans/{{plan_id}}/resume; "
       f"GET {_A}/planner/plans/{{plan_id}}/progress; POST {_A}/run-background", "port:A6.2"),
    _r("review-discoveries-extras", "Discoveries delete and regenerate-plan", "classic", "panel:review/discoveries",
       f"DELETE {_A}/discoveries/{{discovery_id}}; POST {_A}/discoveries/{{discovery_id}}/regenerate-plan", "port:A6.2"),
    _r("review-details", "Proposal detail, bundle detail, approve-all", "proposals,bundles", "panel:review/proposals",
       f"GET {_A}/proposals/{{proposal_id}}; GET {_A}/bundles/api/{{bundle_id}}; POST {_A}/proposals/approve-all-pending", "port:A6.2"),
    _r("review-memory", "Memory stats, episodic, consolidate, navigator", "classic", "panel:review/memory",
       f"GET {_A}/memory/stats; GET {_A}/memory/episodic; DELETE {_A}/memory/episodic/{{fact_id}}; POST {_A}/memory/consolidate; "
       f"GET {_A}/memory/consolidations; GET {_A}/navigator/stats; POST {_A}/navigator/reset", "port:A6.2"),
    _r("review-ledger", "Tasks, sessions, progress, mode, manual search, clarification stats, style lesson", "classic", "panel:review/memory",
       f"GET {_A}/tasks; POST {_A}/tasks; GET {_A}/sessions; GET {_A}/progress; POST {_A}/progress/clear; GET {_A}/mode; POST {_A}/mode; "
       f"POST {_A}/web-search; POST {_A}/github-search; GET {_A}/clarification-stats; POST {_A}/generate-style-lesson", "port:A6.2"),
    _r("review-radar-journal", "Radar scan and scratch journal", "classic", "panel:review/radar-journal",
       f"POST {_A}/radar/scan; GET {_A}/radar/state; GET {_A}/radar/sources; GET {_A}/journal/list; POST {_A}/journal/{{session_id}}/pause; "
       f"POST {_A}/journal/{{session_id}}/clear", "held:MR126"),
    # ---- owner-approved retirements (nothing is deleted before the A6.5 sign-off) -------------
    _r("retire-outbox-apply", "Outbox apply and raw SQL (needs the legacy ADN host)", "classic", None,
       f"POST {_A}/outbox/{{item_id}}/apply", "retire:R2"),
    _r("retire-dead-buttons", "Playground buttons served only by the old catch-all", "playground", None, "", "retire:R3"),
    _r("retire-unmounted-routers", "builder_router and knowledge_books (never mounted)", "none", None, "", "retire:R4"),
    _r("retire-adn-only", "ADN-only paths (Sonar search, mesh_call, /api/iyveris, discovery-to-plan)", "classic", None, "", "retire:R5"),
)


def get(row_id: str) -> ParityRow | None:
    return next((r for r in PARITY_ROWS if r.id == row_id), None)


def check_parity(capabilities: list[Any]) -> list[str]:
    """Problems that make a row unresolved. Empty list = every row resolves."""
    ids = {c.id for c in capabilities}
    routes = {h for c in capabilities if c.kind == "route" for h in c.http}
    problems: list[str] = []
    seen: set[str] = set()
    for row in PARITY_ROWS:
        if row.id in seen:
            problems.append(f"{row.id}: duplicate row id")
        seen.add(row.id)
        if not row.disposition.startswith(DISPOSITIONS_PREFIX):
            problems.append(f"{row.id}: unknown disposition {row.disposition!r}")
        if row.disposition.startswith("retire:"):
            if row.home is not None:
                problems.append(f"{row.id}: a retired row must not name a home")
        elif row.home not in ids:
            problems.append(f"{row.id}: home {row.home!r} is not in the registry")
        if row.disposition == "in_studio" and not row.routes:
            problems.append(f"{row.id}: an in_studio row must name at least one route")
        if row.disposition.startswith("retire:"):
            continue  # retired routes disappear when the retirement is carried out
        for route in row.routes:
            if route not in routes:
                problems.append(f"{row.id}: route not served: {route}")
    return problems
