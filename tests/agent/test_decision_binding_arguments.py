"""Pure B reference syntax; no lookup, scientific input or execution authority."""
import importlib
import importlib.util
import inspect
import json
from collections.abc import Mapping
from itertools import combinations, permutations

import pytest


B1 = 'ordinary-semantic-b1-v1'
B2 = 'ordinary-semantic-b2-v1'
ERROR = 'invalid_binding_arguments'
MOLECULAR = ('property_calculator', 'drug_likeness_assessment',
             'activity_predictor', 'admet_predictor', 'reverse_target_predictor')
CASES = tuple((tool, 'MolecularBindingArguments', {'input_ref': 'obs-1'})
              for tool in MOLECULAR) + (
    ('rag_search', 'RagBindingArguments', {'input_ref': 'user'}),
    ('target_database_search', 'TargetBindingArguments', {'input_ref': 'user'}),
    ('llm_molecular_generator', 'GenerationBindingArguments', {'input_ref': 'user'}),
    ('candidate_ranker', 'RankingBindingArguments', {
        'input_ref': 'source-1', 'evidence_refs': {'properties': 'props-1'}}),
)


def bindings_module():
    name = 'src.agent.contracts.decision_bindings'
    assert importlib.util.find_spec(name) is not None, 'binding contract missing'
    return importlib.import_module(name)


def test_rag_user_reference():
    bindings = bindings_module()
    result = bindings.parse_binding_arguments(
        'rag_search', {'input_ref': 'user'},
        profile_revision='ordinary-semantic-b1-v1')
    assert result.model_dump(mode='json', exclude_none=True) == {'input_ref': 'user'}


def rejected(parser, tool, arguments, **kwargs):
    with pytest.raises(ValueError) as error:
        parser(tool, arguments, **kwargs)
    assert type(error.value) is ValueError
    assert str(error.value) == ERROR
    assert error.value.args == (ERROR,)
    assert error.value.__cause__ is None
    assert error.value.__suppress_context__


def test_exact_api_and_reexport_identity():
    bindings = bindings_module()
    from src.web import ordinary_capabilities as caps
    parameters = inspect.signature(bindings.parse_binding_arguments).parameters
    assert tuple(parameters) == ('tool_name', 'arguments', 'profile_revision')
    assert parameters['profile_revision'].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters['profile_revision'].default is None
    assert all(parameters[name].default is inspect.Parameter.empty
               for name in ('tool_name', 'arguments'))
    for name in ('B1_PROFILE_REVISION', 'B2_PROFILE_REVISION', 'TOOL_FEATURE_PAIRS',
                 'B1_TOOLS', 'B2_TOOLS'):
        assert getattr(caps, name) is getattr(bindings, name)
    assert bindings.B1_PROFILE_REVISION == B1
    assert bindings.B2_PROFILE_REVISION == B2
    assert bindings.B1_TOOLS == frozenset(MOLECULAR + ('rag_search', 'target_database_search'))
    assert bindings.B2_TOOLS == frozenset(tool for tool, _, _ in CASES)


@pytest.mark.parametrize('profile', (B1, B2))
@pytest.mark.parametrize('tool,class_name,arguments', CASES)
def test_closed_tools_and_return_types(profile, tool, class_name, arguments):
    bindings = bindings_module()
    if profile == B1 and tool in ('llm_molecular_generator', 'candidate_ranker'):
        rejected(bindings.parse_binding_arguments, tool, arguments, profile_revision=profile)
        return
    result = bindings.parse_binding_arguments(tool, arguments, profile_revision=profile)
    assert type(result) is getattr(bindings, class_name)
    assert result.model_dump(mode='json', exclude_none=True) == arguments
    assert type(result).model_config['strict'] is True
    assert type(result).model_config['frozen'] is True
    assert type(result).model_config['extra'] == 'forbid'


@pytest.mark.parametrize('tool', MOLECULAR)
def test_molecular_handles_are_not_chemistry_or_existence_proof(tool):
    parser = bindings_module().parse_binding_arguments
    for reference in ('user', 'CCO', 'missing-observation', 'A', 'a' * 128, 'A_0.b:c-d'):
        assert parser(tool, {'input_ref': reference}, profile_revision=B1).input_ref == reference


