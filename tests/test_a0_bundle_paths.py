"""A0-e: bundles stay inside the product root; apply is all-or-nothing."""
from __future__ import annotations

import asyncio
import os

import pytest
from a0_support import a0_tmp  # noqa: F401


@pytest.fixture
def root(a0_tmp, monkeypatch):
    from daedalus import bundle

    r = a0_tmp / "product"
    r.mkdir()
    monkeypatch.setattr(bundle, "ADN_ROOT", r)
    return r


def _run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize("bad", [
    "../outside.txt", "a/../../outside.txt", "/etc/passwd", "C:\\Windows\\x.txt", "\\\\srv\\share\\x",
    ".git/config", "sub/.git/hooks/pre-commit", "data/daedalus/kv/x.json",
    "secrets.env", ".env", "cfg/api.key", "",
])
def test_unsafe_paths_refused_at_stage(root, bad):
    from daedalus import bundle

    async def go():
        bid = await bundle.create_bundle("t")
        return await bundle.stage_file(bid, bad, "create", "why", "x")

    out = _run(go())
    assert out["ok"] is False and out["error"] == "unsafe_path"
    assert not list(root.rglob("*"))


def test_symlink_escape_refused(root, a0_tmp):
    from daedalus import bundle

    outside = a0_tmp / "outside"
    outside.mkdir()
    try:
        os.symlink(outside, root / "link", target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")

    async def go():
        bid = await bundle.create_bundle("t")
        return await bundle.stage_file(bid, "link/pwned.txt", "create", "why", "x")

    out = _run(go())
    assert out["ok"] is False and out["error"] == "unsafe_path"
    assert not (outside / "pwned.txt").exists()


def test_safe_bundle_applies_create_replace_delete(root):
    from daedalus import bundle

    (root / "daedalus").mkdir()
    (root / "daedalus" / "keep.py").write_text("old", encoding="utf-8")
    (root / "daedalus" / "gone.py").write_text("bye", encoding="utf-8")

    async def go():
        bid = await bundle.create_bundle("t")
        await bundle.stage_file(bid, "daedalus/new.py", "create", "r", "print(1)\n")
        await bundle.stage_file(bid, "daedalus/keep.py", "replace", "r", "new")
        await bundle.stage_file(bid, "daedalus/gone.py", "delete", "r", None)
        await bundle.commit_bundle(bid, "t", "s")
        return await bundle.apply_bundle(bid)

    out = _run(go())
    assert out["ok"] is True and sorted(out["applied"]) == [
        "daedalus/gone.py", "daedalus/keep.py", "daedalus/new.py"]
    assert (root / "daedalus" / "new.py").read_text(encoding="utf-8") == "print(1)\n"
    assert (root / "daedalus" / "keep.py").read_text(encoding="utf-8") == "new"
    assert not (root / "daedalus" / "gone.py").exists()


def test_apply_revalidates_and_writes_nothing_when_a_row_is_unsafe(root, a0_tmp):
    """A row planted straight in the DB (bypassing stage_file) is still refused, and nothing is written."""
    from daedalus import bundle

    async def go():
        bid = await bundle.create_bundle("t")
        await bundle.stage_file(bid, "good.txt", "create", "r", "ok")
        pool = await bundle._pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO daedalus_bundle_files
                   (bundle_id, file_path, action, reason, new_content, original_content)
                   VALUES ($1,$2,$3,$4,$5,$6)""",
                bid, "../escape.txt", "create", "r", "evil", "")
        await bundle.commit_bundle(bid, "t", "s")
        return await bundle.apply_bundle(bid)

    out = _run(go())
    assert out["ok"] is False and out["error"] == "unsafe_path" and out["applied"] == []
    assert not (root / "good.txt").exists()
    assert not (a0_tmp / "escape.txt").exists()


def test_failed_write_rolls_back_earlier_files(root):
    from daedalus import bundle

    (root / "a.txt").write_text("original", encoding="utf-8")
    (root / "blocker").write_text("i am a file", encoding="utf-8")  # makes 'blocker/x.txt' impossible

    async def go():
        bid = await bundle.create_bundle("t")
        await bundle.stage_file(bid, "a.txt", "replace", "r", "changed")
        await bundle.stage_file(bid, "created.txt", "create", "r", "new")
        await bundle.stage_file(bid, "blocker/x.txt", "create", "r", "x")
        await bundle.commit_bundle(bid, "t", "s")
        first = await bundle.apply_bundle(bid)
        return bid, first

    bid, out = _run(go())
    assert out["ok"] is False and out.get("rolled_back") is True and out["applied"] == []
    assert (root / "a.txt").read_text(encoding="utf-8") == "original"
    assert not (root / "created.txt").exists()
