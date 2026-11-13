"""First run: what this computer has, what Daedalus needs, which keys matter, and what Daedalus can do (owner,
2026-11-13).

``report()`` checks the machine (Python, Daedalus's own packages, Pillow, a browser for proofs, git, Node, Ollama,
Godot, Unity, free disk space), which keys are set (names only, never a value), and turns that into the list of
abilities that are ready and what each missing one needs. Studio shows it on first run and from the header; the
same check runs in a terminal with ``daedalus doctor`` (before the server is even started).

Every item carries its install commands per system. Texts the owner reads live in ``app/static/i18n.js`` (English
and Bulgarian) under ``onb.<id>.*``; this module returns ids, facts and commands. Never raises.
"""
from __future__ import annotations

import importlib.util
import os
import platform
import shutil
import subprocess
import sys
import time
from typing import Any

VERSION = 1                      # bump when the onboarding changes enough to show it again
PYTHON_MIN = (3, 11)
PYTHON_RECOMMENDED = (3, 12)
DISK_MIN_GB = 2.0
# Daedalus's own packages (pyproject dependencies) by import name.
CORE_MODULES = ("fastapi", "uvicorn", "httpx", "pydantic", "chromadb", "openai", "jinja2", "multipart")
BRAIN_KEYS = ("OPENROUTER_API_KEY", "ALLTOKEN_API_KEY", "NVIDIA_API_KEY", "NOVITA_API_KEY", "ANTHROPIC_API_KEY",
              "OPENAI_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY")
# Keys the owner may add, in the order the onboarding lists them, with the abilities each one opens.
KEYS: tuple[tuple[str, str], ...] = (
    ("NVIDIA_API_KEY", "brain"), ("OPENROUTER_API_KEY", "brain"), ("ALLTOKEN_API_KEY", "brain"),
    ("NOVITA_API_KEY", "brain"), ("ANTHROPIC_API_KEY", "brain"), ("OPENAI_API_KEY", "brain"),
    ("GEMINI_API_KEY", "brain"), ("GROQ_API_KEY", "brain"),
    ("ELEVENLABS_API_KEY", "paid_sound"), ("SUBQ_API_KEY", "judge"),
    ("GITHUB_TOKEN", "publish"), ("GITLAB_TOKEN", "publish"), ("NETLIFY_AUTH_TOKEN", "publish"),
)
_PIP = "python -m pip install"
INSTALL: dict[str, dict[str, str]] = {
    "python": {"windows": "winget install Python.Python.3.12", "mac": "brew install python@3.12",
               "linux": "sudo apt install python3.12 python3.12-venv", "url": "https://www.python.org/downloads/"},
    "packages": {"all": f"{_PIP} -e ."},
    "pillow": {"all": f"{_PIP} Pillow"},
    "browser": {"all": f"{_PIP} playwright && python -m playwright install chromium"},
    "git": {"windows": "winget install Git.Git", "mac": "xcode-select --install", "linux": "sudo apt install git",
            "url": "https://git-scm.com/downloads"},
    "node": {"windows": "winget install OpenJS.NodeJS.LTS", "mac": "brew install node",
             "linux": "sudo apt install nodejs npm", "url": "https://nodejs.org/"},
    "ollama": {"windows": "winget install Ollama.Ollama", "mac": "brew install ollama",
               "linux": "curl -fsSL https://ollama.com/install.sh | sh", "after": "ollama pull qwen3-coder:30b",
               "url": "https://ollama.com/download"},
    "godot": {"windows": "winget install GodotEngine.GodotEngine", "mac": "brew install --cask godot",
              "linux": "flatpak install flathub org.godotengine.Godot", "after": "GODOT_BIN=<path to the Godot binary>",
              "url": "https://godotengine.org/download"},
    "unity": {"all": "Unity Hub → Installs → add an Editor", "after": "UNITY_PATH=<path to the Unity Editor>",
              "url": "https://unity.com/download"},
    "disk": {"all": "Free some space on the drive that holds Daedalus's data folder."},
    "brain": {"all": "Settings → Connectors: add one key (NVIDIA_API_KEY has free models), or run Ollama."},
}
# What Daedalus can do, and which checks each ability needs (all of them).
ABILITIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("web_games", ("brain",)),
    ("apps", ("brain", "node")),
    ("mentor", ("brain",)),
    ("free_sound_art", ()),
    ("tests_and_proof", ("browser",)),
    ("generated_art", ("pillow", "key:image")),
    ("paid_sound", ("key:ELEVENLABS_API_KEY",)),
    ("godot", ("godot",)),
    ("unity", ("unity",)),
    ("local_models", ("ollama",)),
    ("revive", ("git", "brain")),
    ("publish", ("git",)),
    ("judge", ("key:SUBQ_API_KEY",)),
    ("team", ()),
)


def _os() -> str:
    return {"win32": "windows", "darwin": "mac"}.get(sys.platform, "linux")


def _run_version(cmd: list[str]) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=4)
        text = (out.stdout or out.stderr or "").strip().splitlines()
        return text[0][:80] if text else ""
    except Exception:
        return ""