def test_target_and_generation_shapes():
    bindings = bindings_module()
    parser = bindings.parse_binding_arguments
    for arguments in ({'input_ref': 'user'}, {'input_ref': 'source-1', 'record_ref': 'record-1'},
                      {'input_ref': 'CCO', 'record_ref': 'CCO'}):
        result = parser('target_database_search', arguments, profile_revision=B2)
        assert result.model_dump(mode='json', exclude_none=True) == arguments
        assert result.record_ref == arguments.get('record_ref')
    for arguments in ({'input_ref': 'user'}, {'input_ref': 'user', 'target_ref': 'CCO'}):
        result = parser('llm_molecular_generator', arguments, profile_revision=B2)
        assert result.model_dump(mode='json', exclude_none=True) == arguments
        assert result.target_ref == arguments.get('target_ref')
    for tool, arguments in (
        ('rag_search', {'input_ref': 'obs-1'}),
        ('target_database_search', {'input_ref': 'obs-1'}),
        ('target_database_search', {'input_ref': 'user', 'record_ref': 'record-1'}),
        ('target_database_search', {'input_ref': 'obs-1', 'record_ref': 'user'}),
        ('target_database_search', {'input_ref': 'user', 'record_ref': None}),
        ('llm_molecular_generator', {'input_ref': 'obs-1'}),
        ('llm_molecular_generator', {'input_ref': 'user', 'target_ref': 'user'}),
        ('llm_molecular_generator', {'input_ref': 'user', 'target_ref': None}),
    ):
        rejected(parser, tool, arguments, profile_revision=B2)


def test_rank_roles_order_roundtrip_and_unique_sources():
    bindings = bindings_module()
    parser = bindings.parse_binding_arguments
    for optional in ((), ('admet',), ('activity',), ('admet', 'activity')):
        roles = {'properties': 'props-1', **{name: name + '-1' for name in optional}}
        for order in permutations(roles):
            arguments = {'input_ref': 'CCO', 'evidence_refs': {name: roles[name] for name in order}}
            result = parser('candidate_ranker', arguments, profile_revision=B2)
            assert type(result.evidence_refs) is bindings.RankingEvidenceReferences
            expected = {'input_ref': 'CCO', 'evidence_refs': roles}
            assert result.model_dump_json(exclude_none=True) == json.dumps(expected, separators=(',', ':'))
            assert parser('candidate_ranker', json.loads(result.model_dump_json(exclude_none=True)),
                          profile_revision=B2) == result
            for name in ('admet', 'activity'):
                assert getattr(result.evidence_refs, name) == roles.get(name)
    names = ('input_ref', 'properties', 'admet', 'activity')
    for first, second in combinations(names, 2):
        values = {name: name + '-1' for name in names}
        values[second] = values[first]
        rejected(parser, 'candidate_ranker', {'input_ref': values.pop('input_ref'),
                 'evidence_refs': values}, profile_revision=B2)


def test_missing_null_unknown_and_reserved_rank_roles():
    parser = bindings_module().parse_binding_arguments
    for roles in ({}, {'admet': 'admet-1'}, {'properties': None},
                  {'properties': 'props-1', 'admet': None},
                  {'properties': 'props-1', 'activity': None},
                  {'properties': 'props-1', 'unknown': 'obs-1'},
                  {'properties': 'props-1', 'scores': {}}, None, [], 'props-1'):
        rejected(parser, 'candidate_ranker', {'input_ref': 'source-1', 'evidence_refs': roles},
                 profile_revision=B2)
    for field in ('input_ref', 'properties', 'admet', 'activity'):
        arguments = {'input_ref': 'source-1', 'evidence_refs': {
            'properties': 'props-1', 'admet': 'admet-1', 'activity': 'activity-1'}}
        (arguments if field == 'input_ref' else arguments['evidence_refs'])[field] = 'user'
        rejected(parser, 'candidate_ranker', arguments, profile_revision=B2)
    rejected(parser, 'candidate_ranker', {'input_ref': 'source-1'}, profile_revision=B2)


