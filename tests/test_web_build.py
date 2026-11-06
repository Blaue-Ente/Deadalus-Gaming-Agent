"""Unified web build: asset staging, absolute /assets/ detection."""

from daedalus.web_build import (
    rewrite_absolute_assets_in_dist,
    scan_absolute_asset_refs,
    stage_web_assets,
)


def test_stage_web_assets_from_web_build(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "dist").mkdir()
    (tmp_path / "assets" / "hero.png").write_bytes(b"png")
    n = stage_web_assets(tmp_path)
    assert n == 1
    assert (tmp_path / "dist" / "assets" / "hero.png").is_file()


def test_scan_absolute_asset_refs():
    from pathlib import Path

    import tempfile

    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "index.html").write_text('<script src="/assets/bad.js"></script>', encoding="utf-8")
        hits = scan_absolute_asset_refs(d)
        assert hits


def test_rewrite_absolute_assets():
    from pathlib import Path

    import tempfile

    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "index.html").write_text('import x from "/assets/x.png"', encoding="utf-8")
        rewrite_absolute_assets_in_dist(d)
        t = (d / "index.html").read_text(encoding="utf-8")
        assert "./assets/x.png" in t
        assert scan_absolute_asset_refs(d) == []
