"""Idle gardener — opt-in evolution mode.

When the owner turns **Еволюция** on in Studio, a background loop uses spare
machine headroom (even with no open chat) to hunt LLM / coding / memory
innovations, study public GitHub repos, and journal notes. Harness patches
still go through ``self_improve`` as **pending** proposals. Nothing is
silent-applied to ``daedalus/*.py``.

Safety:
  - Default OFF (``settings.json`` → ``evolution_enabled``).
  - ``DAEDALUS_EVOLUTION=0`` is a kill switch; ``=1`` forces on.
  - Offline kit skips network scans.
  - High load / low RAM / running autopilot → skip this tick.
  - Never vendor a GitHub tree into ``daedalus/``. Optional shallow clone
    lands only under ``$DAEDALUS_DATA/evolution/research/``.
  - AGPL / SSPL licenses are never cloned.
  - ``edit_modules`` stays owner-gated; auto-apply never includes it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

DEFAULT_INTERVAL_S = 1800
MIN_INTERVAL_S = 300
MAX_INTERVAL_S = 86400
DISABLED_POLL_S = 20
DEFAULT_MIN_MEM_MB = 400
DEFAULT_BUSY_LOAD = 0.85
SOURCES_PER_TICK = 2

# Gardener will not ask the LLM to patch these even if edit_modules is on.
NEVER_TOUCH = frozenset({
    "daedalus/self_improve.py",
    "daedalus/evolution.py",
    "daedalus/auto_apply.py",
    "daedalus/identity.py",
    "daedalus/identity_standalone.py",
    "daedalus/job_contract.py",
})
_BLOCKED_LICENSES = ("agpl", "sspl", "commons clause")

_task: asyncio.Task[None] | None = None
_stop: asyncio.Event | None = None


def _load_settings() -> dict[str, Any]:
    from . import settings_file
    return settings_file.load()


def _state_dir() -> Path:
    from .paths import data_root
    p = data_root() / "evolution"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _state_path() -> Path:
    return _state_dir() / "state.json"


def _load_state() -> dict[str, Any]:
    path = _state_path()
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {
        "source_cursor": 0,
        "seen_urls": [],
        "last_cycle": None,
        "last_summary": None,
        "last_skip": None,
        "cycle_count": 0,
    }


def _save_state(state: dict[str, Any]) -> None:
    _state_path().write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def enabled() -> bool:
    env = (os.environ.get("DAEDALUS_EVOLUTION") or "").strip().lower()
    if env in ("0", "false", "off", "no"):
        return False
    if env in ("1", "true", "on", "yes"):
        return True
    return bool(_load_settings().get("evolution_enabled", False))


def interval_s() -> int:
    raw = _load_settings().get("evolution_interval_s", DEFAULT_INTERVAL_S)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = DEFAULT_INTERVAL_S
    return max(MIN_INTERVAL_S, min(MAX_INTERVAL_S, value))


def clone_research() -> bool:
    return bool(_load_settings().get("evolution_clone_research", False))


def set_enabled(value: bool) -> dict[str, Any]:
    from . import settings_file
    settings_file.update({"evolution_enabled": bool(value)})  # the single writer: no lost update
    if value:
        start_gardener()
    return snapshot()


def set_config(
    *,
    enabled_flag: bool | None = None,
    interval: int | None = None,
    clone: bool | None = None,
    ask_llm: bool | None = None,
) -> dict[str, Any]:
    from . import settings_file
    patch: dict[str, Any] = {}
    if enabled_flag is not None:
        patch["evolution_enabled"] = bool(enabled_flag)
    if interval is not None:
        patch["evolution_interval_s"] = max(MIN_INTERVAL_S, min(MAX_INTERVAL_S, int(interval)))
    if clone is not None:
        patch["evolution_clone_research"] = bool(clone)
    if ask_llm is not None:
        patch["evolution_ask_llm"] = bool(ask_llm)
    data = settings_file.update(patch)
    if data.get("evolution_enabled"):
        start_gardener()
    return snapshot()


def parse_github_url(url: str) -> tuple[str, str] | None:
    from .skill_acquisition import _parse_github_url
    return _parse_github_url(url or "")


def machine_headroom() -> dict[str, Any]:
    cores = os.cpu_count() or 1
    load1 = 0.0
    try:
        load1 = float(os.getloadavg()[0])
    except (OSError, AttributeError):
        pass
    mem_avail_mb: int | None = None
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                mem_avail_mb = int(line.split()[1]) // 1024
                break
    except Exception:
        pass
    settings = _load_settings()
    try:
        busy_ratio = float(settings.get("evolution_busy_load_ratio", DEFAULT_BUSY_LOAD))
    except (TypeError, ValueError):
        busy_ratio = DEFAULT_BUSY_LOAD
    try:
        min_mem = int(settings.get("evolution_min_mem_mb", DEFAULT_MIN_MEM_MB))
    except (TypeError, ValueError):
        min_mem = DEFAULT_MIN_MEM_MB
    load_ratio = load1 / max(cores, 1)
    idle = load_ratio < busy_ratio and (mem_avail_mb is None or mem_avail_mb >= min_mem)
    return {
        "load1": round(load1, 2),
        "cores": cores,
        "load_ratio": round(load_ratio, 3),
        "mem_avail_mb": mem_avail_mb,
        "busy_ratio": busy_ratio,
        "min_mem_mb": min_mem,
        "idle": idle,
    }


def _autopilot_running() -> bool:
    try:
        from .autopilot_jobs import any_running
        return bool(any_running())
    except Exception:
        return False


def _offline() -> bool:
    try:
        from .offline import enabled as offline_on
        return bool(offline_on())
    except Exception:
        return False


def skip_reason(*, force: bool = False) -> str | None:
    """Why this tick should not run. ``force`` still respects disabled + offline."""
    if not enabled():
        return "disabled"
    if _offline():
        return "offline"
    if force:
        return None
    head = machine_headroom()
    if not head.get("idle"):
        return "machine_busy"
    if _autopilot_running():
        return "autopilot"
    return None


def may_ask_llm() -> bool:
    env = (os.environ.get("DAEDALUS_EVOLUTION_LLM") or "").strip().lower()
    if env in ("0", "false", "off", "no"):
        return False
    if env in ("1", "true", "on", "yes"):
        return True
    if _load_settings().get("evolution_ask_llm") is True:
        return True
    try:
        from .local_llm import local_requested
        return bool(local_requested())
    except Exception:
        return False


def journal_path() -> Path:
    return _state_dir() / "journal.jsonl"


def journal_append(event: dict[str, Any]) -> None:
    row = {"ts": time.time(), **event}
    with journal_path().open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def journal_tail(n: int = 12) -> list[dict[str, Any]]:
    path = journal_path()
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for line in lines[-max(1, n):]:
        try:
            row = json.loads(line)
        except Exception:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def research_dest(owner: str, repo: str) -> Path | None:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", owner or ""):
        return None
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", repo or ""):
        return None
    root = (_state_dir() / "research").resolve()
    root.mkdir(parents=True, exist_ok=True)
    dest = (root / f"{owner}__{repo}").resolve()
    try:
        dest.relative_to(root)
    except ValueError:
        return None
    return dest


def _license_blocked(license_name: str) -> bool:
    text = (license_name or "").lower()
    return any(token in text for token in _BLOCKED_LICENSES)


def _write_learned_note(analysis: dict[str, Any]) -> str | None:
    try:
        from .learned_skills import pending_dir, queue_draft
    except Exception as extra:
        log.debug("evolution: learned skill import failed: %s", extra)
        return None
    slug = analysis.get("slug") or "repo"
    desc = (analysis.get("description") or slug)[:200]
    body = (
        f"## Repo\n{analysis.get('url')}\n\n"
        f"**{analysis.get('name') or slug}** ★{analysis.get('stars', 0)} "
        f"{analysis.get('license') or 'license?'} "
        f"{analysis.get('primary_language') or ''}\n\n"
        f"{desc}\n\n"
        "## Integrate as adapter, not a dump\n"
        "- Do not copy the tree into `daedalus/`.\n"
        "- AGPL stays a sidecar process (see `mirofish_client.py`).\n"
        "- Prefer HTTP/CLI like `reach.py`.\n"
        "- Harness patches go through self_improve (pending), never silent-edit.\n"
    )
    readme = analysis.get("readme") or ""
    if readme:
        body += "\n## README excerpt\n" + readme[:800] + "\n"
    rec = queue_draft(
        name=f"radar-{slug}",
        description=desc or f"Radar note for {slug}",
        body=body,
        origin="evolution",
        kind="radar",
    )
    return str(pending_dir() / f"{rec['id']}.json")


def _maybe_clone(url: str, owner: str, repo: str, license_name: str) -> dict[str, Any]:
    if not clone_research():
        return {"cloned": False, "reason": "clone_research_off"}
    if _license_blocked(license_name):
        return {"cloned": False, "reason": "license_blocked", "license": license_name}
    dest = research_dest(owner, repo)
    if dest is None:
        return {"cloned": False, "reason": "unsafe_path"}
    if dest.exists():
        return {"cloned": False, "reason": "already_present", "path": str(dest)}
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(
            ["git", "clone", "--depth", "1", url, str(dest)],
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
    except FileNotFoundError:
        return {"cloned": False, "reason": "git_missing"}
    except subprocess.TimeoutExpired:
        return {"cloned": False, "reason": "timeout"}
    if proc.returncode != 0:
        return {
            "cloned": False,
            "reason": "git_failed",
            "error": (proc.stderr or proc.stdout or "")[:240],
        }
    return {"cloned": True, "path": str(dest)}


def study_repo(
    url: str,
    *,
    analyzer: Callable[[str], dict[str, Any]] | None = None,
    clone: bool | None = None,
) -> dict[str, Any]:
    """Fetch GitHub metadata (API) and optionally shallow-clone into data/.

    ``analyzer`` is injectable so tests never hit the network.
    """
    parsed = parse_github_url(url)
    if not parsed:
        return {"ok": False, "error": f"Cannot parse GitHub URL: {url!r}", "url": url}
    owner, repo = parsed
    fn = analyzer
    if fn is None:
        from .skill_acquisition import analyze_github_repo
        fn = analyze_github_repo
    analysis = fn(url)
    if not analysis.get("ok"):
        return analysis
    do_clone = clone_research() if clone is None else bool(clone)
    clone_info: dict[str, Any] = {"cloned": False, "reason": "skipped"}
    if do_clone:
        clone_info = _maybe_clone(
            analysis.get("url") or url,
            owner,
            repo,
            str(analysis.get("license") or ""),
        )
    note = None
    try:
        note = _write_learned_note(analysis)
    except Exception as extra:
        log.debug("evolution: learned note failed: %s", extra)
    from .skill_acquisition import log_skill_interest
    try:
        log_skill_interest(analysis.get("slug") or f"{owner}/{repo}", analysis)
    except Exception:
        pass
    return {
        **analysis,
        "ok": True,
        "owner": owner,
        "repo": repo,
        "clone": clone_info,
        "learned_skill": note,
    }


def github_urls_from_radar(radar: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    blobs: list[Any] = []
    blobs.extend(radar.get("sources") or [])
    blobs.extend(radar.get("results") or [])
    for src in radar.get("sources") or []:
        if isinstance(src, dict):
            blobs.extend(src.get("urls") or [])
            blobs.extend(src.get("results") or [])
    for item in blobs:
        candidates: list[str] = []
        if isinstance(item, str):
            candidates.append(item)
        elif isinstance(item, dict):
            for key in ("url", "html_url", "link", "href"):
                val = item.get(key)
                if val:
                    candidates.append(str(val))
            for extra in item.get("urls") or []:
                candidates.append(str(extra))
        for raw in candidates:
            if parse_github_url(raw) and raw not in seen:
                seen.add(raw)
                urls.append(raw)
    return urls


async def _ask_agent(daedalus: Any, studied: dict[str, Any]) -> dict[str, Any] | None:
    if daedalus is None or not hasattr(daedalus, "chat"):
        return None
    try:
        from .skill_acquisition import format_skill_analysis_for_prompt
        brief = format_skill_analysis_for_prompt(studied)
    except Exception:
        brief = json.dumps({
            "slug": studied.get("slug"),
            "description": studied.get("description"),
            "license": studied.get("license"),
        }, ensure_ascii=False)
    never = ", ".join(sorted(NEVER_TOUCH))
    prompt = (
        "━━━ EVOLUTION CYCLE (for_self) ━━━\n"
        "You are idle-gardening Daedalus. Study this public repo as an adapter "
        "candidate — do NOT vendor the tree into daedalus/.\n\n"
        f"{brief}\n\n"
        "Rules:\n"
        "- AGPL stays a sidecar. MIT/Apache may become a thin client like reach.py.\n"
        f"- Never propose patches to: {never}, secrets, manifests.\n"
        "- If a tiny, testable harness improvement exists, use the normal "
        "self_improve propose_patch path (pending owner Approve).\n"
        "- If nothing is worth a patch, reply with 4 short lines: skip + why.\n"
        "Write in Bulgarian.\n"
    )
    try:
        return await daedalus.chat(
            prompt,
            session_id="evolution-gardener",
            for_self=True,
        )
    except TypeError:
        return await daedalus.chat(prompt, session_id="evolution-gardener")
    except Exception as extra:
        log.warning("evolution: LLM reflect failed: %s", extra)
        return {"ok": False, "error": str(extra)[:240]}


def _null_agent() -> Any:
    class _Memory:
        def find_discovery_by_url(self, url: str) -> Any:
            return None

        def capture_discovery(self, **kwargs: Any) -> Any:
            return None

    class _Agent:
        memory = _Memory()

        async def chat(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            return {"ok": True, "reply": ""}

    return _Agent()


async def run_cycle(
    *,
    force: bool = False,
    daedalus: Any = None,
    radar_fn: Callable[..., Any] | None = None,
    study_fn: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """One gardener tick. Network work is skipped when offline."""
    reason = skip_reason(force=force)
    if reason:
        journal_append({"event": "skip", "reason": reason, "force": force})
        state = _load_state()
        state["last_skip"] = {"ts": time.time(), "reason": reason}
        _save_state(state)
        return {"ok": True, "skipped": True, "reason": reason}

    state = _load_state()
    from .radar import rotate_source_ids, run_radar_scan

    picked, next_cursor = rotate_source_ids(
        int(state.get("source_cursor") or 0),
        SOURCES_PER_TICK,
    )
    agent = daedalus
    if agent is None:
        try:
            from .agent import get_daedalus
            agent = get_daedalus()
        except Exception:
            agent = _null_agent()

    radar: dict[str, Any] = {"ok": True, "skipped": True, "sources": []}
    scan = radar_fn or run_radar_scan
    try:
        radar = await scan(agent, source_ids=picked, for_self=True)
    except TypeError:
        radar = await scan(agent)
    except Exception as extra:
        log.warning("evolution: radar failed: %s", extra)
        radar = {"ok": False, "error": str(extra)[:240], "sources": []}

    studied: dict[str, Any] | None = None
    seen = list(state.get("seen_urls") or [])
    for url in github_urls_from_radar(radar):
        if url in seen:
            continue
        try:
            fn = study_fn or study_repo
            studied = await asyncio.to_thread(fn, url)
        except Exception as extra:
            studied = {"ok": False, "error": str(extra)[:240], "url": url}
        seen.append(url)
        break

    llm_reply = None
    if studied and studied.get("ok") and may_ask_llm():
        llm_reply = await _ask_agent(agent, studied)

    state["source_cursor"] = next_cursor
    state["seen_urls"] = seen[-80:]
    state["last_cycle"] = time.time()
    state["cycle_count"] = int(state.get("cycle_count") or 0) + 1
    summary = {
        "sources": picked,
        "radar_found": radar.get("total_found") or radar.get("result_count") or 0,
        "new_discoveries": radar.get("new_discoveries") or 0,
        "studied": (studied or {}).get("slug") if studied else None,
        "clone": (studied or {}).get("clone") if studied else None,
        "asked_llm": bool(llm_reply),
        "force": force,
    }
    state["last_summary"] = summary
    state["last_skip"] = None
    _save_state(state)
    journal_append({"event": "cycle", **summary})
    cycle_ok = bool(radar.get("ok", True)) or bool(studied and studied.get("ok"))
    return {
        "ok": cycle_ok,
        **({"error": str(radar.get("error") or "radar_failed")[:240]} if not cycle_ok else {}),
        "skipped": False,
        "sources": picked,
        "radar": {
            "ok": radar.get("ok", True),
            "total_found": summary["radar_found"],
            "new_discoveries": summary["new_discoveries"],
            "error": radar.get("error"),
        },
        "studied": studied,
        "asked_llm": bool(llm_reply),
        "cycle_count": state["cycle_count"],
    }


def gardener_running() -> bool:
    return _task is not None and not _task.done()


def start_gardener() -> bool:
    global _task, _stop
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False
    if gardener_running():
        return True
    _stop = asyncio.Event()
    _task = loop.create_task(_gardener_loop(), name="daedalus-evolution")
    log.info("evolution: gardener started (enabled=%s interval=%ss)", enabled(), interval_s())
    return True


async def stop_gardener() -> None:
    global _task, _stop
    if _stop is not None:
        _stop.set()
    task = _task
    _task = None
    if task is None:
        return
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass


async def _gardener_loop() -> None:
    assert _stop is not None
    while not _stop.is_set():
        if not enabled():
            try:
                await asyncio.wait_for(_stop.wait(), timeout=DISABLED_POLL_S)
            except asyncio.TimeoutError:
                continue
            break
        try:
            await run_cycle(force=False)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("evolution: cycle crashed (non-fatal)")
            journal_append({"event": "error", "reason": "cycle_crashed"})
        try:
            await asyncio.wait_for(_stop.wait(), timeout=float(interval_s()))
        except asyncio.TimeoutError:
            continue
        break


def snapshot() -> dict[str, Any]:
    state = _load_state()
    return {
        "ok": True,
        "enabled": enabled(),
        "interval_s": interval_s(),
        "clone_research": clone_research(),
        "ask_llm": may_ask_llm(),
        "offline": _offline(),
        "headroom": machine_headroom(),
        "skip_reason": skip_reason(),
        "gardener_running": gardener_running(),
        "last_cycle": state.get("last_cycle"),
        "last_summary": state.get("last_summary"),
        "last_skip": state.get("last_skip"),
        "cycle_count": state.get("cycle_count") or 0,
        "seen": len(state.get("seen_urls") or []),
        "journal": journal_tail(8),
        "never_touch": sorted(NEVER_TOUCH),
    }