@pytest.mark.parametrize('tool,class_name,arguments', CASES)
def test_every_tool_rejects_missing_input_and_extra_fields(tool, class_name, arguments):
    parser = bindings_module().parse_binding_arguments
    rejected(parser, tool, {key: value for key, value in arguments.items() if key != 'input_ref'},
             profile_revision=B2)
    for key in ('count', 'smiles', 'SMILES', 'query', 'k', 'path', 'weights', 'scores',
                'top_n', 'model', 'endpoint', 'unknown'):
        rejected(parser, tool, {**arguments, key: 'fixture'}, profile_revision=B2)
    allowed = set(arguments)
    if tool == 'target_database_search':
        allowed.add('record_ref')
    if tool == 'llm_molecular_generator':
        allowed.add('target_ref')
    for key in {'record_ref', 'target_ref', 'evidence_refs'} - allowed:
        rejected(parser, tool, {**arguments, key: 'obs-1'}, profile_revision=B2)


@pytest.mark.parametrize('slot', ('molecular', 'rag', 'target_input', 'record',
                                  'generation_input', 'target', 'rank_input',
                                  'properties', 'admet', 'activity'))
def test_every_reference_is_strict_and_fullmatched(slot):
    parser = bindings_module().parse_binding_arguments
    for value in ('', 'a' * 129, ' a', 'a ', 'a\nb', 'a\n', 'a\r\n', 'a\t',
                  'https://example.invalid/x', 'a/b', 'a\\b', '../x', 'C:/x',
                  '${obs}', 'C(C)O', '界', '_x', True, 1, 1.5, b'obs-1', [], (), {}, None):
        tool, arguments = 'property_calculator', {'input_ref': value}
        if slot == 'rag':
            tool = 'rag_search'
        elif slot in ('target_input', 'record'):
            tool = 'target_database_search'
            arguments = {'input_ref': 'source-1', 'record_ref': 'record-1'}
            arguments['input_ref' if slot == 'target_input' else 'record_ref'] = value
        elif slot in ('generation_input', 'target'):
            tool = 'llm_molecular_generator'
            arguments = {'input_ref': 'user', 'target_ref': 'target-1'}
            arguments['input_ref' if slot == 'generation_input' else 'target_ref'] = value
        elif slot in ('rank_input', 'properties', 'admet', 'activity'):
            tool = 'candidate_ranker'
            arguments = {'input_ref': 'source-1', 'evidence_refs': {'properties': 'props-1'}}
            (arguments if slot == 'rank_input' else arguments['evidence_refs'])[
                'input_ref' if slot == 'rank_input' else slot] = value
        rejected(parser, tool, arguments, profile_revision=B2)


def test_profile_tool_and_top_level_values_are_closed():
    parser = bindings_module().parse_binding_arguments
    rejected(parser, 'rag_search', {'input_ref': 'user'})
    for profile in (None, '', 'ordinary-semantic-original-four-v1', 'unknown',
                    'x' * 65, B1 + '\n', True, b'ordinary-semantic-b1-v1', [], {}):
        rejected(parser, 'rag_search', {'input_ref': 'user'}, profile_revision=profile)
    for tool in (None, '', 'unknown', 'x' * 65, 'rag_search\n', 'RAG_SEARCH',
                 'rag_retrieval', 'admet_prediction', 'reverse_target', 'molecule_generation',
                 'molecule_ranking', 'molecular_docking', 'alias_property_calculator',
                 'prepare_ligand', True, b'rag_search', [], {}):
        rejected(parser, tool, {'input_ref': 'user'}, profile_revision=B2)
    for value in (None, True, 1, b'{}', '{}', [], (),
                  parser('rag_search', {'input_ref': 'user'}, profile_revision=B1)):
        rejected(parser, 'rag_search', value, profile_revision=B1)
    with pytest.raises(TypeError):
        parser(profile_revision=B1)


def test_non_native_objects_never_invoke_hooks():
    parser = bindings_module().parse_binding_arguments
    calls = []

    def hook(*args, **kwargs):
        calls.append('hook')
        raise AssertionError('custom hook must not run')

    class HostileDict(dict):
        __iter__ = __len__ = __getitem__ = items = keys = values = get = hook

    class HostileList(list):
        __iter__ = __len__ = __getitem__ = hook

    class HostileString(str):
        __hash__ = __eq__ = __len__ = __str__ = encode = hook

    class HostileMapping(Mapping):
        __iter__ = __len__ = __getitem__ = hook

    class HostileObject:
        __str__ = __repr__ = __iter__ = __bool__ = model_dump = __deepcopy__ = hook

    for value in (HostileDict(input_ref='user'), HostileMapping(), HostileObject()):
        rejected(parser, 'rag_search', value, profile_revision=B1)
    for value in (HostileDict(properties='props-1'), HostileList(), HostileString('obs-1'),
                  HostileMapping(), HostileObject()):
        rejected(parser, 'property_calculator', {'input_ref': value}, profile_revision=B1)
        rejected(parser, 'candidate_ranker', {'input_ref': 'source-1', 'evidence_refs': value},
                 profile_revision=B2)
    for tool, profile in ((HostileString('rag_search'), B1), ('rag_search', HostileString(B1))):
        rejected(parser, tool, {'input_ref': 'user'}, profile_revision=profile)
    assert calls == []


