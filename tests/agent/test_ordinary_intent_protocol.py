"""Synthetic, offline tests of proposals only; no admission or execution proof."""
import importlib
import json
import traceback

import pytest
from pydantic import ValidationError

from src.agent.contracts.decision import DecisionProtocolError, parse_decision_json


KINDS = (
    "capability", "general_knowledge", "conversation", "follow_up",
    "scientific_execution", "retrieval", "mixed", "uncertain",
)
RELATIONS = ("none", "prior_ordinary_turn")
FIELDS = ("version", "kind", "history_relation", "unresolved")
EXTRAS = (
    "authority", "tools", "tool_name", "arguments", "rationale", "confidence",
    "code", "input_rewrite", "spans", "action", "text", "evidence_ids",
    "private-key-marker",
)


def contract():
    # Load in the test body so the absent feature produces the full RED matrix,
    # not a collection abort. Do not disguise missing transitive dependencies.
    name = "src.agent.contracts.ordinary_intent"
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name != name:
            raise
        pytest.fail("Task1 ordinary_intent contract is absent", pytrace=False)


def proposal(**changes):
    return dict(version="1", kind="capability", history_relation="none",
                unresolved=False) | changes


def document(**changes):
    return json.dumps({"intent": proposal(**changes)}, ensure_ascii=False)


def assert_safe_rejection(parse, raw, code):
    with pytest.raises(DecisionProtocolError) as caught:
        parse(raw)
    error = caught.value
    assert error.code == code
    assert error.args == (code,)
    assert error.schema_issues == []
    public = str(error) + repr(error) + repr(vars(error))
    assert "private-" not in public
    assert error.__cause__ is None
    # Rendered exception chains must not reveal Pydantic input or JSON text.
    rendered = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    assert "private-value-marker" not in rendered


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("relation", RELATIONS)
@pytest.mark.parametrize("unresolved", [False, True])
def test_all_kinds_relations_and_boolean_values(kind, relation, unresolved):
    module = contract()
    data = proposal(kind=kind, history_relation=relation, unresolved=unresolved)
    if kind == "follow_up" and relation == "none":
        assert_safe_rejection(module.parse_ordinary_intent_json,
                              document(**data), "invalid_ordinary_intent")
        with pytest.raises(ValidationError):
            module.OrdinaryIntent(**data)
        return
    intent = module.parse_ordinary_intent_json(document(**data))
    assert isinstance(intent, module.OrdinaryIntent)
    assert intent.model_dump() == data
    assert type(intent.version) is type(intent.kind) is type(intent.history_relation) is str
    assert type(intent.unresolved) is bool
    assert module.OrdinaryIntent(**data) == intent
    assert module.IntentEnvelope(intent=data).intent == intent
    # A history relation is a proposal, not evidence of actual server history.
    assert not hasattr(intent, "execute")
    assert not hasattr(intent, "authorized")


@pytest.mark.parametrize("field", FIELDS)
def test_every_intent_field_is_required_for_parser_and_direct_model(field):
    module = contract()
    data = proposal()
    del data[field]
    assert_safe_rejection(module.parse_ordinary_intent_json,
                          json.dumps({"intent": data}), "invalid_ordinary_intent")
    with pytest.raises(ValidationError):
        module.OrdinaryIntent(**data)
    with pytest.raises(ValidationError):
        module.IntentEnvelope(intent=data)


def test_envelope_intent_is_required():
    module = contract()
    assert_safe_rejection(module.parse_ordinary_intent_json, "{}", "invalid_ordinary_intent")
    with pytest.raises(ValidationError):
        module.IntentEnvelope()


@pytest.mark.parametrize("location", ["root", "intent"])
@pytest.mark.parametrize("key", EXTRAS)
def test_no_extras_or_authority_fields(location, key):
    module = contract()
    data = {"intent": proposal()}
    target = data if location == "root" else data["intent"]
    target[key] = "private-value-marker"
    assert_safe_rejection(module.parse_ordinary_intent_json,
                          json.dumps(data), "invalid_ordinary_intent")
    with pytest.raises(ValidationError):
        module.IntentEnvelope(**data)
    if location == "intent":
        with pytest.raises(ValidationError):
            module.OrdinaryIntent(**data["intent"])


