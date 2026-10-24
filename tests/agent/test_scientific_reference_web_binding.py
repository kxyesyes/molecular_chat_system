"""Real SQLite/resolver/RDKit references; generation is an explicit fixture."""
import asyncio
from copy import deepcopy
import json

import pytest

from src.agent.contracts import RunOutcome
from src.agent.evidence import EvidenceLedger
from src.agent.orchestrators import WorkflowStep
from src.agent.persistence import SQLiteAgentStateStore
from src.agent.scientific_session.decision_execution import settle_action
from tests.agent.test_generation_ranking_contract import CountingTool, GEN, output
from tests.agent.test_web_profile_bindings import make as web_case, decision
from tests.agent.test_scientific_reference_store import seed


@pytest.fixture
def candidate(web_case):
    case = web_case(generate=True, custom_tools=[CountingTool(GEN, output(GEN))],
                    query='生成 1 个分子')
    compiled = case.steps[0]
    action = case.resolver.resolve(decision(compiled))
    step_id = 'decision-generation'
    metadata = {**case.resolver.register_action(step_id, action),
                **case.resolver.step_metadata(action)}
    case.session.append_step(WorkflowStep(step_id, GEN, input_data=action.input_data,
        output_key=step_id, required=False, metadata=metadata))
    asyncio.run(settle_action(case.session))
    result = case.session.results[0]
    assert result.success
    case.store = case.session.orchestrator.state_store
    case.store.update_run_metadata(case.context.trace_id,
        {'task_requirements': case.requirements.model_dump(mode='json')})
    case.session.finish_dynamic('Explicit offline fixture', outcome=RunOutcome.COMPLETED)
    case.checkpoint = case.store.latest_checkpoint(case.context.trace_id)
    case.result = result
    case.selections = [{'observation_id': case.checkpoint['id'],
                        'candidate_id': result.data['candidates'][0]['candidate_id']}]
    return case


def sources(case, store=None):
    return (store or case.store).get_scientific_sources(
        case.context.trace_id, session_id=case.context.session_id)


def publish(case):
    return case.store.publish_scientific_presentation(case.context.trace_id,
        session_id=case.context.session_id, selections=case.selections)


def checkpoint_change(case, column, value):
    assert column in {'output_json', 'metadata_json'}
    with case.store._connect() as connection:
        connection.execute(f'UPDATE agent_checkpoints SET {column}=? WHERE id=?',
                           (json.dumps(value), case.checkpoint['id']))


def test_web_candidate_reference_uses_trusted_proof_and_survives_ack_reopen(candidate):
    case = candidate
    found = sources(case)
    assert len(found) == 1, 'Valid Web binding must not lose the candidate reference'
    assert found[0]['observation']['quality']['evidence_id'] == case.result.quality['evidence_id']
    view = publish(case)
    assert view is not None
    assert view['evidence'][0]['evidence_id'] == case.result.quality['evidence_id']
    pointer = dict(trace_id=case.context.trace_id, session_id=case.context.session_id,
                   presentation_id=view['presentation_id'], revision=view['revision'])
    keys = [[item['observation_id'], item['candidate']['candidate_id']]
            for item in view['ordered_candidates']]
    assert case.store.confirm_scientific_presentation(**pointer, ordered_keys=keys)
    reopened = SQLiteAgentStateStore(case.store.db_path)
    assert reopened.get_scientific_presentation(**pointer) == view
    assert sources(case, reopened) == found
    assert case.tools[0].calls == 1
    assert reopened.get_scientific_sources(case.context.trace_id, session_id='foreign') is None


def test_internal_report_snapshot_retains_candidate_verifier_owner_context(candidate):
    from src.agent.persistence.scientific_references import _candidates
    view = publish(candidate)
    reference = dict(trace_id=candidate.context.trace_id,
        presentation_id=view['presentation_id'], revision=view['revision'],
        ordered_keys=[[item['observation_id'], item['candidate']['candidate_id']]
            for item in view['ordered_candidates']])
    snapshot = candidate.store.get_scientific_report_snapshot(candidate.context.trace_id,
        session_id=candidate.context.session_id, references=[reference])
    run = snapshot['run']
    assert run['session_id'] == candidate.context.session_id
    assert run['user_id'] == candidate.context.user_id
    checkpoint = snapshot['latest'][candidate.checkpoint['step_id']]
    _, _, evidence_id = _candidates(checkpoint, run['trace_id'],
        row=run, run_metadata=run['metadata'])
    assert evidence_id == candidate.result.quality['evidence_id']