def boundary_spies(monkeypatch, bindings):
    # Warm harness imports, then wrap the real preflight and secret detector.
    from src.agent.harness import decision_bounds
    from src.agent.persistence import redaction
    events = []
    original_bounds = decision_bounds.validate_json
    original_secret = redaction.contains_secret_material
    original_dto = bindings.MolecularBindingArguments.model_validate

    def bounds(value, **kwargs):
        events.append('bounds')
        assert kwargs == dict(max_bytes=4096, max_depth=8, max_nodes=64, reason=ERROR)
        original_bounds(value, **kwargs)
        events.append('bounded')

    def secret(value):
        # The real detector recurses via its module name; observe only its root.
        if type(value) is dict:
            events.append('secret')
        return original_secret(value)

    def dto(cls, value, **kwargs):
        events.append('dto')
        assert kwargs == {'strict': True}
        return original_dto(value, **kwargs)

    monkeypatch.setattr(decision_bounds, 'validate_json', bounds)
    monkeypatch.setattr(redaction, 'contains_secret_material', secret)
    monkeypatch.setattr(bindings.MolecularBindingArguments, 'model_validate', classmethod(dto))
    return events


@pytest.mark.parametrize('bound', ('bytes', 'multibyte', 'depth', 'nodes'))
def test_exact_bounds_before_secret_scan_and_dto_even_with_unknown_field(bound, monkeypatch):
    bindings = bindings_module()
    events = boundary_spies(monkeypatch, bindings)
    parser = bindings.parse_binding_arguments
    parser('property_calculator', {'input_ref': 'obs-1'}, profile_revision=B1)
    assert events == ['bounds', 'bounded', 'secret', 'dto']

    def arguments(over):
        result = {'input_ref': 'obs-1', 'unknown': ''}
        if bound in ('bytes', 'multibyte'):
            padding = 4096 - len(json.dumps(result, ensure_ascii=False).encode('utf-8'))
            text = 'a' * padding if bound == 'bytes' else '界' * (padding // 3) + 'a' * (padding % 3)
            result['unknown'] = text + ('a' if over else '')
            assert len(json.dumps(result, ensure_ascii=False).encode('utf-8')) == 4096 + over
        elif bound == 'depth':
            child = 0
            for _ in range(7 + over):
                child = [child]
            result['unknown'] = child  # root depth0, deepest scalar depth8/9
        else:
            result['unknown'] = [0] * (59 + over)  # root + four key/value nodes + 59/60
        return result

    events.clear()
    rejected(parser, 'property_calculator', arguments(False), profile_revision=B1)
    assert events[:3] == ['bounds', 'bounded', 'secret']  # bound itself is inclusive
    events.clear()
    rejected(parser, 'property_calculator', arguments(True), profile_revision=B1)
    assert events == ['bounds']  # NOT merely unknown-field/DTO rejection


@pytest.mark.parametrize('fault', ('cycle', 'nan', 'infinity', 'negative_infinity',
                                  'surrogate', 'huge_integer', 'nonstring_key'))
def test_invalid_json_stops_before_secret_scan_and_dto(fault, monkeypatch):
    bindings = bindings_module()
    events = boundary_spies(monkeypatch, bindings)
    arguments = {'input_ref': 'obs-1'}
    if fault == 'cycle':
        arguments['unknown'] = arguments
    elif fault == 'nonstring_key':
        arguments[1] = 'fixture'
    else:
        arguments['unknown'] = {'nan': float('nan'), 'infinity': float('inf'),
            'negative_infinity': -float('inf'), 'surrogate': '\ud800',
            'huge_integer': 1 << 4097}[fault]
    rejected(bindings.parse_binding_arguments, 'property_calculator', arguments, profile_revision=B1)
    assert events == ['bounds']


def test_secret_detector_runs_after_bounds_before_dto(monkeypatch, caplog, recwarn):
    bindings = bindings_module()
    events = boundary_spies(monkeypatch, bindings)
    for arguments in ({'input_ref': 'sk-' + 'syntheticfixture'},
                      {'input_ref': 'password=synthetic'},
                      {'input_ref': 'obs-1', 'api_key': 'synthetic'}):
        events.clear()
        rejected(bindings.parse_binding_arguments, 'property_calculator', arguments, profile_revision=B1)
        assert events == ['bounds', 'bounded', 'secret']
    assert not recwarn.list
    assert not caplog.records


def test_actual_outer_decoder_composes_and_rejects_duplicates_first(monkeypatch):
    bindings = bindings_module()
    from src.agent.contracts.decision import decode_protocol_json, DecisionEnvelope, DecisionProtocolError
    calls = []
    original = bindings.parse_binding_arguments

    def parser(*args, **kwargs):
        calls.append('parser')
        return original(*args, **kwargs)

    monkeypatch.setattr(bindings, 'parse_binding_arguments', parser)

    def compose(raw):
        decision = DecisionEnvelope.model_validate(decode_protocol_json(raw), strict=True).decision
        return bindings.parse_binding_arguments(decision.tool_name, decision.arguments, profile_revision=B2)

    raw = '{"decision":{"version":"1","action":"tool","tool_name":"rag_search",' \
          '"arguments":{"input_ref":"user"},"purpose":"fixture"}}'
    assert compose(raw).input_ref == 'user'
    assert calls == ['parser']
    for duplicate in (raw.replace('"input_ref":"user"', '"input_ref":"user","input_ref":"user"'),
                      raw.replace('"version":"1"', '"version":"1","version":"1"'),
                      raw.replace('{"input_ref":"user"}', '{"input_ref":"source-1",' \
                          '"evidence_refs":{"properties":"p-1","properties":"p-2"}}')):
        calls.clear()
        with pytest.raises(DecisionProtocolError, match='^duplicate_json_key$'):
            compose(duplicate)
        assert calls == []


def test_legacy_v1_requirements_still_reject_v2():
    bindings_module()
    from src.agent.contracts.task_requirements import parse_task_requirements
    assert parse_task_requirements({'version': '1'}).version == '1'
    with pytest.raises(ValueError, match='^invalid_task_requirements$'):
        parse_task_requirements({'version': '2'})


def test_detached_frozen_values_and_pure_parser(monkeypatch):
    bindings = bindings_module()
    import builtins
    import io
    import os
    import socket
    from src.agent.tooling.registry import ToolRegistry
    parser = bindings.parse_binding_arguments
    arguments = {'input_ref': 'source-1', 'evidence_refs': {'properties': 'props-1'}}
    result = parser('candidate_ranker', arguments, profile_revision=B2)  # warm lazy imports
    arguments['input_ref'] = 'changed'
    arguments['evidence_refs']['properties'] = 'changed'
    snapshot = result.model_dump(mode='json', exclude_none=True)
    snapshot['evidence_refs']['properties'] = 'changed'
    assert result.input_ref == 'source-1' and result.evidence_refs.properties == 'props-1'
    for obj, field in ((result, 'input_ref'), (result.evidence_refs, 'properties')):
        with pytest.raises(ValueError):
            setattr(obj, field, 'changed')
    calls = []

    def forbidden(*args, **kwargs):
        calls.append('io')
        raise AssertionError('pure parser invoked I/O, registry or health')

    class NoEnvironment:
        get = __getitem__ = __iter__ = __contains__ = forbidden

    with monkeypatch.context() as guard:
        for owner, names in ((builtins, ('open',)), (io, ('open',)),
            (os, ('getenv', 'stat', 'listdir', 'scandir')),
            (socket, ('create_connection', 'getaddrinfo')),
            (socket.socket, ('connect', 'connect_ex')),
            (ToolRegistry, ('__init__', 'as_mapping', 'resolve', 'by_capability',
                            'resolve_capability', 'health'))):
            for name in names:
                guard.setattr(owner, name, forbidden)
        guard.setattr(os, 'environ', NoEnvironment())
        for tool, _, value in CASES:
            assert parser(tool, value, profile_revision=B2).model_dump(mode='json', exclude_none=True) == value
    assert calls == []
