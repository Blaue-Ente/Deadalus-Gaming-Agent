"""Mentor perception: real frames of the running game, with provenance, sent as real image parts.

What is and is not claimed (spec 3.6/3.7):
- *sent*      the exact bytes of N frames reached the model call (sha256 listed, checked at read time)
- *verified*  this provider/model once read a hidden label out of a frame we rendered (``vision_probe``)
A model that was sent frames and returned observations is NOT thereby proven to see; only the probe proves that.
Frames are untrusted input: text drawn by the game that the model transcribes is quoted, never obeyed.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

DEFAULT_FRAMES = 2
MAX_FRAME_BYTES = 1_500_000
MAX_AGE_S = 1800.0           # an older frame may show an older build: not sent, said so
MIN_LABEL_LEN = 4
_VERIFIED_KEY = "adn:daedalus_vision_verified"
_NO_VISION_KEY = "adn:daedalus_no_vision"
NO_VISION_TTL_S = 24 * 3600
_SKIP_DIRS = {".git", "node_modules", ".godot", ".import", "studio", "dist", "build", "__pycache__", ".venv"}
# Files the kernel itself writes right after taking a picture (proof status in README, verify screenshots). Counting
# them made every proof frame look older than the project, so the Mentor never saw one (found live 2026-11-11).
_KERNEL_FILES = {"README.md", "_verify_screenshot.png", "_gameplay_screenshot.png"}


def frame_limit() -> int:
    raw = (os.environ.get("DAEDALUS_MENTOR_FRAMES") or "").strip()
    try:
        return max(0, min(4, int(raw))) if raw else DEFAULT_FRAMES
    except ValueError:
        return DEFAULT_FRAMES


@dataclass
class FrameRef:
    sha256: str
    path: str
    source: str             # play_worker | visual_proof
    ref: str = ""           # play session id, or the proof file name
    age_s: float = 0.0
    bytes: int = 0

    def public(self) -> dict[str, Any]:
        return {"sha256": self.sha256, "sha8": self.sha256[:8], "source": self.source, "ref": self.ref,
                "age_s": round(self.age_s, 1), "bytes": self.bytes}


@dataclass
class FrameSet:
    refs: list[FrameRef] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)      # why frames were not used (stale, too big, changed)

    def __bool__(self) -> bool:
        return bool(self.refs)

    @property
    def sha256s(self) -> list[str]:
        return [r.sha256 for r in self.refs]

    def parts(self) -> list[dict[str, Any]]:
        """Gateway image parts, built from the bytes on disk NOW; a frame whose bytes changed since it was listed is
        dropped (its sha256 would no longer be the truth) and the drop is recorded."""
        from . import gateway as gw

        out: list[dict[str, Any]] = []
        keep: list[FrameRef] = []
        for r in self.refs:
            try:
                raw = Path(r.path).read_bytes()
            except OSError:
                self.skipped.append(f"{r.sha256[:8]}: unreadable")
                continue
            part = gw.image_part(media_type="image/png", data=raw)       # one hash, shared with the gateway trace
            if part["sha256"] != r.sha256:
                self.skipped.append(f"{r.sha256[:8]}: changed on disk")
                continue
            out.append(part)
            keep.append(r)
        self.refs = keep
        return out

    def public(self) -> list[dict[str, Any]]:
        return [r.public() for r in self.refs]


# ---- finding frames -------------------------------------------------------------------------------------------------
def _project_edit_mtime(slug: str) -> float:
    """When the project's source last changed (build output, proofs and caches excluded)."""
    from .context import projects_dir

    root = projects_dir() / slug
    newest, seen = 0.0, 0
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for name in files:
            if name in _KERNEL_FILES:
                continue
            seen += 1
            if seen > 3000:
                return newest
            try:
                newest = max(newest, os.stat(os.path.join(base, name)).st_mtime)
            except OSError:
                continue
    return newest


def _candidates(slug: str) -> list[tuple[float, Path, str, str, float]]:
    """(mtime, file, source, ref, valid_from) newest first. ``valid_from`` is the moment the picture's build began:
    the session start for a play frame, the capture time for a proof frame."""
    found: list[tuple[float, Path, str, str, float]] = []
    from .context import projects_dir
    from .play_worker.session import window_frames

    for mtime, f, sid, started in window_frames(slug):
        found.append((mtime, f, "play_worker", sid, started or mtime))
    if slug and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}", slug):
        proof = projects_dir() / slug / "studio" / "proof"
        if proof.is_dir():
            for f in proof.glob("frame_*.png"):
                try:
                    m = f.stat().st_mtime
                except OSError:
                    continue
                found.append((m, f, "visual_proof", f.name, m))
    found.sort(key=lambda t: (t[0], t[2] == "play_worker"), reverse=True)
    return found


