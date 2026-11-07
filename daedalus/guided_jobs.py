"""Guided (controlled) playground build — checkpoint per stage + ultra orchestrator."""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, AsyncIterator, Optional

from . import playground as pg
from .playground_content_registry import bootstrap_registry, register_merged_file, registry_prompt_block
from .playground_dependency_graph import annotate_stages, next_parallel_batch
from .playground_file_trunk import FileTrunk
from .playground_orchestrator import (
    apply_trunk_merge,
    build_checkpoint_options,
    orchestrator_brief,
    parse_plan_stage_edit,
    resolve_role_model,
    suggest_checkpoint_options,
)
from .playground_parallel_orchestrator import max_parallel_workers, run_parallel_batch
from .playground_stage_builder import StageBuildContext, StageBuildResult, build_stage
from .playground_ultra_plan import expand_stage_ultra, finalize_ultra_plan, ultra_expand_plan

from .harness import runs as _runs

log = logging.getLogger(__name__)

_TERMINAL = frozenset({"done", "error", "interrupted"})
_KA_INTERVAL = 12.0
_MAX_EVENTS = 800

_GD_JOBS: dict[str, "GuidedJob"] = {}
_GD_ACTIVE: dict[str, str] = {}

try:
    from argos_data_nexus import redis_db as _rdb
except Exception:
    from .kv import db as _rdb


def _now() -> float:
    return time.time()


def _job_key(job_id: str) -> str:
    return f"adn:playground:guidedjob:{job_id}"


def _active_key(slug: str) -> str:
    return f"adn:playground:guidedjob:active:{slug}"


class GuidedJob:
    """One guided build run with per-stage checkpoints."""

    def __init__(
        self,
        slug: str,
        description: str,
        plan: dict[str, Any],
        *,
        ultra: bool = False,
        kind: str = "static",
        studio: bool = False,
        studio_preset: str = "custom",
    ):
        self.id = uuid.uuid4().hex[:16]
        self.run_id = ""  # run-ledger id; set by start_guided only
        self.slug = slug
        self.description = description[:500]
        self.plan = plan
        self.kind = kind or "static"
        self.ultra = ultra or bool(plan.get("ultra"))
        self.studio = studio or bool(plan.get("studio"))
        self.studio_preset = studio_preset or plan.get("studio_preset") or "custom"
        self.milestone_scope = ""  # stop guided after this milestone (Game Studio)
        self.auto_approve = False  # Game Studio: skip human checkpoint between stages
        self.context_files: dict[str, str] = dict(plan.get("context_pack") or {})
        self.trunk = FileTrunk()
        self.events: list[dict] = []
        self.status = "running"
        self.error: Optional[str] = None
        self.stage_index = 0
        self.all_files: dict[str, str] = {}
        self.assets_built: list[str] = []
        self.retry_notes = ""
        self.run_assets = False
        self.checkpoint_data: dict[str, Any] = {}
        self.files: list[str] = []
        self.created = _now()
        self.updated = _now()
        self.cond = asyncio.Condition()
        self.task: Optional[asyncio.Task] = None
        self._last_persist = 0.0

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "slug": self.slug,
            "description": self.description,
            "status": self.status,
            "error": self.error,
            "stage_index": self.stage_index,
            "total_stages": len(self.plan.get("stages") or []),
            "ultra": self.ultra,
            "studio": self.studio,
            "studio_preset": self.studio_preset,
            "milestone_scope": self.milestone_scope,
            "auto_approve": self.auto_approve,
            "files": self.files,
            "assets": self.assets_built,
            "checkpoint": self.checkpoint_data,
            "trunk": self.trunk.summary(),
            "event_count": len(self.events),
            "created": self.created,
            "updated": self.updated,
        }

    def _persist(self, force: bool = False) -> None:
        if not force and self.status == "running" and (_now() - self._last_persist) < 8:
            return
        self._last_persist = _now()
        try:
            _rdb[_job_key(self.id)] = json.dumps(
                {
                    "snapshot": self.snapshot(),
                    "events": self.events[-_MAX_EVENTS:],
                },
                ensure_ascii=False,
            )
            _rdb[_active_key(self.slug)] = self.id
        except Exception as exc:
            log.warning("guided job persist failed: %s", exc)

    async def append_event(self, event: str, data: dict) -> None:
        async with self.cond:
            if len(self.events) < _MAX_EVENTS:
                self.events.append({"event": event, "data": data})
            self.updated = _now()
            self.cond.notify_all()
        self._persist()

    async def enter_checkpoint(self, data: dict) -> None:
        async with self.cond:
            self.checkpoint_data = data
            self.status = "checkpoint"
            self.updated = _now()
            self.cond.notify_all()
        self._persist(force=True)
        _runs.sync_job(self, "guided")

    async def complete(self) -> None:
        async with self.cond:
            self.status = "done"
            self.updated = _now()
            self.cond.notify_all()
        self._persist(force=True)
        _runs.sync_job(self, "guided")

    async def fail(self, error: str) -> None:
        async with self.cond:
            self.status = "error"
            self.error = error[:400]
            self.updated = _now()
            self.cond.notify_all()
        self._persist(force=True)
        _runs.sync_job(self, "guided")

    async def mark_interrupted(self, reason: str) -> None:
        async with self.cond:
            self.status = "interrupted"
            self.error = reason
            self.updated = _now()
            self.cond.notify_all()
        self._persist(force=True)
        _runs.sync_job(self, "guided")


