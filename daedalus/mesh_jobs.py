"""Async 3D mesh jobs — Tripo / Meshy / fal Hunyuan. Fail closed without keys.

Never write a fake hero GLB. Greybox is an honest cube, labelled as such.
Suno has no stable public submit here — drop ``assets/music.mp3`` yourself.
"""
from __future__ import annotations

import json
import logging
import os
import struct
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .asset_contract import check as contract_check, normalize as contract_normalize, write_default
from .paths import data_root

log = logging.getLogger(__name__)

FAL_MODEL = "fal-ai/hunyuan3d-v3/text-to-3d"
HTTP_TIMEOUT = 10.0

GetJson = Callable[[str, dict[str, str]], dict[str, Any]]
PostJson = Callable[[str, dict[str, str], dict[str, Any]], dict[str, Any]]
GetBytes = Callable[[str, dict[str, str]], bytes]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _env(*names: str) -> str:
    for n in names:
        v = (os.environ.get(n) or "").strip()
        if v:
            return v
    return ""


def providers() -> dict[str, Any]:
    fal = bool(_env("FAL_KEY", "FAL_API_KEY"))
    tripo = bool(_env("TRIPO_API_KEY"))
    meshy = bool(_env("MESHY_API_KEY"))
    suno = bool(_env("SUNO_API_KEY"))
    return {
        "fal": fal,
        "tripo": tripo,
        "meshy": meshy,
        "suno": suno,
        "suno_submit": False,
        "greybox": True,
        "atlas": True,
        "sfx": True,
        "font": True,
        "any_mesh": fal or tripo or meshy,
    }


def jobs_dir() -> Path:
    d = data_root() / "asset_jobs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _job_path(job_id: str) -> Path:
    safe = "".join(c for c in job_id if c.isalnum() or c in "-_")
    if not safe:
        raise ValueError("empty job id")
    return jobs_dir() / f"{safe}.json"


def load_job(job_id: str) -> dict[str, Any] | None:
    path = _job_path(job_id)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def save_job(job: dict[str, Any]) -> dict[str, Any]:
    job = dict(job)
    job["updated_at"] = _now()
    path = _job_path(str(job["id"]))
    path.write_text(json.dumps(job, indent=2) + "\n", encoding="utf-8")
    slug = str(job.get("slug") or "")
    if slug:
        _index_project(slug, job)
    return job


def _index_project(slug: str, job: dict[str, Any]) -> None:
    from . import playground as pg

    root = pg.project_dir(slug)
    if not root.is_dir():
        return
    idx_path = root / "assets" / "_jobs.json"
    idx_path.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    if idx_path.is_file():
        try:
            raw = json.loads(idx_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("jobs"), list):
                rows = [j for j in raw["jobs"] if isinstance(j, dict)]
        except (OSError, json.JSONDecodeError):
            rows = []
    compact = {k: job.get(k) for k in ("id", "status", "provider", "name", "error", "output", "need_key")}
    rows = [r for r in rows if r.get("id") != job.get("id")]
    rows.append(compact)
    idx_path.write_text(json.dumps({"jobs": rows[-32:]}, indent=2) + "\n", encoding="utf-8")