def _item(iid: str, level: str, ok: bool, detail: str = "", **extra: Any) -> dict[str, Any]:
    """level: required | recommended | optional."""
    how = INSTALL.get(iid, {})
    command = how.get(_os()) or how.get("all") or ""
    return {"id": iid, "level": level, "ok": bool(ok), "detail": detail, "install": command,
            "after": how.get("after", ""), "url": how.get("url", ""), **extra}


def _python() -> dict[str, Any]:
    v = sys.version_info
    ok = (v.major, v.minor) >= PYTHON_MIN
    return _item("python", "required", ok, f"{v.major}.{v.minor}.{v.micro}",
                 minimum=".".join(map(str, PYTHON_MIN)), recommended=".".join(map(str, PYTHON_RECOMMENDED)),
                 outdated=ok and (v.major, v.minor) < PYTHON_RECOMMENDED)


def _packages() -> dict[str, Any]:
    missing = [m for m in CORE_MODULES if importlib.util.find_spec(m) is None]
    return _item("packages", "required", not missing, ", ".join(missing), missing=missing)


def key_set(name: str) -> bool:
    try:
        from .credentials import env
        return bool(env(name))
    except Exception:
        return bool(os.environ.get(name))


SMALL_LOCAL_B = 14        # a local model this size or smaller does not build a game with tools (live test 2026-11-13)


def model_size_b(model: str) -> float | None:
    """'qwen2.5-coder:7b' -> 7.0; None when the name says no size."""
    import re
    m = re.search(r"(?<![\d.])(\d+(?:\.\d+)?)\s*b\b", (model or "").lower())
    return float(m.group(1)) if m else None


def _brain() -> dict[str, Any]:
    """A model to think with: any cloud key, a key saved in Connectors, or a local model that answers. Says which
    model Daedalus actually uses (live test 2026-11-13: an OpenRouter key was set, but .env pinned a local 7B model
    that wrote no files), and warns when that is a small local model."""
    keys = [k for k in BRAIN_KEYS if key_set(k)]
    ok = bool(keys)
    active, local, weak = "", False, False
    try:
        from .llm_config import get_brain_config
        from .local_llm import is_local_provider
        cfg = get_brain_config() or {}
        provider, model = str(cfg.get("provider") or ""), str(cfg.get("model") or "")
        active = f"{provider} {model}".strip()
        local = bool(cfg.get("local")) or is_local_provider(provider)
        ok = ok or bool(cfg.get("api_key")) or local
        size = model_size_b(model)
        weak = local and size is not None and size <= SMALL_LOCAL_B
    except Exception:
        pass
    return _item("brain", "required", ok, active or ", ".join(keys), active=active, local=local, weak=weak,
                 keys=keys)


def _pillow() -> dict[str, Any]:
    try:
        import PIL
        return _item("pillow", "recommended", True, str(getattr(PIL, "__version__", "")))
    except Exception:
        return _item("pillow", "recommended", False)


def _browser() -> dict[str, Any]:
    has_pw = importlib.util.find_spec("playwright") is not None
    exe = ""
    try:
        from .playground_browser import chromium_executable
        exe = chromium_executable() or ""
    except Exception:
        pass
    return _item("browser", "recommended", has_pw and bool(exe), exe, playwright=has_pw)


def _tool(iid: str, names: tuple[str, ...], version_args: tuple[str, ...], level: str) -> dict[str, Any]:
    path = next((p for p in (shutil.which(n) for n in names) if p), "")
    return _item(iid, level, bool(path), _run_version([path, *version_args]) if path else "", path=path)


def _ollama() -> dict[str, Any]:
    path = shutil.which("ollama") or ""
    running, models = False, []
    try:
        from .local_llm import default_base_url, probe_endpoint
        probe = probe_endpoint(default_base_url("ollama"), timeout=1.2)
        running, models = bool(probe.get("ok")), list(probe.get("models") or [])[:8]
    except Exception:
        pass
    return _item("ollama", "optional", bool(path) or running, _run_version([path, "--version"]) if path else "",
                 path=path, running=running, models=models)


def _godot() -> dict[str, Any]:
    try:
        from .harness.play_worker.engines import find_godot
        got = find_godot()
    except Exception:
        got = {}
    return _item("godot", "optional", bool(got.get("ok")), str(got.get("path") or ""))


def _unity() -> dict[str, Any]:
    try:
        from .engine_runners import unity_bin
        path = unity_bin() or ""
    except Exception:
        path = ""
    return _item("unity", "optional", bool(path), path)


def _disk() -> dict[str, Any]:
    try:
        from .paths import data_root
        free = shutil.disk_usage(str(data_root())).free / 1024 ** 3
    except Exception:
        return _item("disk", "recommended", True, "")
    return _item("disk", "recommended", free >= DISK_MIN_GB, f"{free:.1f} GB", free_gb=round(free, 1))