async def subscribe(job: GuidedJob, from_offset: int = 0) -> AsyncIterator[dict]:
    cursor = max(0, from_offset)
    while True:
        out: Optional[dict] = None
        stop = False
        async with job.cond:
            if cursor < len(job.events):
                out = job.events[cursor]
                cursor += 1
            elif job.status in ("done", "error", "interrupted"):
                last = job.events[-1] if job.events else None
                if last and last.get("event") in _TERMINAL:
                    stop = True
                elif job.status == "done":
                    out = {"event": "done", "data": {"files": job.files, "assets": job.assets_built}}
                    stop = True
                else:
                    out = {"event": "error", "data": {"message": job.error or "прекъснат"}}
                    stop = True
            elif job.status == "checkpoint":
                out = {"event": "checkpoint", "data": job.checkpoint_data}
                stop = True
            else:
                try:
                    await asyncio.wait_for(job.cond.wait(), timeout=_KA_INTERVAL)
                except asyncio.TimeoutError:
                    out = {"event": "_ping", "data": {}}
        if out is not None:
            yield out
        if stop:
            return


def _write_trunk_to_disk(job: GuidedJob) -> list[str]:
    written: list[str] = []
    for path, content in job.trunk.trunk.items():
        try:
            pg.write_file(job.slug, path, content)
            written.append(path)
            job.all_files[path] = content
        except Exception:
            pass
    job.files = list(job.all_files.keys())
    return written


def _hydrate_job_from_workspace(job: GuidedJob) -> int:
    """Load existing workspace code into job context (guided starts with empty trunk otherwise)."""
    count = 0
    skip_prefixes = ("assets/", ".git/")
    try:
        for ent in pg.read_tree(job.slug, max_files=400):
            rel = (ent.get("path") or "").strip()
            if not rel or any(rel.startswith(p) for p in skip_prefixes):
                continue
            try:
                job.all_files[rel] = pg.read_file(job.slug, rel)
                count += 1
            except Exception:
                pass
    except Exception:
        pass
    for path, content in (job.context_files or {}).items():
        if path and content:
            job.all_files[path] = content
    job.files = list(job.all_files.keys())
    return count


def _reset_stale_milestone_stages(job: GuidedJob) -> int:
    """Reset done/skipped stages when declared deliverables are missing from workspace."""
    if not job.milestone_scope:
        return 0
    stages = job.plan.get("stages") or []
    ws = set(job.all_files.keys())
    reset = 0
    for s in stages:
        if not isinstance(s, dict) or s.get("milestone_id") != job.milestone_scope:
            continue
        if s.get("status") not in ("done", "skipped"):
            continue
        expected = [str(f).strip() for f in (s.get("files") or []) if f]
        if not expected:
            continue
        if any(f in ws for f in expected):
            continue
        s["status"] = "pending"
        reset += 1
    return reset


def _milestone_code_files(job: GuidedJob) -> list[str]:
    return [
        p for p in job.all_files
        if not p.startswith("assets/") and not p.lower().endswith(".png")
    ]


