import json

import pytest

from src.agent.contracts.decision import (
    DecisionProtocolError, decision_json_schema, parse_decision_json,
)


def tool_payload(**overrides):
    return {"decision": {
        "version": "1", "action": "tool", "tool_name": "property_calculator",
        "arguments": {"query": "CCO"}, "purpose": "Calculate properties",
        **overrides,
    }}


@pytest.mark.parametrize("decision", [
    tool_payload()["decision"],
    {"version": "1", "action": "clarify", "question": "请提供 receptor 文件",
     "missing_fields": ["receptor"]},
    {"version": "1", "action": "finish", "response_kind": "chat",
     "text": "你好，有什么可以帮你？", "evidence_ids": []},
    {"version": "1", "action": "finish", "response_kind": "scientific",
     "text": "已计算性质。", "evidence_ids": ["evidence-abc"]},
])
def test_decision_round_trip(decision):
    result = parse_decision_json(json.dumps({"decision": decision}))
    assert result.model_dump() == decision


@pytest.mark.parametrize("payload", [
    tool_payload(version=1), tool_payload(action="run_shell"),
    tool_payload(tool_name="../run"), tool_payload(tool_name="run docking"),
    tool_payload(arguments="CCO"), tool_payload(purpose=12),
    tool_payload(purpose=""), tool_payload(unknown="fake-secret-marker"),
    {"decision": {"action": "tool"}},
    {"decision": {"version": "1", "action": "clarify", "question": "?", "missing_fields": []}},
    {"decision": {"version": "1", "action": "finish", "response_kind": "scientific",
                  "text": "Calculated", "evidence_ids": []}},
    {"decision": {"version": "1", "action": "finish", "response_kind": "chat",
                  "text": "Hello", "evidence_ids": ["evidence-abc"]}},
    {"decision": {"version": "1", "action": "finish", "response_kind": "chat",
                  "text": "Hello", "evidence_ids": [], "binding_energy": -9.0}},
    {"decision": tool_payload()["decision"], "other": "fake-secret-marker"},
    [tool_payload()],
])
def test_strict_schema_rejects_unknown_and_coerced_decisions(payload):
    with pytest.raises(DecisionProtocolError) as error:
        parse_decision_json(json.dumps(payload))
    assert "fake-secret-marker" not in str(error.value)
    assert error.value.code == "invalid_decision_schema"


@pytest.mark.parametrize("raw", [
    '{"decision":{},"decision":{}}',
    '{"decision":{"arguments":{"query":"CCO","query":"CCN"}}}',
    '{"decision":{"arguments":{"value":NaN}}}',
    '{"decision":{"arguments":{"value":Infinity}}}',
    '{"decision":{"arguments":{"value":-Infinity}}}',
    '{"decision":{"version":"1","action":"tool","tool_name":"property_calculator","arguments":{"value":1e999},"purpose":"test"}}',
    '```json\n{"decision":{}}\n```',
    '{"decision":', "fake-secret-marker",
])
def test_parser_rejects_ambiguous_or_incomplete_json_without_echoing_input(raw):
    with pytest.raises(DecisionProtocolError) as error:
        parse_decision_json(raw)
    assert "fake-secret-marker" not in str(error.value)


@pytest.mark.parametrize("raw", [
    None, b"{}", "", " " * 32769, "\ud800",
    json.dumps(tool_payload(arguments={"nested": [[[[[[[[[[[[[[[[[[0]]]]]]]]]]]]]]]]]]})),
], ids=["none", "bytes", "empty", "oversized", "surrogate", "deep"])
def test_document_boundaries(raw):
    with pytest.raises(DecisionProtocolError):
        parse_decision_json(raw)


def test_native_schema_is_an_object_envelope_with_three_strict_actions():
    schema = decision_json_schema()
    assert schema["type"] == "object" and schema["additionalProperties"] is False
    assert schema["required"] == ["decision"]
    branches = schema["properties"]["decision"]["oneOf"]
    assert len(branches) == 3
    for branch in branches:
        definition = schema["$defs"][branch["$ref"].split("/")[-1]]
        assert definition["additionalProperties"] is False
    json.dumps(schema, allow_nan=False)


@pytest.mark.parametrize("arguments", [{"query": "\ud800"}, {"\ud800": "value"}])
def test_escaped_unpaired_surrogate_is_rejected_inside_arguments(arguments):
    raw = json.dumps(tool_payload(arguments=arguments))
    with pytest.raises(DecisionProtocolError):
        parse_decision_json(raw)


def test_scientific_finish_does_not_accept_duplicate_evidence_ids():
    decision = {"version": "1", "action": "finish", "response_kind": "scientific",
                "text": "结果", "evidence_ids": ["evidence-a", "evidence-a"]}
    with pytest.raises(DecisionProtocolError):
        parse_decision_json(json.dumps({"decision": decision}))


def test_valid_unicode_surrogate_pair_remains_serializable():
    raw = json.dumps(tool_payload(arguments={"query": "分子🧪"}))
    result = parse_decision_json(raw)
    assert json.loads(result.model_dump_json())["arguments"]["query"] == "分子🧪"
