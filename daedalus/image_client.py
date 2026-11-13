"""Standalone image generation (Novita / OpenRouter) without MeshRouter."""
from __future__ import annotations

import logging
import time
from typing import Any

import base64
import httpx

log = logging.getLogger(__name__)


def payload_b64(image: dict[str, Any] | None) -> str:
    """Return base64 payload from ``b64`` or a ``data:`` URL.

    OpenRouter image models return ``data:image/png;base64,…`` in ``url``.
    ``httpx.get`` on that string raises ``URL too long`` and the sprite never
    lands on disk — eight times in the 2026-09-08 Lumen Bloom live eval.
    """
    row = image or {}
    raw = str(row.get("b64") or "").strip()
    if raw:
        return raw
    url = str(row.get("url") or "").strip()
    if url.startswith("data:") and "," in url:
        return url.split(",", 1)[1]
    return ""


def decode_image_bytes(image: dict[str, Any] | None) -> bytes | None:
    blob = payload_b64(image)
    if not blob:
        return None
    try:
        return base64.b64decode(blob)
    except Exception:
        return None


# Novita, checked live 2026-11-12: the old Stable Diffusion route (/v3/async/txt2img) answers 404 "route not
# found"; Seedream 4.0 answers in ~6 s for about $0.03 an image (measured on the balance). FLUX.1 schnell's route
# exists but its upstream failed (502) at the time: it is offered, and a failure says so.
NOVITA_SEEDREAM_URL = "https://api.novita.ai/v3/seedream-4.0"
NOVITA_FLUX_URL = "https://api.novita.ai/v3beta/flux-1-schnell"
_NOVITA_MODELS = {"seedream": "seedream-4.0", "flux-schnell": "flux-1-schnell"}
_NOVITA_ALIASES = {"sdxl": "seedream", "realistic": "seedream", "animated": "seedream"}   # names older code still uses

# OpenRouter image models, checked live 2026-11-12 (the 3.x Gemini images are newer and cheaper than 2.5).
_OR_MODELS = {
    "nano-banana-2": "google/gemini-3.1-flash-image",
    "nano-banana-2-lite": "google/gemini-3.1-flash-lite-image",
    "gemini-flash-image": "google/gemini-2.5-flash-image",
    "gemini-pro-image": "google/gemini-3-pro-image",
    "gpt-image": "openai/gpt-5.4-image-2",
    "gpt5-image-mini": "openai/gpt-5-image-mini",
}
OR_DEFAULT = "nano-banana-2"


def image_models() -> dict[str, list[str]]:
    """The image models Daedalus can call, by provider."""
    return {"novita": sorted(_NOVITA_MODELS), "openrouter": sorted(_OR_MODELS)}


def resolve_image_model(preferred: str = "seedream") -> str:
    pref = (preferred or "").strip()
    pref = _NOVITA_ALIASES.get(pref, pref)
    if pref in _OR_MODELS or pref in _NOVITA_MODELS:
        return pref
    if _cred_env("NOVITA_API_KEY", "").strip():
        return "seedream"
    if _cred_env("OPENROUTER_API_KEY", "").strip() or _cred_env("ALLTOKEN_API_KEY", "").strip():
        return OR_DEFAULT
    return pref or "seedream"


def parse_size(size: str) -> tuple[int, int]:
    """'512x512' -> (512, 512); anything unreadable -> (1024, 1024)."""
    return _size(size)


def seedream_size(w: int, h: int) -> str:
    """Seedream renders at least 1024 on its shorter side: scale both sides by one factor (the aspect is kept)."""
    scale = max(1.0, 1024 / max(1, min(w, h)))
    w2, h2 = round(w * scale), round(h * scale)
    cap = 4096 / max(w2, h2)
    if cap < 1:
        w2, h2 = round(w2 * cap), round(h2 * cap)
    return f"{w2}x{h2}"


def _size(size: str) -> tuple[int, int]:
    try:
        w, h = (int(x) for x in size.lower().replace("×", "x").split("x"))
        return w, h
    except Exception:
        return 1024, 1024