def submit(
    slug: str,
    prompt: str,
    *,
    name: str = "hero",
    provider: str = "auto",
    transport: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Queue a text-to-3D job. Without keys: ``ok=False, need_key=True`` — not a skip-pass."""
    name = _safe_name(name)
    prompt = (prompt or "").strip()
    caps = providers()
    chosen = _pick_provider(provider, caps)
    job = {
        "id": uuid.uuid4().hex[:12],
        "slug": slug,
        "name": name,
        "prompt": prompt[:2000],
        "provider": chosen or (provider or "auto"),
        "status": "need_key",
        "need_key": True,
        "created_at": _now(),
        "remote_id": "",
        "output": "",
        "error": "",
    }
    if os.environ.get("DAEDALUS_OFFLINE", "").strip() in ("1", "true", "yes", "on"):
        job["status"] = "failed"
        job["need_key"] = False
        job["error"] = "offline — mesh submit refused"
        save_job(job)
        return {"ok": False, **job}
    if not chosen:
        job["error"] = (
            "Need FAL_KEY (or FAL_API_KEY), TRIPO_API_KEY, or MESHY_API_KEY. "
            "Greybox cube: action=greybox (not a hero mesh). Never a fake GLB."
        )
        save_job(job)
        return {"ok": False, **job}
    if not prompt:
        job["status"] = "failed"
        job["need_key"] = False
        job["error"] = "mesh prompt is empty"
        save_job(job)
        return {"ok": False, **job}

    job["need_key"] = False
    job["status"] = "queued"
    job["provider"] = chosen
    save_job(job)
    try:
        remote = _start_remote(chosen, prompt, name, transport=transport)
    except Exception as exc:
        job["status"] = "failed"
        job["error"] = f"{chosen} submit failed: {exc}"[:240]
        save_job(job)
        log.warning("mesh submit %s failed: %s", chosen, exc)
        return {"ok": False, **job}
    job["remote_id"] = str(remote.get("remote_id") or "")
    job["status_url"] = str(remote.get("status_url") or "")
    job["response_url"] = str(remote.get("response_url") or "")
    job["status"] = "running" if job["remote_id"] or job["status_url"] else "failed"
    if job["status"] == "failed":
        job["error"] = "provider returned no request id"
        save_job(job)
        return {"ok": False, **job}
    save_job(job)
    return {"ok": True, **job}


def poll(job_id: str, *, transport: dict[str, Any] | None = None) -> dict[str, Any]:
    job = load_job(job_id)
    if not job:
        return {"ok": False, "error": f"unknown job {job_id}"}
    if job.get("status") in ("done", "failed", "need_key"):
        return {"ok": job.get("status") == "done", **job}
    provider = str(job.get("provider") or "")
    try:
        remote = _poll_remote(provider, job, transport=transport)
    except Exception as exc:
        job["status"] = "failed"
        job["error"] = f"{provider} poll failed: {exc}"[:240]
        save_job(job)
        return {"ok": False, **job}
    status = str(remote.get("status") or "running")
    if status == "running":
        job["status"] = "running"
        save_job(job)
        return {"ok": True, **job}
    if status != "done":
        job["status"] = "failed"
        job["error"] = str(remote.get("error") or "provider failed")[:240]
        save_job(job)
        return {"ok": False, **job}
    glb = remote.get("glb") or b""
    if not isinstance(glb, (bytes, bytearray)) or bytes(glb)[:4] != b"glTF":
        job["status"] = "failed"
        job["error"] = "provider did not return a GLB"
        save_job(job)
        return {"ok": False, **job}
    rel = _write_glb(str(job.get("slug") or ""), str(job.get("name") or "hero"), bytes(glb))
    job["output"] = rel
    job["status"] = "done"
    job["contract"] = _contract_for(str(job.get("slug") or ""), str(job.get("name") or "hero"))
    save_job(job)
    return {"ok": True, **job}


def write_greybox(root: Path, *, name: str = "greybox_proxy") -> dict[str, Any]:
    """Honest unit cube. Labelled greybox — not a hero mesh."""
    name = _safe_name(name) or "greybox_proxy"
    rel = f"assets/{name}.glb"
    path = Path(root) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(greybox_glb_bytes())
    contract_path = Path(root) / "studio" / "asset_contract.json"
    if not contract_path.is_file():
        write_default(Path(root), name=name)
    payload = contract_normalize({"name": name, "export_target": f"res://assets/{name}.glb"})
    checked = contract_check(payload)
    _maybe_import_sidecars(Path(root))
    return {
        "ok": bool(checked.get("ok")),
        "greybox": True,
        "hero": False,
        "glb": rel,
        "bytes": path.stat().st_size,
        "contract": checked,
        "note": "greybox cube — not a hero mesh",
    }


def greybox_glb_bytes() -> bytes:
    verts = (
        -0.5, -0.5, -0.5,  0.5, -0.5, -0.5,  0.5,  0.5, -0.5, -0.5,  0.5, -0.5,
        -0.5, -0.5,  0.5,  0.5, -0.5,  0.5,  0.5,  0.5,  0.5, -0.5,  0.5,  0.5,
    )
    idx = (
        0, 1, 2, 0, 2, 3,
        4, 6, 5, 4, 7, 6,
        0, 4, 5, 0, 5, 1,
        3, 2, 6, 3, 6, 7,
        0, 3, 7, 0, 7, 4,
        1, 5, 6, 1, 6, 2,
    )
    bin_blob = b"".join(struct.pack("<f", v) for v in verts)
    bin_blob += b"".join(struct.pack("<H", i) for i in idx)
    json_doc = {
        "asset": {"version": "2.0", "generator": "daedalus-greybox"},
        "extras": {"daedalus": "greybox", "hero": False},
        "scene": 0,
        "scenes": [{"nodes": [0], "name": "greybox"}],
        "nodes": [{"mesh": 0, "name": "greybox_cube"}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
        "accessors": [
            {
                "bufferView": 0,
                "componentType": 5126,
                "count": 8,
                "type": "VEC3",
                "min": [-0.5, -0.5, -0.5],
                "max": [0.5, 0.5, 0.5],
            },
            {"bufferView": 1, "componentType": 5123, "count": 36, "type": "SCALAR"},
        ],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": 96},
            {"buffer": 0, "byteOffset": 96, "byteLength": 72},
        ],
        "buffers": [{"byteLength": len(bin_blob)}],
    }
    json_bytes = json.dumps(json_doc, separators=(",", ":")).encode("utf-8")
    while len(json_bytes) % 4:
        json_bytes += b" "
    while len(bin_blob) % 4:
        bin_blob += b"\x00"
    total = 12 + 8 + len(json_bytes) + 8 + len(bin_blob)
    header = b"glTF" + struct.pack("<II", 2, total)
    json_chunk = struct.pack("<I", len(json_bytes)) + b"JSON" + json_bytes
    bin_chunk = struct.pack("<I", len(bin_blob)) + b"BIN\x00" + bin_blob
    return header + json_chunk + bin_chunk


def music_handoff() -> dict[str, Any]:
    has = bool(_env("SUNO_API_KEY"))
    return {
        "ok": False,
        "supported": False,
        "handoff": True,
        "has_key": has,
        "drop": "assets/music.mp3",
        "error": (
            "Suno has no stable public API in Daedalus. Generate on suno.com "
            "and drop assets/music.mp3. Kit SFX: action=sfx (no key)."
        ),
    }


def _pick_provider(want: str, caps: dict[str, Any]) -> str:
    want = (want or "auto").strip().lower()
    if want in ("fal", "tripo", "meshy"):
        return want if caps.get(want) else ""
    for name in ("fal", "tripo", "meshy"):
        if caps.get(name):
            return name
    return ""


def _safe_name(name: str) -> str:
    raw = "".join(c if c.isalnum() or c == "_" else "_" for c in (name or "hero").lower())
    raw = raw.strip("_") or "hero"
    return raw[:40]


def _write_glb(slug: str, name: str, data: bytes) -> str:
    from . import playground as pg

    rel = f"assets/{name}.glb"
    pg.write_bytes(slug, rel, data)
    root = pg.project_dir(slug)
    _maybe_import_sidecars(root)
    return rel


def _contract_for(slug: str, name: str) -> dict[str, Any]:
    from . import playground as pg

    payload = contract_normalize({"name": name, "export_target": f"res://assets/{name}.glb"})
    checked = contract_check(payload)
    root = pg.project_dir(slug)
    if root.is_dir() and not (root / "studio" / "asset_contract.json").is_file():
        write_default(root, name=name)
    return checked


def _maybe_import_sidecars(root: Path) -> None:
    if not (root / "project.godot").is_file():
        return
    try:
        from .godot_bridge import ensure_import_sidecars
        ensure_import_sidecars(root)
    except Exception:
        log.debug("import sidecars skipped", exc_info=True)


def _http() -> tuple[PostJson, GetJson, GetBytes]:
    import httpx

    def post_json(url: str, headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            r = client.post(url, headers=headers, json=body)
            r.raise_for_status()
            return r.json() if r.content else {}

    def get_json(url: str, headers: dict[str, str]) -> dict[str, Any]:
        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            r = client.get(url, headers=headers)
            r.raise_for_status()
            return r.json() if r.content else {}

    def get_bytes(url: str, headers: dict[str, str]) -> bytes:
        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            r = client.get(url, headers=headers)
            r.raise_for_status()
            return r.content

    return post_json, get_json, get_bytes


def _fns(transport: dict[str, Any] | None) -> tuple[PostJson, GetJson, GetBytes]:
    if transport:
        return transport["post_json"], transport["get_json"], transport["get_bytes"]
    return _http()


def _start_remote(
    provider: str,
    prompt: str,
    name: str,
    *,
    transport: dict[str, Any] | None,
) -> dict[str, Any]:
    post_json, _get_json, _get_bytes = _fns(transport)
    if provider == "fal":
        key = _env("FAL_KEY", "FAL_API_KEY")
        headers = {"Authorization": f"Key {key}", "Content-Type": "application/json"}
        body = post_json(
            f"https://queue.fal.run/{FAL_MODEL}",
            headers,
            {"prompt": prompt, "textured": True},
        )
        return {
            "remote_id": str(body.get("request_id") or ""),
            "status_url": str(body.get("status_url") or ""),
            "response_url": str(body.get("response_url") or ""),
        }
    if provider == "tripo":
        key = _env("TRIPO_API_KEY")
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        body = post_json(
            "https://api.tripo3d.ai/v2/openapi/task",
            headers,
            {"type": "text_to_model", "prompt": prompt},
        )
        data = body.get("data") if isinstance(body.get("data"), dict) else body
        return {"remote_id": str((data or {}).get("task_id") or body.get("task_id") or "")}
    if provider == "meshy":
        key = _env("MESHY_API_KEY")
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        body = post_json(
            "https://api.meshy.ai/openapi/v2/text-to-3d",
            headers,
            {"mode": "preview", "prompt": prompt, "should_remesh": True},
        )
        return {"remote_id": str(body.get("result") or body.get("id") or "")}
    raise ValueError(f"unknown provider {provider}")


def _poll_remote(
    provider: str,
    job: dict[str, Any],
    *,
    transport: dict[str, Any] | None,
) -> dict[str, Any]:
    _post_json, get_json, get_bytes = _fns(transport)
    if provider == "fal":
        key = _env("FAL_KEY", "FAL_API_KEY")
        headers = {"Authorization": f"Key {key}"}
        status_url = str(job.get("status_url") or "")
        if not status_url:
            rid = str(job.get("remote_id") or "")
            status_url = f"https://queue.fal.run/{FAL_MODEL}/requests/{rid}/status"
        st = get_json(status_url, headers)
        state = str(st.get("status") or st.get("state") or "").upper()
        if state in ("IN_QUEUE", "IN_PROGRESS", "IN_QUEUEING"):
            return {"status": "running"}
        if state not in ("COMPLETED", "OK", "SUCCESS"):
            return {"status": "failed", "error": str(st.get("error") or state or "fal failed")}
        resp_url = str(job.get("response_url") or status_url.replace("/status", ""))
        payload = get_json(resp_url, headers)
        url = _first_mesh_url(payload)
        if not url:
            return {"status": "failed", "error": "fal response has no mesh url"}
        return {"status": "done", "glb": get_bytes(url, headers)}
    if provider == "tripo":
        key = _env("TRIPO_API_KEY")
        headers = {"Authorization": f"Bearer {key}"}
        rid = str(job.get("remote_id") or "")
        st = get_json(f"https://api.tripo3d.ai/v2/openapi/task/{rid}", headers)
        data = st.get("data") if isinstance(st.get("data"), dict) else st
        state = str((data or {}).get("status") or "").lower()
        if state in ("queued", "running", "pending", "unknown"):
            return {"status": "running"}
        if state not in ("success", "succeeded", "done", "completed"):
            return {"status": "failed", "error": str((data or {}).get("error") or state)}
        url = _first_mesh_url(data or {})
        if not url:
            return {"status": "failed", "error": "tripo response has no model url"}
        return {"status": "done", "glb": get_bytes(url, {})}
    if provider == "meshy":
        key = _env("MESHY_API_KEY")
        headers = {"Authorization": f"Bearer {key}"}
        rid = str(job.get("remote_id") or "")
        st = get_json(f"https://api.meshy.ai/openapi/v2/text-to-3d/{rid}", headers)
        state = str(st.get("status") or "").upper()
        if state in ("PENDING", "IN_PROGRESS", "PROCESSING"):
            return {"status": "running"}
        if state not in ("SUCCEEDED", "SUCCESS", "DONE"):
            return {"status": "failed", "error": str(st.get("error") or state)}
        url = _first_mesh_url(st)
        if not url:
            return {"status": "failed", "error": "meshy response has no glb url"}
        return {"status": "done", "glb": get_bytes(url, headers)}
    return {"status": "failed", "error": f"unknown provider {provider}"}


def _first_mesh_url(payload: dict[str, Any]) -> str:
    keys = (
        ("model_mesh", "url"),
        ("glb", "url"),
        ("mesh", "url"),
        ("model_urls", "glb"),
        ("output", "model"),
        ("output", "pbr_model"),
    )
    for a, b in keys:
        node = payload.get(a)
        if isinstance(node, dict) and node.get(b):
            return str(node[b])
        if isinstance(node, str) and a in ("glb", "mesh"):
            return node
    for k in ("model", "glb", "url"):
        v = payload.get(k)
        if isinstance(v, str) and v.startswith("http"):
            return v
    return ""