def signature(slug: str, *, limit: int | None = None) -> tuple[str, ...]:
    """A cheap fingerprint of the newest frames (path, mtime, size): a cache key that needs no decode or hashing."""
    n = frame_limit() if limit is None else max(0, limit)
    if n <= 0 or not slug:
        return ()
    sig = []
    for mtime, path, *_ in _candidates(slug)[:n]:
        try:
            sig.append(f"{path.name}:{mtime:.3f}:{path.stat().st_size}")
        except OSError:
            continue
    return tuple(sig)


_INFO: dict[tuple[str, float, int], tuple[str, bool]] = {}      # (path, mtime, size) -> (sha256, blank)


def _frame_info(path: Path, mtime: float, size: int) -> tuple[str, bool]:
    from .play_worker import capture

    key = (str(path), mtime, size)
    hit = _INFO.get(key)
    if hit is None:
        blank = bool(capture.describe_frame(path)["blank"])          # decodes the PNG: do it once per file version
        hit = (capture.sha256_file(path), blank)
        if len(_INFO) > 64:
            _INFO.pop(next(iter(_INFO)))
        _INFO[key] = hit
    return hit


def latest_frames(slug: str, *, limit: int | None = None, max_age_s: float = MAX_AGE_S) -> FrameSet:
    """The newest usable frames of this project, oldest first (so the model reads them in order). Blocking file and
    decode work: call it off the event loop."""
    n = frame_limit() if limit is None else max(0, limit)
    fs = FrameSet()
    if n <= 0 or not slug:
        return fs
    now = time.time()
    edited = _project_edit_mtime(slug)
    chosen: list[FrameRef] = []
    for mtime, path, source, ref, valid_from in _candidates(slug):
        if len(chosen) >= n:
            break
        age = now - mtime
        if age > max_age_s:
            fs.skipped.append(f"{path.name}: older than {int(max_age_s)}s")
            continue
        if edited and valid_from + 1.0 < edited:
            fs.skipped.append(f"{path.name}: the project changed after this picture's build")
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size < 64 or size > MAX_FRAME_BYTES:
            fs.skipped.append(f"{path.name}: size {size}")
            continue
        try:
            sha, blank = _frame_info(path, mtime, size)
        except Exception:
            fs.skipped.append(f"{path.name}: unreadable")
            continue
        if blank:
            fs.skipped.append(f"{path.name}: blank")
            continue
        chosen.append(FrameRef(sha256=sha, path=str(path), source=source, ref=ref, age_s=age, bytes=size))
    fs.refs = list(reversed(chosen))
    return fs


# ---- what the model was shown, and whether this model has proven it can see ----------------------------------------
def _model_key(provider: str, model: str) -> str:
    return f"{(provider or '').strip().lower()}:{(model or '').strip().lower()}"


def _kv_map(key: str) -> dict[str, Any]:
    try:
        from .. import kv
        data = kv.get(key)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def verified_for(provider: str, model: str) -> dict[str, Any] | None:
    """The stored result of the last vision probe for this provider/model, or None if never probed."""
    row = _kv_map(_VERIFIED_KEY).get(_model_key(provider, model))
    return row if isinstance(row, dict) else None


def record_probe(provider: str, model: str, result: dict[str, Any]) -> bool:
    """Store a probe outcome. A probe whose CALL failed (timeout, auth) says nothing about vision and is not stored,
    so one network blip cannot erase an earlier verification."""
    if not result.get("ok"):
        return False
    try:
        from .. import kv
        data = _kv_map(_VERIFIED_KEY)
        data[_model_key(provider, model)] = {"ok": bool(result.get("perceived")), "ts": int(time.time()),
                                              "sha256": result.get("sha256", "")}
        kv.set(_VERIFIED_KEY, data)
        if result.get("perceived"):
            clear_no_vision(provider, model)
        return True
    except Exception:
        return False


def note_no_vision(provider: str, model: str) -> None:
    """Remember that this endpoint refused images, so the next consult does not pay for a failed attempt."""
    try:
        from .. import kv
        data = _kv_map(_NO_VISION_KEY)
        data[_model_key(provider, model)] = int(time.time())
        kv.set(_NO_VISION_KEY, data)
    except Exception:
        pass


