"""SQLite registry + planner tables work without ADN Postgres."""
from __future__ import annotations

import asyncio


def test_sqlite_crud(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path / "projects"))
    from daedalus.paths import data_root, playground_projects_dir, product_root, repo_root
    data_root.cache_clear()
    playground_projects_dir.cache_clear()
    product_root.cache_clear()
    repo_root.cache_clear()
    from daedalus.store import reset_sqlite_pool
    reset_sqlite_pool()
    from daedalus import playground as pg
    root = tmp_path / "projects"
    root.mkdir(parents=True, exist_ok=True)
    pg.PROJECTS_DIR = root

    async def _run():
        created = await pg.create_project("Solo", kind="static", description="x")
        assert created["slug"] == "solo"
        assert await pg.get_project("solo")
        assert await pg.delete_project("solo")

    asyncio.run(_run())
    reset_sqlite_pool()


def test_pg_sql_translates_now_true_and_any():
    from daedalus.store import _pg_to_sqlite

    q, params = _pg_to_sqlite(
        "UPDATE daedalus_plans SET status=$1, updated_at=NOW() WHERE id=$2",
        ("active", "plan_1"),
    )
    assert "datetime('now')" in q
    assert "?" in q
    assert params == ("active", "plan_1")

    q2, _ = _pg_to_sqlite(
        "UPDATE daedalus_plan_steps SET manual_override=TRUE WHERE id=$1",
        ("step_1",),
    )
    assert "TRUE" not in q2.upper() or "manual_override=1" in q2.replace(" ", "")
    assert "manual_override=1" in q2.replace(" ", "")

    q3, p3 = _pg_to_sqlite(
        "SELECT COUNT(*) FROM daedalus_plan_steps WHERE id = ANY($1::text[]) AND status != 'passed'",
        (["a", "b"],),
    )
    assert "ANY" not in q3.upper()
    assert "IN" in q3.upper()
    assert p3 == ("a", "b")

    q4, _ = _pg_to_sqlite(
        "SELECT COUNT(*) FROM daedalus_clarifications WHERE created_at > NOW() - INTERVAL '7 days'",
        (),
    )
    assert "datetime('now', '-7 days')" in q4
    assert "INTERVAL" not in q4.upper()

    q5, p5 = _pg_to_sqlite(
        "INSERT INTO daedalus_learning_records (id, created_at) VALUES ($1, to_timestamp($2))",
        ("rec1", 1755950000.0),
    )
    assert "to_timestamp" not in q5.lower()
    assert "unixepoch" in q5
    assert p5 == ("rec1", 1755950000.0)


def test_planner_crud_and_dependencies_on_sqlite(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path / "projects"))
    monkeypatch.delenv("DAEDALUS_USE_ADN_DB", raising=False)
    from daedalus.paths import data_root, playground_projects_dir, product_root, repo_root
    data_root.cache_clear()
    playground_projects_dir.cache_clear()
    product_root.cache_clear()
    repo_root.cache_clear()
    from daedalus.store import reset_sqlite_pool
    reset_sqlite_pool()

    async def _run():
        from daedalus.planner import (
            add_step,
            create_plan,
            delete_plan,
            get_next_pending_step,
            get_plan,
            list_plans,
            mark_step,
            write_inbox,
            list_inbox,
        )

        created = await create_plan("Ship maze", "walk the labyrinth")
        pid = created["plan_id"]
        s1 = await add_step(pid, "scaffold", "files")
        s2 = await add_step(pid, "play", "fun", depends_on=[s1["step_id"]])
        plans = await list_plans()
        assert any(p["id"] == pid for p in plans)
        plan = await get_plan(pid)
        assert plan and len(plan["steps"]) == 2

        nxt = await get_next_pending_step()
        assert nxt and nxt["id"] == s1["step_id"]

        await mark_step(s1["step_id"], "passed")
        nxt2 = await get_next_pending_step()
        assert nxt2 and nxt2["id"] == s2["step_id"]

        inbox = await write_inbox("hello", "body")
        assert inbox["ok"] is True
        msgs = await list_inbox()
        assert any(m["id"] == inbox["message_id"] for m in msgs)

        ok = await delete_plan(pid)
        assert ok is True
        assert await get_plan(pid) is None

    asyncio.run(_run())
    reset_sqlite_pool()


def test_data_root_alias_and_chroma_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "vol"))
    monkeypatch.delenv("DAEDALUS_DATA_DIR", raising=False)
    from daedalus.paths import chroma_dir, data_root, product_root

    product_root.cache_clear()
    data_root.cache_clear()
    root = data_root()
    chroma = chroma_dir()
    assert root == (tmp_path / "vol").resolve()
    assert chroma == root / "chroma"
    assert chroma.is_dir()

    monkeypatch.delenv("DAEDALUS_DATA")
    monkeypatch.setenv("DAEDALUS_DATA_DIR", str(tmp_path / "alias"))
    data_root.cache_clear()
    assert data_root() == (tmp_path / "alias").resolve()


