"""Parallel batch execution for ultra micro-stages."""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Awaitable, Callable

from .playground_content_registry import registry_prompt_block
from .playground_dependency_graph import annotate_stages, next_parallel_batch
from .playground_stage_builder import StageBuildContext, StageBuildResult, build_stage
from .playground_orchestrator import resolve_role_model

log = logging.getLogger(__name__)

LogFn = Callable[[str, str], Awaitable[None]]


def max_parallel_workers() -> int:
    try:
        return max(1, min(6, int(os.environ.get("DAEDALUS_ORCH_MAX_PARALLEL", "3"))))
    except ValueError:
        return 3


async def run_parallel_batch(
    *,
    slug: str,
    kind: str,
    combined_sys: str,
    default_model: str,
    description: str,
    stages: list[dict[str, Any]],
    indices: list[int],
    trunk_files: dict[str, str],
    assets_built: list[str],
    asset_summary: str,
    is_game: bool,
    route_fn: Any,
    on_log: LogFn,
    client_prefix: str = "orch-parallel",
) -> list[tuple[int, dict[str, Any], StageBuildResult]]:
    """Build multiple stages concurrently (each reads same trunk snapshot)."""
    sem = asyncio.Semaphore(max_parallel_workers())

    async def _one(idx: int) -> tuple[int, dict[str, Any], StageBuildResult]:
        stage = stages[idx]
        sid = stage.get("id", idx + 1)
        role = stage.get("model_role", "builder")
        model = resolve_role_model(str(role), default_model) or default_model
        async with sem:
            await on_log(
                f"  ⚡ parallel [{sid}] {stage.get('title', '')} @ {role}",
                "dim",
            )

            async def _log(line: str, level: str = "info") -> None:
                await on_log(f"    [{sid}] {line}", level)

            reg = registry_prompt_block_from_dict(trunk_files)
            stage_with_reg = dict(stage)
            if reg:
                stage_with_reg["instructions"] = (
                    (stage.get("instructions") or "") + "\n\n📋 REGISTRY:\n" + reg
                ).strip()

            ctx = StageBuildContext(
                slug=slug,
                kind=kind,
                combined_sys=combined_sys,
                build_model=model,
                description=description,
                stage=stage_with_reg,
                stage_index=idx,
                total=len(stages),
                all_files=dict(trunk_files),
                assets_built=list(assets_built),
                asset_summary=asset_summary,
                is_game=is_game,
                client_prefix=client_prefix,
                max_attempts=5,
            )
            result = await build_stage(ctx, route_fn=route_fn, on_log=_log)
            return idx, stage, result

    return list(await asyncio.gather(*[_one(i) for i in indices]))


def registry_prompt_block_from_dict(files: dict[str, str]) -> str:
    from .playground_file_trunk import FileTrunk
    t = FileTrunk()
    t.load_trunk(files)
    return registry_prompt_block(t)


def plan_parallel_batches(stages: list[dict[str, Any]]) -> list[list[int]]:
    """Full execution schedule as list of parallel batches (indices)."""
    stages = annotate_stages(stages)
    done: set[int] = set()
    batches: list[list[int]] = []
    max_p = max_parallel_workers()
    guard = 0
    while guard < len(stages) + 5:
        guard += 1
        batch = next_parallel_batch(stages, done, max_parallel=max_p)
        if not batch:
            break
        batches.append(batch)
        for i in batch:
            done.add(int(stages[i].get("id", i + 1)))
    return batches
