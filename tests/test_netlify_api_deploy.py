"""Owner-token Netlify deploys go through the API: no CLI, no token in argv, secrets never uploaded.

Found live (2026-11-11): netlify-cli stopped at "No teams available" on an account with a team, passed the token in
its argv (--auth), and Daedalus then told the owner to "set NETLIFY_AUTH_TOKEN" although it was set."""
from __future__ import annotations

import asyncio
import io
import zipfile

import pytest

from daedalus import live_publish as lp


class FakeResp:
    def __init__(self, status, data):
        self.status_code, self._data, self.text = status, data, str(data)

    def json(self):
        return self._data


class FakeClient:
    calls: list = []
    site_status = 201

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None, content=None):
        FakeClient.calls.append(("POST", url, dict(headers or {}), content))
        if url == "/sites":
            return FakeResp(FakeClient.site_status, {"id": "site1", "ssl_url": "https://x.netlify.app"}
                            if FakeClient.site_status < 300 else {"message": "Forbidden"})
        return FakeResp(200, {"id": "dep1", "state": "uploaded"})

    async def get(self, url, headers=None):
        FakeClient.calls.append(("GET", url, dict(headers or {}), None))
        return FakeResp(200, {"id": "dep1", "state": "ready"})


@pytest.fixture
def netlify(monkeypatch, tmp_path):
    import httpx

    FakeClient.calls, FakeClient.site_status = [], 201
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    monkeypatch.setenv("NETLIFY_AUTH_TOKEN", "nfp_secret")
    monkeypatch.setattr(lp, "get_site", lambda slug: None)
    monkeypatch.setattr(asyncio, "sleep", lambda s: _noop())          # the deploy poll does not really wait
    (tmp_path / "index.html").write_text("<canvas></canvas>")
    (tmp_path / ".env").write_text("TOKEN=x")
    return tmp_path


async def _noop():
    return None


def test_deploy_creates_a_site_uploads_a_clean_zip_and_waits(netlify):
    out = asyncio.run(lp.deploy_netlify(netlify, "orbit", anonymous=False))
    assert out == {"ok": True, "backend": "netlify", "url": "https://x.netlify.app", "site_id": "site1",
                   "note": "The public link is ready."}
    upload = next(c for c in FakeClient.calls if c[1] == "/sites/site1/deploys")
    assert upload[2]["Content-Type"] == "application/zip" and upload[2]["Authorization"] == "Bearer nfp_secret"
    names = zipfile.ZipFile(io.BytesIO(upload[3])).namelist()
    assert names == ["index.html"]                                   # .env never leaves the machine


def test_a_refusal_is_reported_with_its_reason(netlify, monkeypatch):
    FakeClient.site_status = 403

    async def local(slug):
        return {"ok": True, "local_url": "/apps/orbit/", "dir": str(netlify)}

    monkeypatch.setattr(lp, "record_site", lambda slug, row: row)
    out = asyncio.run(lp.publish_live("orbit", backend="netlify", local_fn=local))
    assert out["url"] is None and "HTTP 403" in out["note"] and "Forbidden" in out["note"]
    assert "сложи NETLIFY_AUTH_TOKEN" not in out["note"]