def test_kv_preserves_colon_keys_and_learning_sqlite(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path / "projects"))
    monkeypatch.delenv("DAEDALUS_USE_ADN_DB", raising=False)
    from daedalus.paths import data_root, playground_projects_dir, product_root, repo_root
    data_root.cache_clear()
    playground_projects_dir.cache_clear()
    product_root.cache_clear()
    repo_root.cache_clear()
    from daedalus.store import reset_sqlite_pool
    reset_sqlite_pool()

    from daedalus import kv
    kv.set("playground:db:maze:high-score", 12)
    assert kv.get("playground:db:maze:high-score") == 12
    assert "playground:db:maze:high-score" in kv.list_keys("playground:db:maze:")

    from daedalus.host import storage
    svc = storage()
    svc.upload_bytes(b"zip", "playground/published/maze.zip")
    assert svc.download_bytes("playground/published/maze.zip") == b"zip"
    names = [o["name"] for o in svc.list_objects(prefix="playground/published/")]
    assert "playground/published/maze.zip" in names

    async def _run():
        from daedalus.learning import get_stats, record_learning
        rid = await record_learning(
            "maze", "build maze", "static", "lite", "test-model", True,
            stage_count=1, files_count=2,
        )
        assert rid
        stats = await get_stats()
        assert stats["total"] >= 1
        assert stats["success_rate"] == 100.0

        from daedalus import playground as pg
        from daedalus.playground_publish import app_dir, publish, published_dir
        root = tmp_path / "projects"
        root.mkdir(parents=True, exist_ok=True)
        pg.PROJECTS_DIR = root
        created = await pg.create_project("Arena", kind="static", description="x")
        pg.write_files(created["slug"], {"index.html": "<h1>hi</h1>"})
        res = await publish(created["slug"])
        assert res["ok"] is True
        assert res["durable"] is True
        assert (app_dir(created["slug"]) / "index.html").is_file()
        assert str(data_root()) in str(published_dir())

        from daedalus.build_jobs import BuildJob, get as get_job
        from daedalus import build_jobs as bj
        job = BuildJob(created["slug"], "create", "create")
        job.text = "print('ok')"
        job.status = "done"
        job._persist(force=True)
        jid = job.id
        bj._JOBS.pop(jid, None)
        restored = get_job(jid)
        assert restored is not None
        assert restored.text == "print('ok')"

    asyncio.run(_run())
    reset_sqlite_pool()


def test_rematerialize_workspace_restore_and_orphan_jobs(tmp_path, monkeypatch):
    """Docker volume wipe: zip in local storage + running job survive process restart."""
    import json
    import shutil

    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path / "projects"))
    monkeypatch.delenv("DAEDALUS_USE_ADN_DB", raising=False)
    from daedalus.paths import data_root, playground_projects_dir, product_root, repo_root
    data_root.cache_clear()
    playground_projects_dir.cache_clear()
    product_root.cache_clear()
    repo_root.cache_clear()
    from daedalus.store import reset_sqlite_pool
    reset_sqlite_pool()
    from daedalus import playground as pg
    root = tmp_path / "projects"
    root.mkdir(parents=True, exist_ok=True)
    pg.PROJECTS_DIR = root

    async def _run():
        from daedalus.playground_publish import app_dir, materialize_all, publish
        from daedalus.playground_workspace_backup import backup_workspace, try_restore_workspace
        from daedalus.standalone_boot import startup

        created = await pg.create_project("Remat", kind="static", description="x")
        slug = created["slug"]
        pg.write_files(slug, {"index.html": "<h1>labyrinth</h1>"})
        pub = await publish(slug)
        assert pub["durable"] is True

        dest = app_dir(slug)
        shutil.rmtree(dest)
        assert not dest.exists() or not any(dest.iterdir())
        n = await materialize_all()
        assert n >= 1
        assert (app_dir(slug) / "index.html").read_text(encoding="utf-8") == "<h1>labyrinth</h1>"

        bak = await backup_workspace(slug, trigger="test")
        assert bak.get("ok") is True
        shutil.rmtree(pg.project_dir(slug))
        restored_ws = await try_restore_workspace(slug)
        assert restored_ws.get("restored") is True
        assert (pg.project_dir(slug) / "index.html").is_file()

        from daedalus.kv import db as rdb
        from daedalus.autopilot_jobs import recover_orphaned_jobs, _job_key
        jid = "orphanjob1"
        rdb[_job_key(jid)] = json.dumps({
            "snapshot": {
                "id": jid, "slug": slug, "status": "running",
                "error": None, "files": [], "assets": [],
            },
            "events": [],
        }, ensure_ascii=False)
        recovered = recover_orphaned_jobs()
        assert recovered >= 1
        raw = rdb.get(_job_key(jid))
        payload = json.loads(raw) if isinstance(raw, str) else dict(raw)
        assert payload["snapshot"]["status"] == "interrupted"

        boot = await startup()
        assert boot.get("skipped") is False
        assert boot.get("sqlite") is True
        assert boot.get("materialized", 0) >= 1

    asyncio.run(_run())
    reset_sqlite_pool()


def test_standalone_playground_defaults_under_data_root(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.delenv("DAEDALUS_PLAYGROUND", raising=False)
    from daedalus.paths import data_root, playground_projects_dir
    data_root.cache_clear()
    playground_projects_dir.cache_clear()
    try:
        path = playground_projects_dir()
        assert path == data_root() / "playground"
        assert path.is_dir()
    finally:
        playground_projects_dir.cache_clear()
        data_root.cache_clear()

