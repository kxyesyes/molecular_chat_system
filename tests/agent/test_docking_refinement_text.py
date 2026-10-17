"""Pure D6 lexical/proposal checks; no refs, chemistry or execution authority."""
from copy import deepcopy
import hashlib
import json

import pytest

from src.agent.contracts.docking_preparation import DockingPreparationProposal
from src.web import docking_request as api


VALUES = {'receptor_ref': 'receptor-1', 'ligand_ref': 'ligand_2',
          'center': '[-1,2.5,0]', 'size': '[10,20,30]',
          'exhaustiveness': '4', 'num_modes': '3'}
TEXT = ' \t' + '； '.join(f'{k} = {v}' for k, v in VALUES.items()) + '\r\n'
REJECTED = dict(consistent=False, reason_code='docking_proposal_clarification_required',
                input_ready=False, execution_authorized=False)


def assess(text, **flags):
    call = getattr(api, 'assess_docking_refinement_text', None)
    assert callable(call), 'D6 refinement text assessment is missing'
    return call(text, **dict(dict(enabled=True, enable_tools=True), **flags))


def validate(text, proposal, index=1, **flags):
    call = getattr(api, 'validate_docking_refinement_proposal', None)
    assert callable(call), 'D6 refinement proposal validation is missing'
    return call(text, proposal, text_segment_index=index,
                **dict(dict(enabled=True, enable_tools=True), **flags))


def wire(text=TEXT, values=None, index=1):
    values = VALUES if values is None else values
    spans = [dict(source_id=f'refinement-{index}', start=0, end=len(text), role='request')]
    fields = {}
    for name, raw in values.items():
        start = text.index(raw, text.index(name) + len(name))
        fields[name] = dict(value=raw if name.endswith('_ref') else json.loads(raw),
                            span_indices=[len(spans)])
        spans.append(dict(source_id=f'refinement-{index}', start=start,
                          end=start + len(raw), role='field'))
    return dict(version='1', kind='docking_refinement', unresolved=False,
                source_spans=spans, fields=fields)


def dto(value=None):
    return DockingPreparationProposal.model_validate(wire() if value is None else value)


def test_all_fields_have_exact_unicode_codepoint_spans_and_no_authority():
    result = assess(TEXT)
    assert result['assessed'] is True
    assert result['fields'] == {k: v if k.endswith('_ref') else json.loads(v)
                                for k, v in VALUES.items()}
    for name, raw in VALUES.items():
        start, end = result['field_spans'][name]
        assert TEXT[start:end] == raw
        assert start == TEXT.index(raw, TEXT.index(name) + len(name))
    assert result['input_ready'] is False and result['execution_authorized'] is False


@pytest.mark.parametrize('name,raw', list(VALUES.items()))
def test_single_explicit_field_never_fills_defaults(name, raw):
    result = assess(f'\r\n {name}\t =\n {raw} \t')
    assert result['assessed'] is True
    assert set(result['fields']) == set(result['field_spans']) == {name}
    assert result['input_ready'] is False and result['execution_authorized'] is False


@pytest.mark.parametrize('text', [
    '', ' ', None, True, b'num_modes=3', '请做一次对接', 'run docking;num_modes=3',
    '不要num_modes=3', 'num_modes=3?', 'num_modes=3？', 'num_modes=3;predict toxicity',
    'num_modes=3;使用共晶配体定义口袋', 'num_modes=3;unknown=1',
    'num_modes=3;num_modes=3', 'num_modes=3;', 'num_modes=3； \r\n',
    ';num_modes=3', 'num_modes=3;;size=[1,2,3]', 'num_modes=3。', 'num_modes=3.',
    'num_modes=3,size=[1,2,3]', 'num_modes=3\nsize=[1,2,3]',
    'NUM_MODES=3', 'num_modes＝3', 'num_modes=３', 'num_modes==3',
    'ligand_ref=CC(=O)O', 'center=[0,0,0]; approve=true',
    '\ud800', 'num_modes=3\udfff',
], ids=lambda v: hashlib.sha256(str(v).encode('utf-8', 'backslashreplace')).hexdigest()[:10])
def test_closed_language_rejects_whole_unsupported_text(text):
    result = assess(text)
    assert result['assessed'] is False
    assert 'fields' not in result and 'field_spans' not in result
    assert result['input_ready'] is False and result['execution_authorized'] is False


