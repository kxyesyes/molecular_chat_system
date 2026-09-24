"""Offline boundary fixtures are transport evidence, not scientific validation."""
from copy import deepcopy

import pytest

from src.agent.contracts import AgentErrorCode, ObservationStatus, ToolResult
from src.agent.tooling.factory import build_tool_registry
from src.agent.tools.target_database_tool import TargetDatabaseTool
from src.agent.tools.reverse_target_tool import ReverseTargetTool


TARGET = 'target_database_search'
REVERSE = 'reverse_target_predictor'


def target_row():
    return {'gene_symbol': 'EGFR', 'uniprot_id': 'P00533', 'source': 'fixture',
            'source_record_id': 'fixture-record', 'structure_count': None,
            'structure_evidence_status': 'unavailable', 'stale': False,
            'recommended_structures': [], 'extension': {'opaque': [1, None]}}


def reverse_row():
    return {'target_name': 'fixture target', 'organism': 'fixture organism',
            'target_identifier': 'fixture-id', 'final_similarity': 0.8,
            'morgan_similarity': 0.9, 'maccs_similarity': 0.7,
            'similar_count': 2, 'assay': {}}


class Service:
    def __init__(self, status, retryable=False):
        self.status = status
        self.retryable = retryable
        self.calls = []
        self.closed = 0

    def search_targets(self, query):
        self.calls.append(query)
        return {'status': self.status,
                'results': [target_row()] if self.status in {'resolved', 'partial'} else [],
                'lookup_path': ['fixture-local', 'fixture-authoritative'],
                'evidence': [{'source': 'fixture', 'id': 'fixture-record', 'stale': False}],
                'warnings': ['fixture only'], 'retryable': self.retryable}

    def close(self):
        self.closed += 1


def adapter(tool):
    return build_tool_registry([tool]).resolve(tool.name)


@pytest.mark.parametrize('status', ['resolved', 'not_found', 'ambiguous', 'unavailable', 'partial'])
def test_actual_wrapper_domain_status(status):
    service = Service(status)
    tool = TargetDatabaseTool()
    tool._service = service
    raw = tool.execute('EGFR')
    before = deepcopy(raw)
    service.calls.clear()
    seen = []
    result = adapter(tool).execute({'query': 'EGFR'}, raw_validator=seen.append)
    expected = {'resolved': 'succeeded', 'not_found': 'succeeded',
                'ambiguous': 'invalid_input', 'unavailable': 'unavailable', 'partial': 'partial'}
    assert result.status.value == expected[status]
    assert service.calls == ['EGFR']
    assert seen == [before]
    assert result.quality['lookup_status'] == status
    assert result.quality['lookup_path'] == before['lookup_path']
    assert result.quality['service_statuses'] == [status]
    assert result.evidence == before['evidence']
    assert result.warnings == before['warnings']
    assert result.formatted == before['formatted']
    assert result.success == before['success']
    if status in {'resolved', 'partial'}:
        assert result.data == before['data']
        assert result.data[0]['structure_count'] is None
    if status == 'unavailable':
        assert result.quality['retryable'] is False


class RawTool:
    def __init__(self, name=TARGET, raw=None):
        self.name = name
        self.raw = raw if raw is not None else {'success': True, 'data': []}
        self.calls = []
        self.closed = 0

    def execute(self, query):
        self.calls.append(query)
        return self.raw

    def close(self):
        self.closed += 1


@pytest.mark.parametrize('payload', ['EGFR', {'gene_symbol': 'EGFR', 'uniprot_id': None, 'opaque': object()},
                                     [{'target_gene': 'EGFR'}], ({'protein_name': 'EGFR'},)])
@pytest.mark.parametrize('wrapped', [False, True])
def test_input_non_projecting(payload, wrapped):
    tool = RawTool()
    result = adapter(tool).execute({'query': payload, 'opaque': object()} if wrapped else payload)
    assert result.success
    assert tool.calls[0] is payload


@pytest.mark.parametrize('name,payload', [(TARGET, 1), (TARGET, None), (TARGET, {'gene_symbol': 3}),
    (TARGET, {'query': {'uniprot_id': False}}), (TARGET, [{'target_name': []}]),
    (REVERSE, ['CCO']), (REVERSE, {'query': 3}), (REVERSE, {'smiles': 'CCO'})])
