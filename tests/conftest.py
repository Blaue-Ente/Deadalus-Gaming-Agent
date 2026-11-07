"""Product tests run with the repository root on ``sys.path``.

HTTP tests send ``DAEDALUS_TOKEN`` automatically. Use the ``raw_http``
fixture when asserting 401/403 without a header.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from httpx import Headers

_PRODUCT = Path(__file__).resolve().parents[1]
if str(_PRODUCT) not in sys.path:
    sys.path.insert(0, str(_PRODUCT))

_orig_request = TestClient.request
_inject_owner_token = True


def _authed_request(self, method, url, **kwargs):
    if _inject_owner_token:
        token = (os.environ.get("DAEDALUS_TOKEN") or "").strip()
        if token:
            headers = Headers(kwargs.get("headers"))
            if "authorization" not in headers and "x-daedalus-token" not in headers:
                headers["Authorization"] = f"Bearer {token}"
                kwargs["headers"] = headers
    return _orig_request(self, method, url, **kwargs)


TestClient.request = _authed_request


@pytest.fixture(autouse=True)
def _owner_token_env(monkeypatch):
    monkeypatch.setenv("DAEDALUS_TOKEN", "pytest-owner-token")
    monkeypatch.setenv("DAEDALUS_BIND_HOST", "127.0.0.1")


@pytest.fixture(autouse=True)
def _reset_llm_routing_watchdog():
    try:
        from daedalus import llm_routing_watchdog as rw
        rw.clear()
    except Exception:
        pass
    yield


@pytest.fixture
def raw_http():
    """Disable TestClient auto-injection of the owner token."""
    global _inject_owner_token
    _inject_owner_token = False
    try:
        yield
    finally:
        _inject_owner_token = True