@pytest.mark.parametrize('char', ['\x00', '\x08', '\x0b', '\x0c', '\x1f', '\x7f',
    '\x85', '\xa0', '\u200b', '\u200d', '\u2028', '\u202e', '\u2060', '\ufeff'])
@pytest.mark.parametrize('position', ['prefix', 'inside', 'suffix'])
def test_controls_and_invisible_text_are_not_normalized(char, position):
    text = {'prefix': char + 'num_modes=3', 'inside': 'num_modes=' + char + '3',
            'suffix': 'num_modes=3' + char}[position]
    assert assess(text)['assessed'] is False


@pytest.mark.parametrize('enabled,tools', [(False, True), (True, False), (1, True),
                                         (True, 1), (None, True), (True, 'true')])
def test_flags_are_native_true_for_both_boundaries(enabled, tools):
    flags = dict(enabled=enabled, enable_tools=tools)
    assert assess(TEXT, **flags)['assessed'] is False
    assert validate(TEXT, dto(), **flags) == REJECTED


@pytest.mark.parametrize('name', ['receptor_ref', 'ligand_ref'])
@pytest.mark.parametrize('raw', ['../private.pdb', '/tmp/a.pdb', 'C:\\private\\a.pdb',
    '\\\\server\\a.pdb', 'file:///tmp/a', 'https://example.invalid/a', 'a.pdb',
    'sha256:' + 'a' * 64, 'sk-' + 'a' * 32, 'ghp_' + 'a' * 36, '', 'a' * 129])
def test_references_reject_paths_secrets_and_oversize_without_resolving(name, raw):
    assert assess(f'{name}={raw}')['assessed'] is False


@pytest.mark.parametrize('name', ['receptor_ref', 'ligand_ref'])
def test_reference_length_boundary_is_lexical_only(name):
    assert assess(f'{name}=' + 'r' * 128)['fields'] == {name: 'r' * 128}


@pytest.mark.parametrize('text', ['center=[1e309,0,0]', 'center=[NaN,0,0]',
    'center=[true,0,0]', 'center=[0,0]', 'center=[0,0,0,0]', 'center=[+1,0,0]',
    'center=[[0],0,0]', 'size=[0,1,1]', 'size=[-1,1,1]', 'size=[100.01,1,1]',
    'size=[1,1,Infinity]', 'exhaustiveness=0', 'exhaustiveness=65', 'num_modes=0',
    'num_modes=51', 'num_modes=3.0', 'num_modes=03', 'num_modes=+3',
    'num_modes=-3', 'num_modes=3e0', 'num_modes=true', 'center=' + '[' * 1100])
def test_numeric_bounds_and_grammar(text):
    assert assess(text)['assessed'] is False


@pytest.mark.parametrize('text', ['center=[-1e308,0,1e308]', 'size=[1e-3,100,1]',
    'exhaustiveness=1', 'exhaustiveness=64', 'num_modes=1', 'num_modes=50'])
def test_numeric_inclusive_valid_boundaries(text):
    assert assess(text)['assessed'] is True


def test_numeric_token_limit_and_whole_text_limit_no_truncation():
    raw = '[0,' + ' ' * 249 + '0,0]'
    assert len(raw) == 256
    assert assess('center=' + raw)['assessed'] is True
    assert assess('center=' + raw.replace(' ', '  ', 1))['assessed'] is False
    text = 'num_modes=3'
    boundary = ' ' * (8192 - len(text)) + text
    assert assess(boundary)['assessed'] is True
    assert assess(boundary + ' ')['assessed'] is False
    assert assess(boundary + ';predict toxicity')['assessed'] is False
    # UTF-8 maximum is redundant under the 8192 codepoint bound, but must
    # remain fail-closed for both exact/over-byte-limit unsupported Unicode.
    assert len(('🧪' * 8192).encode()) == 32768
    assert assess('🧪' * 8192)['assessed'] is False
    assert assess('🧪' * 8193)['assessed'] is False


@pytest.mark.parametrize('index', [1, 2, 3])
@pytest.mark.parametrize('unresolved', [False, True])
def test_exact_refinement_dto_preserves_unresolved_without_authority(index, unresolved):
    value = wire(index=index)
    value['unresolved'] = unresolved
    result = validate(TEXT, dto(value), index)
    assert result == dict(consistent=True, assessment=assess(TEXT),
                         model_unresolved=unresolved, input_ready=False, execution_authorized=False)