def test_bad_input_stops_producer(name, payload):
    tool = RawTool(name)
    result = adapter(tool).execute(payload)
    assert result.error.code == AgentErrorCode.INVALID_INPUT
    assert not result.error.details
    assert tool.calls == []


@pytest.mark.parametrize('rows', [[], [reverse_row()]])
def test_actual_reverse_wrapper_counted_predictor(rows):
    class Predictor:
        calls = []

        def predict(self, smiles, **kwargs):
            self.calls.append((smiles, kwargs))
            return rows

    tool = ReverseTargetTool()
    predictor = Predictor()
    tool._predictor = predictor
    seen = []
    result = adapter(tool).execute({'query': 'CCO'}, raw_validator=seen.append)
    assert result.success
    assert len(predictor.calls) == 1
    assert predictor.calls[0] == ('CCO', {'threshold': 0.6, 'top_k': 10, 'combine_by_target': True})
    assert result.data == seen[0]['data']
    assert result.formatted == seen[0]['formatted']


BAD_ROWS = [
    (TARGET, {'gene_symbol': 3}), (TARGET, {'structure_count': True}),
    (TARGET, {'structure_count': -1}), (TARGET, {'structure_count': '2'}),
    (TARGET, {'recommended_structures': [3]}),
    (TARGET, {'recommended_structures': [{'resolution': float('inf')}]}),
    (TARGET, {'recommended_structures': [{'structure_id': 3}]}),
    (TARGET, {'source': []}), (TARGET, {'stale': 1}),
    (REVERSE, {'final_similarity': float('nan')}),
    (REVERSE, {'morgan_similarity': 1.01}), (REVERSE, {'maccs_similarity': -0.1}),
    (REVERSE, {'final_similarity': True}), (REVERSE, {'final_similarity': '0.9'}),
    (REVERSE, {'similar_count': -1}), (REVERSE, {'similar_count': 1.5}),
    (REVERSE, {'target_identifier': []}), (REVERSE, {'assay': []}),
    (REVERSE, {'assay': {'value': float('inf')}}), (REVERSE, {'assay': {'units': 3}}),
]


@pytest.mark.parametrize('name,changes', BAD_ROWS)
@pytest.mark.parametrize('status,success', [('succeeded', True), ('partial', False), ('failed', False)])
def test_malformed_rows_fail_closed_even_on_failure(name, changes, status, success):
    row = target_row() if name == TARGET else reverse_row()
    row.update(changes)
    raw = {'success': success, 'status': status, 'data': [row]}
    result = adapter(RawTool(name, raw)).execute({'query': 'CCO'})
    assert result.error.code == AgentErrorCode.INVALID_OUTPUT
    assert not result.error.details
    assert result.data is None


@pytest.mark.parametrize('changes', [
    {'success': 1}, {'status': 'resolved', 'data': []},
    {'status': 'not_found', 'data': [target_row()]},
    {'status': 'unavailable'}, {'status': 'ambiguous'},
    {'status': 'succeeded', 'success': False}, {'error': ''},
    {'tool_name': REVERSE}, {'data': {}}, {'warnings': 'warning'},
    {'evidence': [3]}, {'artifacts': [{'path': 3}]},
    {'status': 'resolved', 'quality': {'lookup_status': 'not_found'}},
    {'lookup_path': ['local'], 'quality': {'lookup_path': ['remote']}},
    {'quality': {'retryable': 1}}, {'quality': {'service_statuses': [3]}},
])
def test_conflicting_or_malformed_envelopes(changes):
    raw = {'success': True, 'data': [target_row()]}
    raw.update(changes)
    result = adapter(RawTool(raw=raw)).execute({'query': 'EGFR'})
    assert result.error.code == AgentErrorCode.INVALID_OUTPUT
    assert not result.error.details


def test_raw_validator_precedes_domain_checks_and_keeps_exception_classification():
    raw = {'success': 1, 'data': 'bad'}
    tool = RawTool(raw=raw)
    seen = []

    def check(value):
        assert value is raw
        seen.append(value)
        raise ConnectionError('controlled provider error')

    result = adapter(tool).execute({'query': 'EGFR'}, raw_validator=check, allow_retry=False)
    assert result.error.code == AgentErrorCode.PROVIDER_ERROR
    assert seen == [raw]
    assert len(tool.calls) == 1


