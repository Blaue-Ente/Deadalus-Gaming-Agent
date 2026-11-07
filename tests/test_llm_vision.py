"""Cheap writers have no image input — proof frames must not 404 the turn."""
from __future__ import annotations

from daedalus.llm_client import (
    content_has_images,
    is_no_image_support,
    messages_have_images,
    strip_images_from_messages,
)


def test_detects_openrouter_no_image_404():
    err = Exception(
        "Error code: 404 - {'error': {'message': 'No endpoints found that "
        "support image input', 'code': 404, 'metadata': "
        "{'failed_routing_step': 'Filter by Image Support'}}}"
    )
    assert is_no_image_support(err) is True
    assert is_no_image_support(Exception("rate limit")) is False


def test_strip_proof_frames_keeps_text():
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "RETURN TO WRITER: 1 uncaught"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,xx"}},
            ],
        }
    ]
    assert messages_have_images(messages) is True
    assert content_has_images(messages[0]["content"]) is True
    stripped = strip_images_from_messages(messages)
    assert messages_have_images(stripped) is False
    assert stripped[0]["content"] == [
        {"type": "text", "text": "RETURN TO WRITER: 1 uncaught"},
    ]
