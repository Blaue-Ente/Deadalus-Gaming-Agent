"""Image providers, checked live 2026-11-12: Novita's old SD route is gone (404), Seedream 4.0 works (~$0.03);
OpenRouter has newer Gemini images; every sprite gets a transparent background, free and local."""
from __future__ import annotations

import asyncio
import io
import types

import pytest
from a0_support import a0_tmp  # noqa: F401

from daedalus import image_client, sprite_alpha

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402


def _keys(monkeypatch, **present):
    for name in ("NOVITA_API_KEY", "OPENROUTER_API_KEY", "ALLTOKEN_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    for name, val in present.items():
        monkeypatch.setenv(name, val)


def test_old_model_names_reach_the_routes_that_exist(a0_tmp, monkeypatch):  # noqa: F811
    _keys(monkeypatch, NOVITA_API_KEY="k")
    assert image_client.resolve_image_model("sdxl") == "seedream"
    assert image_client.resolve_image_model("animated") == "seedream"
    assert image_client.resolve_image_model("") == "seedream"
    _keys(monkeypatch, OPENROUTER_API_KEY="k")
    assert image_client.resolve_image_model("") == "nano-banana-2"
    assert image_client._OR_MODELS["nano-banana-2"] == "google/gemini-3.1-flash-image"
    assert set(image_client.image_models()) == {"novita", "openrouter"}


class _Http:
    calls: list = []
    reply = {"images": ["https://img.example/1.jpg"]}
    status = 200

    def __init__(self, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None):
        _Http.calls.append({"url": url, "json": json, "auth": headers.get("Authorization")})
        return types.SimpleNamespace(status_code=_Http.status, text="upstream request failed",
                                     json=lambda: _Http.reply)


@pytest.fixture
def http(monkeypatch):
    _Http.calls, _Http.status, _Http.reply = [], 200, {"images": ["https://img.example/1.jpg"]}
    monkeypatch.setattr(image_client.httpx, "AsyncClient", _Http)
    return _Http


def test_novita_seedream_is_asked_at_its_minimum_size(a0_tmp, monkeypatch, http):  # noqa: F811
    _keys(monkeypatch, NOVITA_API_KEY="nov")
    out = asyncio.run(image_client.generate_image(prompt="frog", size="256x256"))
    assert out["provider"] == "novita" and out["model"] == "seedream-4.0"
    assert out["images"][0]["url"] == "https://img.example/1.jpg"
    call = http.calls[0]
    assert call["url"] == image_client.NOVITA_SEEDREAM_URL and call["json"]["size"] == "1024x1024"
    assert call["json"]["watermark"] is False and call["auth"] == "Bearer nov"


def test_flux_schnell_payload(a0_tmp, monkeypatch, http):  # noqa: F811
    _keys(monkeypatch, NOVITA_API_KEY="nov")
    http.reply = {"images": [{"image_url": "https://img.example/f.png"}]}
    out = asyncio.run(image_client.generate_image(prompt="frog", model="flux-schnell", size="512x512"))
    assert out["images"][0]["url"] == "https://img.example/f.png"
    assert http.calls[0]["url"] == image_client.NOVITA_FLUX_URL
    assert {"width": 512, "height": 512, "steps": 4, "image_num": 1}.items() <= http.calls[0]["json"].items()


def test_a_novita_failure_says_who_served_instead(a0_tmp, monkeypatch, http):  # noqa: F811
    _keys(monkeypatch, NOVITA_API_KEY="nov", OPENROUTER_API_KEY="or")
    http.status = 502

    async def openrouter(**kw):
        return {"provider": "openrouter", "model": kw["model"], "images": [{"b64": "eA=="}]}

    monkeypatch.setattr(image_client, "_openrouter_image", openrouter)
    out = asyncio.run(image_client.generate_image(prompt="frog"))
    assert out["provider"] == "openrouter" and out["model"] == "nano-banana-2"
    assert "502" in out["fallback_from"] and "seedream" in out["fallback_from"]


def test_a_novita_failure_without_a_fallback_is_an_error_with_the_reason(a0_tmp, monkeypatch, http):  # noqa: F811
    _keys(monkeypatch, NOVITA_API_KEY="nov")
    http.status = 502
    with pytest.raises(RuntimeError, match="HTTP 502 upstream request failed"):
        asyncio.run(image_client.generate_image(prompt="frog"))


# ── transparent sprites ──────────────────────────────────────────────────────────────────────────────────────────
def _png(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _sprite_on_white() -> bytes:
    im = Image.new("RGB", (400, 300), (252, 253, 251))
    for x in range(150, 250):                        # a round sprite: the corners of its box are background
        for y in range(100, 200):
            if (x - 199.5) ** 2 + (y - 149.5) ** 2 <= 50 ** 2:
                im.putpixel((x, y), (20, 160, 40))
    for x in range(190, 210):                        # a white eye inside the sprite stays white
        for y in range(130, 150):
            im.putpixel((x, y), (255, 255, 255))
    return _png(im)


def test_a_sprite_loses_its_background_but_not_its_white_parts():
    out, rep = sprite_alpha.prepare_sprite(_sprite_on_white(), size=(64, 64))
    im = Image.open(io.BytesIO(out))
    assert rep["alpha"] and im.mode == "RGBA" and rep["cropped_to"] == [100, 100] and im.size == (64, 64)
    assert im.getpixel((0, 0))[3] == 0                           # border transparent
    eye = im.getpixel((int(50 * 0.64), int(40 * 0.64)))
    assert eye[3] == 255 and eye[:3] == (255, 255, 255)          # the eye is not the background


def test_a_scene_is_kept_as_it_is():
    im = Image.new("RGB", (200, 100))
    for x in range(200):
        for y in range(100):
            im.putpixel((x, y), (x, y * 2, 120))                  # a gradient: no single border colour
    data = _png(im)
    out, rep = sprite_alpha.prepare_sprite(data)
    assert rep["alpha"] is False and "scene" in rep["note"]


def test_without_pillow_the_image_is_unchanged(monkeypatch):
    import builtins

    real = builtins.__import__

    def no_pil(name, *a, **k):
        if name == "PIL" or name.startswith("PIL."):
            raise ImportError("no PIL")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_pil)
    data = _sprite_on_white()
    out, rep = sprite_alpha.prepare_sprite(data)
    assert out == data and "Pillow is not installed" in rep["note"]


@pytest.mark.parametrize("name,ok", [("hero.png", True), ("sprites/frog.png", True), ("enemy_bat.png", True),
                                     ("bg.png", False), ("background-forest.png", False), ("sky.png", False),
                                     ("tiles/grass.png", False), ("level1_bg.jpg", False), ("title.png", False)])
def test_only_sprites_are_cut_out(name, ok):
    assert sprite_alpha.wants_alpha(name) is ok


def test_the_image_tool_saves_a_transparent_png(a0_tmp, monkeypatch):  # noqa: F811
    import base64

    from daedalus import native_tools, paid_assets
    from daedalus import playground as pg

    slug = asyncio.run(pg.create_project("Frog", kind="static"))["slug"]

    async def fake(**kw):
        return {"provider": "novita", "model": "seedream-4.0",
                "images": [{"b64": base64.b64encode(_sprite_on_white()).decode()}]}

    monkeypatch.setattr("daedalus.image_client.generate_image", fake)
    tok = paid_assets._TURN_WORDS.set("AI art for the hero please")
    try:
        out = asyncio.run(native_tools.execute_native_tool(
            "generate_image", {"prompt": "frog", "slug": slug, "filename": "hero.jpg", "size": "128x128"},
            memory=None, session_id="s", read_file_fn=None))
    finally:
        paid_assets._TURN_WORDS.reset(tok)
    assert "generate_image OK" in out and "transparent background" in out
    saved = pg.project_dir(slug) / "assets" / "hero.png"
    assert saved.is_file() and not (pg.project_dir(slug) / "assets" / "hero.jpg").exists()
    assert Image.open(saved).mode == "RGBA"
