"""Local-model wardrobe — pick, install, switch without editing .env.

The owner should not have to know Ollama flags. Daedalus:

1. Reads the machine profile.
2. Shows a short catalog (talk / code / reason / embed) that *fits*.
3. Installs via ``ollama pull`` only after confirm (never surprise 10 GB).
4. Persists the active model in ``$DAEDALUS_DATA/wardrobe.json``.

Cloud keys stay optional. Switching to a local model is a Studio click,
not a restart — ``get_config()`` reads this file every call.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
from typing import Any

log = logging.getLogger(__name__)

# RAM figures are Q4_K_M-ish peak, not parameter count. Honest for laptops.
CATALOG: list[dict[str, Any]] = [
    {
        "id": "talk-1p5",
        "role": "talk",
        "ollama": "qwen2.5:1.5b",
        "ram_gb": 3,
        "label": "Whisper (1.5B)",
        "blurb": "Hello / remember / small advice. Fits a weak laptop.",
    },
    {
        "id": "talk-3b",
        "role": "talk",
        "ollama": "qwen2.5:3b",
        "ram_gb": 4,
        "label": "Talk (3B)",
        "blurb": "Normal chat. Do not write a whole game with it.",
    },
    {
        "id": "code-7b",
        "role": "code",
        "ollama": "qwen2.5-coder:7b",
        "ram_gb": 8,
        "label": "Code (7B)",
        "blurb": "Default for games on 8–16 GB. Tools + Phaser.",
    },
    {
        "id": "code-14b",
        "role": "code",
        "ollama": "qwen2.5-coder:14b",
        "ram_gb": 14,
        "label": "Code (14B)",
        "blurb": "Hallucinates APIs less often. Wants ~16 GB RAM or 8+ GB VRAM.",
    },
    {
        "id": "reason-14b",
        "role": "reason",
        "ollama": "qwen2.5:14b",
        "ram_gb": 14,
        "label": "Reason (14B)",
        "blurb": "Plans, review, playtest judges. Slower than coder 7B.",
    },
    {
        "id": "code-32b",
        "role": "code",
        "ollama": "qwen2.5-coder:32b",
        "ram_gb": 24,
        "label": "Code (32B)",
        "blurb": "Workstation / thick GPU only. Do not pull out of curiosity.",
    },
    {
        "id": "embed",
        "role": "embed",
        "ollama": "nomic-embed-text",
        "ram_gb": 1,
        "label": "Memory (embed)",
        "blurb": "Local embeddings for Chroma if you want RAG without the cloud.",
    },
]


def _path():
    from .paths import data_root
    return data_root() / "wardrobe.json"


def load() -> dict[str, Any]:
    path = _path()
    if not path.is_file():
        return {"backend": "auto", "active": None, "offline": False, "roles": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("backend", "auto")
            data.setdefault("active", None)
            data.setdefault("offline", False)
            data.setdefault("roles", {})
            return data
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("wardrobe.json: %s", exc)
    return {"backend": "auto", "active": None, "offline": False, "roles": {}}


def save(data: dict[str, Any]) -> dict[str, Any]:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def wants_local() -> bool:
    data = load()
    if data.get("offline"):
        return True
    backend = str(data.get("backend") or "auto").lower()
    if backend in {"ollama", "lmstudio", "llamacpp", "vllm", "local"}:
        return True
    return bool(data.get("active"))


def active_model() -> str | None:
    name = load().get("active")
    return str(name) if name else None


def by_id(item_id: str) -> dict[str, Any] | None:
    for row in CATALOG:
        if row["id"] == item_id or row["ollama"] == item_id:
            return row
    return None


def recommend(profile: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """One pick per role that fits RAM (VRAM counts as extra RAM for GPU offload)."""
    if profile is None:
        from .machine_profile import snapshot
        profile = snapshot()
    budget = float(profile.get("ram_gb") or 0) + 0.6 * float((profile.get("gpu") or {}).get("vram_gb") or 0)
    if budget <= 0:
        budget = 8.0
    picked: dict[str, dict[str, Any]] = {}
    for row in CATALOG:
        if float(row["ram_gb"]) > budget + 0.5:
            continue
        role = row["role"]
        prev = picked.get(role)
        # Prefer the largest that still fits.
        if prev is None or float(row["ram_gb"]) >= float(prev["ram_gb"]):
            picked[role] = row
    order = ("talk", "code", "reason", "embed")
    return [picked[r] for r in order if r in picked]


def ollama_bin() -> str | None:
    return shutil.which("ollama")


def installed_ollama_models() -> list[str]:
    exe = ollama_bin()
    if not exe:
        return []
    try:
        proc = subprocess.run(
            [exe, "list"], capture_output=True, text=True, timeout=8, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    names: list[str] = []
    for line in (proc.stdout or "").splitlines()[1:]:
        name = line.split()[0] if line.split() else ""
        if name:
            names.append(name)
    return names


def install_plan(item_id: str) -> dict[str, Any]:
    row = by_id(item_id)
    if not row:
        return {"ok": False, "error": f"unknown model {item_id}"}
    exe = ollama_bin()
    cmd = ["ollama", "pull", row["ollama"]]
    return {
        "ok": True,
        "item": row,
        "have_ollama": bool(exe),
        "already": row["ollama"] in installed_ollama_models() or any(
            n.startswith(row["ollama"]) for n in installed_ollama_models()
        ),
        "command": " ".join(cmd),
        "hint": None if exe else "Инсталирай Ollama от https://ollama.com/download и после потвърди дърпането.",
    }


def pull(item_id: str, *, timeout: int = 600) -> dict[str, Any]:
    """Run ``ollama pull``. Long. Caller should treat this as a job."""
    plan = install_plan(item_id)
    if not plan.get("ok"):
        return plan
    if not plan.get("have_ollama"):
        return {**plan, "ok": False, "error": "ollama not on PATH"}
    if plan.get("already"):
        return {**plan, "pulled": False, "message": "вече е локално"}
    exe = ollama_bin()
    assert exe
    model = plan["item"]["ollama"]
    try:
        proc = subprocess.run(
            [exe, "pull", model],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        return {**plan, "ok": False, "error": "timeout — ollama still pulling in background?"}
    ok = proc.returncode == 0
    return {
        **plan,
        "ok": ok,
        "pulled": ok,
        "exit_code": proc.returncode,
        "output": ((proc.stdout or "") + (proc.stderr or ""))[-4000:],
    }


def activate(item_id: str, *, backend: str = "ollama") -> dict[str, Any]:
    row = by_id(item_id)
    model = row["ollama"] if row else item_id
    data = load()
    data["backend"] = backend or "ollama"
    data["active"] = model
    if row:
        roles = dict(data.get("roles") or {})
        roles[row["role"]] = model
        data["roles"] = roles
    save(data)
    return {"ok": True, "active": model, "backend": data["backend"], "item": row}


def set_offline(flag: bool) -> dict[str, Any]:
    data = load()
    data["offline"] = bool(flag)
    if flag and str(data.get("backend") or "auto") in ("auto", "cloud", ""):
        data["backend"] = "ollama"
    save(data)
    return {"ok": True, "offline": data["offline"], "backend": data.get("backend")}


def use_cloud() -> dict[str, Any]:
    data = load()
    data["backend"] = "cloud"
    data["active"] = None
    data["offline"] = False
    save(data)
    return {"ok": True, "backend": "cloud"}


def status() -> dict[str, Any]:
    from .machine_profile import snapshot
    profile = snapshot()
    rec = recommend(profile)
    rec_ids = {r["id"] for r in rec}
    installed = installed_ollama_models()
    data = load()
    budget = float(profile.get("ram_gb") or 0) + 0.6 * float((profile.get("gpu") or {}).get("vram_gb") or 0)
    catalog = []
    for row in CATALOG:
        have = any(
            n == row["ollama"] or n.startswith(row["ollama"]) or row["ollama"] in n
            for n in installed
        )
        catalog.append({
            **row,
            "fits": float(row["ram_gb"]) <= budget + 0.5 if budget else True,
            "installed": have,
            "recommended": row["id"] in rec_ids,
        })
    return {
        "ok": True,
        "profile": profile,
        "wardrobe": data,
        "ollama": bool(ollama_bin()),
        "installed": installed,
        "recommended": rec,
        "catalog": catalog,
    }
