"""Scan NEED_* markers in an LLM reply into one record.

The action loop in ``agent.py`` still *executes* markers; this module only
parses. Prefer ``tool_protocol`` for anything that maps onto a native tool.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from .batch_verify import extract_batch_block, extract_verify_block
from .edit_diff import extract_edit_markers
from .tool_parser import get_parser
from .workers import parse_worker_markers

_READ_FILE_RE = re.compile(r"<NEED_READ_FILE:\s*([^>|]{1,300})(?:\|\s*(\d+)-(\d+))?\s*>")
_LOGS_RE = re.compile(r"<NEED_LOGS:\s*(\d+)(?:\s*\|\s*([^>]{1,200}))?\s*>")
_SANDBOX_RE = re.compile(r"<NEED_SANDBOX>\s*\n(.*?)\n\s*</NEED_SANDBOX>", re.DOTALL)
_MESH_RE = re.compile(
    r"<NEED_MESH:\s*([\w-]+)\s*:\s*([\w.]+)\s*:\s*(\{[^<>]*?\})\s*>",
    re.DOTALL,
)


def extract_marker(raw: str, name: str) -> str | None:
    if not raw or not name:
        return None
    m = re.search(rf"<{re.escape(name)}:\s*([^>]{{1,300}})>", raw)
    if not m:
        return None
    q = m.group(1).strip()
    return q or None


@dataclass
class NeedScan:
    search_q: str | None = None
    web_q: str | None = None
    fetch_url: str | None = None
    screenshot_u: str | None = None
    gh_q: str | None = None
    mesh_marker: tuple[str, str, dict[str, Any]] | None = None
    skill_q: str | None = None
    doc_q: str | None = None
    pg_q: str | None = None
    backup_q: str | None = None
    gh_repo_q: str | None = None
    gh_issue_q: str | None = None
    pypi_q: str | None = None
    npm_q: str | None = None
    docs_url: str | None = None
    dep_q: str | None = None
    git_log_q: str | None = None
    git_diff_q: str | None = None
    sandbox_code: str | None = None
    batch_block: str | None = None
    verify_block: str | None = None
    edit_markers: list = field(default_factory=list)
    parsed_calls: list = field(default_factory=list)
    endpoints_q: str | None = None
    modules_q: str | None = None
    db_schema_q: str | None = None
    code_search_q: str | None = None
    capability_q: str | None = None
    ai_digest_q: str | None = None
    skill_catalog_q: str | None = None
    lint_q: str | None = None
    tdd_m: Any = None
    pg_new_m: Any = None
    pg_write_matches: list = field(default_factory=list)
    pg_write_m: Any = None
    pg_run_q: str | None = None
    pg_pub_m: Any = None
    pg_clone_m: Any = None
    pg_push_m: Any = None
    pg_read_m: Any = None
    pg_edit_matches: list = field(default_factory=list)
    pg_db_m: Any = None
    pg_manifest_m: Any = None
    drp_law_q: str | None = None
    drp_cases_q: str | None = None
    drp_search_q: str | None = None
    inbox_msg_m: Any = None
    project_plan_m: Any = None
    data_prop_m: Any = None
    data_prop_truncated: bool = False
    progress_m: Any = None
    journal_m: Any = None
    task_state_m: Any = None
    playbook_m: Any = None
    plan_lock_m: Any = None
    check_perm_m: Any = None
    blocked_m: Any = None
    plan_step_m: Any = None
    read_file_m: Any = None
    read_file_path: str | None = None
    read_file_start: int | None = None
    read_file_end: int | None = None
    logs_m: Any = None
    logs_n: int | None = None
    logs_grep: str | None = None
    worker_calls: list = field(default_factory=list)
    subagent_calls: list = field(default_factory=list)
    bundle_start_title: Any = None
    stage_blocks: list = field(default_factory=list)
    bundle_commit: Any = None

    @property
    def has_markers(self) -> bool:
        return bool(
            self.web_q or self.gh_q or self.mesh_marker or self.skill_q or self.doc_q
            or self.pg_q or self.backup_q or self.gh_repo_q or self.gh_issue_q
            or self.pypi_q or self.npm_q or self.docs_url or self.dep_q
            or self.git_log_q or self.git_diff_q or self.sandbox_code
            or self.read_file_path or self.logs_n
            or self.inbox_msg_m or self.plan_step_m
            or self.endpoints_q or self.modules_q or self.db_schema_q
            or self.code_search_q or self.capability_q or self.ai_digest_q
            or self.skill_catalog_q
            or self.pg_new_m or self.pg_write_matches or self.pg_edit_matches
            or self.pg_run_q or self.pg_pub_m or self.pg_clone_m
            or self.pg_push_m or self.pg_read_m
            or self.drp_law_q or self.drp_cases_q or self.drp_search_q
            or self.data_prop_m or self.progress_m or self.check_perm_m
            or self.blocked_m or self.worker_calls or self.subagent_calls
            or self.project_plan_m
            or self.journal_m or self.task_state_m
            or self.playbook_m or self.plan_lock_m
            or self.batch_block or self.verify_block
            or self.edit_markers
            or self.bundle_start_title or self.stage_blocks or self.bundle_commit
            or self.lint_q or self.tdd_m or self.pg_manifest_m or self.pg_db_m
            or self.search_q or self.fetch_url or self.screenshot_u
        )


def scan_need_markers(raw: str | None) -> NeedScan:
    hit = NeedScan()
    if not raw:
        return hit
    em = extract_marker
    hit.search_q = em(raw, "NEED_SEARCH")
    hit.web_q = em(raw, "NEED_WEB")
    hit.fetch_url = em(raw, "NEED_WEB_FETCH")
    hit.screenshot_u = em(raw, "NEED_WEB_SCREENSHOT")
    hit.gh_q = em(raw, "NEED_GITHUB")
    hit.skill_q = em(raw, "NEED_SKILL")
    hit.doc_q = em(raw, "NEED_DOCUMENT")
    hit.pg_q = em(raw, "NEED_PG")
    hit.backup_q = em(raw, "NEED_BACKUP")
    hit.gh_repo_q = em(raw, "NEED_GH_REPO")
    hit.gh_issue_q = em(raw, "NEED_GH_ISSUE")
    hit.pypi_q = em(raw, "NEED_PYPI")
    hit.npm_q = em(raw, "NEED_NPM")
    hit.docs_url = em(raw, "NEED_DOCS")
    hit.dep_q = em(raw, "NEED_DEP")
    hit.git_log_q = em(raw, "NEED_GIT_LOG")
    hit.git_diff_q = em(raw, "NEED_GIT_DIFF")
    hit.endpoints_q = em(raw, "NEED_ENDPOINTS")
    hit.modules_q = em(raw, "NEED_MODULES")
    hit.db_schema_q = em(raw, "NEED_DB_SCHEMA")
    hit.code_search_q = em(raw, "NEED_CODE_SEARCH")
    hit.capability_q = em(raw, "NEED_CAPABILITY")
    hit.ai_digest_q = em(raw, "NEED_AI_DIGEST")
    hit.skill_catalog_q = em(raw, "NEED_SKILL_CATALOG")
    hit.lint_q = em(raw, "NEED_LINT")
    hit.pg_run_q = em(raw, "NEED_PLAYGROUND_RUN")
    hit.drp_law_q = em(raw, "NEED_DRP_LAW")
    hit.drp_cases_q = em(raw, "NEED_DRP_CASES")
    hit.drp_search_q = em(raw, "NEED_DRP_SEARCH")
    if "NEED_SANDBOX" in raw:
        sm = _SANDBOX_RE.search(raw)
        if sm:
            hit.sandbox_code = sm.group(1).strip() or None
    if "NEED_MESH" in raw:
        mm = _MESH_RE.search(raw)
        if mm:
            try:
                payload = json.loads(mm.group(3).strip())
            except Exception:
                payload = {}
            hit.mesh_marker = (mm.group(1).strip(), mm.group(2).strip(), payload)
    hit.batch_block = extract_batch_block(raw)
    hit.verify_block = extract_verify_block(raw)
    hit.edit_markers = extract_edit_markers(raw) if "NEED_EDIT" in raw else []
    hit.parsed_calls = get_parser().extract_all(raw)
    if "NEED_TDD" in raw:
        from .tdd_loop import parse_tdd_marker
        hit.tdd_m = parse_tdd_marker(raw)
    if "NEED_PLAYGROUND_NEW" in raw:
        hit.pg_new_m = re.search(
            r"<NEED_PLAYGROUND_NEW:\s*([^|>]{1,80})(?:\|([^|>]{1,30}))?(?:\|([^>]{1,300}))?\s*>",
            raw, re.DOTALL,
        )
    if "NEED_PLAYGROUND_WRITE" in raw:
        hit.pg_write_matches = re.findall(
            r"<NEED_PLAYGROUND_WRITE:\s*([^|\n]{1,80})\s*\|\s*([^|\n]{1,300})\s*\|\s*([\s\S]*?)>>>",
            raw,
        )
    if "NEED_PLAYGROUND_PUBLISH" in raw:
        hit.pg_pub_m = re.search(
            r"<NEED_PLAYGROUND_PUBLISH:\s*([^|>]{1,80})(?:\|([^>]{1,30}))?\s*>",
            raw, re.DOTALL,
        )
    if "NEED_PLAYGROUND_CLONE" in raw:
        hit.pg_clone_m = re.search(
            r"<NEED_PLAYGROUND_CLONE:\s*([^|>]{1,300})(?:\|([^>]{1,80}))?\s*>",
            raw, re.DOTALL,
        )
    if "NEED_PLAYGROUND_PUSH" in raw:
        hit.pg_push_m = re.search(
            r"<NEED_PLAYGROUND_PUSH:\s*([^|>]{1,80})(?:\|([^>|]{1,80}))?(?:\|([^>]{1,20}))?\s*>",
            raw, re.DOTALL,
        )
    if "NEED_PLAYGROUND_READ" in raw:
        hit.pg_read_m = re.search(
            r"<NEED_PLAYGROUND_READ:\s*([^|>]{1,80})(?:\|([^>]{1,200}))?\s*>",
            raw, re.DOTALL,
        )
    if "NEED_PLAYGROUND_EDIT" in raw:
        hit.pg_edit_matches = re.findall(
            r"<NEED_PLAYGROUND_EDIT:\s*([^|\n]{1,80})\s*\|\s*([^|\n]{1,300})\s*\|\s*([\s\S]*?)\|\|\|([\s\S]*?)>>>",
            raw,
        )
    if "NEED_PLAYGROUND_DB" in raw:
        hit.pg_db_m = re.search(
            r"<NEED_PLAYGROUND_DB:\s*([^|>]{1,80})\|([^|>]{1,20})(?:\|([^|>]{1,200}))?(?:\|([\s\S]{0,65536}))?\s*>",
            raw, re.DOTALL,
        )
    if "NEED_PLAYGROUND_MANIFEST" in raw:
        hit.pg_manifest_m = re.search(
            r"<NEED_PLAYGROUND_MANIFEST:\s*([^|\n]{1,80})\s*\|\s*([\s\S]*?)>>>",
            raw,
        )
    if "NEED_INBOX_MSG" in raw:
        hit.inbox_msg_m = re.search(
            r"<NEED_INBOX_MSG:\s*([^|>]{1,200})\|([^|>]{1,2000})(?:\|(\d))?\s*>",
            raw, re.DOTALL,
        )
    if "NEED_PROJECT_PLAN" in raw:
        hit.project_plan_m = re.search(
            r"<NEED_PROJECT_PLAN:\s*([^>]{10,2000})\s*>",
            raw, re.DOTALL,
        )
    if "NEED_DATA_PROPOSAL" in raw:
        hit.data_prop_m = re.search(
            r"<NEED_DATA_PROPOSAL:\s*([^|>]{1,80})\|([^|>]{1,200})\|([^>]{1,30000})\s*>",
            raw, re.DOTALL,
        )
        opens = [m.start() for m in re.finditer(r"<NEED_DATA_PROPOSAL:", raw)]
        for i, pos in enumerate(opens):
            nxt = opens[i + 1] if i + 1 < len(opens) else len(raw)
            if ">" not in raw[pos:nxt]:
                hit.data_prop_truncated = True
                break
    if "NEED_PROGRESS" in raw:
        hit.progress_m = re.search(
            r"<NEED_PROGRESS:\s*([^|>]{1,80})\|([0-9.]{1,6})\|([^>]{1,200})\s*>",
            raw, re.DOTALL,
        )
    if "NEED_JOURNAL_WRITE" in raw:
        hit.journal_m = re.search(
            r"<NEED_JOURNAL_WRITE:\s*([^|>]{1,80})\|([^|>]{1,1000})(?:\|([^>]{0,8000}))?\s*>",
            raw, re.DOTALL,
        )
    if "NEED_TASK_STATE" in raw:
        hit.task_state_m = re.search(
            r"<NEED_TASK_STATE:\s*([^|>]{1,200})\|([^|>]{1,100})(?:\|([^>]{0,500}))?\s*>",
            raw, re.DOTALL,
        )
    if "NEED_PLAYBOOK" in raw:
        hit.playbook_m = re.search(r"<NEED_PLAYBOOK:\s*([^>]{1,200})\s*>", raw, re.DOTALL)
    if "NEED_PLAN_LOCK" in raw:
        hit.plan_lock_m = re.search(
            r"<NEED_PLAN_LOCK:\s*([^|>]{1,100})\|([^>]{1,20})\s*>",
            raw, re.DOTALL,
        )
    if "NEED_CHECK_PERM" in raw:
        hit.check_perm_m = re.search(r"<NEED_CHECK_PERM:\s*([a-zA-Z0-9_]{1,80})\s*>", raw)
    if "NEED_BLOCKED" in raw:
        hit.blocked_m = re.search(
            r"<NEED_BLOCKED:\s*([^|>]{1,500})\|([^|>]{1,1000})\|([^>]{1,500})\s*>",
            raw, re.DOTALL,
        )
    if "NEED_PLAN_STEP" in raw:
        hit.plan_step_m = re.search(
            r"<NEED_PLAN_STEP:\s*([^|>]{1,100})\|([^|>]{1,200})(?:\|([^>]{1,500}))?\s*>",
            raw, re.DOTALL,
        )
    hit.read_file_m = _READ_FILE_RE.search(raw) if "NEED_READ_FILE" in raw else None
    if hit.read_file_m:
        hit.read_file_path = hit.read_file_m.group(1).strip()
        hit.read_file_start = int(hit.read_file_m.group(2)) if hit.read_file_m.group(2) else None
        hit.read_file_end = int(hit.read_file_m.group(3)) if hit.read_file_m.group(3) else None
    hit.logs_m = _LOGS_RE.search(raw) if "NEED_LOGS" in raw else None
    if hit.logs_m:
        hit.logs_n = int(hit.logs_m.group(1))
        hit.logs_grep = hit.logs_m.group(2).strip() if hit.logs_m.group(2) else None
    hit.worker_calls = parse_worker_markers(raw)
    try:
        from .subagents import parse_subagent_markers
        hit.subagent_calls = parse_subagent_markers(raw)
    except Exception:
        hit.subagent_calls = []
    try:
        from .bundle import extract_all_stage_blocks, extract_bundle_commit, extract_bundle_start
        hit.bundle_start_title = extract_bundle_start(raw) if "NEED_BUNDLE_START" in raw else None
        hit.stage_blocks = extract_all_stage_blocks(raw) if "NEED_STAGE" in raw else []
        hit.bundle_commit = extract_bundle_commit(raw) if "NEED_BUNDLE_COMMIT" in raw else None
    except Exception:
        pass
    return hit