@pytest.mark.parametrize('index', [0, 4, -1, True, 1.0, '1', None])
def test_segment_index_is_native_integer_one_through_three(index):
    assert validate(TEXT, dto(), index) == REJECTED


@pytest.mark.parametrize('fault', ['kind', 'source_index', 'query', 'history', 'other',
    'field_source', 'omit', 'value', 'partial_request', 'request_offset', 'field_bounds',
    'byte_offsets', 'missing_request', 'duplicate_request', 'unused', 'extra_obligation',
    'request_as_field', 'split', 'shared_index'])
def test_proposal_has_exactly_one_whole_request_and_each_explicit_field(fault):
    value = wire()
    spans, fields = value['source_spans'], value['fields']
    if fault == 'kind': value['kind'] = 'docking_request'
    elif fault in {'source_index', 'query', 'history', 'other'}:
        spans[0]['source_id'] = {'source_index': 'refinement-2', 'query': 'query',
                                 'history': 'history-0', 'other': 'observation-1'}[fault]
    elif fault == 'field_source': spans[1]['source_id'] = 'query'
    elif fault == 'omit': del fields['num_modes']
    elif fault == 'value': fields['num_modes']['value'] = 4
    elif fault == 'partial_request': spans[0]['end'] -= 1
    elif fault == 'request_offset': spans[0]['start'] = 1
    elif fault == 'field_bounds': spans[1]['end'] += 1
    elif fault == 'byte_offsets':
        spans[2]['start'] = len(TEXT[:spans[2]['start']].encode())
        spans[2]['end'] = len(TEXT[:spans[2]['end']].encode())
    elif fault == 'missing_request': spans[0]['role'] = 'field'
    elif fault == 'duplicate_request': spans.append(deepcopy(spans[0]))
    elif fault in {'unused', 'extra_obligation'}:
        spans.append(dict(source_id='refinement-1', start=0, end=1,
                          role='field' if fault == 'unused' else 'extra_obligation'))
    elif fault == 'request_as_field': fields['receptor_ref']['span_indices'] = [0]
    elif fault == 'split': fields['center']['span_indices'] = [1, 3]
    elif fault == 'shared_index': fields['ligand_ref']['span_indices'] = [1]
    assert validate(TEXT, dto(value)) == REJECTED


def test_added_field_and_whole_text_extra_clause_cannot_be_hidden():
    text = 'num_modes=3'
    value = wire(text, {'num_modes': '3'})
    value['fields']['exhaustiveness'] = dict(value=3, span_indices=[1])
    assert validate(text, dto(value)) == REJECTED
    value = wire(text, {'num_modes': '3'})
    text += ';predict toxicity'
    value['source_spans'][0]['end'] = len(text)
    assert validate(text, dto(value)) == REJECTED


@pytest.mark.parametrize('part', ['proposal', 'text', 'index', 'version', 'kind', 'unresolved',
    'spans', 'span', 'source', 'start', 'end', 'role', 'fields', 'names', 'name',
    'field', 'indices', 'index_value', 'ref_value', 'vector', 'number', 'integer'])