@pytest.mark.parametrize('depth,valid', [(16, True), (17, False)])
def test_canonical_snapshot_depth(depth, valid):
    raw = {'success': True, 'data': [target_row()]}
    for _ in range(depth):
        raw = ToolResult.error_result(TARGET, AgentErrorCode.PROVIDER_ERROR, 'fixture', {'raw_result': raw})
    result = adapter(RawTool(raw=raw)).execute({'query': 'EGFR'}, allow_retry=False)
    assert (result.error.code != AgentErrorCode.INVALID_OUTPUT) is valid


def test_snapshot_cycles_and_malformed_canonical_failures():
    cyclic = ToolResult.error_result(TARGET, AgentErrorCode.PROVIDER_ERROR, 'fixture')
    cyclic.error.details = {'raw_result': cyclic}
    malformed = ToolResult.error_result(TARGET, AgentErrorCode.PROVIDER_ERROR, 'fixture',
        {'raw_result': {'success': False, 'data': [{'structure_count': 'secret-marker'}]}})
    for raw in (cyclic, malformed):
        result = adapter(RawTool(raw=raw)).execute({'query': 'EGFR'}, allow_retry=False)
        assert result.error.code == AgentErrorCode.INVALID_OUTPUT
        assert not result.error.details


def test_mapping_never_mutates_raw_and_preserves_extensions():
    raw = {'success': True, 'status': 'resolved', 'data': [target_row()],
           'lookup_path': ['fixture'], 'quality': {'lookup_status': 'resolved',
           'lookup_path': ['fixture'], 'extension': {'opaque': 'kept'}}}
    before = deepcopy(raw)
    result = adapter(RawTool(raw=raw)).execute({'query': 'EGFR'})
    assert result.success
    assert raw == before
    assert result.data == before['data']
    assert result.quality == before['quality']


@pytest.mark.parametrize('raw', [
    {'success': True, 'status': 'succeeded', 'quality': {'lookup_status': 'unavailable'}},
    {'success': True, 'status': 'succeeded', 'data': [], 'quality': {'lookup_status': 'resolved'}},
    {'success': True, 'status': 'succeeded', 'data': [target_row()], 'quality': {'lookup_status': 'not_found'}},
    {'success': True, 'quality': {'lookup_status': 'invented'}},
])
@pytest.mark.parametrize('canonical', [False, True])
def test_lookup_metadata_cannot_contradict_canonical_status(raw, canonical):
    if canonical:
        raw = ToolResult(TARGET, raw['success'], '', data=raw.get('data'), quality=raw['quality'])
    result = adapter(RawTool(raw=raw)).execute({'query': 'EGFR'})
    assert result.error.code == AgentErrorCode.INVALID_OUTPUT


@pytest.mark.parametrize('field', ['final_similarity', 'morgan_similarity', 'maccs_similarity', 'similar_count', 'assay'])
def test_explicit_null_reverse_measurement_is_not_a_missing_measurement(field):
    row = reverse_row()
    row[field] = None
    result = adapter(RawTool(REVERSE, {'success': True, 'data': [row]})).execute({'query': 'CCO'})
    assert result.error.code == AgentErrorCode.INVALID_OUTPUT


@pytest.mark.parametrize('kind', ['producer', 'caller', 'raw_check', 'normalization', 'normalized_check'])
def test_all_stages_share_worker_deadline_and_slot(monkeypatch, kind):
    import threading
    import time
    from src.agent.tooling import adapters

    tool = RawTool(raw={'success': True, 'data': [target_row()]})
    tool.timeout_seconds = 0.03
    boundary = adapter(tool)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    threads = []
    original_execute = tool.execute
    original_check = boundary._validate_observation
    original_redact = adapters.redact_sensitive
    checked = []

    def block():
        entered.set()
        release.wait(2)

    def execute(payload):
        threads.append(threading.get_ident())
        if kind == 'producer':
            block()
        return original_execute(payload)

    def caller(raw):
        threads.append(threading.get_ident())
        if kind == 'caller':
            block()

    def check(raw):
        threads.append(threading.get_ident())
        checked.append(raw)
        if kind == ('normalized_check' if isinstance(raw, ToolResult) else 'raw_check'):
            block()
        original_check(raw)
        if isinstance(raw, ToolResult):
            finished.set()

    def redact(value, fields=None):
        threads.append(threading.get_ident())
        if kind == 'normalization':
            block()
        return original_redact(value, fields)

    monkeypatch.setattr(tool, 'execute', execute)
    monkeypatch.setattr(boundary, '_validate_observation', check)
    monkeypatch.setattr(adapters, 'redact_sensitive', redact)
    try:
        start = time.monotonic()
        result = boundary.execute('EGFR', allow_retry=False, raw_validator=caller)
        assert time.monotonic() - start < 0.8
        assert entered.is_set()
        assert result.error.code == AgentErrorCode.TOOL_TIMEOUT
        assert result.quality['invocation_may_still_be_running'] is True
        blocked = boundary.execute('EGFR', allow_retry=False)
        assert blocked.quality['capacity_exhausted'] is True
    finally:
        release.set()
    assert finished.wait(2)
    assert len(tool.calls) == 1
    assert len(checked) == 2
    assert len(set(threads)) == 1
    assert threads[0] != threading.get_ident()
    assert boundary._invocation_slots.acquire(timeout=2)
    boundary._invocation_slots.release()


