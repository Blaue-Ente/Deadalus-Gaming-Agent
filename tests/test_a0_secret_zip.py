"""A0-a: capsule / publish zips never carry secret files."""
from __future__ import annotations

import zipfile
from io import BytesIO

from a0_support import a0_tmp  # noqa: F401
from fastapi.testclient import TestClient


def _seed(root):
    root.mkdir(parents=True)
    (root / "index.html").write_text("<html></html>", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "app.js").write_text("1", encoding="utf-8")
    (root / ".env").write_text("TOKEN=super-secret", encoding="utf-8")
    (root / ".env.local").write_text("TOKEN=super-secret", encoding="utf-8")
    (root / ".env.example").write_text("TOKEN=", encoding="utf-8")
    (root / "secrets.env").write_text("K=v", encoding="utf-8")
    (root / "deploy.pem").write_text("-----BEGIN-----", encoding="utf-8")
    (root / "nested").mkdir()
    (root / "nested" / "api.key").write_text("k", encoding="utf-8")
    (root / "nested" / "id_ed25519").write_text("k", encoding="utf-8")


def test_secret_name_rules():
    from daedalus.secret_paths import is_secret_name, is_secret_path

    for bad in (".env", ".env.local", "secrets.env", "a.pem", "x.KEY", "id_rsa", "id_rsa.pub", ".npmrc", ".netrc"):
        assert is_secret_name(bad), bad
    for ok in (".env.example", ".env.sample", ".env.template", "environment.js", "keyboard.js", "monkey.ts", "index.html"):
        assert not is_secret_name(ok), ok
    assert is_secret_path("nested/dir/token.key")
    assert not is_secret_path("src/app.js")


def test_capsule_zip_excludes_secrets(a0_tmp):
    from daedalus.capsule import capsule_bytes, capsule_meta
    from daedalus.paths import playground_projects_dir

    _seed(playground_projects_dir() / "zipgame")
    names = set(zipfile.ZipFile(BytesIO(capsule_bytes("zipgame"))).namelist())
    assert names == {"index.html", "src/app.js", ".env.example"}
    meta = capsule_meta("zipgame")
    assert meta["excluded_secrets"] == 6
    assert "super-secret" not in repr(meta)


def test_capsule_http_zip_has_no_secret(a0_tmp):
    from app.main import app
    from daedalus.paths import playground_projects_dir

    _seed(playground_projects_dir() / "zipgame2")
    client = TestClient(app)
    res = client.get("/api/v1/playground/projects/zipgame2/capsule")
    assert res.status_code == 200
    zf = zipfile.ZipFile(BytesIO(res.content))
    assert ".env" not in zf.namelist()
    assert all(b"super-secret" not in zf.read(n) for n in zf.namelist())
    info = client.get("/api/v1/playground/projects/zipgame2/capsule.json").json()
    assert info["excluded_secrets"] == 6