async def generate_image(
    *,
    prompt: str,
    model: str = "seedream",
    size: str = "1024x1024",
    n: int = 1,
    client_id: str = "daedalus/img",
    poll_interval: float = 2.0,
    max_wait: float = 90.0,
    image_type: str = "png",
    **_ignored: Any,
) -> dict[str, Any]:
    """Return ``{images: [{url, b64, width, height}], provider, model}``. When the chosen Novita model fails (not a
    timeout) and OpenRouter can serve, the result says so (``fallback_from``): a switch is never silent.

    One image per call (``n`` is kept for old callers; Seedream and the OpenRouter models return one). The format is
    the provider's (often JPEG): sprite_alpha.finalize_image makes it the PNG and size the game asked for.
    ``poll_interval`` and ``image_type`` are accepted for old callers and unused (Novita no longer polls)."""
    t0 = time.perf_counter()
    resolved = resolve_image_model(model)
    if resolved in _OR_MODELS:
        return await _openrouter_image(prompt=prompt, model=resolved, size=size, t0=t0)
    if _cred_env("NOVITA_API_KEY", "").strip():
        try:
            return await _novita_image(prompt=prompt, model=resolved, size=size, max_wait=max_wait, t0=t0)
        except httpx.TimeoutException as exc:
            # Novita may still finish and bill: a second, paid image elsewhere would be paid twice (review 2026-11-12).
            raise RuntimeError(f"Novita {resolved} did not answer in time; it may still finish and be billed. "
                               "Not retried elsewhere.") from exc
        except Exception as exc:
            log.warning("Novita image (%s) failed: %s", resolved, exc)
            if _cred_env("OPENROUTER_API_KEY", "").strip() or _cred_env("ALLTOKEN_API_KEY", "").strip():
                out = await _openrouter_image(prompt=prompt, model=OR_DEFAULT, size=size, t0=t0)
                out["fallback_from"] = f"novita {resolved}: {str(exc)[:160]}"
                return out
            raise
    if _cred_env("OPENROUTER_API_KEY", "").strip() or _cred_env("ALLTOKEN_API_KEY", "").strip():
        return await _openrouter_image(prompt=prompt, model=OR_DEFAULT, size=size, t0=t0)
    raise RuntimeError(
        "No key for image generation. Set NOVITA_API_KEY or OPENROUTER_API_KEY."
    )


async def _novita_image(*, prompt: str, model: str, size: str, max_wait: float, t0: float) -> dict[str, Any]:
    """Novita's current image routes: both answer at once with image URLs (no task polling any more)."""
    w, h = _size(size)
    key = _cred_env("NOVITA_API_KEY").strip()
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if model == "flux-schnell":
        url = NOVITA_FLUX_URL
        payload: dict[str, Any] = {"prompt": prompt, "width": max(256, min(w, 2048)), "height": max(256, min(h, 2048)),
                                   "seed": -1, "steps": 4, "image_num": 1}
    else:
        url = NOVITA_SEEDREAM_URL
        # Seedream renders at least 1024 on the shorter side: asked larger with the same aspect, fitted by the
        # caller (sprite_alpha.finalize_image) to the size the game asked for.
        payload = {"prompt": prompt, "size": seedream_size(w, h), "watermark": False}
    async with httpx.AsyncClient(timeout=max(30.0, max_wait)) as c:
        resp = await c.post(url, headers=headers, json=payload)
    if resp.status_code != 200:
        raise RuntimeError(f"Novita {_NOVITA_MODELS.get(model, model)}: HTTP {resp.status_code} {resp.text[:200]}")
    data = resp.json()
    raw = data.get("images") or []
    images = []
    for item in raw:
        u = item if isinstance(item, str) else (item.get("image_url") or item.get("url") or "")
        if u:
            images.append({"url": u, "width": w, "height": h})
    if not images:
        raise RuntimeError(f"Novita {_NOVITA_MODELS.get(model, model)} returned no images: {str(data)[:200]}")
    return {
        "id": f"daedalus-img-{int(time.time() * 1000)}",
        "model": _NOVITA_MODELS.get(model, model),
        "provider": "novita",
        "images": images,
        "latency_ms": int((time.perf_counter() - t0) * 1000),
    }


async def _openrouter_image(*, prompt: str, model: str, size: str, t0: float) -> dict[str, Any]:
    or_model = _OR_MODELS.get(model, _OR_MODELS[OR_DEFAULT])
    key = (_cred_env("OPENROUTER_API_KEY") or _cred_env("ALLTOKEN_API_KEY") or "").strip()
    if _cred_env("OPENROUTER_API_KEY", "").strip():
        url = "https://openrouter.ai/api/v1/chat/completions"
        provider = "openrouter"
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://daedalus.local",
            "X-Title": "Daedalus Image",
        }
    else:
        url = "https://api.alltoken.ai/v1/chat/completions"
        provider = "alltoken"
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    payload = {
        "model": or_model,
        "messages": [{"role": "user", "content": prompt}],
        "modalities": ["text", "image"],
        "max_tokens": 2048,
    }
    w, h = _size(size)
    async with httpx.AsyncClient(timeout=120.0) as c:
        resp = await c.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        message = resp.json().get("choices", [{}])[0].get("message", {})
        raw_imgs = message.get("images") or []
        images = []
        for img in raw_imgs:
            u = img.get("image_url", {}).get("url", "") if isinstance(img, dict) else ""
            if u:
                b64 = payload_b64({"url": u})
                images.append({
                    "url": "" if b64 else u,
                    "b64": b64,
                    "width": w,
                    "height": h,
                })

        if not images:
            raise RuntimeError(
                f"Image model {or_model} returned no images. "
                f"{str(message.get('content', ''))[:200]}"
            )
    return {
        "id": f"daedalus-img-{int(time.time() * 1000)}",
        "model": model,
        "provider": provider,
        "images": images,
        "latency_ms": int((time.perf_counter() - t0) * 1000),
    }


from .credentials import env as _cred_env  # noqa: E402  (gap 3c: whose key; credentials imports nothing here)
