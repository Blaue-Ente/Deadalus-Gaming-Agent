"""Visual integrity helpers — PNG stats and entrypoint checks."""
from __future__ import annotations

from pathlib import Path

from daedalus.png_codec import encode_rgba
from daedalus.visual_integrity import (
    entrypoint_integrity_failures,
    png_color_stats,
    screenshot_is_suspect_blank,
)


def test_png_solid_black_is_suspect_blank(tmp_path: Path) -> None:
    w, h = 64, 48
    solid = bytes([0, 0, 0, 255]) * (w * h)
    path = tmp_path / "black.png"
    path.write_bytes(encode_rgba(w, h, solid))
    stats = png_color_stats(path)
    assert stats is not None
    assert screenshot_is_suspect_blank(stats)


def test_png_varied_scene_not_suspect_blank(tmp_path: Path) -> None:
    w, h = 32, 24
    px = bytearray(w * h * 4)
    for i in range(0, len(px), 4):
        px[i] = (i * 7) % 256
        px[i + 1] = (i * 13) % 256
        px[i + 2] = (i * 3) % 256
        px[i + 3] = 255
    path = tmp_path / "busy.png"
    path.write_bytes(encode_rgba(w, h, bytes(px)))
    stats = png_color_stats(path)
    assert stats is not None
    assert not screenshot_is_suspect_blank(stats)


def test_scaffold_left_in_place_when_play_still_seed(tmp_path: Path) -> None:
    (tmp_path / ".daedalus-writer-assist.json").write_text("{}", encoding="utf-8")
    (tmp_path / "src" / "scenes").mkdir(parents=True)
    (tmp_path / "src" / "scenes" / "Play.js").write_text(
        "generateTexture('walker'",
        encoding="utf-8",
    )
    idx = '<div id="game"></div><script type="module" src="/src/boot.js"></script>'
    codes = entrypoint_integrity_failures(index_html=idx, root=tmp_path)
    assert "scaffold_left_in_place" in codes


def test_orphan_game_js_not_loaded(tmp_path: Path) -> None:
    (tmp_path / "game.js").write_text("// orphan", encoding="utf-8")
    idx = '<script type="module" src="/src/boot.js"></script>'
    codes = entrypoint_integrity_failures(index_html=idx, root=tmp_path)
    assert "orphan_game_js_not_loaded" in codes


def test_integrity_ok_when_play_modified(tmp_path: Path) -> None:
    (tmp_path / ".daedalus-writer-assist.json").write_text("{}", encoding="utf-8")
    (tmp_path / "src" / "scenes").mkdir(parents=True)
    (tmp_path / "src" / "scenes" / "Play.js").write_text(
        "// custom gameplay — no seed marker",
        encoding="utf-8",
    )
    idx = '<div id="game"></div><script type="module" src="/src/boot.js"></script>'
    assert entrypoint_integrity_failures(index_html=idx, root=tmp_path) == []
