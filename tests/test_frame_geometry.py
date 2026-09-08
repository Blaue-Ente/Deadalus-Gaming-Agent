"""Screenshot center must show a scene, not HUD on a flat clear-color."""
from __future__ import annotations

from pathlib import Path

from daedalus.frame_geometry import frame_shows_scene
from daedalus.png_codec import encode_rgba

ART = Path("/opt/cursor/artifacts/screenshots")


def _png(tmp_path: Path, name: str, width: int, height: int, pixel_at) -> Path:
    buf = bytearray(width * height * 4)
    for y in range(height):
        for x in range(width):
            r, g, b = pixel_at(x, y)
            i = (y * width + x) * 4
            buf[i : i + 4] = bytes((r, g, b, 255))
    dest = tmp_path / name
    dest.write_bytes(encode_rgba(width, height, bytes(buf)))
    return dest


def test_navy_clear_is_not_a_scene(tmp_path: Path):
    path = _png(tmp_path, "navy.png", 64, 64, lambda x, y: (10, 16, 32))
    assert frame_shows_scene(path) is False


def test_coloured_boxes_in_center_pass(tmp_path: Path):
    def px(x, y):
        if 20 <= x < 28 and 20 <= y < 28:
            return (220, 32, 32)
        if 36 <= x < 44 and 20 <= y < 28:
            return (32, 220, 48)
        if 28 <= x < 36 and 36 <= y < 44:
            return (240, 200, 32)
        return (18, 22, 40)

    path = _png(tmp_path, "boxes.png", 64, 64, px)
    assert frame_shows_scene(path) is True


def test_hud_on_edges_still_fails_if_center_is_flat(tmp_path: Path):
    def px(x, y):
        if y < 6 or y >= 58:
            return (240, 240, 240)
        return (8, 12, 28)

    path = _png(tmp_path, "hud.png", 64, 64, px)
    assert frame_shows_scene(path) is False


def test_live_pass2_hud_is_not_a_scene():
    path = ART / "slerp-lookat-pass2.png"
    if not path.is_file():
        return
    assert frame_shows_scene(path) is False


def test_live_dungeon_frame_shows_scene():
    path = ART / "dungeon-keys-proof.png"
    if not path.is_file():
        return
    assert frame_shows_scene(path) is True


def test_chromium_launch_enables_software_webgl():
    src = Path("daedalus/playground_browser.py").read_text(encoding="utf-8")
    assert "--disable-gpu" not in src
    assert "swiftshader" in src
    assert "empty_scene" in src
    assert "has_webgl" in src
