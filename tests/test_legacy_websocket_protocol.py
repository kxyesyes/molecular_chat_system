import json

import pytest

from src.web.legacy_websocket_protocol import LegacyFrameError, decode_legacy_frame


@pytest.mark.parametrize("raw", [
    "not-json",
    json.dumps(["chat", "你好"]),
    json.dumps({"type": "unknown", "message": "你好"}),
    json.dumps({"type": "chat", "message": "你好", "enable_rag": "false"}),
])
def test_rejects_untrusted_frames(raw):
    with pytest.raises(LegacyFrameError) as exc_info:
        decode_legacy_frame(raw)
    assert exc_info.value.code == "invalid_frame"


def test_rejects_oversized_frame():
    with pytest.raises(LegacyFrameError):
        decode_legacy_frame(json.dumps({"message": "x" * 30000}))


def test_normalizes_valid_chat_and_ping_frames():
    chat = decode_legacy_frame(json.dumps({"type": "chat", "message": "你好"}))
    assert chat["enable_rag"] is True
    assert chat["temperature"] == 0.7
    assert decode_legacy_frame(json.dumps({"type": "ping", "timestamp": 3.5})) == {
        "type": "ping", "timestamp": 3.5,
    }
