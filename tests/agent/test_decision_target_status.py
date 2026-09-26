"""Narrow SingleAttemptTool lookup guard, actual typed adapter and owned calls."""
import asyncio
from copy import deepcopy
from dataclasses import replace

import pytest

from src.agent.contracts import ToolResult, ObservationStatus
from src.agent.harness.decision_execution import SingleAttemptTool, settle_owned_call
from src.agent.runtime.worker_ownership import WorkerOwner
from src.agent.tooling.adapters import LegacyPythonToolAdapter
from src.agent.tooling.factory import build_tool_registry
from src.agent.tools.target_database_tool import TargetDatabaseTool
from test_target_tool_contract import RawTool, Service, target_row


@pytest.fixture
def boundary():
    registries = []
    owner = WorkerOwner()

    def make(tool):
        registry = build_tool_registry([tool])
        registries.append(registry)
        return registry.resolve(tool.name)

    def call(fn):
        return asyncio.run(settle_owned_call(fn, worker_owner=owner))

    yield make, call
    asyncio.run(owner.settle())
    for registry in registries:
        registry.close()


@pytest.mark.parametrize('status', ['not_found', 'resolved'])
def test_actual_target_wrapper_matches_direct_typed_adapter(boundary, monkeypatch, status):
    make, call = boundary
    tool = TargetDatabaseTool()
    tool._service = Service(status)
    adapter = make(tool)
    raw_values, validated = [], []
    execute, validate = tool.execute, adapter._validate_observation

    def capture(query):
        raw = execute(query)
        raw_values.append((raw, deepcopy(raw)))
        return raw

    def inspect(raw):
        if type(raw) is dict:
            assert raw is raw_values[-1][0]
            assert raw == raw_values[-1][1]
            validated.append(raw['status'])
        return validate(raw)

    monkeypatch.setattr(tool, 'execute', capture)
    monkeypatch.setattr(adapter, '_validate_observation', inspect)
    direct = call(lambda: adapter.execute({'query': 'EGFR'}, allow_retry=False))
    actual = call(lambda: SingleAttemptTool(adapter).execute({'query': 'EGFR'}))
    assert actual.success and actual.status is ObservationStatus.SUCCEEDED
    assert replace(actual, elapsed_ms=0) == replace(direct, elapsed_ms=0)
    assert validated == [status, status]
    assert all(raw == original for raw, original in raw_values)
    assert actual.quality['lookup_status'] == status
    assert actual.quality['service_statuses'] == [status]
    assert actual.quality['lookup_path'] == ['fixture-local', 'fixture-authoritative']
    assert tool._service.calls == ['EGFR', 'EGFR']


@pytest.mark.parametrize('mode', ['not_found_rows', 'resolved_empty', 'bool_one',
    'bool_string', 'attached_error', 'unknown_status', 'misleading_tool_name',
    'conflicting_lookup_metadata', 'conflicting_lookup_path', 'false_resolved',
    'true_partial', 'true_ambiguous', 'true_unavailable'])
def test_raw_contradictions_never_promoted(boundary, mode):
    make, call = boundary
    raw = dict(success=True, status='resolved', data=[target_row()],
               quality={'lookup_status': 'resolved'}, lookup_path=['fixture'])
    if mode == 'not_found_rows':
        raw.update(status='not_found', quality={'lookup_status': 'not_found'})
    elif mode == 'resolved_empty':
        raw['data'] = []
    elif mode == 'bool_one':
        raw['success'] = 1
    elif mode == 'bool_string':
        raw['success'] = 'true'
    elif mode == 'attached_error':
        raw['error'] = 'error cannot become success'
    elif mode == 'unknown_status':
        raw['status'] = 'made_up_success'
    elif mode == 'misleading_tool_name':
        raw['tool_name'] = 'property_calculator'
    elif mode == 'conflicting_lookup_metadata':
        raw['quality']['lookup_status'] = 'not_found'
    elif mode == 'conflicting_lookup_path':
        raw['quality']['lookup_path'] = ['different']
    elif mode == 'false_resolved':
        raw['success'] = False
    else:
        raw['status'] = mode.removeprefix('true_')
        raw['quality']['lookup_status'] = raw['status']
    before = deepcopy(raw)
    tool = RawTool(raw=raw)
    result = call(lambda: SingleAttemptTool(make(tool)).execute({'query': 'EGFR'}))
    assert not result.success
    assert result.status is not ObservationStatus.SUCCEEDED
    assert raw == before
    assert tool.calls == ['EGFR']