@pytest.mark.parametrize('retryable,calls', [(False, 1), (True, 2)])
def test_real_wrapper_retry_and_close_stay_owned(retryable, calls):
    tool = TargetDatabaseTool()
    service = Service('unavailable', retryable)
    tool._service = service
    registry = build_tool_registry([tool])
    boundary = registry.resolve(TARGET)
    assert boundary.health()['readiness'] == 'not_probed'
    assert boundary.health()['available'] is None
    result = boundary.execute('EGFR')
    assert result.error.code == AgentErrorCode.PROVIDER_ERROR
    assert len(service.calls) == calls
    registry.close()
    assert service.closed == 1
    assert tool._service is None


@pytest.mark.parametrize('exception,code', [(ValueError, AgentErrorCode.INTERNAL_ERROR),
                                         (ConnectionError, AgentErrorCode.PROVIDER_ERROR)])
def test_caller_exception_not_swallowed(exception, code):
    calls = []

    def validator(raw):
        calls.append(raw)
        raise exception('controlled caller failure')

    tool = RawTool(raw={'success': True, 'status': 'resolved', 'data': []})
    result = adapter(tool).execute('EGFR', raw_validator=validator, allow_retry=False)
    assert result.error.code == code
    assert len(calls) == len(tool.calls) == 1


@pytest.mark.parametrize('name', [TARGET, REVERSE])
def test_complete_canonical_observation_is_not_projected(name):
    from src.agent.contracts import ToolProvenance, WorkflowArtifact
    row = target_row() if name == TARGET else reverse_row()
    row['unknown_science_extension'] = {'not-a-contract-field': float('inf')}
    raw = ToolResult.success_result(name, data=[row], message='fixture message', formatted='fixture text',
        warnings=['fixture'], evidence=[{'source': 'fixture', 'unknown': {'value': 'opaque'}}],
        artifacts=[WorkflowArtifact('fixture', 'relative.txt', 'fixture', metadata={'opaque': [2]})],
        quality={'demo_mode': True, 'extension': {'opaque': 4}},
        provenance=ToolProvenance(name, demo_mode=True, fallback_used=True))
    before = deepcopy(raw)
    result = adapter(RawTool(name, raw)).execute({'query': 'CCO'})
    before.elapsed_ms = result.elapsed_ms
    assert result == before


def test_target_evidence_validator_is_reused_for_raw_and_normalized(monkeypatch):
    from src.agent.validators.domain_validators import TargetEvidenceValidator
    calls = []

    def reject(self, observation):
        calls.append(observation)
        return 'controlled domain rejection'

    monkeypatch.setattr(TargetEvidenceValidator, 'validate', reject)
    result = adapter(RawTool(raw={'success': True, 'data': [target_row()]})).execute('EGFR')
    assert result.error.code == AgentErrorCode.INVALID_OUTPUT
    assert len(calls) == 1


@pytest.mark.parametrize('changes', [{'id': True}, {'status': []}, {'source_record_id': 3},
    {'retrieved_at': 3}, {'expires_at': False}, {'stale': 'false'}, {'url': []}])
def test_known_source_metadata_is_strict(changes):
    raw = {'success': True, 'data': [], 'evidence': [{'source': 'fixture', **changes}]}
    result = adapter(RawTool(raw=raw)).execute('EGFR')
    assert result.error.code == AgentErrorCode.INVALID_OUTPUT


