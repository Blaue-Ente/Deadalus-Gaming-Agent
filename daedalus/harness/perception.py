"""Mentor perception: real frames of the running game, with provenance, sent as real image parts.

What is and is not claimed (spec 3.6/3.7):
- *sent*      the exact bytes of N frames reached the model call (sha256 listed, checked at read time)
- *verified*  this provider/model once read a hidden label out of a frame we rendered (``vision_probe``)
A model that was sent frames and returned observations is NOT thereby proven to see; only the probe proves that.
"""
from __future__ import annotations

import hashlib
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
_VERIFIED_KEY = "adn:daedalus_vision_verified"


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
            if hashlib.sha256(raw).hexdigest() != r.sha256:
                self.skipped.append(f"{r.sha256[:8]}: changed on disk")
                continue
            out.append(gw.image_part(media_type="image/png", data=raw))
            keep.append(r)
        self.refs = keep
        return out

    def public(self) -> list[dict[str, Any]]:
        return [r.public() for r in self.refs]


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _candidates(slug: str) -> list[tuple[float, Path, str, str]]:
    """(mtime, file, source, ref) newest first; play-worker frames rank above visual-proof ones of the same age."""
    found: list[tuple[float, Path, str, str]] = []
    from .context import data_dir, projects_dir

    play = data_dir() / "play"
    if play.is_dir():
        for sess in play.iterdir():
            meta = sess / "session.json"
            try:
                info = json.loads(meta.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if slug and info.get("slug") != slug:
                continue
            for f in sess.glob("frame_*_window.png"):
                found.append((f.stat().st_mtime, f, "play_worker", sess.name))
    if slug and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}", slug):
        proof = projects_dir() / slug / "studio" / "proof"
        if proof.is_dir():
            for f in proof.glob("frame_*.png"):
                found.append((f.stat().st_mtime, f, "visual_proof", f.name))
    found.sort(key=lambda t: (t[0], t[2] == "play_worker"), reverse=True)
    return found


def latest_frames(slug: str, *, limit: int | None = None, max_age_s: float = MAX_AGE_S) -> FrameSet:
    """The newest usable frames of this project, oldest first (so the model reads them in order)."""
    n = frame_limit() if limit is None else max(0, limit)
    fs = FrameSet()
    if n <= 0 or not slug:
        return fs
    from ..harness.play_worker import capture as _cap

    now = time.time()
    chosen: list[FrameRef] = []
    for mtime, path, source, ref in _candidates(slug):
        if len(chosen) >= n:
            break
        age = now - mtime
        if age > max_age_s:
            fs.skipped.append(f"{path.name}: older than {int(max_age_s)}s")
            continue
        size = path.stat().st_size
        if size < 64 or size > MAX_FRAME_BYTES:
            fs.skipped.append(f"{path.name}: size {size}")
            continue
        try:
            if _cap.describe_frame(path)["blank"]:
                fs.skipped.append(f"{path.name}: blank")
                continue
        except Exception:
            fs.skipped.append(f"{path.name}: unreadable")
            continue
        chosen.append(FrameRef(sha256=_sha(path), path=str(path), source=source, ref=ref, age_s=age, bytes=size))
    fs.refs = list(reversed(chosen))
    return fs


# ---- what the model was shown, and whether this model has proven it can see ---------------------------------------
def _model_key(provider: str, model: str) -> str:
    return f"{(provider or '').strip().lower()}:{(model or '').strip().lower()}"


def verified_for(provider: str, model: str) -> dict[str, Any] | None:
    """The stored result of the last vision probe for this provider/model, or None if never probed."""
    try:
        from .. import kv
        row = (kv.get(_VERIFIED_KEY) or {}).get(_model_key(provider, model))
        return row if isinstance(row, dict) else None
    except Exception:
        return None


def record_probe(provider: str, model: str, result: dict[str, Any]) -> None:
    try:
        from .. import kv
        data = kv.get(_VERIFIED_KEY)
        data = data if isinstance(data, dict) else {}
        data[_model_key(provider, model)] = {"ok": bool(result.get("perceived")), "ts": int(time.time()),
                                              "sha256": result.get("sha256", "")}
        kv.set(_VERIFIED_KEY, data)
    except Exception:
        pass


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


def lines_for_writer(perception: dict[str, Any] | None, observations: list[str] | None) -> list[str]:
    """What the writer is told. Plain, labelled, and never stronger than what happened."""
    if not isinstance(perception, dict) or not perception.get("mode"):
        return []
    mode = perception["mode"]
    shas = ", ".join(str(x)[:8] for x in (perception.get("frame_sha256s") or []))
    if mode == "image":
        tail = "" if perception.get("verified") else " (this model's vision has not been verified)"
        head = f"MENTOR SAW frame {shas}{tail}:"
        obs = [str(o) for o in (observations or []) if o][:3]
        return [head + (" " + "; ".join(obs) if obs else " no observations returned.")]
    if mode == "text-only":
        why = perception.get("reason") or "no frame"
        return [f"MENTOR text-only ({why}); it judged from files and checks, not from a picture."]
    return []


# ---- vision probe: does this model read a hidden label out of a frame we rendered? -----------------------------------
_LABEL_RE = re.compile(r"[^A-Z0-9]")


def _norm(text: str) -> str:
    return _LABEL_RE.sub("", (text or "").upper())


async def vision_probe(ask: Callable[[str, list[dict[str, Any]]], Awaitable[str]], frame_path: Path | str,
                       expected_label: str) -> dict[str, Any]:
    """Send one frame and a question that does NOT contain the label; perceived only if the answer has it.

    ``ask(prompt, parts)`` is whatever calls the model (the mentor's director route in production, a stub in
    tests). Returns ``{ok, perceived, sha256, answer}``; ``ok`` is False when the call itself failed.
    """
    from . import gateway as gw

    path = Path(frame_path)
    part = gw.image_part(media_type="image/png", path=path)
    prompt = ("Read the small text label drawn near the left edge of the attached game frame, exactly. "
              'Reply with JSON only: {"label":"<the text you read>"} and nothing else. If you cannot see any image '
              'or any label, reply {"label":""}.')
    assert expected_label and expected_label not in prompt           # the question must not leak the answer
    try:
        answer = await ask(prompt, [part])
    except Exception as exc:
        return {"ok": False, "perceived": False, "sha256": part["sha256"], "error": f"{type(exc).__name__}: {exc}"[:200]}
    got = ""
    try:
        blob = json.loads(re.search(r"\{.*\}", answer or "", re.S).group(0))      # type: ignore[union-attr]
        got = str(blob.get("label") or "")
    except Exception:
        got = answer or ""
    perceived = bool(_norm(expected_label)) and _norm(expected_label) in _norm(got)
    return {"ok": True, "perceived": perceived, "sha256": part["sha256"], "answer": (answer or "")[:200]}