async def _finish_guided(job: GuidedJob, *, log_line: str) -> None:
    """Unified guided terminal — always milestone_done for scoped runs; fail if no code files."""
    await job.append_event("log", {"line": log_line, "level": "ok"})
    if job.milestone_scope:
        if not _milestone_code_files(job):
            await job.fail(
                f"Milestone {job.milestone_scope}: няма записани файлове. "
                "Провери мозъка в Connectors (/admin/daedalus/connectors#brain) и пусни Guided отново."
            )
            return
        await job.append_event("milestone_done", {"milestone_id": job.milestone_scope})
    await job.append_event("done", {"files": job.files, "assets": job.assets_built})
    await job.complete()


async def _run_guided_parallel_batch(
    job: GuidedJob,
    *,
    kind: str,
    combined_sys: str,
    default_model: str,
    is_game: bool,
    stages: list[dict],
    batch_indices: list[int],
    route_fn: Any,
) -> None:
    from . import playground_assets as pa

    total = len(stages)
    await job.append_event("log", {
        "line": f"⚡ Parallel batch ({len(batch_indices)}): "
                + ", ".join(str(stages[i].get("id")) for i in batch_indices),
        "level": "info",
    })
    await job.append_event("log", {
        "line": orchestrator_brief(job.ultra, job.trunk)
        + f" | parallel≤{max_parallel_workers()}",
        "level": "dim",
    })

    asset_summary = pa.format_asset_summary(job.assets_built)

    async def on_log(line: str, level: str = "info") -> None:
        await job.append_event("log", {"line": line, "level": level})

    results = await run_parallel_batch(
        slug=job.slug,
        kind=kind,
        combined_sys=combined_sys,
        default_model=default_model,
        description=job.description,
        stages=stages,
        indices=batch_indices,
        trunk_files=dict(job.trunk.trunk),
        assets_built=list(job.assets_built),
        asset_summary=asset_summary,
        is_game=is_game,
        route_fn=route_fn,
        on_log=on_log,
        client_prefix="guided-parallel",
    )

    batch_items: list[dict] = []
    all_merged: list[str] = []
    all_rejected: list[str] = []
    all_ok = True

    for idx, stage, result in results:
        sid = stage.get("id", idx + 1)
        task_id = f"s{sid}"
        role = stage.get("model_role", "builder")
        build_model = resolve_role_model(str(role), default_model)
        merged, rejected = apply_trunk_merge(
            job.trunk, result, task_id=task_id, model=build_model or "",
        )
        written: list[str] = []
        if merged:
            for path in merged:
                register_merged_file(job.trunk, path, stage, task_id=task_id)
            written = _write_trunk_to_disk(job)
            all_merged.extend(merged)
        all_rejected.extend(rejected)
        ok = result.ok and bool(merged)
        all_ok = all_ok and ok
        batch_items.append({
            "stage_id": sid,
            "stage_index": idx,
            "title": stage.get("title", ""),
            "ok": ok,
            "files": written or list(result.stage_files.keys()),
            "merged": merged,
            "rejected": rejected,
            "review": (result.review_notes or "")[:500],
        })
        await job.append_event("stage_done", batch_items[-1])

    primary = batch_items[0] if batch_items else {}
    options = build_checkpoint_options(
        stages[batch_indices[0]], results[0][2] if results else StageBuildResult(False, {}, ""),
        trunk=job.trunk, ultra=job.ultra,
    )
    options.insert(0, {
        "id": "approve",
        "label": f"✅ Одобри batch ({len(batch_items)} задачи)",
        "hint": "Merge е направен — потвърди и продължи",
    })

    cp_data = {
        "parallel_batch": True,
        "batch_indices": batch_indices,
        "batch_results": batch_items,
        "stage_id": primary.get("stage_id"),
        "stage_index": min(batch_indices),
        "title": f"Batch ×{len(batch_items)}",
        "ok": all_ok,
        "files": all_merged,
        "merged": all_merged,
        "rejected": all_rejected,
        "review": "\n---\n".join(
            f"[{b['stage_id']}] {b['review'][:200]}" for b in batch_items
        )[:2000],
        "options": options,
        "trunk": job.trunk.summary(),
    }
    await job.enter_checkpoint(cp_data)
    job.retry_notes = ""


