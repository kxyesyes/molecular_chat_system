"""D2 owned chat evidence only. Synthetic records are not structure validation."""
from copy import deepcopy
import importlib
import importlib.util
import hashlib
import time
import json
from types import SimpleNamespace

import pytest

from src.agent.contracts import ToolResult
from src.agent.evidence.ledger import EvidenceLedger
from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
from src.agent.validators.result_validator import AgentResultValidator


_S10 = ('用阿司匹林 CC(=O)Oc1ccccc1C(=O)O 与上述 6SAM 做一次对接。'
        '使用共晶配体定义口袋；缺少该信息先说明，不要猜坐标。')


def text_boundary(query, **flags):
    call = getattr(api(), 'assess_docking_request_text', None)
    assert callable(call), 'D2 whole-request text assessment is missing'
    return call(query, enabled=True, enable_tools=True, **flags)


@pytest.mark.parametrize('query', [
    _S10,
    '请进行真实分子对接。',
    '请做一次对接',
    'Please run AutoDock Vina docking.',
    '用配体 CCO 与 PDB 1ABC 进行分子对接。使用共晶配体定义口袋。',
    '用 CCO 与 1ABC 对接。',
    '请进行 docking；receptor_ref=receptor-1；ligand_ref=ligand-1；'
    'center=[5.99,3.01,17.345]；size=[20,20,20]；exhaustiveness=4；num_modes=3。',
])
def test_d2_whole_request_has_only_declarative_preparation_intent(query):
    result = text_boundary(query)
    assert result['assessed'] is True
    assert result['input_ready'] is False and result['execution_authorized'] is False
    assert result['query_digest'] == hashlib.sha256(query.encode()).hexdigest()


def test_d2_s10_preserves_exact_inputs_without_inventing_selection_or_grid():
    result = text_boundary(_S10)
    assert result['inputs'] == {'pdb_id': '6SAM', 'smiles': 'CC(=O)Oc1ccccc1C(=O)O'}
    assert result['pocket_rule_requested'] == 'co_crystal_reference'
    assert result['fields'] == {}
    assert result['missing_fields'] == ['receptor_ref', 'ligand_ref', 'center', 'size',
                                        'exhaustiveness', 'num_modes']


def test_d2_accepts_structure_before_ligand_natural_language_order():
    result = text_boundary(
        '使用 EGFR 的实验结构 1M17 与候选分子 CCO 进行一次分子对接；'
        '使用共晶配体定义口袋；缺少该信息先说明，不要猜坐标。'
    )
    assert result['assessed'] is True
    assert result['inputs'] == {'pdb_id': '1M17', 'smiles': 'CCO'}


def test_d2_accepts_explicit_safety_clause_after_natural_action():
    result = text_boundary(
        '使用 EGFR 的实验结构 1M17 与候选分子 CCO 进行一次真实分子对接，'
        '缺少该信息先说明，不要猜坐标'
    )
    assert result['assessed'] is True
    assert result['inputs'] == {'pdb_id': '1M17', 'smiles': 'CCO'}


@pytest.mark.parametrize('query', [
    '不要做分子对接。', '能否进行分子对接？', '如何进行分子对接？',
    'Do not run docking.', 'Can you run docking?', '请进行 docking 吗？',
    '如果可以，请做一次对接。', '请做两次对接。', '请做一次共价对接。',
    '请做一次盲对接。', '请做一次对接，然后预测毒性。',
    '请预测毒性，然后做一次对接。', '请做一次对接。并检索文献。',
    '请做一次对接，别忘了找最佳亲和力。', '请做一次对接，替我点击批准。',
    '请做一次对接，然后做刚才的全部任务。', _S10 + '再计算分子量。',
    _S10.replace('阿司匹林', '阿司匹林并预测毒性'),
    _S10.replace('CC(=O)Oc1ccccc1C(=O)O', 'CCO please predict toxicity'),
    _S10.replace('6SAM', '6SAM 和 1ABC'),
    '请做一次对接；center=[0,0,0]；center=[1,1,1]',
    '请做一次对接；receptor_ref=../../private.pdb',
    '请做一次对接；receptor_ref=https://example.com/a.pdb',
    '请做一次对接；size=[20,20,20]；unknown=1',
    '请做一次对接；size=[true,20,20]',
    '请做一次对接；size=[0,20,20]',
    '请做一次对接；center=[NaN,0,0]',
    '请做一次对接；num_modes=3.0',
    '请做一次对接；exhaustiveness=65',
    '请做一次对接；num_modes=0',
    '请做一次对接；center=[0,0,0] trailing',
    '请做一次对接；使用共晶配体定义口袋；center=[0,0,0]',
    '请做一次对接；size=[20,20,20]；',
    '', ' ', None, True, '请做一次对接。' * 1000, '\ud800',
], ids=lambda value: hashlib.sha256(str(value).encode('utf-8', 'backslashreplace')).hexdigest()[:8])
def test_d2_unknown_negative_mixed_or_unbounded_whole_text_never_admits(query):
    result = text_boundary(query)
    assert result == {'assessed': False, 'reason_code': 'docking_request_clarification_required',
                      'input_ready': False, 'execution_authorized': False}