def test_native_type_defenses_reject_subclasses_without_invoking_hooks(part):
    hooks = []
    class Meta(type):
        def __eq__(cls, other): hooks.append('type_eq'); return True
        __hash__ = type.__hash__
    class Trap(metaclass=Meta):
        def __len__(self): hooks.append('len'); return 1
        def __iter__(self): hooks.append('iter'); return iter(())
        def __getattr__(self, name): hooks.append('getattr'); raise AttributeError(name)
        def __eq__(self, other): hooks.append('eq'); return True
        def __hash__(self): return 1
    class String(str):
        def encode(self, *args): hooks.append('encode'); return b''
    class Integer(int):
        def __eq__(self, other): hooks.append('int_eq'); return True
    class List(list):
        def __iter__(self): hooks.append('list_iter'); return super().__iter__()
    p, text, index = dto(), TEXT, 1
    trap = Trap()
    if part == 'proposal': p = trap
    elif part == 'text': text = String(TEXT)
    elif part == 'index': index = Integer(1)
    elif part in {'version', 'kind'}: object.__setattr__(p, part, String(getattr(p, part)))
    elif part == 'unresolved': object.__setattr__(p, part, 1)
    elif part == 'spans': object.__setattr__(p, 'source_spans', List(p.source_spans))
    elif part == 'span': p.source_spans[0] = trap
    elif part in {'source', 'start', 'end', 'role'}:
        key = 'source_id' if part == 'source' else part
        current = getattr(p.source_spans[0], key)
        object.__setattr__(p.source_spans[0], key, String(current) if type(current) is str else Integer(current))
    elif part == 'fields': object.__setattr__(p, 'fields', trap)
    elif part == 'names': object.__setattr__(p.fields, '__pydantic_fields_set__', trap)
    elif part == 'name': object.__setattr__(p.fields, '__pydantic_fields_set__', {trap})
    elif part == 'field': object.__setattr__(p.fields, 'receptor_ref', trap)
    elif part == 'indices': object.__setattr__(p.fields.receptor_ref, 'span_indices', List([1]))
    elif part == 'index_value': p.fields.receptor_ref.span_indices[0] = True
    elif part == 'ref_value': object.__setattr__(p.fields.receptor_ref, 'value', String('receptor-1'))
    elif part == 'vector': object.__setattr__(p.fields.center, 'value', List([-1, 2.5, 0]))
    elif part == 'number': p.fields.center.value[0] = trap
    elif part == 'integer': object.__setattr__(p.fields.num_modes, 'value', Integer(3))
    assert validate(text, p, index) == REJECTED
    assert hooks == []


@pytest.mark.parametrize('part', ['proposal', 'fields', 'span', 'field'])
def test_actual_dto_subclasses_are_not_native_dtos(part):
    p = dto()
    target = {'proposal': p, 'fields': p.fields, 'span': p.source_spans[0],
              'field': p.fields.receptor_ref}[part]
    subclass = type('Subclass', (type(target),), {})
    replacement = subclass.model_construct(**target.__dict__)
    if part == 'proposal': p = replacement
    elif part == 'fields': object.__setattr__(p, 'fields', replacement)
    elif part == 'span': p.source_spans[0] = replacement
    else: object.__setattr__(p.fields, 'receptor_ref', replacement)
    assert validate(TEXT, p) == REJECTED


@pytest.mark.parametrize('fault', ['nan', 'infinity', 'bool_number', 'negative_span',
    'out_of_range_index', 'empty_indices', 'tuple_indices', 'missing_kind', 'wrong_version'])
def test_mutated_native_dto_revalidates_values_and_bounds(fault):
    p = dto()
    if fault in {'nan', 'infinity', 'bool_number'}:
        p.fields.center.value[0] = {'nan': float('nan'), 'infinity': float('inf'), 'bool_number': True}[fault]
    elif fault == 'negative_span': object.__setattr__(p.source_spans[0], 'start', -1)
    elif fault == 'out_of_range_index': p.fields.center.span_indices[0] = 999
    elif fault == 'empty_indices': p.fields.center.span_indices.clear()
    elif fault == 'tuple_indices': object.__setattr__(p.fields.center, 'span_indices', (3,))
    elif fault == 'missing_kind': object.__delattr__(p, 'kind')
    elif fault == 'wrong_version': object.__setattr__(p, 'version', '2')
    assert validate(TEXT, p) == REJECTED


def test_plain_dict_is_not_a_proposal_and_assessment_rejects_string_subclass():
    class String(str): pass
    assert assess(String('num_modes=3'))['assessed'] is False
    assert validate(TEXT, wire()) == REJECTED


@pytest.mark.parametrize('hidden', ['exhaustiveness', 'unknown'])
def test_mutated_field_storage_cannot_hide_an_added_field(hidden):
    text = 'num_modes=3'
    p = dto(wire(text, {'num_modes': '3'}))
    object.__setattr__(p.fields, hidden, dto().fields.exhaustiveness)
    assert p.fields.model_fields_set == {'num_modes'}
    assert validate(text, p) == REJECTED


def review_case(mode):
    text = 'num_modes=3' if mode == 'refinement' else 'run docking;num_modes=3'
    value = wire(text, {'num_modes': '3'})
    if mode == 'initial':
        value['kind'] = 'docking_request'
        for span in value['source_spans']:
            span['source_id'] = 'query'
    return text, dto(value)