@pytest.mark.parametrize('field', [
    'binding_proof', 'binding_record_sha256', 'request_input_digest',
    'input_evidence_ids', 'operation_key', 'web_step_ref', 'web_plan_sha256',
])
def test_web_reference_requires_each_server_checkpoint_commitment(candidate, field):
    metadata = deepcopy(candidate.checkpoint['metadata'])
    metadata.pop(field)
    checkpoint_change(candidate, 'metadata_json', metadata)
    assert sources(candidate) == []
    assert publish(candidate) is None


@pytest.mark.parametrize('field', [
    'binding_proof', 'binding_record_sha256', 'request_input_digest',
    'input_evidence_ids', 'operation_key', 'step_id', 'output_key',
])
def test_web_reference_rejects_quality_changes_without_server_commitment(candidate, field):
    raw = deepcopy(candidate.checkpoint['output'])
    raw['quality'][field] = {'forged': True} if field == 'binding_proof' else 'forged'
    checkpoint_change(candidate, 'output_json', raw)
    assert sources(candidate) == []
    assert publish(candidate) is None


@pytest.mark.parametrize('mutation', ['missing', 'profile', 'owner', 'plan', 'invalidated'])
def test_web_reference_rejects_untrusted_or_inconsistent_run_profile(candidate, mutation):
    run = candidate.store.get_run(candidate.context.trace_id)
    metadata = run['metadata']
    requirements = metadata['task_requirements']
    if mutation == 'missing':
        metadata.pop('task_requirements')
    elif mutation == 'profile':
        requirements['profile'] = 'ordinary-semantic-b1-v1'
    elif mutation == 'owner':
        requirements['request']['session_id'] = 'foreign'
        from src.agent.contracts.web_requirements import web_plan_digest
        requirements['plan_sha256'] = web_plan_digest(
            {key: value for key, value in requirements.items() if key != 'plan_sha256'})
    elif mutation == 'plan':
        requirements['plan_sha256'] = '0' * 64
    else:
        metadata['decision_publication_invalidation'] = {'reason': 'source_changed'}
    with candidate.store._connect() as connection:
        connection.execute('UPDATE agent_runs SET metadata_json=? WHERE trace_id=?',
                           (json.dumps(metadata), candidate.context.trace_id))
    assert sources(candidate) == []
    assert publish(candidate) is None


def test_confirmed_web_reference_is_revoked_when_trusted_plan_changes(candidate):
    view = publish(candidate)
    assert view is not None
    pointer = dict(trace_id=candidate.context.trace_id, session_id=candidate.context.session_id,
                   presentation_id=view['presentation_id'], revision=view['revision'])
    keys = [[item['observation_id'], item['candidate']['candidate_id']]
            for item in view['ordered_candidates']]
    assert candidate.store.confirm_scientific_presentation(**pointer, ordered_keys=keys)
    candidate.store.update_run_metadata(candidate.context.trace_id, {'task_requirements': {}})
    assert candidate.store.get_scientific_presentation(**pointer) is None
    assert not candidate.store.confirm_scientific_presentation(**pointer, ordered_keys=keys)


@pytest.mark.parametrize('raw_proof', [None, {'profile': 'medchat-web-v1', 'forged': True}])
def test_legacy_candidate_identity_never_opts_in_from_raw_quality(tmp_path, raw_proof):
    store = SQLiteAgentStateStore(tmp_path / 'legacy.sqlite')
    selections = seed(store)
    checkpoint = store.latest_checkpoint('trace')
    original_id = checkpoint['output']['quality']['evidence_id']
    checkpoint['output']['quality']['binding_proof'] = raw_proof
    with store._connect() as connection:
        connection.execute('UPDATE agent_checkpoints SET output_json=? WHERE id=?',
                           (json.dumps(checkpoint['output']), checkpoint['id']))
    found = store.get_scientific_sources('trace', session_id='session')
    assert len(found) == 1
    assert found[0]['observation']['quality']['evidence_id'] == original_id
    view = store.publish_scientific_presentation('trace', session_id='session', selections=selections)
    assert view['evidence'][0]['evidence_id'] == original_id


