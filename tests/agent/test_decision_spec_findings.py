"""Independent SPEC probes; synthetic inputs and temporary SQLite only."""
import json

import pytest

from test_decision_loop import setup_loop, CountingTool, tool, clarify, finish_last, run
from test_decision_continuation import start, fresh, invoke
from src.agent.contracts import AgentErrorCode, AgentExecutionError
from src.agent.evidence import EvidenceLedger
from test_decision_migration_boundaries import KINDS, bad_value


@pytest.mark.parametrize('action', ['finish', 'downstream'])
@pytest.mark.parametrize('invalid_fields', [
    {'error': {'code': 'provider_error', 'message': 'synthetic failure', 'details': {}}},
    {'status': 'failed'}, {'status': 'partial'}, {'success': 'false'}, {'success': 1},
])
def test_legacy_error_bearing_success_cannot_be_used_or_completed(setup_loop, action, invalid_fields):
    from test_decision_inputs import downstream

    class ContradictoryTool(CountingTool):
        def execute(self, query):
            raw = super().execute(query).to_legacy_dict()
            raw.update(invalid_fields)
            return raw

    target = CountingTool('drug_likeness_assessment')
    b = setup_loop([tool(), finish_last if action == 'finish' else downstream, finish_last],
                   [ContradictoryTool(), target])
    result = run(b)
    persisted = b.store.get_tool_executions('trace-test')[0]['output']
    assert not result.success
    assert not target.inputs
    assert result.tool_results[0].error is not None and persisted['error'] is not None
    assert '46.069' not in result.final_answer


@pytest.mark.parametrize('success', ['false', 1])
@pytest.mark.parametrize('action', ['finish', 'downstream'])
def test_typed_success_must_be_a_real_boolean(setup_loop, success, action):
    from test_decision_inputs import downstream

    class MalformedTool(CountingTool):
        def execute(self, query):
            result = super().execute(query)
            result.success = success
            return result

    target = CountingTool('drug_likeness_assessment')
    b = setup_loop([tool(), finish_last if action == 'finish' else downstream, finish_last],
                   [MalformedTool(), target])
    result = run(b)
    persisted = b.store.get_tool_executions('trace-test')[0]['output']
    assert not result.success and not target.inputs
    assert result.tool_results[0].success is False and persisted['success'] is False
    assert result.tool_results[0].error is not None and persisted['error'] is not None
    assert '46.069' not in result.final_answer


@pytest.mark.parametrize('change', ['terminal_failed', 'repair_error_code'])
def test_waiting_history_cannot_end_in_failed_call_or_repair_provider_error(setup_loop, monkeypatch, change):
    from test_decision_protocol_recovery import configured, invalid
    b = configured(setup_loop, [invalid(), clarify()])
    waiting = invoke(b)
    record = b.store.get_run('owned-trace')
    payload = record['metadata']['decision_continuation']
    calls = payload['snapshot']['model_calls']
    if change == 'terminal_failed':
        calls.reverse()
        for index, call in enumerate(calls, 1): call['round'] = index
    else:
        calls[0]['error_code'] = 'provider_error'
    payload['checksum'] = EvidenceLedger.output_digest({k: v for k, v in payload.items() if k != 'checksum'})
    monkeypatch.setattr(b.store, 'get_run', lambda _: record)
    def forbidden(*a, **kw): pytest.fail('invalid repaired history reached CAS')
    monkeypatch.setattr(b.store, 'transition_decision_continuation', forbidden)
    result = invoke(b, continuation_id=waiting.metadata['continuation_id'], clarified_query='SMILES: CCO')
    assert result.metadata['stop_reason'] == 'continuation_rejected'


