"""OpenRouter image models return data: URLs; those must become PNG bytes."""
from __future__ import annotations

import base64

from daedalus.image_client import decode_image_bytes, payload_b64

# 1×1 PNG
_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def test_payload_b64_from_data_url():
    b64 = base64.b64encode(_PNG).decode("ascii")
    url = f"data:image/png;base64,{b64}"
    assert payload_b64({"url": url}) == b64
    assert decode_image_bytes({"url": url}) == _PNG


def test_payload_b64_prefers_explicit_field():
    assert payload_b64({"b64": "abc", "url": "data:image/png;base64,zzz"}) == "abc"


def test_payload_b64_empty_http_url():
    assert payload_b64({"url": "https://example.com/a.png"}) == ""
    assert decode_image_bytes({"url": "https://example.com/a.png"}) is None