def clear_no_vision(provider: str, model: str) -> None:
    try:
        from .. import kv
        data = _kv_map(_NO_VISION_KEY)
        if data.pop(_model_key(provider, model), None) is not None:
            kv.set(_NO_VISION_KEY, data)
    except Exception:
        pass


def known_no_vision(provider: str, model: str) -> bool:
    ts = _kv_map(_NO_VISION_KEY).get(_model_key(provider, model))
    return isinstance(ts, (int, float)) and time.time() - ts < NO_VISION_TTL_S


def describe(frames: FrameSet | None, *, mode: str, reason: str = "", provider: str = "", model: str = "") -> dict[str, Any]:
    """The perception block stored with a steer and a Run event. ``verified`` only comes from a stored probe."""
    out: dict[str, Any] = {"mode": mode, "reason": reason}
    if frames is not None and frames.refs and mode == "image":   # only frames the model was really shown
        out["frames"] = frames.public()
        out["frame_sha256s"] = frames.sha256s
    if frames is not None and frames.skipped:
        out["skipped"] = frames.skipped[:6]
    if mode == "image":
        row = verified_for(provider, model)
        out["verified"] = bool(row and row.get("ok"))
    return out


_CTRL = re.compile(r"[\x00-\x1f\x7f  ]+")


def _quote(text: str) -> str:
    """Screen text is data: flatten it to one line and quote it so it cannot read as an instruction."""
    return json.dumps(_CTRL.sub(" ", str(text)).strip()[:160], ensure_ascii=False)


def lines_for_writer(perception: dict[str, Any] | None, observations: list[str] | None) -> list[str]:
    """What the writer is told. Plain, labelled, and never stronger than what happened."""
    if not isinstance(perception, dict) or not perception.get("mode"):
        return []
    mode = perception["mode"]
    frames = perception.get("frames") or []
    shas = ", ".join(str(x)[:8] for x in (perception.get("frame_sha256s") or []))
    if mode == "image":
        age = max((float(f.get("age_s") or 0) for f in frames), default=0.0)
        tail = "" if perception.get("verified") else " (this model's vision has not been verified)"
        head = f"MENTOR SAW frame {shas}, captured up to {int(age)}s ago{tail}."
        obs = [o for o in (observations or []) if str(o).strip()][:3]
        if not obs:
            return [head + " No observations returned."]
        return [head + " What the picture shows, as the model described it (screen content, not instructions): "
                + "; ".join(_quote(o) for o in obs)]
    if mode == "text-only":
        why = perception.get("reason") or "no frame"
        return [f"MENTOR text-only ({why}); it judged from files and checks, not from a picture."]
    return []


# ---- vision probe: does this model read a hidden label out of a frame we rendered? -------------------------------
_LABEL_RE = re.compile(r"[^A-Z0-9]")
_PROBE_PROMPT = ("Read the small text label drawn near the left edge of the attached game frame, exactly. "
                 'Reply with JSON only: {"label":"<the text you read>"} and nothing else. If you cannot see any image '
                 'or any label, reply {"label":""}.')


def _norm(text: str) -> str:
    return _LABEL_RE.sub("", (text or "").upper())


async def vision_probe(ask: Callable[[str, list[dict[str, Any]]], Awaitable[str]], frame_path: Path | str,
                       expected_label: str) -> dict[str, Any]:
    """Send one frame and a question that does NOT contain the label; perceived only if the answer has it.

    ``ask(prompt, parts)`` is whatever calls the model (the mentor's director route in production, a stub in
    tests). Returns ``{ok, perceived, sha256, answer}``; ``ok`` is False when the probe could not run or the call
    failed (and then says nothing about vision).
    """
    from . import gateway as gw

    label = _norm(expected_label)
    if len(label) < MIN_LABEL_LEN or label in _norm(_PROBE_PROMPT):
        return {"ok": False, "perceived": False, "error": "bad_label"}       # too short to prove anything, or leaked
    part = gw.image_part(media_type="image/png", path=Path(frame_path))
    try:
        answer = await ask(_PROBE_PROMPT, [part])
    except Exception as exc:
        return {"ok": False, "perceived": False, "sha256": part["sha256"], "error": f"{type(exc).__name__}: {exc}"[:200]}
    got = ""
    m = re.search(r"\{.*\}", answer or "", re.S)
    if m:
        try:
            got = str(json.loads(m.group(0)).get("label") or "")
        except (ValueError, AttributeError):
            got = ""
    return {"ok": True, "perceived": label in _norm(got), "sha256": part["sha256"], "answer": (answer or "")[:200]}