@pytest.mark.parametrize('identifier', ['', '  ', None, 3, False])
def test_supplied_reverse_identifier_must_be_stable_text(identifier):
    raw = {'success': True, 'data': [{**reverse_row(), 'target_identifier': identifier}]}
    result = adapter(RawTool(REVERSE, raw)).execute('CCO')
    assert result.error.code == AgentErrorCode.INVALID_OUTPUT


@pytest.mark.parametrize('status', list(ObservationStatus))
@pytest.mark.parametrize('name', [TARGET, REVERSE])
def test_all_canonical_statuses_validate_records(name, status):
    raw = ToolResult(name, status == ObservationStatus.SUCCEEDED, '', status=status,
                     data=[{'gene_symbol': ['malformed']}])
    result = adapter(RawTool(name, raw)).execute('CCO', allow_retry=False)
    assert result.error.code == AgentErrorCode.INVALID_OUTPUT
    assert not result.error.details


def test_actual_target_input_precedence_normalization_dedup_and_cap():
    service = Service('resolved')
    tool = TargetDatabaseTool()
    tool._service = service
    rows = ({'gene_symbol': 'BuChE', 'target_name': 'ignored', 'opaque': {'kept': True}},
            {'gene_symbol': None, 'target_gene': 'BCHE'},
            {'uniprot_id': 'P00533'}, {'protein_name': 'BRAF'},
            'KRAS', 'JAK2', 'MET')
    seen = []
    result = adapter(tool).execute({'query': rows}, raw_validator=seen.append)
    assert result.success
    assert service.calls == ['BCHE', 'P00533', 'BRAF', 'KRAS', 'JAK2']
    assert seen[0]['query'] is rows


@pytest.mark.parametrize('kind', ['raw', 'canonical'])
def test_wrong_provenance_and_corrupt_artifacts_are_not_projected(kind):
    from src.agent.contracts import ToolProvenance, WorkflowArtifact
    provenance = ToolProvenance(REVERSE)
    artifact = WorkflowArtifact('fixture', 3, 'fixture')
    for change in ({'provenance': provenance if kind == 'canonical' else provenance.to_dict()},
                   {'artifacts': [artifact if kind == 'canonical' else vars(artifact)]}):
        raw = {'success': True, 'data': [target_row()], **change}
        if kind == 'canonical':
            raw = ToolResult(TARGET, True, '', data=raw['data'], **change)
        result = adapter(RawTool(raw=raw)).execute('EGFR')
        assert result.error.code == AgentErrorCode.INVALID_OUTPUT
        assert not result.error.details


@pytest.mark.parametrize('name', [TARGET, REVERSE])
def test_raw_complete_observation_matches_compat_without_projection(name):
    from src.agent.contracts import ToolProvenance
    from src.agent.tools.base_tool import execute_tool_compat
    raw = {'success': True, 'data': [target_row() if name == TARGET else reverse_row()],
        'warnings': ['fixture warning'], 'evidence': [{'source': 'fixture', 'extension': {'opaque': True}}],
        'artifacts': [{'artifact_type': 'fixture', 'path': 'relative.txt', 'label': 'label',
                       'metadata': {'opaque': {'x': True}}}],
        'quality': {'demo_mode': True, 'fallback_used': True, 'extension': {'x': 3}},
        'provenance': ToolProvenance(name, demo_mode=True, fallback_used=True).to_dict(),
        'message': 'fixture message', 'formatted': 'fixture formatted'}
    before = deepcopy(raw)
    expected = execute_tool_compat(RawTool(name, deepcopy(raw)), 'CCO')
    result = adapter(RawTool(name, raw)).execute('CCO')
    expected.elapsed_ms = result.elapsed_ms
    assert result == expected
    assert raw == before


@pytest.mark.parametrize('name', [TARGET, REVERSE])
def test_registered_schema_models_can_be_used_as_inputs(name):
    boundary = adapter(RawTool(name))
    query = 'CCO' if name == REVERSE else {'gene_symbol': 'EGFR', 'extra': [1, 2]}
    model = boundary.spec.input_schema.model_validate({'query': query})
    assert boundary.execute(model).success
    assert boundary.tool.calls == [query]


def test_normalized_result_is_checked_after_redaction_inside_worker(monkeypatch):
    from src.agent.tooling import adapters
    monkeypatch.setattr(adapters, 'redact_sensitive', lambda value, fields: [{'gene_symbol': False}])
    result = adapter(RawTool(raw={'success': True, 'data': [target_row()]})).execute('EGFR')
    assert result.error.code == AgentErrorCode.INVALID_OUTPUT
    assert not result.error.details