@pytest.mark.parametrize('enabled,tools', [(False, True), (True, False), (1, True), (True, 1)])
def test_d2_disabled_request_text_gate_does_not_accept(enabled, tools):
    call = getattr(api(), 'assess_docking_request_text', None)
    assert callable(call), 'D2 whole-request text assessment is missing'
    result = call(_S10, enabled=enabled, enable_tools=tools)
    assert result['assessed'] is False


def test_d2_numeric_fields_are_exact_and_defaults_remain_absent():
    query = '请做一次对接；center=[-1,2.5,0]；size=[10,20,30]；num_modes=3'
    result = text_boundary(query)
    assert result['fields'] == {'center': [-1, 2.5, 0], 'size': [10, 20, 30], 'num_modes': 3}
    assert result['missing_fields'] == ['receptor_ref', 'ligand_ref', 'exhaustiveness']


@pytest.mark.parametrize('field', ['center', 'size', 'exhaustiveness', 'num_modes'])
def test_d2_deep_numeric_json_is_bounded_before_decoder(field):
    query = '请做一次对接；' + field + '=' + '[' * 1100 + '0' + ']' * 1100
    assert text_boundary(query)['assessed'] is False


@pytest.mark.parametrize('prefix', ['请', '用 CCO 与 1ABC', '用'])
def test_d2_near_match_whitespace_does_not_backtrack_quadratically(prefix):
    query = prefix + ' ' * 16000 + 'X'
    start = time.monotonic()
    assert text_boundary(query)['assessed'] is False
    # This is a regression guard, not a production timing/latency claim.
    assert time.monotonic() - start < 1.5


@pytest.mark.parametrize('field', ['center', 'ligand_ref'])
def test_d2_assignment_newline_near_match_has_bounded_tokenization(field):
    query = '请做一次对接；' + field + '=' + ' ' * 16000 + '\nX'
    start = time.monotonic()
    for _ in range(4):
        assert text_boundary(query)['assessed'] is False
    assert time.monotonic() - start < 1.5


@pytest.mark.parametrize('operand', [
    'https://example.com/a.pdb', 'file:///tmp/a.pdb', '../../private.pdb',
    'C:\\private\\a.pdb', '/tmp/a.pdb', '\\\\server\\a.pdb',
    './a.sdf', 'data/receptor.pdb', 'ligand.mol2', 'structure.cif',
])
def test_d2_smiles_operand_cannot_be_a_recognizable_file_or_url(operand):
    assert text_boundary('用 ' + operand + ' 与 1ABC 对接')['assessed'] is False


@pytest.mark.parametrize('smiles', ['F/C=C/F', 'F/C=C\\F', '[13CH3][O-]', 'c1ccccc1'])
def test_d2_smiles_lexical_controls_preserve_bond_slashes(smiles):
    result = text_boundary('用 ' + smiles + ' 与 1ABC 对接')
    assert result['assessed'] is True and result['inputs']['smiles'] == smiles


_FIELD_QUERY = ('请做一次对接；receptor_ref=receptor-1；ligand_ref=ligand-1；'
                'center=[-1,2.5,0]；size=[10,20,30]；exhaustiveness=4；num_modes=3。')
_FIELD_TEXT = {'receptor_ref': 'receptor-1', 'ligand_ref': 'ligand-1',
               'center': '[-1,2.5,0]', 'size': '[10,20,30]', 'exhaustiveness': '4', 'num_modes': '3'}