async def _run_guided_step(job: GuidedJob) -> None:
    from . import daedalus_mesh as mesh
    from .build_prompts import build_combined_sys, resolve_build_model
    from .game_platform_guide import is_likely_game
    from . import playground_assets as pa

    _runs.bind(job.run_id)
    _runs.sync_job(job, "guided")  # a continue after a checkpoint moves the run back to running
    try:
        proj = await pg.get_project(job.slug)
        if not proj:
            await job.fail("project not found")
            return

        kind = proj.get("kind", "static")
        coding_mode = proj.get("coding_mode", "lite")
        combined_sys = build_combined_sys(kind, coding_mode)
        try:
            from .game_engineer import attach_to_sys
            combined_sys = attach_to_sys(
                combined_sys,
                text=f"{proj.get('name', '')} {proj.get('description', '')} {job.description}",
                kind=kind,
            )
        except Exception:
            pass
        try:
            from .dissection import prompt_block_for_slug
            specimen = prompt_block_for_slug(job.slug)
            if specimen:
                combined_sys = f"{combined_sys}\n{specimen}"
        except Exception:
            pass
        if job.studio and job.context_files:
            from .playground_studio_mode import studio_combined_sys
            combined_sys = studio_combined_sys(combined_sys, job.context_files)
        if job.milestone_scope:
            from .game_studio_build import HANDOFF_SYS_ADDENDUM
            combined_sys = f"{combined_sys}\n\n{HANDOFF_SYS_ADDENDUM}"
        default_model, _ = resolve_build_model()
        is_game = is_likely_game(f"{proj.get('name', '')} {job.description}")
        stages = job.plan.get("stages") or []
        total = len(stages)

        if not job.all_files:
            _hydrate_job_from_workspace(job)
        job.trunk.load_trunk(job.all_files)
        bootstrap_registry(job.trunk)
        stages = annotate_stages(stages)
        job.plan["stages"] = stages

        if job.milestone_scope:
            from .game_studio_build import last_index_for_milestone
            _ms_last = last_index_for_milestone(job.plan, job.milestone_scope)
            if _ms_last is not None and job.stage_index > _ms_last:
                await _finish_guided(
                    job,
                    log_line=f"✅ Milestone {job.milestone_scope} завършен — спирам scoped build",
                )
                return

        if job.stage_index >= total:
            await _finish_guided(job, log_line="✅ Всички етапи завършени")
            return

        done_ids = {
            int(s.get("id", i + 1))
            for i, s in enumerate(stages)
            if s.get("status") in ("done", "skipped")
        }
        if job.ultra and max_parallel_workers() > 1:
            batch = [
                i for i in next_parallel_batch(stages, done_ids)
                if i >= job.stage_index
            ]
            if len(batch) > 1:
                await _run_guided_parallel_batch(
                    job,
                    kind=kind,
                    combined_sys=combined_sys,
                    default_model=default_model,
                    is_game=is_game,
                    stages=stages,
                    batch_indices=batch,
                    route_fn=mesh.route,
                )
                return
            if len(batch) == 1 and batch[0] != job.stage_index:
                job.stage_index = batch[0]

        while job.stage_index < total:
            st = stages[job.stage_index]
            if job.milestone_scope and st.get("milestone_id") != job.milestone_scope:
                job.stage_index += 1
                continue
            if st.get("status") in ("done", "skipped"):
                job.stage_index += 1
                continue
            break

        if job.milestone_scope:
            from .game_studio_build import last_index_for_milestone
            _ms_last = last_index_for_milestone(job.plan, job.milestone_scope)
            if _ms_last is not None and job.stage_index > _ms_last:
                await _finish_guided(
                    job,
                    log_line=f"✅ Milestone {job.milestone_scope} завършен — спирам scoped build",
                )
                return

        if job.stage_index >= total:
            await _finish_guided(job, log_line="✅ Всички етапи завършени")
            return

        stage = stages[job.stage_index]
        sid = stage.get("id", job.stage_index + 1)
        stitle = stage.get("title", f"Етап {sid}")
        role = stage.get("model_role", "builder")
        build_model = resolve_role_model(str(role), default_model)

        reg_block = registry_prompt_block(job.trunk)
        if reg_block:
            stage = dict(stage)
            stage["instructions"] = (
                (stage.get("instructions") or "") + "\n\n📋 REGISTRY:\n" + reg_block
            ).strip()
        if job.studio:
            from .playground_studio_mode import augment_stage_for_studio
            stage = augment_stage_for_studio(stage)

        micro_tag = " 🔬" if stage.get("micro") else ""
        await job.append_event("stage", {
            "stage_id": sid,
            "title": stitle,
            "idx": job.stage_index,
            "total": total,
            "status": "start",
            "micro": bool(stage.get("micro")),
            "model_role": role,
        })
        await job.append_event("log", {
            "line": f"━━ [{job.stage_index + 1}/{total}]{micro_tag} {stitle} ━━",
            "level": "info",
        })
        if build_model and build_model != default_model:
            await job.append_event("log", {
                "line": f"  🤖 Модел ({role}): {build_model}",
                "level": "dim",
            })

        if job.run_assets:
            job.run_assets = False
            asset_model = resolve_role_model("asset", default_model)
            await job.append_event("log", {"line": "🎨 Генерирам assets…", "level": "info"})
            try:
                from .playground_assets import (
                    ensure_stage_assets,
                    ensure_style_bible,
                )
                bible = await ensure_style_bible(
                    slug=job.slug,
                    name=proj.get("name", job.slug),
                    description=job.description,
                    plan=job.plan,
                    model=asset_model or default_model,
                )
                new_assets = await ensure_stage_assets(
                    job.slug,
                    stage,
                    name=proj.get("name", job.slug),
                    kind=kind,
                    description=job.description,
                    assets_built=job.assets_built,
                    style_bible=bible,
                    model=asset_model or default_model,
                    client_id=f"guided-assets/{job.slug}/s{sid}",
                )
                for a in new_assets:
                    job.assets_built.append(a)
                    await job.append_event("asset", {"path": a, "status": "ok"})
            except Exception as exc:
                await job.append_event("log", {"line": f"⚠️ Assets: {str(exc)[:60]}", "level": "warn"})

        asset_summary = pa.format_asset_summary(job.assets_built)

        async def on_log(line: str, level: str = "info") -> None:
            await job.append_event("log", {"line": line, "level": level})

        ctx = StageBuildContext(
            slug=job.slug,
            kind=kind,
            combined_sys=combined_sys,
            build_model=build_model or default_model,
            description=job.description,
            stage=stage,
            stage_index=job.stage_index,
            total=total,
            all_files=dict(job.trunk.trunk),
            assets_built=list(job.assets_built),
            asset_summary=asset_summary,
            is_game=is_game,
            user_notes=job.retry_notes,
            client_prefix="guided-build",
            max_attempts=5 if job.ultra else 5,
        )
        result = await build_stage(ctx, route_fn=mesh.route, on_log=on_log)

        task_id = f"s{sid}"
        merged, rejected = apply_trunk_merge(
            job.trunk, result, task_id=task_id, model=build_model or "",
        )
        files_written = _write_trunk_to_disk(job) if merged else []

        if merged:
            await job.append_event("log", {
                "line": f"  🔀 Merge → trunk: {', '.join(merged)}",
                "level": "ok",
            })
            for path in merged:
                register_merged_file(job.trunk, path, stage, task_id=task_id)
            files_written = _write_trunk_to_disk(job)
        if rejected:
            await job.append_event("log", {
                "line": f"  ⛔ Branch rejected: {', '.join(rejected)}",
                "level": "warn",
            })

        options = build_checkpoint_options(
            stage, result, trunk=job.trunk, ultra=job.ultra,
        )
        ai_opts = await suggest_checkpoint_options(
            stage, result,
            description=job.description,
            route_fn=mesh.route,
            model=resolve_role_model("reviewer", default_model),
            client_id=f"guided-cp-ai/{job.slug}/s{sid}",
        )
        for o in ai_opts:
            if o["id"] not in {x["id"] for x in options}:
                options.insert(1, o)

        cp_data = {
            "stage_id": sid,
            "stage_index": job.stage_index,
            "title": stitle,
            "ok": result.ok,
            "files": files_written or list(result.stage_files.keys()),
            "merged": merged,
            "rejected": rejected,
            "review": result.review_notes[:2000],
            "options": options,
            "uses_visuals": bool(stage.get("uses_visuals")),
            "micro": bool(stage.get("micro")),
            "model_role": role,
            "trunk": job.trunk.summary(),
            "ai_options": ai_opts,
        }
        await job.append_event("stage_done", cp_data)
        if job.auto_approve:
            await job.enter_checkpoint(cp_data)
            result = await continue_job(job.id, "approve")
            if not result.get("ok"):
                await job.fail(result.get("error") or "auto-approve failed")
            return
        await job.enter_checkpoint(cp_data)
        job.retry_notes = ""

    except asyncio.CancelledError:
        await job.mark_interrupted("Спряно от потребителя")
        raise
    except Exception as exc:
        log.exception("guided step failed")
        await job.fail(str(exc)[:300])