@pytest.mark.parametrize('mode', ['native', 'json'])
@pytest.mark.parametrize('change', [
    'authorized_tool', 'input_ref', 'proposal_turn', 'proposal_id', 'proposal_kind',
    'proposal_call', 'observation_error', 'acceptance', 'system', 'extra_user',
])
def test_semantic_history_reconciled_not_merely_rechecksummed(setup_loop, monkeypatch, mode, change):
    b = setup_loop([], [CountingTool(), CountingTool('drug_likeness_assessment')], mode=mode)
    options = {'allowed_tools': {'property_calculator', 'drug_likeness_assessment'}}
    waiting = start(b, [tool(), clarify()], **options)
    record = b.store.get_run('owned-trace')
    payload = record['metadata']['decision_continuation']
    snapshot = payload['snapshot']
    if change in {'authorized_tool', 'input_ref'}:
        # Change BOTH recorded proposal and actual assistant message: the
        # executed result/input binding still has to agree with that action.
        proposal = snapshot['proposals'][0]['decision']
        if change == 'authorized_tool':
            proposal['tool_name'] = 'drug_likeness_assessment'
        else:
            proposal['arguments']['input_ref'] = snapshot['results'][0]['quality']['evidence_id']
        text = json.dumps({'decision': proposal})
        if mode == 'native':
            snapshot['messages'][2]['tool_calls'][0]['function']['arguments'] = text
        else:
            snapshot['messages'][2]['content'] = text
    elif change.startswith('proposal_'):
        proposal = snapshot['proposals'][0]
        if change == 'proposal_turn': proposal['input_turn'] = 2
        if change == 'proposal_id': proposal['decision_id'] = snapshot['proposals'][-1]['decision_id']
        if change == 'proposal_kind': proposal['decision'] = clarify().model_dump()
        if change == 'proposal_call': proposal['tool_call_id'] = 'call-substituted'
    elif change in {'observation_error', 'acceptance'}:
        observation = json.loads(snapshot['messages'][3]['content'])
        if change == 'observation_error': observation['error'] = {'code': 'provider_error', 'message': 'changed', 'details': {}}
        else: observation['task_acceptance']['satisfied'] = False
        snapshot['messages'][3]['content'] = json.dumps(observation)
    elif change == 'system': snapshot['messages'][0]['content'] = 'Changed system policy'
    else: snapshot['messages'].append({'role': 'user', 'content': 'Unrecorded turn'})
    payload['checksum'] = EvidenceLedger.output_digest({k: v for k, v in payload.items() if k != 'checksum'})
    monkeypatch.setattr(b.store, 'get_run', lambda _: record)
    def forbidden(*a, **kw): pytest.fail('inconsistent history reached CAS')
    monkeypatch.setattr(b.store, 'transition_decision_continuation', forbidden)
    result = invoke(b, continuation_id=waiting.metadata['continuation_id'], clarified_query='SMILES: CCO', **options)
    assert result.metadata['stop_reason'] == 'continuation_rejected'


@pytest.mark.parametrize('mode', ['native', 'json'])
def test_semantic_replay_accepts_references_reuse_and_multiple_input_turns(setup_loop, mode):
    from test_decision_inputs import downstream
    from test_decision_continuation import ContinuationModel
    b = setup_loop([], [CountingTool(), CountingTool('drug_likeness_assessment')], mode=mode)
    options = {'allowed_tools': {'property_calculator', 'drug_likeness_assessment'}}
    waiting = start(b, [tool(), downstream, tool(), clarify()], **options)
    assert waiting.metadata['reused_decisions'] == 1
    b.loop.model = ContinuationModel([clarify()])
    waiting = invoke(b, continuation_id=waiting.metadata['continuation_id'], clarified_query='SMILES: CCN', **options)
    assert waiting.metadata['waiting_for_input']
    b.loop.model = ContinuationModel([tool(), finish_last])
    result = invoke(b, continuation_id=waiting.metadata['continuation_id'], clarified_query='SMILES: CCO', **options)
    assert result.success, result.metadata
    assert len(b.tools[0].inputs) == len(b.tools[1].inputs) == 1


def test_raw_gate_is_request_local_and_keeps_default_adapter_compatibility(setup_loop):
    from src.agent.contracts import ToolResult
    class LargeTool(CountingTool):
        def execute(self, query):
            self.inputs.append(query)
            return ToolResult.success_result(self.name, {'smiles': 'CCO', 'large': 'x' * 65537})
    b = setup_loop([tool(), finish_last], [LargeTool()])
    adapter = b.registry.resolve('property_calculator')
    original_spec, original_invoke = adapter.spec, adapter.invoke
    assert not run(b).success
    result = adapter.execute({'query': 'CCO'})
    assert result.success and len(result.data['large']) == 65537
    assert adapter.spec is original_spec and adapter.invoke == original_invoke
    assert len(b.tools[0].inputs) == 2


def test_raw_gate_keeps_input_and_output_schema_validation():
    from test_tool_adapters import make_spec, LegacyValueTool
    from src.agent.tooling.adapters import LegacyPythonToolAdapter
    from src.agent.harness.decision_bounds import validate_raw_observation
    source = LegacyValueTool()
    adapter = LegacyPythonToolAdapter(make_spec(), source)
    result = adapter.execute({'not_query': 'CCO'}, raw_validator=validate_raw_observation)
    assert result.error.code == AgentErrorCode.INVALID_INPUT and source.calls == 0
    source.execute = lambda _: {'success': True, 'data': {'wrong': 1}}
    result = adapter.execute({'query': 'CCO'}, raw_validator=validate_raw_observation)
    assert result.error.code == AgentErrorCode.INVALID_OUTPUT