def source_proposal(query=_FIELD_QUERY):
    spans = [dict(source_id='query', start=0, end=len(query), role='request')]
    fields = {}
    if query == _FIELD_QUERY:
        for name, text in _FIELD_TEXT.items():
            start = query.index(name + '=') + len(name) + 1
            fields[name] = dict(value=text if name.endswith('_ref') else json.loads(text),
                                span_indices=[len(spans)])
            spans.append(dict(source_id='query', start=start, end=start + len(text), role='field'))
    return dict(version='1', kind='docking_request', unresolved=False, source_spans=spans, fields=fields)


def proposal_boundary(query, value):
    from src.agent.contracts.docking_preparation import parse_docking_preparation_json
    call = getattr(api(), 'validate_docking_text_proposal', None)
    assert callable(call), 'D2 original-text proposal consistency validation is missing'
    proposal = parse_docking_preparation_json(json.dumps({'proposal': value}))
    return call(query, proposal, enabled=True, enable_tools=True)


def test_d2_proposal_matches_every_original_field_but_is_not_ready():
    result = proposal_boundary(_FIELD_QUERY, source_proposal())
    assert result['consistent'] is True
    assert result['assessment']['fields'] == {k: (v if k.endswith('_ref') else json.loads(v))
                                               for k, v in _FIELD_TEXT.items()}
    assert result['input_ready'] is False and result['execution_authorized'] is False


def test_d2_model_complete_claim_cannot_fill_missing_inputs():
    result = proposal_boundary(_S10, source_proposal(_S10))
    assert result['consistent'] is True
    assert len(result['assessment']['missing_fields']) == 6
    assert result['input_ready'] is False and result['execution_authorized'] is False


@pytest.mark.parametrize('mutation', [
    'value', 'drop', 'field_source', 'field_bounds', 'request_prefix', 'request_source',
    'request_bounds', 'request_role', 'field_role', 'field_index', 'duplicate_span',
    'unused_span', 'extra_obligation', 'split_span',
])
def test_d2_proposal_omission_invention_or_forged_span_requires_clarification(mutation):
    value = source_proposal()
    if mutation == 'value': value['fields']['center']['value'][0] = 999
    elif mutation == 'drop': del value['fields']['num_modes']
    elif mutation == 'field_source': value['source_spans'][1]['source_id'] = 'history-0'
    elif mutation == 'field_bounds': value['source_spans'][1]['end'] += 1
    elif mutation == 'request_prefix': value['source_spans'][0]['end'] -= 1
    elif mutation == 'request_source': value['source_spans'][0]['source_id'] = 'refinement'
    elif mutation == 'request_bounds': value['source_spans'][0]['end'] += 10
    elif mutation == 'request_role': value['source_spans'][0]['role'] = 'field'
    elif mutation == 'field_role': value['source_spans'][1]['role'] = 'request'
    elif mutation == 'field_index': value['fields']['receptor_ref']['span_indices'] = [0]
    elif mutation == 'duplicate_span': value['source_spans'].append(deepcopy(value['source_spans'][0]))
    elif mutation == 'unused_span': value['source_spans'].append(dict(source_id='query', start=0, end=1, role='field'))
    elif mutation == 'extra_obligation':
        value['source_spans'].append(dict(source_id='query', start=0, end=1, role='extra_obligation'))
    elif mutation == 'split_span': value['fields']['center']['span_indices'] = [1, 3]
    result = proposal_boundary(_FIELD_QUERY, value)
    assert result == {'consistent': False, 'reason_code': 'docking_proposal_clarification_required',
                      'input_ready': False, 'execution_authorized': False}


@pytest.mark.parametrize('kind', ['not_c', 'mixed', 'uncertain', 'docking_observation', 'docking_refinement'])
def test_d2_other_proposal_kinds_cannot_become_original_request(kind):
    value = source_proposal(_S10)
    value.update(kind=kind, unresolved=True)
    assert proposal_boundary(_S10, value)['consistent'] is False


def test_d2_model_cannot_drop_extra_original_obligation():
    query = _S10 + '再预测毒性。'
    assert proposal_boundary(query, source_proposal(query))['consistent'] is False


def test_d2_proposal_unresolved_signal_is_preserved_not_silently_cleared():
    value = source_proposal()
    value['unresolved'] = True
    result = proposal_boundary(_FIELD_QUERY, value)
    assert result['consistent'] is True and result['model_unresolved'] is True
    assert result['input_ready'] is False


