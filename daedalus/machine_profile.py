"""Hardware snapshot so Daedalus can recommend a local model that actually fits.

No cloud call. Reads ``/proc``, ``nvidia-smi`` if present, and disk free
under ``DAEDALUS_DATA``.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from typing import Any


def _mem_gb() -> float:
    try:
        text = open("/proc/meminfo", encoding="utf-8").read()
    except OSError:
        return 0.0
    for line in text.splitlines():
        if line.startswith("MemTotal:"):
            kb = float(line.split()[1])
            return round(kb / (1024 * 1024), 1)
    return 0.0


def _disk_gb() -> float:
    try:
        from .paths import data_root
        usage = shutil.disk_usage(str(data_root()))
    except Exception:
        usage = shutil.disk_usage("/")
    return round(usage.free / (1024 ** 3), 1)


def _gpu() -> dict[str, Any] | None:
    smi = shutil.which("nvidia-smi")
    if not smi:
        return None
    try:
        proc = subprocess.run(
            [smi, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    line = (proc.stdout or "").strip().splitlines()[:1]
    if not line:
        return None
    parts = [p.strip() for p in line[0].split(",")]
    name = parts[0] if parts else "NVIDIA"
    vram = 0.0
    if len(parts) > 1:
        try:
            raw = float(parts[1])
            vram = round(raw / 1024.0, 1) if raw > 64 else raw
        except ValueError:
            vram = 0.0
    return {"name": name, "vram_gb": vram}


def classify(*, ram_gb: float, vram_gb: float) -> str:
    """tiny < laptop < studio < workstation — drives the wardrobe."""
    if ram_gb and ram_gb < 7.5 and vram_gb < 6:
        return "tiny"
    if vram_gb >= 16 or ram_gb >= 48:
        return "workstation"
    if vram_gb >= 8 or ram_gb >= 24:
        return "studio"
    return "laptop"


def snapshot() -> dict[str, Any]:
    ram = _mem_gb()
    gpu = _gpu()
    vram = float((gpu or {}).get("vram_gb") or 0)
    klass = classify(ram_gb=ram, vram_gb=vram)
    return {
        "ram_gb": ram,
        "cpu_cores": os.cpu_count() or 1,
        "disk_free_gb": _disk_gb(),
        "gpu": gpu,
        "class": klass,
        "note": {
            "tiny": "1.5B–3B Q4. Fine for conversation; heavy code only in pieces.",
            "laptop": "7B Q4 is the sweet spot for code on 8–16 GB RAM.",
            "studio": "14B Q4 fits. 32B only with ≥16 GB VRAM or a lot of RAM.",
            "workstation": "14B–32B. Do not pull 70B unless you want to spend the disk.",
        }.get(klass, ""),
    }
