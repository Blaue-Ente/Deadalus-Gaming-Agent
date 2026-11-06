"""Real Chromium smoke — optional locally; required in CI (GitLab job)."""

from __future__ import annotations

import asyncio

import pytest

MIN_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def test_chromium_catches_absolute_assets_under_apps_prefix(monkeypatch, tmp_path):
    """Under ``/apps/slug/``, ``src="/assets/x.png"`` 404s — proof should fail the request."""
    from daedalus.playground import project_dir as real_pd
    from daedalus.playground_browser import _serve_static, _stage_dist_as_published, browser_verify

    slug = "ci-visual"
    monkeypatch.setattr("daedalus.playground.project_dir", lambda s: tmp_path if s == slug else real_pd(s))

    dist = tmp_path / "dist"
    dist.mkdir(parents=True)
    (dist / "assets").mkdir()
    (dist / "assets" / "pixel.png").write_bytes(MIN_PNG)
    (dist / "index.html").write_text(
        "<!DOCTYPE html><html><body>"
        '<img id="x" src="/assets/pixel.png" alt="">'
        "<p>ok</p></body></html>",
        encoding="utf-8",
    )

    stage_root, cleanup = _stage_dist_as_published(dist, slug)
    base, stop = _serve_static(stage_root)
    try:
        r = asyncio.run(
            browser_verify(
                slug, base + f"/apps/{slug}/", kind="static", settle_ms=800, nav_timeout_ms=15000,
            )
        )
    finally:
        stop()
        cleanup()

    if r.get("skipped") or r.get("error") == "chromium_not_found":
        pytest.skip("Chromium/Playwright not available")

    assert r.get("reachable") is True
    failed = r.get("failed_requests") or []
    assert any("404" in fe and "image" in fe for fe in failed)


def test_chromium_relative_assets_ok(monkeypatch, tmp_path):
    from daedalus.playground import project_dir as real_pd
    from daedalus.playground_browser import _serve_static, _stage_dist_as_published, browser_verify

    slug = "ci-visual-rel"
    monkeypatch.setattr("daedalus.playground.project_dir", lambda s: tmp_path if s == slug else real_pd(s))

    dist = tmp_path / "dist"
    dist.mkdir(parents=True)
    (dist / "assets").mkdir()
    (dist / "assets" / "pixel.png").write_bytes(MIN_PNG)
    (dist / "index.html").write_text(
        "<!DOCTYPE html><html><body>"
        '<img id="x" src="./assets/pixel.png" alt="">'
        "<p>ok</p></body></html>",
        encoding="utf-8",
    )

    stage_root, cleanup = _stage_dist_as_published(dist, slug)
    base, stop = _serve_static(stage_root)
    try:
        r = asyncio.run(
            browser_verify(
                slug, base + f"/apps/{slug}/", kind="static", settle_ms=800, nav_timeout_ms=15000,
            )
        )
    finally:
        stop()
        cleanup()

    if r.get("skipped") or r.get("error") == "chromium_not_found":
        pytest.skip("Chromium/Playwright not available")

    assert r.get("reachable") is True
    failed = [x for x in (r.get("failed_requests") or []) if "404" in x and "image" in x]
    assert not failed