def test_raw_gate_keeps_timed_out_invocation_slot_until_worker_settles():
    import threading
    from test_tool_adapters import make_spec
    from src.agent.tooling.adapters import LegacyPythonToolAdapter
    from src.agent.harness.decision_bounds import validate_raw_observation
    release = threading.Event()
    calls = []
    class BlockingTool:
        def execute(self, query):
            calls.append(query)
            release.wait(2)
            return {'success': True, 'data': {'value': 3}}
    adapter = LegacyPythonToolAdapter(make_spec(timeout_seconds=0.03), BlockingTool())
    try:
        first = adapter.execute({'query': 'CCO'}, raw_validator=validate_raw_observation)
        second = adapter.execute({'query': 'CCO'}, raw_validator=validate_raw_observation)
        assert first.error.code == AgentErrorCode.TOOL_TIMEOUT
        assert first.quality['retryable'] is False
        assert second.error.code == AgentErrorCode.TOOL_UNAVAILABLE
        assert calls == ['CCO']
    finally:
        release.set()


def test_publication_retry_cannot_replace_original_full_seal(setup_loop, monkeypatch):
    import sqlite3
    import src.agent.harness.decision_loop as loop
    class Retained(CountingTool):
        def execute(self, query):
            self.last = super().execute(query)
            self.last.error = AgentExecutionError(AgentErrorCode.PROVIDER_ERROR, 'original failure')
            return self.last
    source = Retained()
    b = setup_loop([tool(), finish_last], [source])
    original, attempts, seals = b.store.record_tool_execution, [], []
    capture = loop.seal_observation
    def capture_once(result, session):
        capture(result, session)
        seals.append(session._decision_observation_seals)
        with pytest.raises(TypeError):
            seals[-1][result.quality['evidence_id']] = 'replace'
    monkeypatch.setattr(loop, 'seal_observation', capture_once)
    def write(record):
        attempts.append(True)
        original(record)
        if len(attempts) == 1:
            source.last.error = None
            raise sqlite3.OperationalError('synthetic commit then error')
    monkeypatch.setattr(b.store, 'record_tool_execution', write)
    result = run(b)
    assert not result.success and len(source.inputs) == 1 and len(seals) == 1
    assert result.tool_results[0].error.code == AgentErrorCode.PROVIDER_ERROR
    assert 'provider_error' in next(iter(seals[0].values()))
    assert '46.069' not in result.final_answer


@pytest.mark.parametrize('boundary', ['execution', 'checkpoint', 'warning', 'completed'])
@pytest.mark.parametrize('action', ['finish', 'downstream'])
def test_full_seal_precedes_every_publication(setup_loop, monkeypatch, boundary, action):
    from test_decision_inputs import downstream

    class Retained(CountingTool):
        def execute(self, query):
            self.last = super().execute(query)
            self.last.error = AgentExecutionError(AgentErrorCode.PROVIDER_ERROR, 'original failure')
            return self.last

    source, target = Retained(), CountingTool('drug_likeness_assessment')
    b = setup_loop([tool(), finish_last if action == 'finish' else downstream, finish_last], [source, target])
    changed = []

    def mutate():
        if hasattr(source, 'last'):
            source.last.error = None
            changed.append(True)

    if boundary in {'execution', 'checkpoint'}:
        name = 'record_tool_execution' if boundary == 'execution' else 'save_checkpoint'
        original = getattr(b.store, name)
        def write(record):
            mutate()
            return original(record)
        monkeypatch.setattr(b.store, name, write)
    else:
        def callback(event):
            if event.event.value == ('validation_warning' if boundary == 'warning' else 'tool_completed'):
                mutate()
        b.bus.on_event = callback
    result = run(b)
    assert changed and not result.success and not target.inputs
    assert result.tool_results[0].error.code == AgentErrorCode.PROVIDER_ERROR
    assert '46.069' not in result.final_answer


@pytest.mark.parametrize('kind', KINDS)
@pytest.mark.parametrize('field', ['data', 'error', 'artifacts'])
def test_raw_result_fields_bounded_before_normalize(setup_loop, monkeypatch, kind, field):
    from src.agent.contracts import WorkflowArtifact
    value = bad_value(kind, 65536)
    class RawTool(CountingTool):
        def execute(self, query):
            result = super().execute(query)
            if field == 'error':
                result.error = AgentExecutionError(AgentErrorCode.PROVIDER_ERROR, 'failed', {'bad': value})
            elif field == 'artifacts':
                result.artifacts = [WorkflowArtifact('test', 'test.txt', 'test', metadata={'bad': value})]
            else:
                result.data = value
            return result
    b = setup_loop([tool(), finish_last], [RawTool()])
    adapter = b.registry.resolve('property_calculator')
    def forbidden(*a, **kw):
        pytest.fail('raw result reached normalization before field bounds')
    monkeypatch.setattr(adapter, '_normalize', forbidden)
    assert not run(b).success


