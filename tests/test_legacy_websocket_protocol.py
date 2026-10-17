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


@pytest.mark.parametrize("payload", [
    {"type": "docking_watch", "task_id": "Task_1"},
    {"type": "docking_refine", "request_id": "Request-1", "revision": 2**53 - 1,
     "message": "调整\t范围\r\n🧪"},
])
def test_docking_controls_require_explicit_opt_in(payload):
    raw = json.dumps(payload, ensure_ascii=False)
    with pytest.raises(LegacyFrameError) as raised:
        decode_legacy_frame(raw)
    assert raised.value.code == "invalid_frame"
    result = decode_legacy_frame(raw, allow_docking_controls=True)
    assert type(result) is dict and result == payload
    with pytest.raises(LegacyFrameError):
        decode_legacy_frame(raw, allow_docking_controls=False)


@pytest.mark.parametrize("raw", [
    '{"type":"docking_watch","task_id":"T1","task_id":"T2"}',
    '{"type":"docking_watch","type":"docking_watch","task_id":"T1"}',
    '{"type":"docking_watch","type":"chat","message":"hi"}',
    '{"type":"docking_refine","type":"ping","timestamp":1}',
    '{"type":"chat","type":"docking_watch","task_id":"T1"}',
    '{"type":"docking_watch","ty\\u0070e":"chat","message":"hi"}',
    '{"type":"docking_refine","request_id":"R1","revision":NaN,"message":"hi"}',
    '{"type":"docking_watch","task_id":"sk-fake-marker"}',
    '{"type":"docking_watch","task_id":"T1","owner":"private-marker"}',
    '{"type":"docking_refine","request_id":"R1","revision":9007199254740992,"message":"hi"}',
    '{"type":"docking_refine","request_id":"R1","revision":true,"message":"hi"}',
    '{"type":"docking_refine","request_id":"R1","revision":0,"message":"   "}',
    '{"type":"docking_watch","task_id":' + '[' * 20 + '0' + ']' * 20 + '}',
    json.dumps({"type": "docking_refine", "request_id": "R1", "revision": 0,
                "message": "🧪" * 8192}, ensure_ascii=False),
], ids=lambda raw: "wire")
def test_enabled_invalid_controls_never_fall_back_to_chat(raw):
    with pytest.raises(LegacyFrameError) as raised:
        decode_legacy_frame(raw, allow_docking_controls=True)
    assert raised.value.code == "invalid_frame"
    assert "private-marker" not in str(raised.value)
    assert raised.value.__cause__ is None


@pytest.mark.parametrize("raw", [
    '{}', '{"message":"hi"}', '{"type":"ping"}', '{"type":"ping","timestamp":3.5}',
    json.dumps({"type": "chat", "message": "🧪" * 8192}, ensure_ascii=False),
    json.dumps({"message": "hello", "enable_rag": False, "enable_tools": False,
                "rag_count": 50, "temperature": 2, "mol_count": 100,
                "reference": {"id": "r"}, "selection": [1, "two"]}),
], ids=lambda raw: "wire")
def test_enabled_valid_legacy_chat_and_ping_outputs_unchanged(raw):
    expected = decode_legacy_frame(raw)
    assert decode_legacy_frame(raw, allow_docking_controls=True) == expected
    assert decode_legacy_frame(raw, allow_docking_controls=False) == expected


def test_default_retains_permissive_non_control_handling():
    # Deliberately not "fixed" by introducing the opt-in parser.
    raw = '{"type":"chat","message":"first","message":"last","reference":NaN}'
    result = decode_legacy_frame(raw)
    assert result["message"] == "last"
    assert result["reference"] != result["reference"]
    assert decode_legacy_frame('{"message":"\\ud800"}')["message"] == "\ud800"


def test_legacy_opt_in_is_keyword_only():
    with pytest.raises(TypeError):
        decode_legacy_frame('{}', True)


@pytest.mark.parametrize("field", ["reference", "selection"])
@pytest.mark.parametrize("frame_type", ["chat", "ping"])
@pytest.mark.parametrize("depth", [16, 32])
def test_enabled_legacy_nested_values_keep_baseline_outputs(field, frame_type, depth):
    nested = 0
    for _ in range(depth):
        nested = [nested]
    raw = json.dumps({"type": frame_type, "message": "hi", field: nested})
    before = raw
    expected = decode_legacy_frame(raw)
    if frame_type == "chat":
        assert expected[field] == nested
    else:
        assert expected == {"type": "ping", "timestamp": 0}
    assert decode_legacy_frame(raw, allow_docking_controls=True) == expected
    assert decode_legacy_frame(raw, allow_docking_controls=False) == expected
    assert raw == before


@pytest.mark.parametrize("field", ["reference", "selection"])
@pytest.mark.parametrize("value", [float("inf"), float("-inf"), "\ud800"])
def test_enabled_legacy_values_do_not_inherit_control_scalar_rules(field, value):
    raw = json.dumps({"message": "hi", field: value})
    expected = decode_legacy_frame(raw)
    assert expected[field] == value
    assert decode_legacy_frame(raw, allow_docking_controls=True) == expected


@pytest.mark.parametrize("field", ["reference", "selection"])
def test_enabled_legacy_nan_retains_baseline_behavior(field):
    raw = json.dumps({"message": "hi", field: float("nan")})
    expected = decode_legacy_frame(raw)
    result = decode_legacy_frame(raw, allow_docking_controls=True)
    assert expected[field] != expected[field]
    assert result[field] != result[field]
    assert {key: value for key, value in result.items() if key != field} == {
        key: value for key, value in expected.items() if key != field}
