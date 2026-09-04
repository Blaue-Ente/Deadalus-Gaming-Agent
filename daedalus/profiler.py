"""Profiler / test-log connectors — parse Unity, Godot, Chrome, NUnit, generic FPS logs.

Does not vendor Unity. ``unity_tests`` needs UNITY_PATH (batchmode) and is not Play.
Missing binaries return an honest need, not a green skip.

Native tool: ``profiler``. CLI: ``python -m daedalus.profiler``.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from .game_lint import resolve_path

_MAX_LOG = 2_000_000
_FPS_RE = re.compile(r"\b(?:fps|FPS)\s*[:=]?\s*(\d+(?:\.\d+)?)", re.I)
_MS_RE = re.compile(r"\b(?:frame(?:[_ ]?time)?|dt|delta)\s*[:=]?\s*(\d+(?:\.\d+)?)\s*ms\b", re.I)
_GC_RE = re.compile(r"\b(gc|GC)[^\n]{0,40}(\d+(?:\.\d+)?)\s*(ms|MB|mb)", re.I)
_FAIL_RE = re.compile(r"\b(FAIL(?:ED)?|ERROR|Exception|Assertion(?:Error)?)\b", re.I)


def _read_text(path: Path) -> str:
    data = path.read_bytes()
    if len(data) > _MAX_LOG:
        data = data[:_MAX_LOG]
    return data.decode("utf-8", errors="replace")


def detect_kind(text: str, path: str = "") -> str:
    name = Path(path).name.lower()
    if name.endswith(".xml") or "<test-run" in text[:2000] or "<test-case" in text[:4000]:
        return "nunit"
    if text.lstrip().startswith("{") or text.lstrip().startswith("["):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict) and ("traceEvents" in data or data.get("displayTimeUnit")):
            return "chrome"
        if isinstance(data, list) and data and isinstance(data[0], dict) and "ph" in data[0]:
            return "chrome"
    if "Godot Engine" in text or "SCRIPT ERROR" in text or "DaedalusBridge" in text:
        return "godot"
    if "UnityEngine" in text or "Microsoft NUnit" in text or "Unity Editor" in text:
        return "unity"
    return "generic"


def parse_nunit(text: str) -> dict[str, Any]:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        return {"ok": False, "kind": "nunit", "error": f"xml: {exc}"}
    cases: list[dict[str, Any]] = []
    failed = 0
    total = 0
    for node in root.iter():
        tag = node.tag.split("}")[-1].lower()
        if tag not in ("test-case", "testcase"):
            continue
        total += 1
        result = (node.get("result") or node.get("status") or "").lower()
        name = node.get("name") or node.get("fullname") or "case"
        row = {"name": name, "result": result or "unknown"}
        fail_node = None
        for child in list(node):
            ctag = child.tag.split("}")[-1].lower()
            if ctag in ("failure", "reason"):
                fail_node = child
                break
        if result in ("failed", "failure", "error") or fail_node is not None:
            failed += 1
            msg = ""
            if fail_node is not None:
                msg = (fail_node.get("message") or fail_node.text or "")[:400]
                for sub in list(fail_node):
                    if (sub.tag.split("}")[-1].lower()) in ("message", "stack-trace"):
                        msg = ((sub.text or "") + "\n" + msg)[:800]
            row["message"] = msg.strip()
        cases.append(row)
    ok = failed == 0 and total > 0
    return {
        "ok": ok,
        "kind": "nunit",
        "total": total,
        "failed": failed,
        "passed": max(0, total - failed),
        "failures": [c for c in cases if c.get("message")][:20],
        "cases": cases[:40],
        "note": "empty xml is not a pass" if total == 0 else "",
    }


def parse_chrome_trace(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return {"ok": False, "kind": "chrome", "error": str(exc)[:200]}
    events = data.get("traceEvents") if isinstance(data, dict) else data
    if not isinstance(events, list):
        return {"ok": False, "kind": "chrome", "error": "no traceEvents"}
    durations: list[float] = []
    names: dict[str, float] = {}
    for ev in events:
        if not isinstance(ev, dict):
            continue
        ph = ev.get("ph")
        dur = ev.get("dur")
        if ph in ("X", "B") and isinstance(dur, (int, float)):
            ms = float(dur) / 1000.0 if dur > 1000 else float(dur)
            durations.append(ms)
            label = str(ev.get("name") or "event")
            names[label] = names.get(label, 0.0) + ms
    if not durations:
        return {"ok": True, "kind": "chrome", "events": len(events), "frames": 0, "note": "no duration events"}
    durations.sort()
    p95 = durations[min(len(durations) - 1, int(len(durations) * 0.95))]
    avg = sum(durations) / len(durations)
    heavy = sorted(names.items(), key=lambda kv: -kv[1])[:8]
    budget_ok = p95 <= 16.7
    return {
        "ok": budget_ok,
        "kind": "chrome",
        "events": len(events),
        "samples": len(durations),
        "avg_ms": round(avg, 3),
        "p95_ms": round(p95, 3),
        "max_ms": round(durations[-1], 3),
        "budget_60fps": budget_ok,
        "heaviest": [{"name": n, "ms": round(ms, 3)} for n, ms in heavy],
        "note": "" if budget_ok else "p95 frame/slice over 16.7ms (60 FPS budget)",
    }


def parse_generic(text: str, *, kind: str = "generic") -> dict[str, Any]:
    fps = [float(x) for x in _FPS_RE.findall(text)]
    frame_ms = [float(x) for x in _MS_RE.findall(text)]
    gc = _GC_RE.findall(text)
    fail_lines = [ln.strip()[:240] for ln in text.splitlines() if _FAIL_RE.search(ln)][:20]
    avg_fps = sum(fps) / len(fps) if fps else None
    avg_ms = sum(frame_ms) / len(frame_ms) if frame_ms else None
    ok = not fail_lines
    if avg_fps is not None and avg_fps < 30:
        ok = False
    if avg_ms is not None and avg_ms > 33:
        ok = False
    return {
        "ok": ok,
        "kind": kind,
        "fps_samples": len(fps),
        "avg_fps": round(avg_fps, 2) if avg_fps is not None else None,
        "min_fps": round(min(fps), 2) if fps else None,
        "frame_ms_samples": len(frame_ms),
        "avg_frame_ms": round(avg_ms, 3) if avg_ms is not None else None,
        "gc_hits": len(gc),
        "failures": fail_lines,
        "note": "no fps/ms numbers found" if not fps and not frame_ms and not fail_lines else "",
    }


def parse_log(path: str = "", *, slug: str = "", text: str | None = None, kind: str = "auto") -> dict[str, Any]:
    if text is None:
        resolved = resolve_path(path, slug=slug)
        blob = _read_text(resolved)
        source = str(resolved)
    else:
        blob = text
        source = path or "inline"
    detected = (kind or "auto").lower()
    if detected in ("", "auto"):
        detected = detect_kind(blob, source)
    if detected in ("nunit", "unity-tests", "utf"):
        result = parse_nunit(blob)
    elif detected in ("chrome", "trace"):
        result = parse_chrome_trace(blob)
    elif detected in ("godot", "unity", "generic", "tracy"):
        result = parse_generic(blob, kind=detected)
    else:
        result = parse_generic(blob, kind=detected)
    result["source"] = source
    result["detected"] = detected
    return result


def unity_tests(slug: str, *, platform: str = "EditMode") -> dict[str, Any]:
    """Batchmode Unity Test Framework. Import/test run is not Play mode."""
    from .engine_runners import unity_bin
    from .unity_runtime import HONEST, is_unity_slug

    if not slug:
        return {"ok": False, "error": "slug required"}
    if not is_unity_slug(slug):
        return {"ok": False, "error": f"{slug} is not a Unity project"}
    bin_path = unity_bin()
    if not bin_path:
        return {
            "ok": False,
            "honest": True,
            "need": "unity_local",
            "play": False,
            "error": HONEST,
        }
    from . import playground as pg
    from .paths import data_root

    root = pg.project_dir(slug)
    out_dir = data_root() / "profiler" / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    results = out_dir / "unity-tests.xml"
    plat = platform if platform in ("EditMode", "PlayMode") else "EditMode"
    cmd = [
        bin_path, "-runTests", "-batchmode", "-nographics", "-quit",
        "-projectPath", str(root),
        "-testResults", str(results),
        "-testPlatform", plat,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Unity test run timed out (180s)", "need": "unity_local"}
    except OSError as exc:
        return {"ok": False, "error": str(exc)[:200], "need": "unity_local"}
    parsed: dict[str, Any] = {}
    if results.is_file():
        parsed = parse_nunit(_read_text(results))
        parsed["results_path"] = str(results)
    return {
        "ok": bool(parsed.get("ok")) and proc.returncode == 0,
        "via": "unity-batchmode",
        "play": False,
        "platform": plat,
        "exit_code": proc.returncode,
        "log_tail": ((proc.stdout or "") + "\n" + (proc.stderr or ""))[-2000:],
        "tests": parsed,
        "note": "batchmode -runTests is not Editor Play mode",
    }


def status() -> dict[str, Any]:
    from .engine_runners import unity_bin
    from .godot_runtime_probe import godot_bin

    try:
        gbin = godot_bin()
    except Exception:
        gbin = ""
    return {
        "ok": True,
        "unity": bool(unity_bin()),
        "godot": bool(gbin),
        "note": (
            "Parse logs anytime. Unity Test Framework needs UNITY_PATH. "
            "That run is not Play."
        ),
    }


def run_action(action: str = "status", **fields: Any) -> dict[str, Any]:
    act = (action or "status").strip().lower()
    slug = str(fields.get("slug") or "")
    path = str(fields.get("path") or "")
    kind = str(fields.get("kind") or "auto")
    text = fields.get("text")
    if text is not None:
        text = str(text)
    if act in ("status", "tools"):
        return status()
    if act in ("parse", "read", "summarize"):
        try:
            return parse_log(path, slug=slug, text=text, kind=kind)
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:300]}
    if act in ("unity_tests", "utf", "nunit"):
        return unity_tests(slug, platform=str(fields.get("platform") or "EditMode"))
    return {"ok": False, "error": f"unknown action {act}"}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        print(json.dumps(status(), indent=2))
        return 0
    if args[0].startswith("{"):
        payload = json.loads(args[0])
        result = run_action(str(payload.pop("action", "parse")), **payload)
    else:
        action = args[0]
        payload = {"path": args[1]} if len(args) > 1 else {}
        result = run_action(action, **payload)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