@pytest.mark.parametrize("field,value", [
    ("version", 1), ("version", True), ("version", 1.0), ("version", "2"),
    ("version", "01"), ("version", " 1"), ("version", None),
    ("kind", 1), ("kind", True), ("kind", None), ("kind", []), ("kind", {}),
    ("kind", "Capability"), ("kind", "capability "), ("kind", "tool"),
    ("kind", "private-value-marker"),
    ("history_relation", 0), ("history_relation", False), ("history_relation", None),
    ("history_relation", []), ("history_relation", {}), ("history_relation", "prior_turn"),
    ("history_relation", "NONE"), ("history_relation", "none "),
    ("unresolved", 0), ("unresolved", 1), ("unresolved", 0.0), ("unresolved", 1.0),
    ("unresolved", "false"), ("unresolved", "true"), ("unresolved", "0"),
    ("unresolved", "1"), ("unresolved", "yes"), ("unresolved", "no"),
    ("unresolved", None), ("unresolved", []), ("unresolved", {}),
])
def test_strict_types_and_closed_literals_without_coercion(field, value):
    module = contract()
    data = proposal(**{field: value})
    assert_safe_rejection(module.parse_ordinary_intent_json,
                          document(**data), "invalid_ordinary_intent")
    with pytest.raises(ValidationError):
        module.OrdinaryIntent(**data)
    with pytest.raises(ValidationError):
        module.IntentEnvelope(intent=data)


@pytest.mark.parametrize("field,value", [
    ("version", b"1"), ("kind", b"capability"),
    ("history_relation", b"none"), ("unresolved", b"false"),
])
def test_direct_models_reject_bytes(field, value):
    module = contract()
    with pytest.raises(ValidationError):
        module.OrdinaryIntent(**proposal(**{field: value}))
    with pytest.raises(ValidationError):
        module.IntentEnvelope(intent=proposal(**{field: value}))


@pytest.mark.parametrize("field", FIELDS)
def test_intent_is_frozen(field):
    module = contract()
    intent = module.parse_ordinary_intent_json(document())
    with pytest.raises(ValidationError, match="frozen_instance"):
        setattr(intent, field, getattr(intent, field))
    with pytest.raises(ValidationError, match="frozen_instance"):
        delattr(intent, field)
    assert intent.model_dump() == proposal()


def test_envelope_is_frozen_including_nested_intent():
    module = contract()
    envelope = module.IntentEnvelope(intent=proposal())
    with pytest.raises(ValidationError, match="frozen_instance"):
        envelope.intent = module.OrdinaryIntent(**proposal())
    with pytest.raises(ValidationError, match="frozen_instance"):
        envelope.intent.unresolved = True
    with pytest.raises(ValidationError, match="frozen_instance"):
        del envelope.intent


@pytest.mark.parametrize("raw", [None, False, 0, {}, [], b"{}", ""])
def test_invalid_raw_document_types(raw):
    module = contract()
    assert_safe_rejection(module.parse_ordinary_intent_json, raw, "invalid_decision_document")


@pytest.mark.parametrize("raw", [
    " ", "private-value-marker", "{", "{'intent': {}}", '{"intent":{},}',
    "```json\n" + document() + "\n```", "prefix " + document(),
    document() + " suffix", document() + document(),
    "// comment\n" + document(), "\ufeff" + document(),
    '{"intent":"\x00"}',
])
def test_json_syntax_never_extracts_a_substring(raw):
    module = contract()
    assert_safe_rejection(module.parse_ordinary_intent_json, raw, "invalid_decision_json")


@pytest.mark.parametrize("raw", [
    "null", "true", "1", '"private-value-marker"', "[]",
    json.dumps([{"intent": proposal()}]), '{"intent":null}', '{"intent":[]}',
    '{"intent":false}', '{"intent":1}', '{"intent":"private-value-marker"}',
    json.dumps(proposal()),
])
def test_json_values_must_be_exact_envelopes(raw):
    module = contract()
    assert_safe_rejection(module.parse_ordinary_intent_json, raw, "invalid_ordinary_intent")


@pytest.mark.parametrize("raw", [
    '{"intent":{},"intent":{}}',
    '{"intent":{"kind":"capability","kind":"conversation"}}',
    '{"intent":{"version":"1","version":"1"}}',
    '{"intent":{"unresolved":false,"unresolved":true}}',
    '{"intent":{"history_relation":"none","history_relation":"none"}}',
    '{"intent":{"kind":"capability","\\u006bind":"conversation"}}',
    '{"private-key-marker":{"nested":1,"nested":2}}',
])
def test_duplicate_keys_rejected_at_all_levels(raw):
    module = contract()
    assert_safe_rejection(module.parse_ordinary_intent_json, raw, "duplicate_json_key")


@pytest.mark.parametrize("number", ["NaN", "Infinity", "-Infinity", "1e999", "-1e999"])
@pytest.mark.parametrize("template", ['{}', '{{"intent":{{"unresolved":{}}}}}',
                                       '{{"private-key-marker":[{}]}}'])
def test_nonfinite_json_numbers_rejected(number, template):
    module = contract()
    assert_safe_rejection(module.parse_ordinary_intent_json,
                          template.format(number), "nonfinite_json_number")