def test_valid_web_proof_in_legacy_quality_does_not_enroll_legacy_identity(candidate, tmp_path):
    store = SQLiteAgentStateStore(tmp_path / 'legacy-valid-proof.sqlite')
    selections = seed(store)
    checkpoint = store.latest_checkpoint('trace')
    original_id = checkpoint['output']['quality']['evidence_id']
    checkpoint['output']['quality']['binding_proof'] = candidate.result.quality['binding_proof']
    with store._connect() as connection:
        connection.execute('UPDATE agent_checkpoints SET output_json=? WHERE id=?',
                           (json.dumps(checkpoint['output']), checkpoint['id']))
    view = store.publish_scientific_presentation('trace', session_id='session', selections=selections)
    assert view['evidence'][0]['evidence_id'] == original_id


def test_rehashed_tool_quality_proof_cannot_replace_server_authority(candidate):
    forged = deepcopy(candidate.result)
    forged.quality['binding_proof']['action_sha256'] = '0' * 64
    forged.quality['evidence_id'] = EvidenceLedger(candidate.context.trace_id).register_tool_result(
        candidate.checkpoint['step_id'], candidate.checkpoint['input_hash'], forged,
        binding_proof=forged.quality['binding_proof'])
    checkpoint_change(candidate, 'output_json', forged.to_legacy_dict())
    assert sources(candidate) == []
    assert publish(candidate) is None


def test_web_candidate_cannot_downgrade_to_legacy_identity(candidate):
    forged = deepcopy(candidate.result)
    forged.quality['evidence_id'] = EvidenceLedger(candidate.context.trace_id).register_tool_result(
        candidate.checkpoint['step_id'], candidate.checkpoint['input_hash'], forged)
    checkpoint_change(candidate, 'output_json', forged.to_legacy_dict())
    metadata = deepcopy(candidate.checkpoint['metadata'])
    metadata.pop('binding_proof')
    checkpoint_change(candidate, 'metadata_json', metadata)
    assert sources(candidate) == []
    assert publish(candidate) is None


def test_actual_pi_generation_reference_ack_and_reopen_without_followup(tmp_path):
    from fastapi.testclient import TestClient
    from tests.agent.test_scientific_reference_browser_lab import lab, DeadlineSocket, receive_until

    app = lab(tmp_path)
    base = 'http://127.0.0.1:6017'
    url = '/api/agent/workflows/references/'
    with TestClient(app, base_url=base) as client:
        assert client.get('/').status_code == 200
        with client.websocket_connect('ws://127.0.0.1:6017/ws', headers={'Origin': base}) as transport:
            ws = DeadlineSocket(transport, app.state.inbox)
            receive_until(ws, 'connection_ready')
            ws.send_json({'message': '生成5个分子', 'enable_rag': False,
                          'enable_tools': True, 'mol_count': 5})
            event = receive_until(ws, 'molecule_candidates')
            receive_until(ws, 'complete')
            assert len(event['candidate_set']['candidates']) == 5
            ref = event['reference']
            pointer = {key: ref[key] for key in ('trace_id', 'presentation_id', 'revision')}
            assert client.post(url + 'restore', json=pointer).status_code == 404
            assert client.post(url + 'confirm', json=ref).status_code == 200
            response = client.post(url + 'restore', json=pointer)
            assert response.status_code == 200
            view = response.json()['data']
        assert len(app.state.calls[GEN]) == 1
        cookies = dict(client.cookies)
    reopened = lab(tmp_path)
    with TestClient(reopened, base_url=base) as client:
        client.cookies.update(cookies)
        response = client.post(url + 'restore', json=pointer)
        assert response.status_code == 200
        assert response.json()['data'] == view
        assert all(not calls for calls in reopened.state.calls.values())
