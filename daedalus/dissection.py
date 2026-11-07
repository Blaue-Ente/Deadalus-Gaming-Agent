"""Game dissection — turn a name, URL, or Play Lab install into a specimen.

Scout (web research) is the shallow pass. Dissection is the field study:
stack fingerprint, live browser probe when possible, and a brief the builder
must follow as a *мостра* — inspire mechanics and feel, never copy assets/code.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .playground import project_dir, write_file, _safe_join
from .playground_assets import ensure_assets_dir
from .reference_scout import (
    classify_reference,
    format_brief_block,
    scout_reference,
)

log = logging.getLogger(__name__)

DISSECTION_REL = "assets/_dissection.json"
DISSECTION_MD = "design/dissection.md"

LEGAL_LINE = (
    "ВАЖНО: Вдъхнови се от механиките, усещането и стека. "
    "НЕ копирай код, асети, търговски марки или музика 1:1 — оригинална имплементация."
)

_URL_RE = re.compile(r"^https?://", re.I)
_GIT_RE = re.compile(r"(github\.com|gitlab\.com|codeberg\.org)/", re.I)
_STACK_MARKERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("three.js", re.compile(r"three(\.module|\.min)?\.js|from ['\"]three['\"]|THREE\.|WebGPURenderer", re.I)),
    ("webgpu", re.compile(r"navigator\.gpu|WebGPURenderer|GPUDevice|WGSL", re.I)),
    ("webgl", re.compile(r"getContext\(\s*['\"]webgl|WebGLRenderer", re.I)),
    ("phaser", re.compile(r"phaser", re.I)),
    ("pixi", re.compile(r"pixi\.js|PIXI\.", re.I)),
    ("babylon", re.compile(r"babylon", re.I)),
    ("playcanvas", re.compile(r"playcanvas", re.I)),
    ("godot", re.compile(r"godot|project\.godot|\.tscn", re.I)),
    ("unity", re.compile(r"unity.?webgl|UnityLoader", re.I)),
    ("canvas-2d", re.compile(r"getContext\(\s*['\"]2d['\"]|canvas", re.I)),
    ("howler", re.compile(r"howler", re.I)),
    ("web-audio", re.compile(r"AudioContext|OscillatorNode", re.I)),
    ("rapier", re.compile(r"rapier", re.I)),
    ("cannon", re.compile(r"cannon-es|CANNON\.", re.I)),
    ("vite", re.compile(r"vite|@vite", re.I)),
    ("react", re.compile(r"react(\.min)?\.js|from ['\"]react['\"]", re.I)),
    ("pwa", re.compile(r"manifest\.webmanifest|serviceWorker", re.I)),
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def classify_source(raw: str) -> tuple[str, str]:
    """Return (kind, value) where kind is url | git | name | path | lab."""
    ref = (raw or "").strip()
    if not ref:
        raise ValueError("празен източник за дисекция")
    if ref.startswith("lab-") and " " not in ref and "/" not in ref:
        return "lab", ref
    if _URL_RE.match(ref) or ref.startswith("www."):
        if ref.startswith("www."):
            ref = "https://" + ref
        if _GIT_RE.search(ref) and not ref.rstrip("/").endswith((".html", ".htm")):
            return "git", ref
        return "url", ref
    path = Path(ref).expanduser()
    if path.exists():
        return "path", str(path.resolve())
    if ref.startswith(("/", "./", "../")) or (len(ref) > 2 and ref[1:3] == ":\\"):
        return "path", ref
    # bare github owner/repo
    if re.match(r"^[\w.-]+/[\w.-]+$", ref) and "/" in ref and " " not in ref:
        return "git", f"https://github.com/{ref}"
    kind, value = classify_reference(ref)
    if kind == "url":
        if _GIT_RE.search(value):
            return "git", value
        return "url", value
    return "name", value


def fingerprint_stack(text: str, *, extra_urls: list[str] | None = None) -> dict[str, Any]:
    """Detect engine/renderer from HTML, JS, or file list — no LLM."""
    blob = text or ""
    if extra_urls:
        blob += "\n" + "\n".join(extra_urls)
    hits: list[str] = []
    for name, rx in _STACK_MARKERS:
        if rx.search(blob) and name not in hits:
            hits.append(name)
    renderer = ""
    if "webgpu" in hits:
        renderer = "WebGPU"
    elif "webgl" in hits:
        renderer = "WebGL"
    elif "canvas-2d" in hits:
        renderer = "Canvas 2D"
    engine = ""
    for cand in ("three.js", "phaser", "pixi", "babylon", "playcanvas", "godot", "unity"):
        if cand in hits:
            engine = cand
            break
    return {
        "engine": engine,
        "renderer": renderer,
        "markers": hits,
    }


def format_specimen_block(data: dict[str, Any]) -> str:
    title = data.get("title") or data.get("source") or "Specimen"
    lines = [
        f"🔬 ДИСЕКЦИЯ — мостра «{title}»",
        f"Резюме: {(data.get('summary') or '').strip()}",
    ]
    stack = data.get("stack") or {}
    if isinstance(stack, dict) and (stack.get("engine") or stack.get("renderer") or stack.get("markers")):
        bits = [stack.get("engine") or "", stack.get("renderer") or ""]
        marks = stack.get("markers") or []
        lines.append(
            "Стек: "
            + ", ".join(b for b in bits if b)
            + ((" · " + ", ".join(marks[:8])) if marks else "")
        )
    if data.get("core_loop"):
        lines.append(f"Core loop: {data['core_loop']}")
    mech = data.get("mechanics") or []
    if mech:
        lines.append("Механики: " + "; ".join(str(m) for m in mech[:8]))
    if data.get("feel"):
        lines.append(f"Усещане: {data['feel']}")
    if data.get("controls"):
        lines.append(f"Управление: {data['controls']}")
    if data.get("visual_language"):
        lines.append(f"Визуален език: {data['visual_language']}")
    steal = data.get("steal") or []
    if steal:
        lines.append("Вземи (идеи, не код): " + "; ".join(str(s) for s in steal[:8]))
    avoid = data.get("do_not_copy") or []
    if avoid:
        lines.append("Не копирай: " + "; ".join(str(s) for s in avoid[:8]))
    if data.get("scope_hints"):
        lines.append(f"MVP насоки: {data['scope_hints']}")
    if data.get("playable_url"):
        lines.append(f"Проба: {data['playable_url']}")
    if data.get("lab_id"):
        lines.append(f"Лаборатория: {data['lab_id']}")
    lines.append(LEGAL_LINE)
    return "\n".join(lines)


def format_specimen_md(data: dict[str, Any]) -> str:
    title = data.get("title") or "Дисекция"
    lines = [
        f"# Дисекция — {title}",
        "",
        f"_Мостра за строеж · {data.get('saved_at') or _now_iso()}_",
        "",
        "## Резюме",
        "",
        (data.get("summary") or "").strip() or "(няма)",
        "",
        "## Стек",
        "",
        json.dumps(data.get("stack") or {}, ensure_ascii=False, indent=2),
        "",
        "## Core loop",
        "",
        data.get("core_loop") or "(не е извлечен)",
        "",
        "## Механики",
        "",
    ]
    for m in data.get("mechanics") or []:
        lines.append(f"- {m}")
    if not data.get("mechanics"):
        lines.append("- (няма)")
    lines.extend([
        "",
        "## Усещане / визуален език",
        "",
        data.get("feel") or data.get("visual_language") or "(няма)",
        "",
        "## Какво да вземеш",
        "",
    ])
    for s in data.get("steal") or []:
        lines.append(f"- {s}")
    lines.extend(["", "## Какво да не копираш", ""])
    for s in data.get("do_not_copy") or ["код", "асети", "търговски марки", "музика"]:
        lines.append(f"- {s}")
    lines.extend(["", LEGAL_LINE, ""])
    if data.get("research_brief"):
        lines.extend(["", "## Research scout", "", str(data["research_brief"]), ""])
    return "\n".join(lines)


def _dissection_path(slug: str) -> Path:
    return _safe_join(project_dir(slug), DISSECTION_REL)


def load_dissection(slug: str) -> dict[str, Any] | None:
    path = _dissection_path(slug)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def save_dissection(slug: str, data: dict[str, Any]) -> None:
    ensure_assets_dir(slug)
    payload = dict(data)
    payload["saved_at"] = _now_iso()
    write_file(slug, DISSECTION_REL, json.dumps(payload, ensure_ascii=False, indent=2))
    write_file(slug, DISSECTION_MD, format_specimen_md(payload))


def prompt_block_for_slug(slug: str) -> str:
    data = load_dissection(slug or "")
    if not data:
        return ""
    block = data.get("specimen_block") or format_specimen_block(data)
    return "\n━━━ SPECIMEN (дисекция — мостра, не копие) ━━━\n" + block + "\n"


def _fallback_synth(
    *,
    source: str,
    title: str,
    stack: dict[str, Any],
    research: dict[str, Any] | None = None,
    live: dict[str, Any] | None = None,
) -> dict[str, Any]:
    research = research or {}
    live = live or {}
    engine = stack.get("engine") or "unknown"
    renderer = stack.get("renderer") or ""
    mechanics = list(research.get("mechanics") or [])
    if live.get("has_canvas") and "canvas / realtime render" not in mechanics:
        mechanics.append("realtime render (canvas or WebGL)")
    summary = (research.get("summary") or "").strip()
    if not summary:
        summary = (
            f"Мостра «{title}» ({source}). "
            f"Стек: {engine} {renderer}. "
            "Използвай като ориентир за механики и усещане при строежа на новата игра."
        )
    return {
        "title": title[:120],
        "summary": summary,
        "mechanics": mechanics[:10],
        "core_loop": research.get("scope_hints") or "input → update → render → win/lose",
        "feel": research.get("visual_style") or (live.get("title") or ""),
        "controls": live.get("controls_hint") or "",
        "visual_language": research.get("visual_style") or "",
        "scope_hints": research.get("scope_hints") or "Core loop first; polish later.",
        "steal": [
            "core loop and failure/reward cadence",
            "readable camera / HUD hierarchy",
            "input feel (not the art files)",
        ],
        "do_not_copy": ["source code", "sprites/audio/fonts", "trademarks", "level layouts 1:1"],
    }


async def _synthesize(
    *,
    source: str,
    source_kind: str,
    stack: dict[str, Any],
    research: dict[str, Any] | None,
    live: dict[str, Any] | None,
    page_text: str,
    project_name: str,
    description: str,
) -> dict[str, Any]:
    research = research or {}
    live = live or {}
    title = research.get("title") or live.get("title") or source
    try:
        from . import daedalus_mesh as mesh
        user = (
            f"ИЗТОЧНИК: {source} (тип: {source_kind})\n"
            f"Проект на потребителя: {project_name or '(нов)'}\n"
            f"Какво иска да направи: {description or '(няма)'}\n"
            f"СТЕК: {json.dumps(stack, ensure_ascii=False)}\n"
            f"LIVE: title={live.get('title','')} canvas={live.get('has_canvas')} "
            f"scripts={live.get('script_count', 0)}\n"
        )
        if research.get("summary"):
            user += f"SCOUT: {research.get('summary')}\nMechanics: {research.get('mechanics')}\n"
        if page_text:
            user += f"PAGE (отрязък):\n{page_text[:2800]}\n"
        user += (
            "Върни САМО JSON:\n"
            '{"title":"...","summary":"2-3 изречения на български","mechanics":["..."],'
            '"core_loop":"...","feel":"...","controls":"...","visual_language":"english for image prompts",'
            '"scope_hints":"...","steal":["ideas not assets"],'
            '"do_not_copy":["code","assets","trademarks"]}'
        )
        sys = (
            "Ти си game-design field researcher. Дисекцията е мостра за оригинална игра, "
            "не рецепта за клонинг. Български за summary/mechanics/core_loop. Без markdown."
        )
        res = await mesh.route(
            messages=[{"role": "system", "content": sys}, {"role": "user", "content": user}],
            model="alltoken:deepseek-v3.2",
            intent="chat",
            max_tokens=1400,
            temperature=0.3,
            client_id="dissection/synth",
        )
        text = ""
        for blk in (res.get("content") or []):
            if blk.get("text"):
                text = blk["text"]
                break
        m = re.search(r"\{[\s\S]+\}", text)
        if m:
            parsed = json.loads(m.group(0))
            if isinstance(parsed, dict) and parsed.get("summary"):
                return parsed
    except Exception as exc:
        log.info("dissection synthesis fallback: %s", exc)
    return _fallback_synth(
        source=source, title=str(title), stack=stack, research=research, live=live,
    )


def _read_install_blob(lab_id: str, limit: int = 80_000) -> tuple[str, list[str]]:
    from .play_lab import install_dir

    root = install_dir(lab_id)
    chunks: list[str] = []
    files: list[str] = []
    budget = limit
    skip = {".git", "node_modules", "__pycache__"}
    prefer = {".html", ".htm", ".js", ".css", ".json", ".tscn", ".gd", ".godot"}
    paths = sorted(root.rglob("*"), key=lambda p: (p.suffix.lower() not in prefer, str(p)))
    for p in paths:
        if not p.is_file() or any(part in skip for part in p.parts):
            continue
        try:
            rel = str(p.relative_to(root))
        except ValueError:
            continue
        files.append(rel)
        if p.suffix.lower() not in prefer and p.stat().st_size > 200_000:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")[: min(12_000, budget)]
        except Exception:
            continue
        chunks.append(f"--- {rel} ---\n{text}")
        budget -= len(text)
        if budget <= 0 or len(chunks) >= 12:
            break
    return "\n".join(chunks), files


async def live_probe(url: str, *, lab_id: str | None = None, slug: str | None = None) -> dict[str, Any]:
    """Load a playable URL in Chromium and report stack-relevant signals."""
    from .playground_browser import _find_chrome

    chrome = _find_chrome()
    if not chrome:
        return {
            "ok": False, "skipped": True, "error": "chromium_not_found",
            "summary": "Chromium липсва — live probe пропуснат",
        }
    try:
        from playwright.async_api import async_playwright
    except Exception as exc:
        return {
            "ok": False, "skipped": True, "error": "playwright_unavailable",
            "summary": f"Playwright недостъпен — {exc}",
        }

    console_errors: list[str] = []
    scripts: list[str] = []
    screenshot_path: str | None = None

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True, executable_path=chrome,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
            )
            page = await browser.new_page(viewport={"width": 1280, "height": 800})
            page.on("console", lambda msg: console_errors.append(msg.text[:200]) if msg.type == "error" else None)
            page.on("response", lambda resp: scripts.append(resp.url) if (resp.request.resource_type or "") == "script" else None)
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=25000)
                await page.wait_for_timeout(1800)
            except Exception as exc:
                await browser.close()
                return {"ok": False, "error": str(exc)[:300], "url": url}

            title = await page.title()
            html = await page.content()
            has_canvas = bool(await page.query_selector("canvas, canvas, svg"))
            webgpu = await page.evaluate(
                "() => !!(navigator.gpu)"
            )
            body_len = await page.evaluate("() => (document.body && document.body.innerText || '').length")
            if lab_id:
                from .play_lab import probes_dir
                dest = probes_dir(lab_id)
                dest.mkdir(parents=True, exist_ok=True)
                shot = dest / "probe.png"
                await page.screenshot(path=str(shot), full_page=False)
                screenshot_path = str(shot)
            elif slug:
                from .playground_assets import ensure_assets_dir
                ensure_assets_dir(slug)
                rel = "assets/_dissection_probe.png"
                dest = _safe_join(project_dir(slug), rel)
                dest.parent.mkdir(parents=True, exist_ok=True)
                await page.screenshot(path=str(dest), full_page=False)
                screenshot_path = rel
            await browser.close()
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:300], "url": url}

    stack = fingerprint_stack(html, extra_urls=scripts[:30])
    if webgpu and "webgpu" not in stack["markers"]:
        stack["markers"].append("webgpu")
        stack["renderer"] = stack.get("renderer") or "WebGPU"
    return {
        "ok": True,
        "url": url,
        "title": title,
        "has_canvas": has_canvas,
        "dom_text_len": body_len,
        "script_count": len(scripts),
        "scripts": scripts[:20],
        "console_errors": console_errors[:8],
        "html_excerpt": html[:4000],
        "screenshot_path": screenshot_path,
        "stack": stack,
        "controls_hint": "keyboard/pointer likely" if has_canvas else "",
    }


async def _clone_git(url: str, dest: Path) -> dict[str, Any]:
    import asyncio
    dest.mkdir(parents=True, exist_ok=True)
    if any(dest.iterdir()):
        dest = dest / "repo"
        dest.mkdir(parents=True, exist_ok=True)
    proc = await asyncio.create_subprocess_exec(
        "git", "clone", "--depth", "1", url, str(dest),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=90)
    except TimeoutError:
        proc.kill()
        return {"ok": False, "error": "git clone timeout"}
    text = (out or b"").decode("utf-8", errors="replace")[-800:]
    if proc.returncode != 0:
        return {"ok": False, "error": text or "git clone failed"}
    return {"ok": True, "dest": str(dest), "log": text[:400]}


def _pick_playable_url(sources: list[dict[str, str]], fallback: str = "") -> str:
    ranked: list[tuple[int, str]] = []
    for s in sources:
        url = (s.get("url") or "").strip()
        if not url.startswith("http"):
            continue
        host = urlparse(url).netloc.lower()
        score = 0
        if "itch.io" in host:
            score += 3
        if host.endswith("github.io") or "gitlab.io" in host:
            score += 4
        if url.rstrip("/").endswith((".html", ".htm")):
            score += 2
        if "store.steampowered.com" in host or "play.google.com" in host:
            score -= 2
        ranked.append((score, url))
    ranked.sort(reverse=True)
    if ranked and ranked[0][0] >= 0:
        return ranked[0][1]
    return fallback


async def dissect(
    source: str = "",
    *,
    slug: str | None = None,
    lab_id: str | None = None,
    install_path: str = "",
    create_lab: bool = True,
    live: bool = True,
    project_name: str = "",
    description: str = "",
    memory: Any = None,
) -> dict[str, Any]:
    """Run a dissection. Optionally create a Play Lab and attach to a project."""
    from . import play_lab as lab
    from .offline import enabled as offline_enabled

    install_path = (install_path or "").strip()
    lab_id = (lab_id or "").strip() or None
    source = (source or "").strip()
    if not source and not lab_id and not install_path:
        raise ValueError("source, lab_id, or install_path required")

    kind = "lab"
    value = lab_id or ""
    if source:
        kind, value = classify_source(source)
    elif install_path:
        kind, value = "path", install_path

    if offline_enabled() and kind in {"url", "git", "name"}:
        return {
            "ok": False,
            "error": "offline",
            "summary": "Офлайн кит: дисекция по име/URL е изключена. Инсталирай файлове в лабораторията.",
        }

    research: dict[str, Any] | None = None
    page_text = ""
    playable_url = ""
    git_result: dict[str, Any] | None = None
    install_result: dict[str, Any] | None = None
    live_result: dict[str, Any] | None = None
    stack: dict[str, Any] = {"engine": "", "renderer": "", "markers": []}

    if kind == "lab" and not lab_id:
        lab_id = value
    if kind == "path" and not install_path:
        install_path = value

    if create_lab and not lab_id:
        created = lab.create_lab(
            title=project_name or source or "specimen",
            source=value or source,
            source_kind=kind,
        )
        lab_id = created["id"]

    if kind == "name":
        research = await scout_reference(
            value,
            project_name=project_name,
            description=description,
            memory=memory,
        )
        playable_url = _pick_playable_url(research.get("sources") or [], "")
        if not playable_url:
            # name-only: still a valid specimen from research
            pass
        elif create_lab and lab_id:
            # keep research in lab; live probe the demo if we found one
            pass
    elif kind == "url":
        playable_url = value
        try:
            from .tools import web_fetch_tool
            fetched = await web_fetch_tool(value, max_chars=8000)
            if fetched.get("ok") and fetched.get("text"):
                page_text = str(fetched["text"])[:6000]
        except Exception as exc:
            log.debug("dissection fetch %s: %s", value, exc)
        research = await scout_reference(
            value,
            project_name=project_name,
            description=description,
            memory=memory,
        )
        stack = fingerprint_stack(page_text)
    elif kind == "git":
        if not lab_id:
            created = lab.create_lab(title=project_name or value, source=value, source_kind="git")
            lab_id = created["id"]
        git_result = await _clone_git(value, lab.install_dir(lab_id))
        if git_result.get("ok"):
            playable = lab.refresh_playable(lab_id)
            install_result = {
                "ok": True,
                "playable": playable,
                "files": lab.list_install_files(lab_id)[:40],
            }
            meta = lab.load_lab(lab_id) or {}
            meta["source"] = value
            meta["source_kind"] = "git"
            lab.save_meta(lab_id, meta)
        blob, files = _read_install_blob(lab_id)
        page_text = blob
        stack = fingerprint_stack(blob, extra_urls=files)
    elif kind == "path":
        if not lab_id:
            created = lab.create_lab(title=project_name or Path(install_path).name, source=install_path, source_kind="install")
            lab_id = created["id"]
        install_result = lab.install_into_lab(lab_id, install_path)
        blob, files = _read_install_blob(lab_id)
        page_text = blob
        stack = fingerprint_stack(blob, extra_urls=files)
    elif kind == "lab":
        blob, files = _read_install_blob(lab_id or "")
        page_text = blob
        stack = fingerprint_stack(blob, extra_urls=files)
        playable_url = lab.serve_url(lab_id or "") or ""

    started_http = False
    if lab_id and live and kind in {"path", "git", "lab"}:
        try:
            served = lab.start_local_http(lab_id)
            playable_url = served.get("url") or playable_url
            started_http = True
        except Exception as exc:
            log.info("lab http for probe skipped: %s", exc)

    probe_url = playable_url or (value if kind == "url" else "")
    if live and probe_url.startswith("http"):
        live_result = await live_probe(probe_url, lab_id=lab_id, slug=slug)
        if live_result.get("ok"):
            live_stack = live_result.get("stack") or {}
            markers = list(dict.fromkeys((stack.get("markers") or []) + (live_stack.get("markers") or [])))
            stack = {
                "engine": live_stack.get("engine") or stack.get("engine") or "",
                "renderer": live_stack.get("renderer") or stack.get("renderer") or "",
                "markers": markers,
            }
            if live_result.get("html_excerpt") and not page_text:
                page_text = str(live_result["html_excerpt"])

    if started_http and kind != "lab":
        # leave lab files; free the port after a one-shot probe
        try:
            lab.stop_local_http(lab_id or "")
        except Exception:
            pass

    synth = await _synthesize(
        source=value or source,
        source_kind=kind,
        stack=stack,
        research=research,
        live=live_result,
        page_text=page_text,
        project_name=project_name or (slug or ""),
        description=description,
    )

    result: dict[str, Any] = {
        "ok": True,
        "source": value or source,
        "source_kind": kind,
        "title": synth.get("title") or value or source,
        "summary": synth.get("summary") or "",
        "mechanics": synth.get("mechanics") or [],
        "core_loop": synth.get("core_loop") or "",
        "feel": synth.get("feel") or "",
        "controls": synth.get("controls") or "",
        "visual_language": synth.get("visual_language") or "",
        "scope_hints": synth.get("scope_hints") or "",
        "steal": synth.get("steal") or [],
        "do_not_copy": synth.get("do_not_copy") or [],
        "stack": stack,
        "playable_url": probe_url,
        "lab_id": lab_id,
        "live": {k: v for k, v in (live_result or {}).items() if k != "html_excerpt"} if live_result else None,
        "research": {
            "title": (research or {}).get("title"),
            "summary": (research or {}).get("summary"),
            "sources": (research or {}).get("sources") or [],
        } if research else None,
        "git": git_result,
        "install": install_result,
        "host": lab.probe_host(),
    }
    result["specimen_block"] = format_specimen_block(result)
    if research and research.get("brief_block"):
        result["research_brief"] = research["brief_block"]
    elif research:
        result["research_brief"] = format_brief_block(research)

    if lab_id:
        try:
            lab.save_lab_specimen(lab_id, result)
        except Exception as exc:
            log.debug("save lab specimen: %s", exc)

    if slug:
        save_dissection(slug, result)

    return result