@pytest.mark.parametrize("raw", [
    "\ud800", '{"intent":"\\ud800"}', '{"\\udfff":{}}',
])
def test_invalid_utf8_raw_and_decoded_strings(raw):
    module = contract()
    assert_safe_rejection(module.parse_ordinary_intent_json, raw, "invalid_decision_json")


@pytest.mark.parametrize("size", [4095, 4096, 4097])
def test_raw_byte_limit_includes_whitespace(size):
    module = contract()
    raw = "\r\n\t " + document()
    raw += " " * (size - len(raw.encode("utf-8")))
    assert len(raw.encode("utf-8")) == size
    if size <= 4096:
        assert module.parse_ordinary_intent_json(raw).model_dump() == proposal()
    else:
        assert_safe_rejection(module.parse_ordinary_intent_json, raw, "decision_too_large")


@pytest.mark.parametrize("character", ["中", "🧪"])
@pytest.mark.parametrize("size", [4096, 4097])
def test_limit_counts_utf8_bytes_not_characters(character, size):
    module = contract()
    raw = document(kind=character * 900)
    raw += " " * (size - len(raw.encode("utf-8")))
    assert len(raw) < size and len(raw.encode("utf-8")) == size
    assert_safe_rejection(module.parse_ordinary_intent_json, raw,
                          "decision_too_large" if size > 4096 else "invalid_ordinary_intent")


@pytest.mark.parametrize("container", ["list", "object"])
@pytest.mark.parametrize("depth", [4, 5, 16, 17, 1000])
def test_depth_bound_precedes_schema_validation(container, depth, monkeypatch):
    module = contract()
    # All legal intents are flat. Observe the real validator to distinguish the
    # depth-4 boundary from schema rejection of an otherwise invalid deep shape.
    visited = []
    validate = module.IntentEnvelope.model_validate

    def observe(value, *args, **kwargs):
        visited.append(True)
        return validate(value, *args, **kwargs)

    monkeypatch.setattr(module.IntentEnvelope, "model_validate", observe)
    opening, closing = ("[", "]") if container == "list" else ('{"x":', "}")
    raw = opening * depth + "0" + closing * depth
    with pytest.raises(DecisionProtocolError) as caught:
        module.parse_ordinary_intent_json(raw)
    assert visited == ([True] if depth == 4 else [])
    if depth <= 16:
        assert caught.value.code == "invalid_ordinary_intent"
    assert "private-" not in str(caught.value)


@pytest.mark.parametrize("action,fields", [
    ("finish", {"response_kind": "chat", "text": "Synthetic hello", "evidence_ids": []}),
    ("clarify", {"question": "Synthetic question?", "missing_fields": ["input"]}),
    ("tool", {"tool_name": "synthetic_tool", "arguments": {}, "purpose": "proposal"}),
])
def test_decision_and_intent_namespaces_never_cross(action, fields):
    module = contract()
    decision = {"version": "1", "action": action, **fields}
    raw = json.dumps({"decision": decision})
    assert parse_decision_json(raw).model_dump() == decision
    assert_safe_rejection(module.parse_ordinary_intent_json, raw, "invalid_ordinary_intent")
    assert_safe_rejection(module.parse_ordinary_intent_json,
                          json.dumps({"intent": decision}), "invalid_ordinary_intent")
    with pytest.raises(DecisionProtocolError):
        parse_decision_json(document())
    assert_safe_rejection(module.parse_ordinary_intent_json,
                          json.dumps({"intent": proposal(), "decision": decision}),
                          "invalid_ordinary_intent")


def test_schema_helper_matches_existing_contract_style_and_is_fresh():
    module = contract()
    schema = module.ordinary_intent_json_schema()
    assert schema == module.IntentEnvelope.model_json_schema()
    assert schema["type"] == "object" and schema["additionalProperties"] is False
    assert schema["required"] == ["intent"]
    assert set(schema["properties"]) == {"intent"}
    inner = schema["$defs"]["OrdinaryIntent"]
    assert inner["type"] == "object" and inner["additionalProperties"] is False
    assert set(inner["required"]) == set(inner["properties"]) == set(FIELDS)
    properties = inner["properties"]
    assert properties["version"]["const"] == "1"
    assert properties["version"]["type"] == "string"
    assert properties["kind"]["enum"] == list(KINDS)
    assert properties["history_relation"]["enum"] == list(RELATIONS)
    assert properties["unresolved"]["type"] == "boolean"
    assert all("default" not in prop for prop in properties.values())
    assert "default" not in schema["properties"]["intent"]
    schema["$defs"]["OrdinaryIntent"]["properties"].clear()
    assert module.ordinary_intent_json_schema() == module.IntentEnvelope.model_json_schema()
    json.dumps(module.ordinary_intent_json_schema(), allow_nan=False)
