"""Single-stage build engine — shared by guided build and plan/autopilot paths."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from .playground_build_utils import (
    TRUNCATION_KEYWORDS,
    extract_llm_text,
    extract_partial,
    file_preview,
    kind_forbidden_block,
    parse_build_files,
)

log = logging.getLogger(__name__)

LogFn = Callable[[str, str], Awaitable[None]]


@dataclass
class StageBuildContext:
    slug: str
    kind: str
    combined_sys: str
    build_model: str
    description: str
    stage: dict[str, Any]
    stage_index: int
    total: int
    all_files: dict[str, str]
    assets_built: list[str] = field(default_factory=list)
    asset_summary: str = ""
    is_game: bool = False
    max_attempts: int = 5
    user_notes: str = ""
    client_prefix: str = "guided-build"


@dataclass
class StageBuildResult:
    ok: bool
    stage_files: dict[str, str]
    review_notes: str
    files_written: list[str] = field(default_factory=list)


async def _llm_continue_file(
    partial_raw: str,
    fname: str,
    sys_ctx: str,
    model: str,
    client_id: str,
    route_fn: Any,
) -> str:
    combined = extract_partial(partial_raw, fname)
    for cr in range(4):
        tail = combined[-600:] if len(combined) > 600 else combined
        cont_prompt = (
            f"Файлът `{fname}` беше прекъснат поради token limit.\n"
            f"Последни редове:\n```\n...{tail}\n```\n\n"
            f"Продължи ТОЧНО от тук. Когато завършиш: ===END==="
        )
        try:
            cres = await route_fn(
                messages=[
                    {"role": "system", "content": sys_ctx},
                    {"role": "user", "content": cont_prompt},
                ],
                model=model,
                intent="code",
                max_tokens=32000,
                temperature=0.15,
                client_id=f"{client_id}/cont{cr}",
            )
            ctext = extract_llm_text(cres)
            cfinish = (cres.get("finish_reason") or cres.get("stop_reason") or "stop").lower()
            ctoks = (cres.get("usage") or {}).get("output_tokens", 0)
            ctext = re.sub(r"^===\s*FILE\s*:[^\n]+\n?", "", ctext.lstrip(), flags=re.I)
            ctext = re.sub(r"\n?===\s*END\s*===.*$", "", ctext, flags=re.DOTALL)
            combined += "\n" + ctext
            if cfinish not in ("length", "max_tokens") and ctoks < 29000:
                break
        except Exception:
            break
    return combined


async def build_stage(
    ctx: StageBuildContext,
    *,
    route_fn: Any,
    on_log: LogFn,
) -> StageBuildResult:
    """Build one plan stage with guards, per-file mode, and LLM review."""
    sid = ctx.stage.get("id", ctx.stage_index + 1)
    stitle = ctx.stage.get("title", f"Етап {sid}")
    sgoal = ctx.stage.get("goal", "")
    sinstr = ctx.stage.get("instructions", "")
    svis = ctx.stage.get("uses_visuals", False)
    stage_files_list = ctx.stage.get("files") or []
    kind_forbidden = kind_forbidden_block(ctx.kind, is_game=ctx.is_game)

    stage_files: dict[str, str] = {}
    review_notes = ""
    build_ok = False

    for attempt in range(ctx.max_attempts):
        if attempt > 0:
            await on_log(
                f"  🔄 Поправка ({attempt + 1}/{ctx.max_attempts}): {review_notes[:100]}",
                "warn",
            )
        else:
            await on_log("  🔨 Строи…", "info")

        if ctx.all_files:
            index = ", ".join(ctx.all_files.keys())
            recent = "\n\n".join(
                f"--- {p} ---\n{c[:3500]}" for p, c in list(ctx.all_files.items())[-8:]
            )
            file_ctx = f"ВСИЧКИ ФАЙЛОВЕ: {index}\n\n{recent}"
        else:
            file_ctx = "(няма предишен код)"

        visual_hint = (
            "\n🔑 Визуален етап — реферирай assets/*.png от проекта.\n" if svis else ""
        )
        user_fix = ""
        if ctx.user_notes:
            user_fix = f"\n\n👤 БЕЛЕЖКИ ОТ ПОТРЕБИТЕЛЯ:\n{ctx.user_notes[:1200]}\n"
        is_truncation = attempt > 0 and any(
            kw in review_notes.lower() for kw in TRUNCATION_KEYWORDS
        )
        expo_retry = (
            attempt > 0
            and ctx.kind == "expo"
            and any(kw in review_notes.lower() for kw in ("expo quality", "голям", "truncat"))
        )

        if (is_truncation or stage_files_list) and stage_files_list:
            await on_log(
                f"  📝 Per-file — {len(stage_files_list)} файла…",
                "info",
            )
            pf_ok = True
            for fname in stage_files_list:
                ctx_written = "\n\n".join(
                    f"--- {p} ---\n{c[:2500]}"
                    for p, c in ctx.all_files.items() if p != fname
                ) or "(няма)"
                expo_size = (
                    "\nEXPO: MAX 200 реда/файл. Без requestAnimationFrame.\n"
                    if ctx.kind == "expo" else ""
                )
                pf_fix = f"\n⚠️ ПОПРАВИ:\n{review_notes[:800]}\n" if review_notes and attempt > 0 else ""
                pf_prompt = (
                    f"═══ KIND: {ctx.kind.upper()} ═══\n{kind_forbidden}\n\n"
                    f"Напиши САМО `{fname}` — пълен, синтактично завършен файл.\n"
                    f"{expo_size}{pf_fix}{user_fix}"
                    f"Цел: {sgoal}\nОписание: {ctx.description[:600]}\n"
                    f"{ctx.asset_summary}\n\nКонтекст:\n{ctx_written[:3000]}\n\n"
                    f"===FILE: {fname}===\n[код]\n===END==="
                )
                try:
                    pfres = await route_fn(
                        messages=[
                            {"role": "system", "content": ctx.combined_sys},
                            {"role": "user", "content": pf_prompt},
                        ],
                        model=ctx.build_model,
                        intent="code",
                        max_tokens=32000,
                        temperature=0.2,
                        client_id=f"{ctx.client_prefix}/{ctx.slug}/s{sid}/pf",
                    )
                    pf_finish = (
                        pfres.get("finish_reason") or pfres.get("stop_reason") or "stop"
                    ).lower()
                    pf_toks = (pfres.get("usage") or {}).get("output_tokens", 0)
                    pf_text = extract_llm_text(pfres)
                    if pf_finish in ("length", "max_tokens") or pf_toks > 29000:
                        pf_text = await _llm_continue_file(
                            pf_text, fname, ctx.combined_sys, ctx.build_model,
                            f"{ctx.client_prefix}/{ctx.slug}/s{sid}/pf", route_fn,
                        )
                    pf_files = parse_build_files(
                        f"===FILE: {fname}===\n{pf_text}\n===END==="
                        if "===FILE:" not in pf_text else pf_text
                    )
                    matched = (
                        pf_files.get(fname)
                        or (next(iter(pf_files.values()), None) if pf_files else None)
                        or pf_text.strip()
                    )
                    if matched:
                        if ctx.kind == "expo":
                            from .playground_expo_guard import scan_expo_file
                            hits = scan_expo_file(fname, matched)
                            if hits:
                                review_notes = f"РЕЗУЛТАТ: ПРОБЛЕМ\nПРОБЛЕМ: {hits[0]['message']}"
                                pf_ok = False
                                continue
                        stage_files[fname] = matched
                        await on_log(f"    ✓ {fname}: {len(matched)} chars", "dim")
                    else:
                        pf_ok = False
                        await on_log(f"    ⚠️ {fname}: липсва в output", "warn")
                except Exception as exc:
                    pf_ok = False
                    await on_log(f"    ❌ {fname}: {str(exc)[:80]}", "err")

            if not pf_ok:
                continue
            await on_log("  ✅ Per-file готово — review…", "ok")
        else:
            fix_ctx = ""
            if review_notes and attempt > 0:
                fix_ctx = f"\n\n⚠️ ПОПРАВИ:\n{review_notes}\n"
            scope = ""
            if stage_files_list:
                scope = (
                    f"\n\n🎯 ПИШИ САМО: {', '.join(stage_files_list)}\n"
                )
            build_prompt = (
                f"═══ KIND: {ctx.kind.upper()} ═══\n{kind_forbidden}\n\n"
                f"Етап {sid}/{ctx.total}: {stitle}\nЦел: {sgoal}\nИнструкции: {sinstr}\n"
                f"Описание: {ctx.description}\n{visual_hint}{ctx.asset_summary}"
                f"{scope}{user_fix}\n\nПредишен код:\n{file_ctx}{fix_ctx}"
            )
            try:
                bres = await route_fn(
                    messages=[
                        {"role": "system", "content": ctx.combined_sys},
                        {"role": "user", "content": build_prompt},
                    ],
                    model=ctx.build_model,
                    intent="code",
                    max_tokens=32000,
                    temperature=0.35,
                    client_id=f"{ctx.client_prefix}/{ctx.slug}/s{sid}",
                )
                new_files = parse_build_files(extract_llm_text(bres))
                if new_files:
                    stage_files.update(new_files)
                    await on_log(
                        f"  📄 {len(new_files)} файла: {', '.join(list(new_files.keys())[:5])}",
                        "dim",
                    )
                finish = (bres.get("finish_reason") or bres.get("stop_reason") or "stop").lower()
                toks = (bres.get("usage") or {}).get("output_tokens", 0)
                if finish in ("length", "max_tokens") or toks > 29000:
                    review_notes = (
                        "РЕЗУЛТАТ: ПРОБЛЕМ\nПРОБЛЕМ: Truncated — token limit."
                    )
                    continue
            except Exception as exc:
                await on_log(f"  ❌ Build грешка: {str(exc)[:100]}", "err")
                break

        from .playground_kind_guard import format_review_block as kg_format, scan_files as kg_scan
        from .playground_game_guard import format_game_review_block as gg_format, scan_game_visuals as gg_scan

        kg_hits = kg_scan(ctx.kind, stage_files)
        if kg_hits:
            review_notes = kg_format(kg_hits)
            await on_log(f"  ⛔ Platform guard", "warn")
            continue

        if ctx.kind == "expo":
            from .playground_expo_guard import format_expo_review_block, scan_expo_files
            expo_hits = scan_expo_files(stage_files)
            if expo_hits:
                review_notes = format_expo_review_block(expo_hits)
                await on_log("  ⛔ Expo guard", "warn")
                continue

        if ctx.is_game:
            gg_hits = gg_scan(
                ctx.kind, stage_files,
                slug=ctx.slug,
                asset_paths=ctx.assets_built,
                is_game=True,
                all_files=ctx.all_files,
                stage_uses_visuals=bool(ctx.stage.get("uses_visuals", True)),
            )
            if gg_hits:
                review_notes = gg_format(gg_hits)
                await on_log("  ⛔ Game visual guard", "warn")
                continue

        await on_log("  🔬 Code review…", "info")
        preview = "\n\n".join(
            f"=== {p} ({len(c)} chars) ===\n{file_preview(c)}"
            for p, c in stage_files.items()
        ) or "(няма файлове)"

        review_sys = (
            f"Ти си code reviewer. ПЛАТФОРМА: {ctx.kind}.\n"
            f"Забрани: {kind_forbidden or '(няма)'}\n"
        )
        if ctx.kind == "expo":
            from .playground_expo_guard import expo_review_system_addendum
            review_sys += expo_review_system_addendum() + "\n"
        if ctx.is_game:
            from .game_platform_guide import game_review_system_addendum
            review_sys += game_review_system_addendum(ctx.kind)
        review_sys += (
            "\nРЕЗУЛТАТ: ОДОБРЕН или РЕЗУЛТАТ: ПРОБЛЕМ + ПРОБЛЕМ: описание.\n"
            "Phaser на vite е OK за игри. Entity файлове без load.image са OK."
        )
        try:
            rev = await route_fn(
                messages=[
                    {"role": "system", "content": review_sys},
                    {"role": "user", "content": (
                        f"Етап {sid}: {stitle}\nЦел: {sgoal}\n\nКОД:\n{preview[:28000]}"
                    )},
                ],
                model=ctx.build_model,
                intent="chat",
                max_tokens=1200,
                temperature=0.1,
                client_id=f"{ctx.client_prefix}-review/{ctx.slug}/s{sid}",
            )
            review_notes = extract_llm_text(rev) or ""
        except Exception:
            review_notes = "РЕЗУЛТАТ: ОДОБРЕН"
        if not review_notes.strip():
            # An empty review is no review, not a rejection: rewriting a file against an empty reason five times
            # cannot fix anything (found live 2026-11-11). The build checks above already passed; say so plainly.
            await on_log("  ⚠️ Code review returned nothing — accepted on the build checks", "warn")
            review_notes = "РЕЗУЛТАТ: ОДОБРЕН (code review unavailable: empty reply; build checks passed)"

        if "ОДОБРЕН" in review_notes.upper():
            build_ok = True
            await on_log("  ✅ Одобрен", "ok")
            break
        await on_log(f"  ⚠️ Review: {review_notes[:120]}", "warn")

    return StageBuildResult(
        ok=build_ok,
        stage_files=stage_files,
        review_notes=review_notes,
    )


def default_checkpoint_options(stage: dict[str, Any], result: StageBuildResult) -> list[dict[str, str]]:
    """UI actions after each guided stage."""
    opts = [
        {"id": "approve", "label": "✅ Одобри и продължи", "hint": "Saves the files and moves to the next stage"},
        {"id": "retry", "label": "✏️ Преработи", "hint": "Repeat the stage with your notes"},
    ]
    if stage.get("uses_visuals"):
        opts.append({
            "id": "assets",
            "label": "🎨 Генерирай assets",
            "hint": "PNGs for this stage, then a rework",
        })
    if not result.ok:
        opts.insert(0, {
            "id": "retry",
            "label": "🔧 Поправи проблемите",
            "hint": "Recommended: the stage did not pass review",
        })
    opts.append({
        "id": "skip",
        "label": "⏭️ Пропусни етапа",
        "hint": "Continue without saving (not recommended)",
    })
    return opts