@pytest.mark.parametrize('part', ['spans', 'span', 'fields', 'field', 'indices', 'value'])
def test_d2_mutated_private_dto_cannot_run_custom_hooks(part):
    from src.agent.contracts.docking_preparation import parse_docking_preparation_json
    proposal = parse_docking_preparation_json(json.dumps({'proposal': source_proposal()}))
    hooks = []
    class Meta(type):
        def __eq__(cls, other):
            hooks.append('eq')
            return True
        __hash__ = type.__hash__
    class Trap(metaclass=Meta):
        def __len__(self): hooks.append('len'); return 0
        def __iter__(self): hooks.append('iter'); return iter(())
        def __getattr__(self, name): hooks.append('getattr'); raise AttributeError(name)
        def __reduce__(self): hooks.append('reduce'); return (dict, ())
    value = Trap()
    if part == 'spans': object.__setattr__(proposal, 'source_spans', value)
    elif part == 'span': proposal.source_spans[0] = value
    elif part == 'fields': object.__setattr__(proposal, 'fields', value)
    elif part == 'field': object.__setattr__(proposal.fields, 'receptor_ref', value)
    elif part == 'indices': object.__setattr__(proposal.fields.receptor_ref, 'span_indices', value)
    elif part == 'value': object.__setattr__(proposal.fields.center, 'value', [value, 2.5, 0])
    result = api().validate_docking_text_proposal(_FIELD_QUERY, proposal, enabled=True, enable_tools=True)
    assert result['consistent'] is False and hooks == []


def api():
    name = 'src.web.docking_request'
    assert importlib.util.find_spec(name) is not None, 'D2 owned request evidence is missing'
    module = importlib.import_module(name)
    assert callable(getattr(module, 'OwnedDockingStructureEvidence', None))
    return module


@pytest.fixture
def target_case(tmp_path):
    structure = dict(structure_id='1ABC', source='RCSB_PDB', structure_type='experimental',
        organism='Synthetic organism', source_url='https://www.rcsb.org/structure/1ABC',
        retrieved_at='2026-01-01T00:00:00+00:00', expires_at='2026-01-02T00:00:00+00:00',
        stale=False, download_url='https://files.rcsb.org/download/1ABC.cif')
    target = dict(uniprot_id='P12345', organism='Synthetic organism', source='UniProt',
        source_record_id='P12345', source_url='https://www.uniprot.org/uniprotkb/P12345/entry',
        target_stale=False, structures_stale=False, stale=False,
        target_retrieved_at=structure['retrieved_at'], target_expires_at=structure['expires_at'],
        structure_evidence_status='fresh', recommended_structures=[structure])
    result = AgentResultValidator().validate_tool_result(ToolResult.success_result(
        'target_database_search', data=[target], quality={'lookup_status': 'resolved'},
        evidence=[dict(source='UniProt', id='P12345'), dict(source='RCSB_PDB', id='1ABC')]))
    assert result.success
    result.quality['step_id'] = 'target'
    ledger = EvidenceLedger('target-trace')
    ledger.prepare_provenance('a' * 64, result)
    result.quality['evidence_id'] = ledger.register_tool_result('target', 'a' * 64, result)
    payload = result.to_legacy_dict()
    store = SQLiteAgentStateStore(tmp_path / 'agent.sqlite')
    store.start_run(dict(trace_id='target-trace', session_id='owner-session', user_id='owner',
        status='completed', workflow_version='1', query='synthetic target request', metadata={}))
    checkpoint = dict(id='target-observation', trace_id='target-trace', step_id='target',
        status='succeeded', input_hash='a' * 64, workflow_version='1',
        tool_name='target_database_search', tool_version='1', output=payload)
    store.save_checkpoint(checkpoint)
    agent = dict(trace_id='target-trace', status='completed', success=True,
        tool_result_sequence=[dict(step_id='target', tool_name='target_database_search', **payload)])
    return SimpleNamespace(store=store, checkpoint=checkpoint, agent=agent, now=1767268800.0)


def boundary(case):
    return api().OwnedDockingStructureEvidence(case.store, clock=lambda: case.now)


