"""Revive a repository: clone → analyse → install and verify → repair → publish (local or as a site).

Owner's ask (2026-11-11): give Daedalus a repo address; it analyses it, installs what it needs, makes it run as it
should, and publishes it locally or as a site. Before, the Studio's Revive only cloned and ran one smoke test: the LLM
repair existed but was never called (no model was passed), nothing was published, a static site counted as working
without ever being opened, and the whole thing was one HTTP request that could run for many minutes.

A revive is a background job with visible steps. Every step says what it did and what it found; a step that could not
run says why (never a silent pass). Publishing as a site goes through ``live_publish`` with its own owner gates.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

PUBLISH_MODES = ("none", "local", "site")
DEFAULT_REPAIR_ROUNDS = 2
_JOBS: dict[str, "ReviveJob"] = {}
_MAX_JOBS = 50


@dataclass
class ReviveJob:
    url: str
    publish: str = "local"
    repair_rounds: int = DEFAULT_REPAIR_ROUNDS
    confirm_public: bool = False
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: str = "running"            # running | done | failed
    slug: str = ""
    steps: list[dict[str, Any]] = field(default_factory=list)
    analysis: dict[str, Any] = field(default_factory=dict)
    local_url: str = ""
    site_url: str = ""
    error: str = ""
    started: float = field(default_factory=time.time)
    ended: float | None = None
    task: Any = None

    def step(self, name: str, ok: bool | None, detail: str = "", **extra: Any) -> dict[str, Any]:
        row = {"step": name, "ok": ok, "detail": detail[:1500], "at": round(time.time() - self.started, 1), **extra}
        self.steps.append(row)
        log.info("revive %s %s ok=%s %s", self.id, name, ok, detail[:200])
        return row

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "url": self.url, "status": self.status, "slug": self.slug, "publish": self.publish,
                "steps": list(self.steps), "analysis": dict(self.analysis), "local_url": self.local_url,
                "site_url": self.site_url, "error": self.error, "started": self.started, "ended": self.ended}


def analyze(root: Path, kind: str) -> dict[str, Any]:
    """What the repo is and how it runs: kind, framework, package manager, scripts, static build, Python deps."""
    from . import static_build

    out: dict[str, Any] = {"kind": kind, "files": sum(1 for p in root.rglob("*") if p.is_file() and ".git" not in p.parts)}
    pkg = static_build._package(root)
    if pkg:
        scripts = pkg.get("scripts") if isinstance(pkg.get("scripts"), dict) else {}
        out["scripts"] = sorted(scripts)[:12]
        deps = static_build._deps(pkg)
        out["server"] = sorted(deps & static_build.SERVER_DEPS)
        sb = static_build.plan(root)
        if sb:
            out["static_build"] = sb
            out["framework"] = sb["framework"]
    for req in ("requirements.txt", "pyproject.toml", "Pipfile"):
        if (root / req).is_file():
            out.setdefault("python", []).append(req)
    if kind == "static":
        out["entry"] = "index.html" if (root / "index.html").is_file() else ""
    if not any(out.get(k) for k in ("scripts", "python", "entry")) and kind in ("static", "node"):
        out["warning"] = "No index.html, package.json scripts or Python entry found at the repo root."
    return out


async def _verify(slug: str, kind: str, analysis: dict[str, Any]) -> dict[str, Any]:
    """Does it run? Static sites are opened in a browser; static-build apps are built; servers are started."""
    from . import playground as pg
    from . import playground_runner as runner

    if kind == "static" or analysis.get("static_build"):
        if analysis.get("static_build"):
            from . import static_build
            try:
                out_dir = await static_build.build(pg.project_dir(slug))
            except Exception as exc:
                return {"ok": False, "error": str(exc)[:2500], "how": "build"}
            how = f"build → {out_dir.name}/ + browser"
        else:
            how = "browser"
        from .visual_proof import run_visual_proof
        proof = await run_visual_proof(slug, "static" if kind == "static" else "vite")
        if proof.honest_gap:
            return {"ok": True, "how": how, "note": f"built; browser check unavailable ({proof.honest_gap})"}
        errs = list((proof.details or {}).get("page_errors") or [])[:5]
        ok = bool(proof.ok) and not proof.blank_screen
        return {"ok": ok, "how": how, "error": "" if ok else (
            "; ".join(str(e) for e in errs) or proof.summary or "the page did not render"), "frames": proof.frames}
    smoke = await runner.smoke_test(slug)
    if smoke.get("skipped"):
        return {"ok": False, "how": "smoke", "error": f"cannot verify a {kind} project here (check skipped)"}
    return {"ok": bool(smoke.get("ok")), "how": "smoke",
            "error": smoke.get("error") or "\n".join((smoke.get("logs") or [])[-20:])}


async def _repair(slug: str, kind: str, error: str) -> list[str]:
    from . import daedalus_mesh as mesh
    from . import playground_verify_fix as pvf
    from .build_prompts import fallback_build_model_tag

    model, _ = fallback_build_model_tag()
    files = await pvf.llm_fix_from_error(
        slug, kind=kind, error=error[:2500],
        combined_sys="You are Daedalus, reviving a project cloned from a repository. Fix only what keeps it from "
                     "installing, building or running. Return only the changed files in ===FILE: path=== format.",
        build_model=model, route_fn=mesh.route, client_id=f"revive-repair/{slug}",
        problem_label="REPAIR MODE: the project does not install, build or start after the clone.")
    return pvf.write_fix_files(slug, files) if files else []


async def run(job: ReviveJob, *, clone_fn: Any = None) -> ReviveJob:
    from . import playground as pg

    try:
        # 1 clone
        if clone_fn is None:
            from .playground_git_push import clone_project_remote as clone_fn
        try:
            proj = await clone_fn(job.url, source="revived", keep_git=True)
        except Exception as exc:
            from .playground_git_push import forge_error_hint
            job.step("clone", False, forge_error_hint(exc))
            raise _Stop() from exc
        job.slug = proj["slug"]
        kind = str(proj.get("kind") or "static")
        root = pg.project_dir(job.slug)
        job.step("clone", True, f"{job.slug} · kind {kind}")

        # 2 analyse
        job.analysis = analyze(root, kind)
        job.step("analyze", not job.analysis.get("warning"), job.analysis.get("warning") or
                 ", ".join(f"{k}: {v}" for k, v in job.analysis.items() if k in ("kind", "framework", "scripts",
                                                                                 "python", "server")))
        # 3 install + verify, 4 repair rounds
        res = await _verify(job.slug, kind, job.analysis)
        job.step("verify", res["ok"], res.get("error") or res.get("note") or res.get("how", ""), how=res.get("how"))
        rounds = 0
        while not res["ok"] and rounds < max(0, job.repair_rounds):
            rounds += 1
            written = await _repair(job.slug, kind, res.get("error") or "")
            job.step(f"repair {rounds}", bool(written), ("changed: " + ", ".join(written)) if written else
                     "the model returned no fix")
            if not written:
                break
            res = await _verify(job.slug, kind, analyze(root, kind))
            job.step(f"verify after repair {rounds}", res["ok"], res.get("error") or res.get("how", ""))
        if not res["ok"]:
            job.error = f"it does not run yet: {str(res.get('error') or '')[:300]}"
            raise _Stop()

        # 5 publish
        if job.publish in ("local", "site"):
            from .live_publish import ensure_local
            local = await ensure_local(job.slug)
            if not local.get("ok"):
                job.step("publish local", False, str(local.get("error") or "cannot publish this kind as a site"))
                job.error = "runs, but this kind cannot be published as a static site"
                raise _Stop()
            job.local_url = str(local.get("local_url") or "")
            job.step("publish local", True, job.local_url)
        if job.publish == "site":
            from .live_publish import publish_live
            live = await publish_live(job.slug, confirm_public=job.confirm_public)
            job.site_url = str(live.get("url") or "")
            job.step("publish site", bool(job.site_url), job.site_url or str(live.get("note") or live.get("error")))
        job.status = "done"
    except _Stop:
        job.status = "failed"
        job.error = job.error or (job.steps[-1]["detail"] if job.steps else "failed")
    except asyncio.CancelledError:
        job.status, job.error = "failed", "stopped"
        raise
    except Exception as exc:
        log.exception("revive failed")
        job.status, job.error = "failed", f"{type(exc).__name__}: {str(exc)[:300]}"
    finally:
        job.ended = time.time()
    return job


class _Stop(Exception):
    pass


def start(url: str, *, publish: str = "local", repair_rounds: int = DEFAULT_REPAIR_ROUNDS,
          confirm_public: bool = False) -> ReviveJob:
    url = (url or "").strip()
    if not url:
        raise ValueError("url required")
    if publish not in PUBLISH_MODES:
        raise ValueError(f"publish must be one of {', '.join(PUBLISH_MODES)}")
    if len(_JOBS) >= _MAX_JOBS:
        for jid in sorted(_JOBS, key=lambda k: _JOBS[k].started)[: len(_JOBS) - _MAX_JOBS + 1]:
            if _JOBS[jid].status != "running":
                _JOBS.pop(jid, None)
    job = ReviveJob(url=url, publish=publish, repair_rounds=max(0, min(4, int(repair_rounds))),
                    confirm_public=bool(confirm_public))
    _JOBS[job.id] = job
    job.task = asyncio.get_running_loop().create_task(run(job))
    return job


def get(job_id: str) -> ReviveJob | None:
    return _JOBS.get((job_id or "").strip())
