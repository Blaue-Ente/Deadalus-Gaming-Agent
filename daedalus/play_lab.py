"""Play Lab — Daedalus's virtual computer for specimen games.

A lab is a jailed workspace under ``$DAEDALUS_DATA/labs/{id}/``. It can host
an HTML5 / cloned / extracted game so dissection can probe it.

Backends, in order:
1. Docker container (optional) — isolated HTTP server for the install tree.
2. Local HTTP on 127.0.0.1 — always available; not a full OS VM.
3. Filesystem only — install + inspect without serving.

This is not QEMU Windows and not Steam. Native installers (.exe / .msi / .dmg)
are refused unless the owner drops extracted HTML5/git into the lab.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import subprocess
import threading
import uuid
import zipfile
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .paths import data_root, playground_projects_dir

log = logging.getLogger(__name__)

LAB_META = "lab.json"
INSTALL_DIR = "install"
PROBES_DIR = "probes"
SPECIMEN_REL = "specimen.json"
DOCKER_IMAGE_DEFAULT = "python:3.12-alpine"
_NATIVE_SUFFIXES = {
    ".exe", ".msi", ".dmg", ".pkg", ".appimage", ".apk", ".ipa", ".deb", ".rpm",
}
_MAX_INSTALL_BYTES = 200 * 1024 * 1024

# lab_id → {httpd, thread, port}
_HTTP: dict[str, dict[str, Any]] = {}


def labs_root() -> Path:
    path = data_root() / "labs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug_id(title: str = "") -> str:
    raw = "".join(ch.lower() if ch.isalnum() else "-" for ch in (title or ""))
    raw = "-".join(p for p in raw.split("-") if p)[:24]
    tail = uuid.uuid4().hex[:8]
    return f"lab-{raw}-{tail}" if raw else f"lab-{tail}"


def lab_dir(lab_id: str) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "" for ch in (lab_id or ""))
    if not safe.startswith("lab-") or len(safe) > 80:
        raise ValueError("invalid lab id")
    root = labs_root()
    path = (root / safe).resolve()
    if root.resolve() not in path.parents and path != root.resolve():
        raise ValueError("lab path escaped labs root")
    return path


def install_dir(lab_id: str) -> Path:
    return lab_dir(lab_id) / INSTALL_DIR


def probes_dir(lab_id: str) -> Path:
    return lab_dir(lab_id) / PROBES_DIR


def _write_meta(lab_id: str, data: dict[str, Any]) -> None:
    path = lab_dir(lab_id) / LAB_META
    data = dict(data)
    data["updated_at"] = _now_iso()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_lab(lab_id: str) -> dict[str, Any] | None:
    try:
        path = lab_dir(lab_id) / LAB_META
    except ValueError:
        return None
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def probe_host() -> dict[str, Any]:
    """What this machine can actually run — no network, no pulls."""
    docker_bin = shutil.which("docker")
    qemu_bin = shutil.which("qemu-system-x86_64") or shutil.which("qemu-system-aarch64")
    wine_bin = shutil.which("wine")
    chrome = None
    for name in ("chromium", "chromium-browser", "google-chrome", "chrome"):
        chrome = shutil.which(name)
        if chrome:
            break
    docker_ready = bool(docker_bin)
    image = os.environ.get("DAEDALUS_LAB_IMAGE", DOCKER_IMAGE_DEFAULT)
    image_present = False
    if docker_ready:
        image_present = _docker_image_present(image)
    return {
        "ok": True,
        "filesystem": True,
        "local_http": True,
        "docker": {
            "bin": docker_bin,
            "ready": docker_ready,
            "image": image,
            "image_present": image_present,
            "pull_opt_in": _env_flag("DAEDALUS_LAB_PULL"),
        },
        "chromium": chrome,
        "qemu": qemu_bin,
        "wine": wine_bin,
        "note": (
            "Play Lab is a jailed install tree + HTTP. Docker is an optional VM. "
            "QEMU/Wine are detected but not auto-started."
        ),
    }


def _env_flag(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


def _docker_image_present(image: str) -> bool:
    if not shutil.which("docker"):
        return False
    try:
        proc = subprocess.run(
            ["docker", "image", "inspect", image],
            capture_output=True,
            timeout=8,
            check=False,
        )
        return proc.returncode == 0
    except Exception:
        return False


def create_lab(*, title: str = "", source: str = "", source_kind: str = "") -> dict[str, Any]:
    lab_id = _slug_id(title)
    root = lab_dir(lab_id)
    root.mkdir(parents=True, exist_ok=True)
    (root / INSTALL_DIR).mkdir(exist_ok=True)
    (root / PROBES_DIR).mkdir(exist_ok=True)
    meta = {
        "id": lab_id,
        "title": (title or lab_id).strip()[:120],
        "created_at": _now_iso(),
        "source": (source or "").strip()[:500],
        "source_kind": (source_kind or "").strip()[:40],
        "playable": "",
        "vm": {
            "backend": "filesystem",
            "status": "stopped",
            "port": 0,
            "container": "",
            "url": "",
        },
    }
    _write_meta(lab_id, meta)
    return load_lab(lab_id) or meta


def list_labs() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    root = labs_root()
    if not root.is_dir():
        return out
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        meta = load_lab(child.name)
        if meta:
            live = _HTTP.get(child.name)
            if live:
                meta = dict(meta)
                vm = dict(meta.get("vm") or {})
                vm["status"] = "running"
                vm["backend"] = vm.get("backend") or "local-http"
                vm["port"] = live.get("port") or vm.get("port")
                vm["url"] = f"http://127.0.0.1:{live['port']}/"
                meta["vm"] = vm
            out.append(meta)
    return out


def destroy_lab(lab_id: str) -> dict[str, Any]:
    stop_vm(lab_id)
    root = lab_dir(lab_id)
    if not root.exists():
        return {"ok": False, "error": "lab not found"}
    shutil.rmtree(root, ignore_errors=True)
    return {"ok": True, "id": lab_id, "destroyed": True}


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def allowed_install_src(src: Path) -> bool:
    """Owner may install from data/, playground, product tmp labs, or /tmp."""
    src = src.resolve()
    import tempfile
    roots = [
        data_root(),
        playground_projects_dir(),
        Path(tempfile.gettempdir()),
        Path("/tmp"),
        Path("/var/tmp"),
    ]
    extra = os.environ.get("DAEDALUS_LAB_INSTALL_ROOT", "").strip()
    if extra:
        roots.append(Path(extra))
    try:
        from .paths import product_root
        roots.append(product_root())
    except Exception:
        pass
    return any(_is_under(src, r) or src == r.resolve() for r in roots)


def _tree_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    skip = {".git", "node_modules", "__pycache__", ".venv"}
    for p in path.rglob("*"):
        if p.is_file() and not any(part in skip for part in p.parts):
            try:
                total += p.stat().st_size
            except OSError:
                pass
            if total > _MAX_INSTALL_BYTES:
                return total
    return total


def _looks_native_installer(path: Path) -> bool:
    suffix = path.suffix.lower()
    if suffix in _NATIVE_SUFFIXES:
        return True
    if path.is_dir() and (path / "Contents" / "MacOS").is_dir():
        return True
    return False


def _pick_playable(install: Path) -> str:
    for name in ("index.html", "index.htm", "game.html", "play.html"):
        if (install / name).is_file():
            return name
    html = sorted(install.rglob("*.html"))
    if html:
        try:
            return str(html[0].relative_to(install))
        except ValueError:
            return html[0].name
    if (install / "project.godot").is_file():
        return "project.godot"
    if (install / "package.json").is_file():
        return "package.json"
    return ""


def install_into_lab(lab_id: str, src: str | Path) -> dict[str, Any]:
    """Copy a folder, HTML file, or zip into the lab install tree."""
    meta = load_lab(lab_id)
    if not meta:
        raise ValueError("lab not found")
    path = Path(src).expanduser()
    if not path.exists():
        raise ValueError(f"install source not found: {src}")
    if not allowed_install_src(path):
        raise ValueError(
            "install path must sit under DAEDALUS_DATA, playground, or /tmp "
            "(set DAEDALUS_LAB_INSTALL_ROOT to allow another root)"
        )
    if _looks_native_installer(path):
        raise ValueError(
            "native installer refused — Play Lab hosts HTML5, git checkouts, "
            "and extracted zips, not .exe/.msi/.dmg. Unpack the game first."
        )
    dest = install_dir(lab_id)
    dest.mkdir(parents=True, exist_ok=True)
    size = _tree_size(path)
    if size > _MAX_INSTALL_BYTES:
        raise ValueError(f"install too large ({size} bytes, cap {_MAX_INSTALL_BYTES})")

    if path.is_file() and path.suffix.lower() == ".zip":
        _extract_zip(path, dest)
    elif path.is_file() and path.suffix.lower() in {".html", ".htm"}:
        shutil.copy2(path, dest / path.name)
        if path.name.lower() not in {"index.html", "index.htm"}:
            # keep original name; playable picker still finds *.html
            pass
    elif path.is_dir():
        for child in path.iterdir():
            target = dest / child.name
            if child.is_dir():
                if target.exists():
                    shutil.rmtree(target)
                shutil.copytree(child, target, dirs_exist_ok=True)
            else:
                shutil.copy2(child, target)
    else:
        shutil.copy2(path, dest / path.name)

    playable = _pick_playable(dest)
    meta["playable"] = playable
    meta["source"] = meta.get("source") or str(path)
    meta["source_kind"] = meta.get("source_kind") or "install"
    _write_meta(lab_id, meta)
    return {
        "ok": True,
        "lab_id": lab_id,
        "playable": playable,
        "files": _list_install_files(dest)[:40],
    }


def _list_install_files(install: Path) -> list[str]:
    out: list[str] = []
    skip = {".git", "node_modules", "__pycache__"}
    for p in sorted(install.rglob("*")):
        if not p.is_file() or any(part in skip for part in p.parts):
            continue
        try:
            out.append(str(p.relative_to(install)))
        except ValueError:
            out.append(p.name)
        if len(out) >= 80:
            break
    return out


def _extract_zip(zip_path: Path, dest: Path) -> None:
    dest = dest.resolve()
    with zipfile.ZipFile(zip_path) as zf:
        total = 0
        for info in zf.infolist():
            name = info.filename.replace("\\", "/")
            if name.endswith("/") or not name:
                continue
            if name.startswith("/") or ".." in Path(name).parts:
                raise ValueError(f"zip slip blocked: {name}")
            target = (dest / name).resolve()
            if dest not in target.parents and target != dest:
                raise ValueError(f"zip slip blocked: {name}")
            total += info.file_size
            if total > _MAX_INSTALL_BYTES:
                raise ValueError("zip too large")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)


def write_install_file(lab_id: str, rel: str, content: str) -> None:
    dest = install_dir(lab_id)
    dest.mkdir(parents=True, exist_ok=True)
    target = (dest / rel).resolve()
    if dest.resolve() not in target.parents and target != dest.resolve():
        raise ValueError("path traversal blocked")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    meta = load_lab(lab_id) or {}
    meta["playable"] = _pick_playable(dest)
    _write_meta(lab_id, meta)


def save_meta(lab_id: str, data: dict[str, Any]) -> None:
    _write_meta(lab_id, data)


def refresh_playable(lab_id: str) -> str:
    dest = install_dir(lab_id)
    playable = _pick_playable(dest)
    meta = load_lab(lab_id) or {}
    meta["playable"] = playable
    _write_meta(lab_id, meta)
    return playable


def list_install_files(lab_id: str) -> list[str]:
    return _list_install_files(install_dir(lab_id))


def save_lab_specimen(lab_id: str, data: dict[str, Any]) -> None:
    path = lab_dir(lab_id) / SPECIMEN_REL
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_lab_specimen(lab_id: str) -> dict[str, Any] | None:
    path = lab_dir(lab_id) / SPECIMEN_REL
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def start_local_http(lab_id: str) -> dict[str, Any]:
    """Serve ``install/`` on 127.0.0.1. Reuses an already-running server."""
    meta = load_lab(lab_id)
    if not meta:
        raise ValueError("lab not found")
    existing = _HTTP.get(lab_id)
    if existing and existing.get("port"):
        url = f"http://127.0.0.1:{existing['port']}/"
        playable = meta.get("playable") or ""
        if playable and playable.endswith((".html", ".htm")):
            url = f"http://127.0.0.1:{existing['port']}/{playable}"
        return {"ok": True, "backend": "local-http", "url": url, "port": existing["port"], "reused": True}

    install = install_dir(lab_id)
    install.mkdir(parents=True, exist_ok=True)
    directory = str(install)

    class _Handler(_QuietHandler):
        def __init__(self, *args: Any, **kwargs: Any):
            super().__init__(*args, directory=directory, **kwargs)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    httpd.daemon_threads = True
    port = int(httpd.server_address[1])
    thread = threading.Thread(target=httpd.serve_forever, name=f"daedalus-lab-{lab_id}", daemon=True)
    thread.start()
    _HTTP[lab_id] = {"httpd": httpd, "thread": thread, "port": port}
    playable = meta.get("playable") or _pick_playable(install)
    url = f"http://127.0.0.1:{port}/"
    if playable and str(playable).endswith((".html", ".htm")):
        url = f"http://127.0.0.1:{port}/{playable}"
    vm = dict(meta.get("vm") or {})
    vm.update({"backend": "local-http", "status": "running", "port": port, "url": url, "container": ""})
    meta["vm"] = vm
    meta["playable"] = playable
    _write_meta(lab_id, meta)
    return {"ok": True, "backend": "local-http", "url": url, "port": port, "lab_id": lab_id}


def stop_local_http(lab_id: str) -> None:
    row = _HTTP.pop(lab_id, None)
    if not row:
        return
    httpd = row.get("httpd")
    try:
        if httpd:
            httpd.shutdown()
            httpd.server_close()
    except Exception as exc:
        log.debug("lab http stop %s: %s", lab_id, exc)


def stop_all_http() -> None:
    for lab_id in list(_HTTP):
        stop_local_http(lab_id)


def start_docker_vm(lab_id: str) -> dict[str, Any]:
    """Run an optional Docker HTTP VM. Never pulls unless DAEDALUS_LAB_PULL=1."""
    meta = load_lab(lab_id)
    if not meta:
        raise ValueError("lab not found")
    if _env_flag("DAEDALUS_LAB_DOCKER") is False and os.environ.get("DAEDALUS_LAB_DOCKER", "").strip() in (
        "0", "false", "no", "off",
    ):
        raise ValueError("docker lab disabled")
    host = probe_host()
    docker = host.get("docker") or {}
    if not docker.get("ready"):
        raise ValueError("docker not on PATH")
    image = str(docker.get("image") or DOCKER_IMAGE_DEFAULT)
    if not docker.get("image_present"):
        if docker.get("pull_opt_in"):
            pull = subprocess.run(
                ["docker", "pull", image],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            if pull.returncode != 0:
                raise ValueError(f"docker pull failed: {(pull.stderr or pull.stdout or '')[:300]}")
        else:
            raise ValueError(
                f"docker image {image} is not local — set DAEDALUS_LAB_PULL=1 to pull, "
                "or use the local HTTP lab"
            )
    install = install_dir(lab_id)
    install.mkdir(parents=True, exist_ok=True)
    name = f"daedalus-{lab_id}"[:60]
    subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=20, check=False)
    port = _free_port()
    args = [
        "docker", "run", "-d", "--name", name,
        "-p", f"127.0.0.1:{port}:8080",
        "-v", f"{install}:/lab:ro",
        image,
        "python", "-m", "http.server", "8080", "--directory", "/lab",
    ]
    proc = subprocess.run(args, capture_output=True, text=True, timeout=40, check=False)
    if proc.returncode != 0:
        raise ValueError((proc.stderr or proc.stdout or "docker run failed")[:400])
    container = (proc.stdout or "").strip()[:64]
    playable = meta.get("playable") or _pick_playable(install)
    url = f"http://127.0.0.1:{port}/"
    if playable and str(playable).endswith((".html", ".htm")):
        url = f"http://127.0.0.1:{port}/{playable}"
    vm = {
        "backend": "docker",
        "status": "running",
        "port": port,
        "url": url,
        "container": container or name,
        "name": name,
    }
    meta["vm"] = vm
    _write_meta(lab_id, meta)
    return {"ok": True, **vm, "lab_id": lab_id}


def stop_docker_vm(lab_id: str) -> None:
    meta = load_lab(lab_id) or {}
    vm = meta.get("vm") or {}
    name = vm.get("name") or f"daedalus-{lab_id}"[:60]
    if shutil.which("docker"):
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=20, check=False)


def start_vm(lab_id: str, *, prefer_docker: bool = True) -> dict[str, Any]:
    """Boot the lab. Docker if ready; otherwise local HTTP."""
    if prefer_docker:
        try:
            host = probe_host()
            docker = host.get("docker") or {}
            if docker.get("ready") and docker.get("image_present"):
                return start_docker_vm(lab_id)
        except Exception as exc:
            log.info("docker lab fallback to local-http: %s", exc)
    return start_local_http(lab_id)


def stop_vm(lab_id: str) -> dict[str, Any]:
    stop_local_http(lab_id)
    try:
        stop_docker_vm(lab_id)
    except Exception as exc:
        log.debug("docker stop %s: %s", lab_id, exc)
    meta = load_lab(lab_id)
    if meta:
        vm = dict(meta.get("vm") or {})
        vm["status"] = "stopped"
        vm["url"] = ""
        meta["vm"] = vm
        _write_meta(lab_id, meta)
    return {"ok": True, "lab_id": lab_id, "status": "stopped"}


def serve_url(lab_id: str) -> str | None:
    live = _HTTP.get(lab_id)
    if live and live.get("port"):
        meta = load_lab(lab_id) or {}
        playable = meta.get("playable") or ""
        if playable and str(playable).endswith((".html", ".htm")):
            return f"http://127.0.0.1:{live['port']}/{playable}"
        return f"http://127.0.0.1:{live['port']}/"
    meta = load_lab(lab_id) or {}
    vm = meta.get("vm") or {}
    if vm.get("status") == "running" and vm.get("url"):
        return str(vm["url"])
    return None