@pytest.mark.parametrize('elapsed', [True, -1, '2', float('inf')])
@pytest.mark.parametrize('canonical', [False, True])
def test_supplied_elapsed_metadata_is_strict(elapsed, canonical):
    raw = {'success': True, 'data': [], 'elapsed_ms': elapsed}
    if canonical:
        raw = ToolResult.success_result(TARGET, data=[], elapsed_ms=elapsed)
    result = adapter(RawTool(raw=raw)).execute('EGFR')
    assert result.error.code == AgentErrorCode.INVALID_OUTPUT
    assert type(result.elapsed_ms) is int and result.elapsed_ms >= 0


@pytest.mark.parametrize('name', [TARGET, REVERSE])
@pytest.mark.parametrize('canonical', [False, True])
@pytest.mark.parametrize('status,success', [('succeeded', True), ('failed', False),
                                         ('partial', True), ('partial', False)])
@pytest.mark.parametrize('supplied_elapsed', [None, 0, 37])
def test_elapsed_preserves_legacy_producer_scope_and_object_semantics(
        monkeypatch, name, canonical, status, success, supplied_elapsed):
    import time
    from dataclasses import replace
    from src.agent.contracts import AgentExecutionError
    from src.agent.tooling.adapters import LegacyPythonToolAdapter

    # Exact binary fractions: no sleeping or wall-clock timing tolerance.
    clock = {'now': 1000.0}
    monkeypatch.setattr(time, 'perf_counter', lambda: clock['now'])
    data = [target_row() if name == TARGET else reverse_row()] if status != 'failed' else None
    error = None if success else {'code': 'internal_error', 'message': 'fixture failure',
                                 'details': {'fixture': True}}

    def observe(typed):
        clock['now'] = 1000.0
        raw = {'success': success, 'status': status, 'data': deepcopy(data),
               'error': deepcopy(error), 'message': 'fixture', 'elapsed_ms': supplied_elapsed}
        if canonical:
            raw = ToolResult(name, success, 'fixture', data=deepcopy(data),
                status=ObservationStatus(status), elapsed_ms=supplied_elapsed,
                error=None if success else AgentExecutionError(
                    AgentErrorCode.INTERNAL_ERROR, 'fixture failure', {'fixture': True}))
        before = deepcopy(raw)

        class TimedTool(RawTool):
            def execute(self, query):
                clock['now'] += 0.125  # producer, before CompletedInvocation
                return super().execute(query)

        tool = TimedTool(name, raw)
        boundary = adapter(tool)
        if not typed:
            boundary = LegacyPythonToolAdapter(
                replace(boundary.spec, input_schema=None, output_schema=None), tool)
        checked_elapsed = []
        if typed:
            original_check = boundary._validate_observation

            def check(observation):
                if isinstance(observation, ToolResult):
                    checked_elapsed.append(observation.elapsed_ms)
                original_check(observation)

            monkeypatch.setattr(boundary, '_validate_observation', check)
        callback_elapsed = []

        def caller(observation):
            assert observation is raw
            callback_elapsed.append(observation.elapsed_ms if canonical else observation['elapsed_ms'])
            clock['now'] += 0.0625  # callback also belongs to framework duration

        result = boundary.execute('CCO', allow_retry=False, raw_validator=caller)
        assert len(tool.calls) == 1
        assert callback_elapsed == [supplied_elapsed]
        assert result.status == ObservationStatus(status)
        assert result.success is success
        if canonical:
            assert result is raw  # retain legacy ownership; do not copy the producer result
            before.elapsed_ms = result.elapsed_ms
        assert raw == before
        return result.elapsed_ms, checked_elapsed

    expected = supplied_elapsed if canonical and supplied_elapsed is not None else 187
    legacy_elapsed, _ = observe(False)
    actual_elapsed, checked_elapsed = observe(True)
    assert legacy_elapsed == expected
    assert actual_elapsed == legacy_elapsed
    # The worker must retain missing canonical elapsed as None until the outer
    # framework timing step. Raw dict elapsed remains framework-owned as before.
    assert checked_elapsed == ([supplied_elapsed, supplied_elapsed] if canonical else [None])
