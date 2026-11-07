"""Autopilot PHASE 4 — ultra mode with parallel orchestrator + trunk merge."""
from __future__ import annotations

import logging
from typing import Any, AsyncIterator

from .playground_content_registry import bootstrap_registry
from .playground_dependency_graph import annotate_stages, next_parallel_batch
from .playground_parallel_orchestrator import max_parallel_workers, run_parallel_batch
from .playground_trunk_session import TrunkSession

log = logging.getLogger(__name__)


async def ultra_build_phase(
    *,
    slug: str,
    kind: str,
    stages: list[dict[str, Any]],
    description: str,
    combined_sys: str,
    build_model: str,
    all_files: dict[str, str],
    assets_built: list[str],
    asset_summary: str,
    is_game: bool,
    route_fn: Any,
    mark_stage_done_fn: Any,
    project_name: str = "",
    style_bible: dict[str, Any] | None = None,
    milestone_scope: str = "",
    milestone_last_idx: int | None = None,
) -> AsyncIterator[tuple[str, dict]]:
    """Yield (event, data) tuples like autopilot _pipeline. Mutates all_files in place."""
    from . import playground_assets as pa

    trunk_sess = TrunkSession(slug)
    trunk_sess.load(all_files)
    bootstrap_registry(trunk_sess.trunk)
    stages = annotate_stages(stages)
    total = len(stages)
    done_ids: set[int] = set()
    max_p = max_parallel_workers()

    yield ("log", {"line": f"🎛 Ultra build: trunk merge + parallel (max {max_p})", "level": "info"})
    yield ("log", {"line": f"📋 Registry: src/registry/entities.json + levels.json", "level": "dim"})

    guard = 0
    while guard < total + 10:
        guard += 1
        batch = next_parallel_batch(stages, done_ids, max_parallel=max_p)
        if milestone_scope:
            batch = [
                i for i in batch
                if stages[i].get("milestone_id") == milestone_scope
                and stages[i].get("status") != "done"
            ]
        if not batch:
            if milestone_scope and milestone_last_idx is not None:
                ms_done = all(
                    stages[i].get("status") == "done" or int(stages[i].get("id", i + 1)) in done_ids
                    for i, s in enumerate(stages)
                    if s.get("milestone_id") == milestone_scope
                )
                if ms_done:
                    yield ("log", {
                        "line": f"✅ Milestone {milestone_scope} завършен — ultra scoped stop",
                        "level": "ok",
                    })
                    yield ("milestone_done", {"milestone_id": milestone_scope})
            break

        yield ("log", {
            "line": f"⚡ Parallel batch ({len(batch)} tasks): "
                    + ", ".join(str(stages[i].get("id")) for i in batch),
            "level": "info",
        })

        for idx in batch:
            stage = stages[idx]
            if stage.get("uses_visuals") or stage.get("fragment_kind") in ("entity", "level"):
                try:
                    new_assets = await pa.ensure_stage_assets(
                        slug,
                        stage,
                        name=project_name or slug,
                        kind=kind,
                        description=description,
                        assets_built=assets_built,
                        style_bible=style_bible,
                        model=build_model,
                        client_id=f"ultra-stage-asset/{slug}/s{stage.get('id')}",
                    )
                    if new_assets:
                        for ap in new_assets:
                            if ap not in assets_built:
                                assets_built.append(ap)
                        asset_summary = pa.format_asset_summary(assets_built)
                        yield ("log", {
                            "line": f"  🎨 Stage {stage.get('id')}: +{len(new_assets)} assets ({', '.join(new_assets[:3])})",
                            "level": "dim",
                        })
                except Exception as exc:
                    log.warning("ultra stage assets: %s", exc)

        async def on_log(line: str, level: str = "info") -> None:
            pass  # filled below via closure hack — use inline logging instead

        results = await run_parallel_batch(
            slug=slug,
            kind=kind,
            combined_sys=combined_sys,
            default_model=build_model,
            description=description,
            stages=stages,
            indices=batch,
            trunk_files=dict(trunk_sess.trunk.trunk),
            assets_built=assets_built,
            asset_summary=asset_summary,
            is_game=is_game,
            route_fn=route_fn,
            on_log=_batch_log_collector(),
            client_prefix="autopilot-ultra",
        )

        for idx, stage, result in results:
            sid = stage.get("id", idx + 1)
            stitle = stage.get("title", f"Етап {sid}")
            task_id = f"s{sid}"

            yield ("stage", {
                "stage_id": sid, "title": stitle, "idx": idx, "total": total,
                "status": "start", "parallel": True,
            })

            merged, rejected = trunk_sess.commit_stage(
                result, stage, task_id=task_id, model=build_model,
            )
            written = trunk_sess.flush_to_disk() if merged else []

            if merged:
                yield ("log", {
                    "line": f"  🔀 [{sid}] merge → trunk: {', '.join(merged)}",
                    "level": "ok",
                })
            if rejected:
                yield ("log", {
                    "line": f"  ⛔ [{sid}] rejected: {', '.join(rejected)}",
                    "level": "warn",
                })

            build_ok = result.ok and bool(merged)
            if build_ok:
                try:
                    await mark_stage_done_fn(slug, sid)
                    stage["status"] = "done"
                except Exception:
                    pass

            if build_ok:
                done_ids.add(int(sid))
            yield ("stage", {
                "stage_id": sid, "status": "done", "ok": build_ok,
                "files": written or list(result.stage_files.keys()),
                "merged": merged, "rejected": rejected,
                "review": (result.review_notes or "")[:300],
                "parallel": True,
            })
            yield ("log", {
                "line": f"━━ [{sid}/{total}] {stitle} {'✅' if build_ok else '⚠️'} ━━",
                "level": "ok" if build_ok else "warn",
            })
            if milestone_scope and milestone_last_idx is not None and idx >= milestone_last_idx:
                yield ("log", {
                    "line": f"✅ Milestone {milestone_scope} завършен — ultra scoped stop",
                    "level": "ok",
                })
                yield ("milestone_done", {"milestone_id": milestone_scope})
                guard = total + 99
                break

    all_files.clear()
    all_files.update(trunk_sess.all_files)
    yield ("log", {
        "line": f"🎛 Trunk main: {len(trunk_sess.trunk.trunk)} файла",
        "level": "dim",
    })


def _batch_log_collector():
    """No-op log sink — parallel worker logs are emitted per-stage above."""
    async def _noop(line: str, level: str = "info") -> None:
        return
    return _noop
