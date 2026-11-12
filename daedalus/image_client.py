"""Standalone image generation (Novita / OpenRouter) without MeshRouter."""
from __future__ import annotations

import asyncio
import logging
import os
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


_NOVITA_MODELS = {
    "sdxl": {"sd_name": "sd_xl_base_1.0.safetensors", "sampler": "DPM++ 2M Karras", "steps": 25, "guidance": 7.0},
    "realistic": {
        "sd_name": "epicrealism_naturalSinRC1VAE_106430.safetensors",
        "sampler": "DPM++ 2M Karras", "steps": 30, "guidance": 7.0,
    },
    "animated": {
        "sd_name": "revAnimated_v122.safetensors",
        "sampler": "DPM++ 2M Karras", "steps": 25, "guidance": 7.5,
    },
}

_OR_MODELS = {
    "gemini-flash-image": "google/gemini-2.5-flash-image",
    "gemini-pro-image": "google/gemini-3-pro-image-preview",
    "gpt5-image-mini": "openai/gpt-5-image-mini",
}


def resolve_image_model(preferred: str = "sdxl") -> str:
    pref = (preferred or "sdxl").strip()
    if pref in _OR_MODELS or pref in _NOVITA_MODELS:
        return pref
    if os.environ.get("NOVITA_API_KEY", "").strip():
        return "sdxl"
    if os.environ.get("OPENROUTER_API_KEY", "").strip() or os.environ.get("ALLTOKEN_API_KEY", "").strip():
        return "gemini-flash-image"
    return pref or "sdxl"


def _size(size: str) -> tuple[int, int]:
    try:
        w, h = (int(x) for x in size.lower().replace("×", "x").split("x"))
        return w, h
    except Exception:
        return 1024, 1024


async def generate_image(
    *,
    prompt: str,
    model: str = "sdxl",
    size: str = "1024x1024",
    n: int = 1,
    client_id: str = "daedalus/img",
    poll_interval: float = 2.0,
    max_wait: float = 90.0,
    image_type: str = "png",
    **_ignored: Any,
) -> dict[str, Any]:
    """Return ``{images: [{url, b64, width, height}], provider, model}``."""
    t0 = time.perf_counter()
    resolved = resolve_image_model(model)
    if resolved in _OR_MODELS:
        return await _openrouter_image(prompt=prompt, model=resolved, size=size, t0=t0)
    if os.environ.get("NOVITA_API_KEY", "").strip():
        try:
            return await _novita_txt2img(
                prompt=prompt, model=resolved, size=size, n=n,
                image_type=image_type, poll_interval=poll_interval,
                max_wait=max_wait, t0=t0,
            )
        except Exception as exc:
            log.warning("Novita image failed (%s); trying OpenRouter", exc)
            if os.environ.get("OPENROUTER_API_KEY", "").strip() or os.environ.get("ALLTOKEN_API_KEY", "").strip():
                return await _openrouter_image(prompt=prompt, model="gemini-flash-image", size=size, t0=t0)
            raise
    if os.environ.get("OPENROUTER_API_KEY", "").strip() or os.environ.get("ALLTOKEN_API_KEY", "").strip():
        return await _openrouter_image(prompt=prompt, model="gemini-flash-image", size=size, t0=t0)
    raise RuntimeError(
        "No key for image generation. Set NOVITA_API_KEY or OPENROUTER_API_KEY."
    )


async def _novita_txt2img(
    *,
    prompt: str,
    model: str,
    size: str,
    n: int,
    image_type: str,
    poll_interval: float,
    max_wait: float,
    t0: float,
) -> dict[str, Any]:
    cfg = _NOVITA_MODELS.get(model) or _NOVITA_MODELS["sdxl"]
    w, h = _size(size)
    key = os.environ["NOVITA_API_KEY"].strip()
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    img_type = image_type if image_type in ("jpeg", "png", "webp") else "png"
    payload = {
        "extra": {"response_image_type": img_type},
        "request": {
            "model_name": cfg["sd_name"],
            "prompt": prompt,
            "width": w,
            "height": h,
            "image_num": max(1, min(n, 4)),
            "steps": cfg["steps"],
            "guidance_scale": cfg["guidance"],
            "sampler_name": cfg["sampler"],
        },
    }
    async with httpx.AsyncClient(timeout=30.0) as c:
        resp = await c.post("https://api.novita.ai/v3/async/txt2img", headers=headers, json=payload)
        resp.raise_for_status()
        task_id = resp.json()["task_id"]
        deadline = time.perf_counter() + max_wait
        while time.perf_counter() < deadline:
            await asyncio.sleep(poll_interval)
            poll = await c.get(
                "https://api.novita.ai/v3/async/task-result",
                headers=headers, params={"task_id": task_id},
            )
            poll.raise_for_status()
            data = poll.json()
            status = data.get("task", {}).get("status", "")
            if status == "TASK_STATUS_SUCCEED":
                images = [
                    {"url": img.get("image_url", img.get("url", "")), "width": img.get("width", w), "height": img.get("height", h)}
                    for img in (data.get("images") or [])
                ]
                return {
                    "id": f"daedalus-img-{int(time.time() * 1000)}",
                    "model": model,
                    "provider": "novita",
                    "images": images,
                    "latency_ms": int((time.perf_counter() - t0) * 1000),
                }
            if status in ("TASK_STATUS_FAILED", "TASK_STATUS_CANCELED"):
                reason = data.get("task", {}).get("reason", "unknown")
                raise RuntimeError(f"Image task {status}: {reason}")
    raise RuntimeError(f"Image generation timeout after {max_wait}s")


async def _openrouter_image(*, prompt: str, model: str, size: str, t0: float) -> dict[str, Any]:
    or_model = _OR_MODELS.get(model, _OR_MODELS["gemini-flash-image"])
    key = (os.environ.get("OPENROUTER_API_KEY") or os.environ.get("ALLTOKEN_API_KEY") or "").strip()
    if os.environ.get("OPENROUTER_API_KEY", "").strip():
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