def review_validate(mode, text, proposal):
    if mode == 'initial':
        return api.validate_docking_text_proposal(text, proposal, enabled=True, enable_tools=True)
    return validate(text, proposal)


def review_target(proposal, level):
    return {'proposal': proposal, 'fields': proposal.fields,
            'request_span': proposal.source_spans[0], 'field_span': proposal.source_spans[1],
            'field_input': proposal.fields.num_modes}[level]


@pytest.mark.parametrize('mode', ['initial', 'refinement'])
@pytest.mark.parametrize('level', ['proposal', 'request_span', 'field_span', 'field_input'])
def test_review_extra_obligation_in_each_dto_storage_rejects(mode, level):
    text, p = review_case(mode)
    assert review_validate(mode, text, p)['consistent'] is True
    target = review_target(p, level)
    object.__setattr__(target, 'extra_obligation', 'predict toxicity')
    assert 'extra_obligation' not in target.model_fields_set
    assert review_validate(mode, text, p) == REJECTED


@pytest.mark.parametrize('hidden', ['exhaustiveness', 'unknown'])
def test_review_initial_hidden_fields_reject_without_changing_valid_defaults(hidden):
    text, p = review_case('initial')
    before = review_validate('initial', text, p)
    assert before['consistent'] is True and before['assessment']['fields'] == {'num_modes': 3}
    injected = dto().fields.exhaustiveness
    object.__setattr__(injected, 'value', 64)
    object.__setattr__(p.fields, hidden, injected)
    assert p.fields.model_fields_set == {'num_modes'}
    assert review_validate('initial', text, p) == REJECTED


@pytest.mark.parametrize('mode', ['initial', 'refinement'])
@pytest.mark.parametrize('level', ['proposal', 'fields', 'request_span', 'field_span', 'field_input'])
@pytest.mark.parametrize('fault', ['extra_slot', 'private_slot', 'non_native_storage',
    'non_native_names', 'non_native_key', 'unknown_name', 'oversize_storage', 'hidden_trap'])
def test_review_closed_storage_is_bounded_native_and_never_serializes(mode, level, fault, monkeypatch):
    text, p = review_case(mode)
    target = review_target(p, level)
    hooks = []
    class Trap:
        def __repr__(self): hooks.append('repr'); return 'trap'
        def __iter__(self): hooks.append('iter'); return iter(())
        def __eq__(self, other): hooks.append('eq'); return True
    class Storage(dict):
        def __iter__(self): hooks.append('storage_iter'); return super().__iter__()
        def __len__(self): hooks.append('storage_len'); return super().__len__()
    class Names(set):
        def __iter__(self): hooks.append('names_iter'); return super().__iter__()
    class Key(str):
        def __eq__(self, other): hooks.append('key_eq'); return super().__eq__(other)
        __hash__ = str.__hash__
    def no_serialization(*args, **kwargs):
        pytest.fail('untrusted DTO must not be serialized or revalidated')
    for model in {type(review_target(p, name)) for name in
                  ('proposal', 'fields', 'request_span', 'field_input')}:
        monkeypatch.setattr(model, 'model_dump', no_serialization)
        monkeypatch.setattr(model, 'model_dump_json', no_serialization)
        monkeypatch.setattr(model, 'model_validate', no_serialization)
    if fault == 'extra_slot': object.__setattr__(target, '__pydantic_extra__', {'extra_obligation': Trap()})
    elif fault == 'private_slot': object.__setattr__(target, '__pydantic_private__', {'extra_obligation': Trap()})
    elif fault == 'non_native_storage': object.__setattr__(target, '__dict__', Storage(target.__dict__))
    elif fault == 'non_native_names': object.__setattr__(target, '__pydantic_fields_set__', Names(target.model_fields_set))
    elif fault == 'non_native_key':
        storage = target.__dict__.copy()
        name = next(iter(storage))
        item = storage.pop(name)
        storage[Key(name)] = item
        object.__setattr__(target, '__dict__', storage)
    elif fault == 'unknown_name': target.model_fields_set.add('extra_obligation')
    elif fault == 'oversize_storage': target.__dict__.update({f'extra_{i}': Trap() for i in range(1000)})
    elif fault == 'hidden_trap': object.__setattr__(target, 'extra_obligation', Trap())
    hooks.clear()
    assert review_validate(mode, text, p) == REJECTED
    assert hooks == []