def subq_switch() -> dict[str, Any]:
    """The SubQ switch as the owner sees it: on or off, whether a key exists, whether the environment pins it."""
    from . import subq_judge
    env = (os.environ.get("DAEDALUS_SUBQ_SHADOW") or "").strip()
    stats = {q: {"judged": v["judged"], "agree": v["agree"]}
             for q, v in (subq_judge.summary(limit=1).get("questions") or {}).items() if v.get("judged")}
    return {"on": subq_judge.switch_on(), "has_key": bool(subq_judge.key()), "active": subq_judge.enabled(),
            "env_locked": bool(env), "env": "DAEDALUS_SUBQ_SHADOW", "stats": stats}


def _abilities(items: dict[str, dict[str, Any]], keys: dict[str, bool]) -> list[dict[str, Any]]:
    image_key = any(keys.get(k) for k in ("NOVITA_API_KEY", "OPENROUTER_API_KEY", "NVIDIA_API_KEY"))
    out = []
    for aid, needs in ABILITIES:
        missing = []
        for need in needs:
            if need == "key:image":
                ok = image_key
            elif need.startswith("key:"):
                ok = keys.get(need[4:], False)
            else:
                ok = items.get(need, {}).get("ok", False)
            if not ok:
                missing.append(need)
        out.append({"id": aid, "ready": not missing, "needs": missing})
    return out


def report() -> dict[str, Any]:
    """The whole first-run picture. Never raises; each check that fails is reported as missing."""
    t0 = time.monotonic()
    checks = [_python, _packages, _brain, _pillow, _browser,
              lambda: _tool("git", ("git",), ("--version",), "recommended"),
              lambda: _tool("node", ("node",), ("--version",), "recommended"),
              _ollama, _godot, _unity, _disk]
    items = []
    for check in checks:
        try:
            items.append(check())
        except Exception as exc:                   # a broken check is a missing item, never a crash
            items.append({"id": getattr(check, "__name__", "?").strip("_"), "level": "optional", "ok": False,
                          "detail": f"check failed: {exc}"[:160]})
    by_id = {i["id"]: i for i in items}
    keys = {name: key_set(name) for name, _use in KEYS}
    try:
        switch = subq_switch()
    except Exception:
        switch = {"on": False, "has_key": False, "active": False, "env_locked": False}
    ready = all(i["ok"] for i in items if i["level"] == "required")
    return {
        "ok": True, "version": VERSION, "ready": ready, "seen": seen(), "auto_open": auto_open(), "os": _os(),
        "platform": f"{platform.system()} {platform.release()}".strip(),
        "start": {"url": "http://127.0.0.1:8787", "command": "daedalus serve", "check": "daedalus doctor"},
        "items": items, "keys": [{"name": n, "use": u, "set": keys[n]} for n, u in KEYS],
        "abilities": _abilities(by_id, keys), "subq": switch,
        "ms": int((time.monotonic() - t0) * 1000),
    }


def auto_open() -> bool:
    """Studio opens the onboarding by itself on first run, unless DAEDALUS_ONBOARDING_AUTO=0 (kiosk, CI, the test
    suite's own servers). The Setup button always opens it."""
    return (os.environ.get("DAEDALUS_ONBOARDING_AUTO") or "1").strip().lower() not in {"0", "false", "off", "no"}


def seen() -> bool:
    try:
        from . import settings_file
        return int(settings_file.load().get("onboarding_seen") or 0) >= VERSION
    except Exception:
        return False


def mark_seen() -> None:
    from . import settings_file
    settings_file.update({"onboarding_seen": VERSION})


_MARK = {True: "ok ", False: "-- "}


def text_report(rep: dict[str, Any] | None = None) -> str:
    """The same check for a terminal (``daedalus doctor``), in English."""
    rep = rep or report()
    lines = [f"Daedalus check on {rep['platform']}  ({'ready' if rep['ready'] else 'NOT READY'})", ""]
    for i in rep["items"]:
        mark = _MARK[i["ok"]] if i["ok"] or i["level"] != "required" else "!! "
        lines.append(f"  {mark}{i['id']:<9} {i['level']:<12} {i.get('detail') or ''}".rstrip())
        if i.get("weak"):
            lines.append(f"       warning: {i['active']} is a small local model; it will not build a game with "
                         f"tools. Use a cloud model (DAEDALUS_PROVIDER / DAEDALUS_MODEL) or a larger local one.")
        if not i["ok"] and i.get("install"):
            lines.append(f"       install: {i['install']}")
            if i.get("after"):
                lines.append(f"       then:    {i['after']}")
    lines += ["", "  keys (names only): " + ", ".join(
        f"{k['name']}={'set' if k['set'] else '-'}" for k in rep["keys"])]
    sq = rep["subq"]
    lines.append(f"  SubQ judge: {'on' if sq.get('on') else 'off'}, key {'set' if sq.get('has_key') else 'missing'}")
    lines += ["", "  abilities:"]
    for a in rep["abilities"]:
        lines.append(f"    {'ok ' if a['ready'] else '-- '}{a['id']}" + (f"  (needs {', '.join(a['needs'])})"
                                                                         if a["needs"] else ""))
    lines += ["", f"  start: {rep['start']['command']}  →  {rep['start']['url']}"]
    return "\n".join(lines)


if __name__ == "__main__":
    print(text_report())
