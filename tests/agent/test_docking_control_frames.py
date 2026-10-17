"""Parsing-only C controls: no coordinator, model, backend or route execution."""
import json

import pytest

from src.web import docking_consent as controls


def refine():
    return dict(type="docking_refine", request_id="Request_1-a", revision=0,
                message="调整范围\t保留\r\n配体 🧪")


def api():
    # Resolve at call phase so the initial RED is a missing API assertion,
    # never an import/collection error.
    assert callable(getattr(controls, "decode_docking_control", None))
    assert callable(getattr(controls, "validate_docking_control", None))
    error = getattr(controls, "DockingControlError", None)
    assert isinstance(error, type) and issubclass(error, ValueError)
    return controls.decode_docking_control, controls.validate_docking_control, error


def test_control_api_exists_at_call_phase():
    api()


@pytest.mark.parametrize("payload", [
    refine(), {"type": "docking_watch", "task_id": "T-1_2"},
    {**refine(), "revision": 2**53 - 1},
    {**refine(), "request_id": "A" * 128, "message": "x" * 8192},
    {**refine(), "message": "🧪" * 8192},
    {"type": "docking_watch", "task_id": "0"},
])
def test_native_controls_are_exact_detached_and_do_not_mutate_input(payload):
    _, validate, _ = api()
    before = payload.copy()
    result = validate(payload)
    assert type(result) is dict and result == before and result is not payload
    assert all(type(key) is str and type(value) in (str, int)
               for key, value in result.items())
    result["type"] = "changed"
    assert payload == before
    other = validate(payload)
    payload["type"] = "changed-input"
    assert other == before
    payload.update(before)


@pytest.mark.parametrize("payload", [refine(), {"type": "docking_watch", "task_id": "T-1_2"}])
def test_wire_controls_round_trip_without_normalization(payload):
    decode, _, _ = api()
    raw = json.dumps(payload, ensure_ascii=False)
    before = raw
    assert decode(raw) == payload
    assert raw == before
    assert decode(raw) is not decode(raw)


@pytest.mark.parametrize("field", ["request_id", "task_id"])
@pytest.mark.parametrize("value", [
    "", "_task", "-task", "a.b", "a:b", "a/b", "a\\b", "a b", "a\n", "é", "Ａ",
    "a" * 129, "a" * 100000, "sk-fake-marker", "prefixBearerSuffix",
    "github_pat_fake", None, 12, True, [], {},
], ids=lambda value: type(value).__name__)
def test_identifiers_reject_invalid_labels_and_sensitive_text(field, value):
    decode, validate, error = api()
    payload = refine() if field == "request_id" else {"type": "docking_watch", "task_id": "T1"}
    payload[field] = value
    with pytest.raises(error, match="^invalid_docking_control$"):
        validate(payload)
    with pytest.raises(error, match="^invalid_docking_control$"):
        decode(json.dumps(payload))


@pytest.mark.parametrize("revision", [-1, 2**53, 2**10000, True, False, 1.0, "1", None, [], {},
                                      float("nan"), float("inf"), float("-inf")],
                         ids=["negative", "unsafe-js", "huge-int", "true", "false", "float", "str",
                              "none", "list", "dict", "nan", "inf", "minus-inf"])
def test_revision_is_native_js_safe_integer(revision):
    _, validate, error = api()
    with pytest.raises(error, match="^invalid_docking_control$"):
        validate({**refine(), "revision": revision})


@pytest.mark.parametrize("message", ["", " \t\r\n", "\u2003\u3000", "x" * 8193,
                                    "\ud800", "\udfff", "x\x00", "x\x0b", "x\x1f",
                                    "x\x7f", "x\x85", "x\x9f", None, 1, [], {}],
                         ids=lambda value: type(value).__name__)
def test_message_contract(message):
    decode, validate, error = api()
    payload = {**refine(), "message": message}
    before = payload.copy()
    for call, argument in [(validate, payload), (decode, json.dumps(payload))]:
        with pytest.raises(error, match="^invalid_docking_control$"):
            call(argument)
    assert payload == before


@pytest.mark.parametrize("field", ["owner", "nonce", "path", "action", "execution", "task_id",
                                  "root_id", "send_generation", "unknown"])
def test_refine_rejects_every_extra_field(field):
    decode, validate, error = api()
    payload = {**refine(), field: "private-marker"}
    for call, argument in [(validate, payload), (decode, json.dumps(payload))]:
        with pytest.raises(error) as raised:
            call(argument)
        assert raised.value.args == ("invalid_docking_control",)
        assert raised.value.__cause__ is None