def test_legacy_raw_dict_bounded_before_compat_conversion(setup_loop, monkeypatch):
    import src.agent.tools.base_tool as base
    class RawTool(CountingTool):
        def execute(self, query):
            return {'success': True, 'artifacts': [None] * 65537}
    original = base.execute_tool_compat
    def compat(tool, *a, **kw):
        if type(tool).__name__ == 'CompletedInvocation':
            pytest.fail('unbounded raw dict reached compat conversion')
        return original(tool, *a, **kw)
    monkeypatch.setattr(base, 'execute_tool_compat', compat)
    assert not run(setup_loop([tool(), finish_last], [RawTool()])).success


def test_error_cannot_be_erased_before_first_observation_seal(setup_loop):
    class Retained(CountingTool):
        def execute(self, query):
            self.last = super().execute(query)
            self.last.error = AgentExecutionError(AgentErrorCode.PROVIDER_ERROR, 'synthetic contradiction')
            return self.last

    source = Retained()
    b = setup_loop([tool(), finish_last], [source])
    erased = []

    def callback(event):
        if event.event.value == 'tool_completed':
            assert event.payload['error']['code'] == 'provider_error'
            source.last.error = None
            erased.append(True)

    b.bus.on_event = callback
    result = run(b)
    assert erased
    stored_error = b.store.get_tool_executions('trace-test')[0]['output']['error']
    assert stored_error['code'] == 'provider_error'
    assert not result.success, (
        'Contradictory observation became COMPLETED after callback cleared error; '
        f'scientific_value_rendered={"46.069" in result.final_answer}; persisted_error=provider_error'
    )
    assert '46.069' not in result.final_answer


@pytest.mark.parametrize('mode,change', [
    ('native', 'observation'), ('native', 'action'),
    ('json', 'observation'), ('json', 'malformed_action'),
    ('native', 'clarified_user'),
])
def test_full_history_mismatch_rejected_before_cas(setup_loop, monkeypatch, mode, change):
    b = setup_loop([], mode=mode)
    waiting = start(b, [tool(), clarify()])
    if change == 'clarified_user':
        fresh(b, [clarify()])
        waiting = invoke(b, continuation_id=waiting.metadata['continuation_id'], clarified_query='SMILES: CCN')
    record = b.store.get_run('owned-trace')
    payload = record['metadata']['decision_continuation']
    snapshot = payload['snapshot']
    messages = snapshot['messages']
    if change == 'observation':
        index = 3
        observation = json.loads(messages[index]['content'])
        observation['data']['molecular_weight'] = 999999
        messages[index]['content'] = json.dumps(observation)
    elif change == 'action':
        action = json.loads(messages[2]['tool_calls'][0]['function']['arguments'])
        action['decision']['tool_name'] = 'activity_predictor'
        messages[2]['tool_calls'][0]['function']['arguments'] = json.dumps(action)
    elif change == 'malformed_action':
        messages[2]['content'] = '{not valid JSON'
    else:
        assert messages[-1]['role'] == 'user'
        messages[-1]['content'] = 'SMILES: CCO'
    payload['checksum'] = EvidenceLedger.output_digest({k: v for k, v in payload.items() if k != 'checksum'})
    monkeypatch.setattr(b.store, 'get_run', lambda _: record)
    reached_cas = []

    def reject_cas(*args, **kwargs):
        reached_cas.append(True)
        return False

    monkeypatch.setattr(b.store, 'transition_decision_continuation', reject_cas)
    result = invoke(b, continuation_id=waiting.metadata['continuation_id'], clarified_query='SMILES: CCO')
    assert result.metadata['stop_reason'] == 'continuation_rejected'
    assert not reached_cas, f'{mode}/{change}: semantically inconsistent history reached CAS'


def test_raw_oversized_observation_bounded_before_credential_scan(setup_loop, monkeypatch):
    import src.agent.tooling.adapters as adapters

    payload = {'smiles': 'CCO', 'molecular_weight': 46.069, 'oversized': 'x' * 65537}

    class LargeTool(CountingTool):
        def execute(self, query):
            from src.agent.contracts import ToolResult
            self.inputs.append(query)
            return ToolResult.success_result(self.name, payload)

    scanned = []
    original = adapters.redact_sensitive

    def spy(value, *args, **kwargs):
        if value is payload:
            scanned.append(True)
        return original(value, *args, **kwargs)

    monkeypatch.setattr(adapters, 'redact_sensitive', spy)
    b = setup_loop([tool(), finish_last], [LargeTool()])
    result = run(b)
    assert not result.success
    assert not scanned, 'Oversized raw observation traversed credential redaction before harness bounds'
