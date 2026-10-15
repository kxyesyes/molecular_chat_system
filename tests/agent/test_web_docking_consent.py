"""D2 owned chat evidence only. Synthetic records are not structure validation."""
from copy import deepcopy
import importlib
import importlib.util
import json
from types import SimpleNamespace

import pytest

from src.agent.contracts import ToolResult
from src.agent.evidence.ledger import EvidenceLedger
from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
from src.agent.validators.result_validator import AgentResultValidator


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