def _insert_ultra_micros(job: GuidedJob, stage_index: int) -> int:
    """Replace stage at index with micro-tasks. Returns number inserted."""
    from .game_platform_guide import is_likely_game

    stages = job.plan.get("stages") or []
    if stage_index >= len(stages):
        return 0
    parent = stages[stage_index]
    micros = expand_stage_ultra(
        parent,
        kind=job.kind,
        is_game=is_likely_game(job.description),
    )
    if len(micros) <= 1:
        return 0
    # re-id from current position
    new_stages = stages[:stage_index] + micros + stages[stage_index + 1:]
    for i, s in enumerate(new_stages):
        s["id"] = i + 1
    job.plan["stages"] = new_stages
    job.ultra = True
    return len(micros)


def start_guided(
    slug: str,
    description: str,
    plan: dict[str, Any],
    *,
    stage_index: int = 0,
    ultra: bool = False,
    kind: str = "static",
    studio: bool = False,
    studio_preset: str = "custom",
    milestone_scope: str = "",
    auto_approve: bool = False,
) -> GuidedJob:
    from .game_platform_guide import is_likely_game
    from .playground_studio_mode import finalize_studio_plan

    for jb in list(_GD_JOBS.values()):
        if jb.slug == slug and jb.status in ("running", "checkpoint") and jb.task:
            jb.task.cancel()

    proj_kind = kind or "static"
    is_game = is_likely_game(description)
    if studio or plan.get("studio"):
        preset = studio_preset or plan.get("studio_preset") or "custom"
        plan = finalize_studio_plan(
            plan,
            kind=proj_kind,
            preset=preset,
            description=description,
            project_name=slug,
            is_game=is_game,
        )
    if ultra or plan.get("ultra"):
        plan = finalize_ultra_plan(
            plan,
            kind=proj_kind,
            is_game=is_game,
            ultra_requested=True,
            description=description,
        )

    job = GuidedJob(
        slug,
        description,
        plan,
        ultra=ultra or bool(plan.get("ultra")),
        kind=proj_kind,
        studio=studio or bool(plan.get("studio")),
        studio_preset=studio_preset or plan.get("studio_preset") or "custom",
    )
    job.milestone_scope = (milestone_scope or "").strip()
    job.auto_approve = bool(auto_approve)
    _hydrate_job_from_workspace(job)
    if job.milestone_scope:
        from .game_studio_build import first_pending_index
        _reset_stale_milestone_stages(job)
        _ms_idx = first_pending_index(plan, job.milestone_scope)
        job.stage_index = _ms_idx if _ms_idx is not None else stage_index
    else:
        job.stage_index = stage_index
    _GD_JOBS[job.id] = job
    _GD_ACTIVE[slug] = job.id
    job.run_id = _runs.safe_open("guided", slug=slug, ref=f"guided:{job.id}", summary=description)
    job.task = asyncio.create_task(_run_guided_step(job))
    job.task.add_done_callback(_runs.on_task_done(job, "guided"))
    return job