def test_d2_offered_structure_is_only_an_owned_reference_not_ready_input(target_case):
    service = boundary(target_case)
    pointer = service.capture(target_case.agent, session_id='owner-session')
    assert pointer and pointer['observation_id'] == 'target-observation'
    assert 'download_url' not in json.dumps(pointer)
    result = service.resolve(pointer, session_id='owner-session', pdb_id='1ABC')
    assert result['target']['uniprot_id'] == 'P12345'
    assert result['structure']['structure_id'] == '1ABC'
    assert result['input_ready'] is False
    assert result['source_authentication'] == 'required_before_preparation'
    assert 'pdb_bytes' not in result
    result['target']['uniprot_id'] = 'CHANGED'
    assert service.resolve(pointer, session_id='owner-session', pdb_id='1ABC')['target']['uniprot_id'] == 'P12345'


@pytest.mark.parametrize('pdb_id', ['2XYZ', '1abc', '../1ABC', 'https://www.rcsb.org/structure/1ABC', True, None])
def test_d2_cannot_select_hidden_id_path_url_or_coercible_id(target_case, pdb_id):
    service = boundary(target_case)
    pointer = service.capture(target_case.agent, session_id='owner-session')
    assert service.resolve(pointer, session_id='owner-session', pdb_id=pdb_id) is None


@pytest.mark.parametrize('mutation', ['owner', 'run_status', 'checkpoint_status', 'data', 'input',
    'tool_version', 'workflow', 'evidence_id', 'validated', 'demo', 'error'])
def test_d2_changed_owned_source_revokes_captured_reference(target_case, mutation):
    service = boundary(target_case)
    pointer = service.capture(target_case.agent, session_id='owner-session')
    assert pointer
    with target_case.store._connect() as conn:
        if mutation == 'owner': conn.execute("UPDATE agent_runs SET session_id='foreign'")
        elif mutation == 'run_status': conn.execute("UPDATE agent_runs SET status='failed'")
        elif mutation in {'checkpoint_status', 'input', 'tool_version', 'workflow', 'error'}:
            column, value = {'checkpoint_status': ('status', 'failed'), 'input': ('input_hash', 'b' * 64),
                'tool_version': ('tool_version', '2'), 'workflow': ('workflow_version', '2'),
                'error': ('error_json', '{}')}[mutation]
            conn.execute(f'UPDATE agent_checkpoints SET {column}=?', (value,))
        else:
            value = deepcopy(target_case.checkpoint['output'])
            if mutation == 'data': value['data'][0]['organism'] = 'Other organism'
            elif mutation == 'evidence_id': value['quality']['evidence_id'] = 'forged'
            elif mutation == 'validated': value['quality']['validated'] = False
            elif mutation == 'demo': value['provenance']['demo_mode'] = True
            conn.execute('UPDATE agent_checkpoints SET output_json=?', (json.dumps(value),))
    assert service.resolve(pointer, session_id='owner-session', pdb_id='1ABC') is None
    assert service.capture(target_case.agent, session_id='owner-session') is None


@pytest.mark.parametrize('mutation', ['foreign', 'prose', 'changed_data', 'duplicate', 'failed'])
def test_d2_capture_requires_exact_accepted_live_checkpoint(target_case, mutation):
    value = deepcopy(target_case.agent)
    session = 'owner-session'
    if mutation == 'foreign': session = 'foreign'
    elif mutation == 'prose': value = dict(content='1ABC P12345 Synthetic organism')
    elif mutation == 'changed_data': value['tool_result_sequence'][0]['data'][0]['uniprot_id'] = 'Q12345'
    elif mutation == 'duplicate': value['tool_result_sequence'] *= 2
    elif mutation == 'failed': value['status'] = 'failed'
    assert boundary(target_case).capture(value, session_id=session) is None


def test_d2_foreign_and_expired_evidence_are_unavailable(target_case):
    service = boundary(target_case)
    pointer = service.capture(target_case.agent, session_id='owner-session')
    assert service.resolve(pointer, session_id='foreign', pdb_id='1ABC') is None
    target_case.now = 1767312000.0  # Exact immutable expiration, not a sliding TTL.
    assert service.resolve(pointer, session_id='owner-session', pdb_id='1ABC') is None