@pytest.mark.parametrize('status', ['ambiguous', 'partial', 'unavailable'])
def test_non_success_lookup_states_remain_non_success(boundary, status):
    make, call = boundary
    tool = TargetDatabaseTool()
    tool._service = Service(status)
    adapter = make(tool)
    direct = call(lambda: adapter.execute({'query': 'EGFR'}, allow_retry=False))
    result = call(lambda: SingleAttemptTool(adapter).execute({'query': 'EGFR'}))
    assert not result.success and result.status is not ObservationStatus.SUCCEEDED
    if status == 'partial':
        # The actual legacy wrapper says success=True/status=partial. The
        # approved exception is only resolved/not_found; preserve rejection.
        assert direct.status is ObservationStatus.PARTIAL
        assert result.message == 'conflicting_tool_result'
        return
    assert result.quality.get('lookup_status') == direct.quality.get('lookup_status') == status
    assert result.status == direct.status


@pytest.mark.parametrize('kind', ['legacy_target_name', 'reverse', 'other_tool'])
def test_domain_mapping_never_applies_by_name_alone_or_other_tools(boundary, kind):
    make, call = boundary
    name = {'legacy_target_name': 'target_database_search', 'reverse': 'reverse_target_predictor',
            'other_tool': 'property_calculator'}[kind]
    tool = RawTool(name=name, raw=dict(success=True, status='not_found', data=[]))
    adapter = make(tool)
    if kind == 'legacy_target_name':
        adapter = LegacyPythonToolAdapter(adapter.spec, tool)
    result = call(lambda: SingleAttemptTool(adapter).execute({'query': 'CCO'}))
    assert not result.success and result.message == 'conflicting_tool_result'


@pytest.mark.parametrize('mode', ['valid', 'wrong_identity', 'domain_status', 'wrong_lookup', 'nonbool'])
def test_canonical_envelopes_still_use_generic_and_typed_validation(boundary, mode):
    make, call = boundary
    raw = ToolResult.success_result('target_database_search', [], quality={'lookup_status': 'not_found'})
    if mode == 'wrong_identity':
        raw.tool_name = 'property_calculator'
    elif mode == 'domain_status':
        raw.status = 'not_found'
    elif mode == 'wrong_lookup':
        raw.quality['lookup_status'] = 'resolved'
    elif mode == 'nonbool':
        raw.success = 1
    tool = RawTool(raw=raw)
    result = call(lambda: SingleAttemptTool(make(tool)).execute({'query': 'EGFR'}))
    assert result.success is (mode == 'valid')
    if mode == 'valid':
        assert result.quality['lookup_status'] == 'not_found'


@pytest.mark.parametrize('mode', ['nonnative', 'cycle', 'oversize', 'nan'])
def test_native_bounds_before_status_view_or_typed_validator(boundary, monkeypatch, mode):
    make, call = boundary
    class Hostile:
        def __deepcopy__(self, memo):
            pytest.fail('copied before native bound')
    raw = dict(success=True, status='not_found', data=[])
    if mode == 'cycle':
        raw['extension'] = raw
    else:
        raw['extension'] = {'nonnative': Hostile(), 'oversize': 'x' * 65537,
                            'nan': float('nan')}[mode]
    tool = RawTool(raw=raw)
    adapter = make(tool)
    seen = []
    monkeypatch.setattr(adapter, '_validate_observation', lambda value: seen.append(value))
    result = call(lambda: SingleAttemptTool(adapter).execute({'query': 'EGFR'}))
    assert not result.success and not seen