async def continue_job(
    job_id: str,
    action: str,
    *,
    notes: str = "",
) -> dict[str, Any]:
    job = _GD_JOBS.get(job_id)
    if not job:
        return {"ok": False, "error": "job not found"}
    if job.status != "checkpoint":
        return {"ok": False, "error": f"job not at checkpoint (status={job.status})"}

    stages = job.plan.get("stages") or []
    stage = stages[job.stage_index] if job.stage_index < len(stages) else {}
    sid = stage.get("id", job.stage_index + 1)
    action = (action or "approve").strip().lower()

    if action == "approve":
        if job.checkpoint_data.get("parallel_batch"):
            indices = job.checkpoint_data.get("batch_indices") or []
            for idx in indices:
                if idx < len(stages):
                    s = stages[idx]
                    sid_done = s.get("id", idx + 1)
                    await pg.mark_stage_done(job.slug, sid_done)
                    s["status"] = "done"
            job.stage_index = max(indices) + 1 if indices else job.stage_index + 1
        else:
            await pg.mark_stage_done(job.slug, sid)
            for s in job.plan.get("stages") or []:
                if s.get("id") == sid:
                    s["status"] = "done"
            job.stage_index += 1
        try:
            await pg.save_autopilot_plan(job.slug, job.plan)
        except Exception:
            pass
        job.retry_notes = ""
    elif action == "skip":
        if job.checkpoint_data.get("parallel_batch"):
            indices = job.checkpoint_data.get("batch_indices") or []
            for idx in indices:
                if idx < len(stages):
                    stages[idx]["status"] = "skipped"
            job.stage_index = max(indices) + 1 if indices else job.stage_index + 1
        else:
            if job.stage_index < len(stages):
                stages[job.stage_index]["status"] = "skipped"
            job.stage_index += 1
        job.retry_notes = ""
        try:
            await pg.save_autopilot_plan(job.slug, job.plan)
        except Exception:
            pass
    elif action == "retry" or action.startswith("custom"):
        job.retry_notes = (notes or "").strip() or "Преработи според review."
    elif action == "assets":
        job.run_assets = True
        job.retry_notes = (notes or "").strip()
    elif action == "edit_plan":
        stages[job.stage_index] = parse_plan_stage_edit(notes, stage)
        job.plan["stages"] = stages
        job.retry_notes = notes or "Приложени промени по плана."
        try:
            await pg.save_autopilot_plan(job.slug, job.plan)
        except Exception:
            pass
    elif action == "ultra_split":
        n = _insert_ultra_micros(job, job.stage_index)
        if n <= 1:
            return {"ok": False, "error": "Етапът вече е атомарен"}
        try:
            await pg.save_autopilot_plan(job.slug, job.plan)
        except Exception:
            pass
        job.retry_notes = ""
    else:
        return {"ok": False, "error": f"unknown action: {action}"}

    if action in ("approve", "skip") and job.milestone_scope:
        from .game_studio_build import last_index_for_milestone
        _ms_last = last_index_for_milestone(job.plan, job.milestone_scope)
        if _ms_last is not None and job.stage_index > _ms_last:
            async with job.cond:
                job.status = "running"
                job.checkpoint_data = {}
                job.updated = _now()
                job.cond.notify_all()
            if not _milestone_code_files(job):
                await job.fail(
                    f"Milestone {job.milestone_scope}: няма записани файлове след одобрение. "
                    "Провери мозъка в Connectors и пусни отново."
                )
                return {"ok": False, "error": job.error or "no files written"}
            async with job.cond:
                job.status = "done"
                job.updated = _now()
                job.cond.notify_all()
            await job.append_event("log", {
                "line": f"✅ Milestone {job.milestone_scope} завършен",
                "level": "ok",
            })
            await job.append_event("milestone_done", {"milestone_id": job.milestone_scope})
            await job.append_event("done", {"files": job.files, "assets": job.assets_built})
            return {"ok": True, "action": action, "milestone_complete": True}

    async with job.cond:
        job.status = "running"
        job.checkpoint_data = {}
        job.updated = _now()
        job.cond.notify_all()

    await job.append_event("log", {"line": f"▶️ {action}…", "level": "ok"})
    job.task = asyncio.create_task(_run_guided_step(job))
    job.task.add_done_callback(_runs.on_task_done(job, "guided"))
    return {"ok": True, "action": action, "stage_index": job.stage_index}


def get_job(job_id: str) -> Optional[GuidedJob]:
    return _GD_JOBS.get(job_id)


def get_active(slug: str) -> Optional[GuidedJob]:
    jid = _GD_ACTIVE.get(slug)
    if jid:
        job = _GD_JOBS.get(jid)
        if job:
            return job
    if _rdb is not None:
        try:
            stored = _rdb.get(_active_key(slug))
            if isinstance(stored, str):
                return _GD_JOBS.get(stored)
        except Exception:
            pass
    return None


async def stop_active(slug: str) -> bool:
    job = get_active(slug)
    if not job or not job.task:
        return False
    job.task.cancel()
    return True
