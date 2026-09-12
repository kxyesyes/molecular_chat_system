import json

import pytest

from src.agent.contracts.decision import (
    MAX_DECISION_BYTES, MAX_DECISION_DEPTH, DecisionProtocolError,
    decode_protocol_json, decision_json_schema, parse_decision_json,
    public_schema_issues,
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


def test_schema_error_identifies_missing_version_without_input():
    value = tool_payload()
    del value['decision']['version']
    with pytest.raises(DecisionProtocolError) as caught:
        parse_decision_json(json.dumps(value))
    assert caught.value.schema_issues == [
        {'path': 'decision.tool.version', 'type': 'missing'},
    ]
    assert str(caught.value) == 'invalid_decision_schema'


def test_schema_errors_mask_provider_keys_values_and_validation_context():
    value = tool_payload(version='private-value-marker')
    value['decision']['private-key-marker'] = {'input': 'private-content-marker'}
    with pytest.raises(DecisionProtocolError) as caught:
        parse_decision_json(json.dumps(value))
    assert caught.value.schema_issues == [
        {'path': 'decision.tool.version', 'type': 'literal_error'},
        {'path': 'decision.tool.*', 'type': 'extra_forbidden'},
    ]
    assert 'private-' not in repr(vars(caught.value))


@pytest.mark.parametrize('issues', [None, {}, 'private-marker', 12, ()])
def test_public_schema_issues_rejects_non_list_payload(issues):
    assert public_schema_issues(issues) == []


def test_public_schema_issues_bounds_and_detaches_provider_payload():
    issue = {
        'path': 'decision.tool.arguments.private-marker.123.version.text',
        'type': 'private-error-marker', 'input': 'private-input-marker',
        'ctx': {'error': 'private-context-marker'}, 'msg': 'private-message-marker',
    }
    raw = [issue] * 20
    expected = [{'path': 'decision.tool.arguments.*.*.version',
                 'type': 'validation_error'}] * 8
    assert public_schema_issues(raw) == expected
    error = DecisionProtocolError('invalid_decision_schema', schema_issues=raw)
    issue['path'] = 'private-mutation-marker'
    assert error.schema_issues == expected
    assert 'private-' not in repr(vars(error))
    json.dumps(error.schema_issues, allow_nan=False)


@pytest.mark.parametrize('path,expected', [
    ('decision.tool.version', 'decision.tool.version'),
    ('x' * 256, '*'), ('x' * 257, '$'), (None, '$'), (['decision'], '$'),
    ('\ud800', '*'), ('decision..text', 'decision.*.text'),
    ('C:/private-marker/file', '*'),
])
def test_public_schema_paths_use_only_closed_vocabulary(path, expected):
    assert public_schema_issues([None, {'path': path, 'type': {'private': 'marker'}}]) == [
        {'path': expected, 'type': 'validation_error'},
    ]


def test_parser_schema_issues_bound_many_extra_keys_and_mask_list_indices():
    value = {'decision': {'version': '1', 'action': 'finish', 'response_kind': 'scientific',
                          'text': 'proposal only', 'evidence_ids': [12]}}
    value['decision'].update({f'private-key-{i}': 'private-value' for i in range(20)})
    with pytest.raises(DecisionProtocolError) as caught:
        parse_decision_json(json.dumps(value))
    assert len(caught.value.schema_issues) == 8
    assert caught.value.schema_issues[0] == {
        'path': 'decision.finish.evidence_ids.*', 'type': 'string_type',
    }
    assert 'private-' not in repr(vars(caught.value))


def test_document_byte_limit_counts_utf8_and_accepts_exact_boundary():
    raw = json.dumps(tool_payload(arguments={'query': '分子🧪'}), ensure_ascii=False)
    padded = raw + ' ' * (MAX_DECISION_BYTES - len(raw.encode('utf-8')))
    assert parse_decision_json(padded).arguments == {'query': '分子🧪'}
    with pytest.raises(DecisionProtocolError, match='^decision_too_large$'):
        parse_decision_json(padded + ' ')


def test_decode_depth_boundary_and_transport_byte_override_remain_compatible():
    raw = '[' * MAX_DECISION_DEPTH + '0' + ']' * MAX_DECISION_DEPTH
    decode_protocol_json(raw)
    with pytest.raises(DecisionProtocolError, match='^decision_too_deep$'):
        decode_protocol_json('[' + raw + ']')
    assert decode_protocol_json('{}  ', max_bytes=4) == {}
    with pytest.raises(DecisionProtocolError, match='^decision_too_large$'):
        decode_protocol_json('{}   ', max_bytes=4)


def test_schema_return_is_detached_and_proposals_do_not_grant_privileges():
    schema = decision_json_schema()
    definitions = set(schema['$defs'])
    schema['$defs'].clear()
    assert set(decision_json_schema()['$defs']) == definitions
    proposal = parse_decision_json(json.dumps(tool_payload(tool_name='run_shell')))
    assert proposal.tool_name == 'run_shell'  # Shape only, not an authorization gate.
    assert not hasattr(proposal, 'execute')


@pytest.mark.parametrize('field,value', [
    ('version', True), ('purpose', '   '), ('tool_name', 'x' * 65),
    ('purpose', 'x' * 501),
])
def test_tool_field_bounds_are_strict(field, value):
    with pytest.raises(DecisionProtocolError, match='^invalid_decision_schema$'):
        parse_decision_json(json.dumps(tool_payload(**{field: value})))