def republish(case, mutate):
    """New synthetic accepted observation, not a tamper of a captured pointer."""
    from src.agent.contracts import ObservationStatus, ToolProvenance
    payload = deepcopy(case.checkpoint['output'])
    mutate(payload['data'])
    result = ToolResult(tool_name='target_database_search', success=True, data=payload['data'],
        message=payload['message'], formatted=payload['formatted'], elapsed_ms=payload['elapsed_ms'],
        warnings=payload['warnings'], evidence=payload['evidence'], quality=payload['quality'],
        status=ObservationStatus.SUCCEEDED, provenance=ToolProvenance.from_dict(payload['provenance']))
    result.quality['evidence_id'] = EvidenceLedger('target-trace').register_tool_result('target', 'a' * 64, result)
    payload = result.to_legacy_dict()
    case.checkpoint['output'] = payload
    with case.store._connect() as conn:
        conn.execute('UPDATE agent_checkpoints SET output_json=?', (json.dumps(payload),))
    case.agent['tool_result_sequence'] = [dict(step_id='target', tool_name='target_database_search', **payload)]


@pytest.mark.parametrize('where,key,value', [
    ('target', 'source', 'fixture'), ('target', 'source_record_id', 'Q12345'),
    ('target', 'uniprot_id', 'EGFR'), ('target', 'uniprot_id', 'p12345'),
    ('target', 'target_stale', True), ('target', 'structures_stale', True),
    ('target', 'stale', None), ('target', 'structure_evidence_status', 'partial'),
    ('target', 'organism', ''), ('target', 'target_expires_at', '2026-01-01T10:00:00+00:00'),
    ('target', 'target_retrieved_at', '2026-01-02T00:00:00+00:00'),
    ('target', 'target_expires_at', '2026-01-02T00:00:00'),
    ('target', 'target_expires_at', 'not-a-date'),
    ('structure', 'source', 'AlphaFold'), ('structure', 'structure_type', 'predicted'),
    ('structure', 'stale', True), ('structure', 'organism', 'Other organism'),
    ('structure', 'source_url', 'https://evil.example/1ABC'),
    ('structure', 'expires_at', '2026-01-01T10:00:00+00:00'),
    ('structure', 'retrieved_at', '2026-01-02T00:00:00+00:00'),
])
def test_d2_accepted_observation_still_requires_fresh_exact_offered_identity(target_case, where, key, value):
    def mutate(rows):
        item = rows[0] if where == 'target' else rows[0]['recommended_structures'][0]
        item[key] = value
    republish(target_case, mutate)
    service = boundary(target_case)
    pointer = service.capture(target_case.agent, session_id='owner-session')
    assert pointer  # The synthetic observation is accepted; its input is not eligible.
    assert service.resolve(pointer, session_id='owner-session', pdb_id='1ABC') is None


@pytest.mark.parametrize('mutation', ['duplicate_structure', 'duplicate_target', 'oversize_structures'])
def test_d2_ambiguous_or_unbounded_offer_rejects(target_case, mutation):
    def mutate(rows):
        if mutation == 'duplicate_target': rows.append(deepcopy(rows[0]))
        elif mutation == 'duplicate_structure': rows[0]['recommended_structures'] *= 2
        else: rows[0]['recommended_structures'] *= 101
    republish(target_case, mutate)
    service = boundary(target_case)
    pointer = service.capture(target_case.agent, session_id='owner-session')
    assert service.resolve(pointer, session_id='owner-session', pdb_id='1ABC') is None


def test_d2_stored_paths_and_download_urls_never_become_fetch_instructions(target_case):
    def mutate(rows):
        rows[0]['recommended_structures'][0].update(
            local_file_path='SYNTHETIC_PRIVATE_PATH', download_url='https://evil.example/file')
    republish(target_case, mutate)
    service = boundary(target_case)
    pointer = service.capture(target_case.agent, session_id='owner-session')
    result = service.resolve(pointer, session_id='owner-session', pdb_id='1ABC')
    assert result and 'SYNTHETIC_PRIVATE_PATH' not in json.dumps(result)
    assert 'evil.example' not in json.dumps(result) and 'download_url' not in json.dumps(result)


def test_d2_expiration_during_revalidation_prevents_publication(target_case):
    ticks = iter([target_case.now, 1767312000.0])
    service = api().OwnedDockingStructureEvidence(target_case.store, clock=lambda: next(ticks))
    pointer = service.capture(target_case.agent, session_id='owner-session')
    assert service.resolve(pointer, session_id='owner-session', pdb_id='1ABC') is None


@pytest.mark.parametrize('now', [True, None, float('nan'), float('inf')])
def test_d2_invalid_clock_cannot_extend_evidence(target_case, now):
    service = boundary(target_case)
    pointer = service.capture(target_case.agent, session_id='owner-session')
    target_case.now = now
    assert service.resolve(pointer, session_id='owner-session', pdb_id='1ABC') is None