@pytest.mark.parametrize("payload", [None, [], (), "{}", {}, {"type": "docking_refine"},
    {"type": "docking_watch"}, {"task_id": "T1"}, {"type": "chat", "message": "hi"},
    {"type": [], "task_id": "T1"}, {"type": True, "task_id": "T1"},
    {"type": "docking_watch", "task_id": "T1", "message": "hi"},
    {"type": "docking_watch", "task_id": "T1", "revision": 0},
    {**refine(), "type": "docking_watch"}, {1: "docking_watch", "task_id": "T1"},
    {"x" * 100000: "value", "task_id": "T1"},
    {str(i): i for i in range(10000)},
])
def test_exact_schema_only(payload):
    _, validate, error = api()
    with pytest.raises(error, match="^invalid_docking_control$"):
        validate(payload)


@pytest.mark.parametrize("raw", [
    None, b"{}", bytearray(b"{}"), "", "private-marker", "[]", "null", "{}",
    '{"type":"docking_watch","task_id":"T1","task_id":"T1"}',
    '{"type":"docking_watch","type":"docking_watch","task_id":"T1"}',
    '{"type":"docking_watch","task_id":"T1","ta\\u0073k_id":"T1"}',
    '{"type":"docking_refine","request_id":"R1","revision":NaN,"message":"hi"}',
    '{"type":"docking_refine","request_id":"R1","revision":Infinity,"message":"hi"}',
    '{"type":"docking_refine","request_id":"R1","revision":-Infinity,"message":"hi"}',
    '{"type":"docking_refine","request_id":"R1","revision":1e999,"message":"hi"}',
    '{"type":"docking_watch","task_id":"T1",}', "\ud800",
    '{"type":"docking_watch","task_id":' + '[' * 20 + '0' + ']' * 20 + '}',
    '[' * 1500 + '0' + ']' * 1500,
    ' ' * (24 * 1024 + 1),
], ids=lambda value: type(value).__name__)
def test_strict_wire_rejections_have_fixed_diagnostics(raw):
    decode, _, error = api()
    with pytest.raises(error) as raised:
        decode(raw)
    assert raised.value.args == ("invalid_docking_control",)
    assert raised.value.__cause__ is None


def test_wire_limit_counts_utf8_bytes_including_whitespace():
    decode, _, error = api()
    payload = {**refine(), "message": "🧪" * 6000}
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    boundary = raw + " " * (24 * 1024 - len(raw.encode("utf-8")))
    assert len(boundary) < 24 * 1024
    assert len(boundary.encode("utf-8")) == 24 * 1024
    assert decode(boundary) == payload
    with pytest.raises(error):
        decode(boundary + " ")
    with pytest.raises(error):
        decode(json.dumps({**payload, "message": "🧪" * 8192}, ensure_ascii=False))


class Hostile:
    def fail(self, *args, **kwargs):
        raise AssertionError("custom behavior must never run")

    __str__ = __repr__ = __iter__ = __len__ = __bool__ = __eq__ = fail


class HostileDict(dict):
    __iter__ = __len__ = __getitem__ = get = items = copy = Hostile.fail


class HostileString(str):
    __len__ = __eq__ = __str__ = encode = strip = Hostile.fail
    __hash__ = str.__hash__


class HostileInt(int):
    __lt__ = __le__ = __gt__ = __ge__ = __int__ = Hostile.fail


def test_hostile_native_types_rejected_without_invoking_hooks():
    decode, validate, error = api()
    for raw in (Hostile(), HostileString('{}'), HostileDict()):
        with pytest.raises(error):
            decode(raw)
    for payload in (Hostile(), HostileDict(refine()), {HostileString("type"): "docking_watch",
                    "task_id": "T1"}, {**refine(), "revision": HostileInt(0)}):
        with pytest.raises(error):
            validate(payload)
    for field in ("type", "request_id", "revision", "message"):
        for value in (Hostile(), HostileString("value")):
            with pytest.raises(error):
                validate({**refine(), field: value})


def test_nested_and_cyclic_native_values_are_rejected_without_traversal():
    _, validate, error = api()
    cycle = []
    cycle.append(cycle)
    for value in (cycle, {"nested": Hostile()}, [Hostile()] * 10000):
        with pytest.raises(error):
            validate({**refine(), "message": value})
