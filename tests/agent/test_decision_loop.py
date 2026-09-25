"""Isolated controller contracts; scripted models are not real LLM acceptance."""
import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.agent.contracts import AgentContext, AgentErrorCode, ToolResult, RunOutcome
from src.agent.contracts.decision import ToolDecision, FinishDecision, ClarifyDecision
from src.agent.decision_transport import DecisionResponse
from src.agent.orchestrators.base import WorkflowStep
from src.agent.orchestrators.workflow import WorkflowOrchestrator
from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
from src.agent.runtime.event_bus import AgentEventBus
from src.agent.runtime.run_session import WorkflowRunSession, SessionLifecycleError
from src.agent.tooling.factory import build_tool_registry
from tests.agent.test_analysis_contract import analysis_rows
from tests.agent.test_family_activity_tool import boundary as family_boundary, conflict_row


def tool(name='property_calculator', arguments=None):
    return ToolDecision(version='1', action='tool', tool_name=name,
                        arguments=arguments or {'input_ref': 'user'}, purpose='compute')


def finish(ids=(), text='model prose must not become scientific facts', kind='scientific'):
    return FinishDecision(version='1', action='finish', response_kind=kind,
                          text=text, evidence_ids=list(ids))


def clarify():
    return ClarifyDecision(version='1', action='clarify', question='Please provide input',
                           missing_fields=['smiles'])


class ScriptedModel:
    def __init__(self, decisions):
        self.decisions = iter(decisions)
        self.messages = []

    async def decide(self, messages, **kwargs):
        self.messages.append(json.loads(json.dumps(messages)))
        decision = next(self.decisions)
        if callable(decision):
            decision = decision(messages)
        return DecisionResponse(decision, None, f'call-{len(self.messages)}',
                                {'model': 'scripted-test', 'request_attempts': 1})


class CountingTool:
    description = 'Explicit contract test double'
    version = 'test-1'
    timeout_seconds = 0.5

    def __init__(self, name='property_calculator', fail=False, demo=False):
        self.name, self.fail, self.demo = name, fail, demo
        self.inputs = []

    def execute(self, query):
        self.inputs.append(query)
        if self.fail:
            return ToolResult.error_result(self.name, AgentErrorCode.MODEL_UNAVAILABLE,
                                          'Test dependency unavailable', warnings=['test-warning'])
        rows = (analysis_rows(self.name) if self.name in {
            'property_calculator', 'drug_likeness_assessment', 'admet_predictor',
        } else {'smiles': 'CCO', 'molecular_weight': 46.069})
        if self.name == 'property_calculator':
            # Preserve this explicit fixture's observed precision/assertions.
            rows[0]['properties']['molecular_weight'] = 46.069
        return ToolResult.success_result(self.name, rows,
                                         quality={'demo_mode': self.demo}, warnings=['test-warning'])


def last_observation(messages):
    return json.loads(messages[-1]['content'])


@pytest.mark.parametrize('cancelled', [False, True])
def test_owned_loop_has_no_terminal_until_nested_workers_join(setup_loop, monkeypatch, cancelled):
    import inspect
    import threading
    from tests.agent.test_worker_ownership import (
        owner_if_available, instrument_executors, signalled, pending,
    )
    entered, release, exited, allow_join = (threading.Event() for _ in range(4))
    records = instrument_executors(monkeypatch, allow_join)

    class Blocked(CountingTool):
        timeout_seconds = 0.03

        def execute(self, query):
            entered.set()
            try:
                assert release.wait(5)
                return super().execute(query)
            finally:
                exited.set()

    b = setup_loop([tool(), finish_last], [Blocked()])
    owner = owner_if_available()

    async def exercise():
        options = ({'worker_owner': owner} if 'worker_owner' in inspect.signature(b.loop.run).parameters
                   else {})  # RED exercises actual pre-owner loop behavior.
        task = asyncio.create_task(b.loop.run(AgentContext('CCO', 'owned-loop'),
            request_kind='scientific', allowed_tools={'property_calculator'},
            required_tools={'property_calculator'}, event_bus=b.bus, **options))
        try:
            await signalled(entered)
            await signalled(records[0].future_done)
            await pending(task)
            assert b.store.get_run('owned-loop')['status'] == 'running'
            if cancelled:
                for _ in range(3):
                    task.cancel()
                    await pending(task)
            release.set()
            await signalled(exited)
            await pending(task)
            assert len(b.model.messages) == 1
            allow_join.set()
            result = await task
            assert all(e.joined.is_set() for e in records)
            assert not result.success
            assert result.outcome == (RunOutcome.CANCELLED if cancelled else RunOutcome.FAILED)
            assert result.tool_results[0].error.code == AgentErrorCode.TOOL_TIMEOUT
            assert len(b.model.messages) == 1 and len(b.tools[0].inputs) == 1
            assert owner.status == 'settled' and owner.pending_roots == 0
        finally:
            release.set()
            allow_join.set()
            await asyncio.gather(task, return_exceptions=True)
            for executor in records:
                await asyncio.to_thread(executor.shutdown, wait=True)

    asyncio.run(exercise())


def test_owned_loop_failed_join_retains_owner_without_terminal(setup_loop, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from src.agent.runtime.worker_ownership import WorkerOwner, WorkerCleanupError
    created = []

    class FailedJoin(ThreadPoolExecutor):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            created.append(self)

        def shutdown(self, wait=True, **kwargs):
            if wait:
                raise RuntimeError('private join diagnostic')
            return super().shutdown(wait=wait, **kwargs)

    monkeypatch.setattr('src.agent.tooling.adapters.ThreadPoolExecutor', FailedJoin)
    b = setup_loop([tool(), finish_last])
    owner = WorkerOwner()

    async def exercise():
        try:
            with pytest.raises(WorkerCleanupError, match='^Owned worker cleanup remains unresolved$'):
                await b.loop.run(AgentContext('CCO', 'join-failed'), request_kind='scientific',
                    allowed_tools={'property_calculator'}, required_tools={'property_calculator'},
                    event_bus=b.bus, worker_owner=owner)
            assert owner.status == 'unresolved' and owner.pending_roots == 1
            assert b.store.get_run('join-failed')['status'] == 'running'
            terminal = {'task_completed', 'task_failed', 'task_cancelled', 'task_rejected'}
            assert not terminal.intersection(e.event.value for e in b.bus.events)
            assert len(b.model.messages) == 1 and len(b.tools[0].inputs) == 1
        finally:
            for executor in created:
                await asyncio.to_thread(ThreadPoolExecutor.shutdown, executor, wait=True)

    asyncio.run(exercise())


@pytest.mark.parametrize('owned', [False, True])
@pytest.mark.parametrize('failure', ['submit', 'task_creation'])
def test_outer_session_dispatch_failure_closes_only_unstarted_root(setup_loop, monkeypatch, owned, failure):
    """Migrated SPEC repro: real loop, zero execution, settled failure journal."""
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from src.agent.runtime.worker_ownership import WorkerOwner
    failed = threading.Event()

    def is_outer(call):
        name = getattr(call, '__qualname__', '')
        return name.endswith('_ActionRoot.run') or name.endswith('settle_action.<locals>.advance')

    class DefaultExecutor(ThreadPoolExecutor):
        def submit(self, fn, *args, **kwargs):
            call = getattr(fn, 'args', (None,))[0]
            if failure == 'submit' and not failed.is_set() and is_outer(call):
                failed.set()
                raise RuntimeError('synthetic outer submit failure')
            return super().submit(fn, *args, **kwargs)

    original_create_task = asyncio.create_task

    def create_task(coro, **kwargs):
        frame = getattr(coro, 'cr_frame', None)
        if (failure == 'task_creation' and not failed.is_set() and frame is not None
                and is_outer(frame.f_locals.get('func'))):
            failed.set()
            # Caller, not the failing task factory, owns closing this coroutine.
            raise RuntimeError('synthetic outer task creation failure')
        return original_create_task(coro, **kwargs)

    monkeypatch.setattr(asyncio, 'create_task', create_task)
    b = setup_loop([tool(), finish_last])
    owner = WorkerOwner() if owned else None

    async def exercise():
        asyncio.get_running_loop().set_default_executor(DefaultExecutor(max_workers=4))
        task = asyncio.create_task(b.loop.run(AgentContext('CCO', 'outer-dispatch-failed'),
            request_kind='scientific', allowed_tools={'property_calculator'},
            required_tools={'property_calculator'}, event_bus=b.bus, worker_owner=owner))
        try:
            assert await asyncio.to_thread(failed.wait, 3)
            done, _ = await asyncio.wait({task}, timeout=0.2)
            assert not b.tools[0].inputs and len(b.model.messages) == 1
            assert done, 'never-started outer dispatch left an open action root'
            result = await task
            assert not result.success
            assert b.store.get_run('outer-dispatch-failed')['status'] == 'failed'
            assert [e.event.value for e in b.bus.events].count('task_failed') == 1
            if owned:
                assert owner.status == 'settled' and owner.pending_roots == 0
        finally:
            # RED-only teardown: close the proven empty, never-dispatched root
            # AFTER the failure assertion, so no permanently blocked test helper.
            if owner:
                for root in tuple(owner._roots):
                    if not root.finished and not root.records:
                        root.run(lambda: None)
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(exercise())


def finish_last(messages):
    return finish([last_observation(messages)['quality']['evidence_id']])


@pytest.fixture
def setup_loop(tmp_path):
    registries = []

    def build(decisions, tools=None, *, legacy_tools=(), **kwargs):
        from src.agent.harness.decision_loop import ModelDecisionLoop
        supplied = tools if tools is not None else [CountingTool()]
        registry = build_tool_registry(supplied)
        if legacy_tools:
            # Explicit downstream-guard probes only: domain adapters correctly
            # reject these intentionally contradictory observations earlier.
            # No invocation/resources have started; the final registry owns and
            # closes every supplied tool once, including each retained adapter.
            from src.agent.tooling.adapters import LegacyPythonToolAdapter
            from src.agent.tooling.factory import LegacyQueryInput
            from src.agent.tooling.registry import ToolRegistry
            adapters = registry.as_mapping()
            assert set(legacy_tools) <= adapters.keys()
            registry = ToolRegistry()
            for name, adapter in adapters.items():
                if name in legacy_tools:
                    adapter = LegacyPythonToolAdapter(
                        replace(adapter.spec, input_schema=LegacyQueryInput, output_schema=None),
                        adapter.tool, readiness_unknown=adapter.readiness_unknown)
                registry.register(adapter)
        registries.append(registry)
        store = SQLiteAgentStateStore(tmp_path / f'state-{len(registries)}.sqlite')
        bus = AgentEventBus(state_store=store)
        model = ScriptedModel(decisions)
        return SimpleNamespace(loop=ModelDecisionLoop(model, registry, store, **kwargs),
                               registry=registry, store=store, bus=bus, model=model, tools=supplied)
    yield build
    for registry in registries:
        registry.close()


def run(bundle, **kwargs):
    return asyncio.run(bundle.loop.run(AgentContext('SMILES: CCO', 'trace-test'),
        allowed_tools=kwargs.pop('allowed_tools', {'property_calculator', 'drug_likeness_assessment'}),
        required_tools=kwargs.pop('required_tools', {'property_calculator'}),
        request_kind=kwargs.pop('request_kind', 'scientific'), event_bus=bundle.bus, **kwargs))


def test_dynamic_session_uses_existing_validation_ledger_and_keeps_running():
    source = CountingTool()
    registry = build_tool_registry([source])
    try:
        session = WorkflowRunSession(WorkflowOrchestrator(), AgentContext('CCO', 'one'),
                                     [], registry.as_mapping(), dynamic=True)
        session.start()
        session.append_step(WorkflowStep('round-1', source.name, {'query': 'CCO'}, required=False))
        advance = session.execute_step(0)
        assert not advance.terminal
        assert session.results[0].quality['evidence_id']
        assert session.results[0].provenance.input_digest
        result = session.finish_dynamic('observed')
        assert result.final_answer == 'observed'
        with pytest.raises(SessionLifecycleError):
            session.append_step(WorkflowStep('late', source.name))
    finally:
        registry.close()


def test_exclusive_start_never_overwrites_trace_across_store_instances(tmp_path):
    one = SQLiteAgentStateStore(tmp_path / 'state.sqlite')
    two = SQLiteAgentStateStore(tmp_path / 'state.sqlite')
    one.start_run({'trace_id': 'same', 'user_id': 'owner'}, exclusive=True)
    with pytest.raises(Exception):
        two.start_run({'trace_id': 'same', 'user_id': 'intruder'}, exclusive=True)
    assert one.get_run('same')['user_id'] == 'owner'


def test_loop_uses_model_observations_not_planner(setup_loop, monkeypatch):
    from src.agent.planning.task_planner import TaskPlanner
    monkeypatch.setattr(TaskPlanner, 'plan', lambda *a, **k: pytest.fail('static planner called'))
    b = setup_loop([tool(), finish_last])
    result = run(b)
    assert result.success
    assert result.metadata['backend'] == 'model_decision_loop'
    assert len(b.model.messages) == 2
    assert b.tools[0].inputs == ['SMILES: CCO']
    assert '46.069' in result.final_answer
    assert 'model prose' not in result.final_answer
    events = [e.event.value for e in b.bus.events]
    assert events.count('task_started') == events.count('task_completed') == 1
    assert events.count('planning_started') == events.count('planning_completed') == 2
    assert all(e.progress is None for e in b.bus.events if e.event.value.startswith('tool_'))


@pytest.mark.parametrize('failed', [False, True])
def test_next_action_changes_with_observation(setup_loop, failed):
    def branch(messages):
        return clarify() if not last_observation(messages)['success'] else tool('drug_likeness_assessment')
    b = setup_loop([tool(), branch, finish_last],
                   [CountingTool(fail=failed), CountingTool('drug_likeness_assessment')])
    result = run(b)
    assert len(b.tools[1].inputs) == (0 if failed else 1)
    assert result.success is (not failed)
    if failed:
        assert result.metadata['waiting_for_input']
        assert b.store.get_run('trace-test')['status'] == 'waiting_for_input'


@pytest.mark.parametrize('bad_tool,arguments', [
    ('run_docking', None), ('llm_molecular_generator', None),
    ('property_calculator', {'query': 'invented SMILES'}),
    ('property_calculator', {'input_ref': 'other-trace'}),
    ('property_calculator', {'input_ref': 'user', 'smiles': 'CCN'}),
])
def test_gate_rejects_unknown_tools_and_model_invented_inputs(setup_loop, bad_tool, arguments):
    b = setup_loop([tool(bad_tool, arguments)])
    result = run(b)
    assert not result.success
    assert b.tools[0].inputs == []


def test_unknown_evidence_and_unfulfilled_obligation_fail(setup_loop):
    b = setup_loop([tool(), finish(['evidence-foreign'])])
    assert not run(b).success
    b = setup_loop([tool(), finish_last])
    assert not run(b, required_tools={'property_calculator', 'activity_predictor'}).success


def test_demo_cannot_be_promoted_to_scientific_success(setup_loop):
    b = setup_loop([tool(), finish_last], [CountingTool(demo=True)])
    result = run(b)
    assert not result.success
    assert '46.069' not in result.final_answer


@pytest.mark.parametrize('transport', ['loop', 'chat'])
@pytest.mark.parametrize('mixed', [False, True, 'classification_failed', 'regression_failed'])
def test_family_conflict_finish_preserves_review_not_scientific_success(setup_loop, family_boundary, transport, mixed):
    from src.agent.contracts import ObservationStatus
    from src.agent.harness.decision_policy import usable
    from tests.agent.test_family_activity_tool import family_row
    from test_decision_chat import handler, Socket, terminal
    activity, calls, state = family_boundary
    rows = [conflict_row()]
    if mixed is True:
        rows.append(family_row(smiles='CCN', execution_status='passed'))
    elif mixed:
        sibling = family_row(smiles='CCN', success=False, status='partial', execution_status='partial',
                             predicted_pIC50=None, classification_regression_consistent=None,
                             errors={'regression': 'synthetic_unavailable'})
        if mixed == 'classification_failed':
            sibling.update(status='failed', execution_status='failed', activity_class=None,
                           activity_probability=None, errors={'classification': 'synthetic_unavailable'})
        rows.append(sibling)
    state.update(rows=rows, status='partial')
    b = setup_loop([tool('activity_predictor'), finish_last], [activity])
    ctx = AgentContext('预测PDE5A活性；SMILES: CCO' + ('；SMILES: CCN' if mixed else ''),
                       'family-review', user_id='test-owner', session_id='test-session')
    options = dict(request_kind='scientific', allowed_tools={'activity_predictor'},
                   required_tools={'activity_predictor'})
    ws = Socket()
    if transport == 'chat':
        result = asyncio.run(handler().process_decision_message(
            ws, context=ctx, decision_loop=b.loop, **options))
    else:
        result = asyncio.run(b.loop.run(ctx, event_bus=b.bus, **options))
    observation = result.tool_results[0]
    assert observation.status == ObservationStatus.PARTIAL
    assert observation.error is None
    assert observation.data == rows
    assert not usable(observation)
    assert result.outcome == RunOutcome.PARTIAL
    assert not result.success
    assert not result.metadata['task_acceptance']['satisfied']
    assert result.metadata['stop_reason'] == 'prediction_needs_review'
    assert b.store.get_run(ctx.trace_id)['status'] == 'partial'
    assert len(calls) == 1 and len(b.model.messages) == 2
    assert '需复核' in result.final_answer
    assert 'model prose' not in result.final_answer
    answer = json.loads(result.final_answer.removeprefix('```json\n').removesuffix('\n```'))
    assert answer['status'] == 'partial' and answer['scientific_usable'] is False
    assert answer['data'] == rows
    if isinstance(mixed, str):
        assert '计算已完成' not in answer['review_message']
        assert '阶段错误' in answer['review_message']
        assert answer['data'][1]['predicted_pIC50'] is None
        assert answer['data'][1]['errors'] == rows[1]['errors']
    assert answer['warnings'] == observation.warnings
    assert answer['evidence_id'] == observation.quality['evidence_id']
    assert observation.evidence == [{'prediction': row} for row in rows]
    assert last_observation(b.model.messages[1])['data'] == rows
    if transport == 'chat':
        assert terminal(ws)['status'] == 'partial'
        assert terminal(ws)['content'] == result.final_answer
        envelope = next(m for m in ws.messages if m['type'] == 'agent_result')
        assert envelope['tool_result_sequence'][0]['data'] == rows


@pytest.mark.parametrize('damage', ['regression_failure', 'demo', 'fallback', 'unproven', 'forged', 'legacy'])
def test_non_review_partial_cannot_enter_review_finish(setup_loop, family_boundary, damage):
    activity, calls, state = family_boundary
    row = conflict_row()
    if damage == 'regression_failure':
        row.update(execution_status='partial', predicted_pIC50=None,
                   classification_regression_consistent=None, errors={'regression': 'unavailable'})
    elif damage in {'demo', 'fallback'}:
        row['provenance']['models']['classification'][damage + ('_mode' if damage == 'demo' else '_used')] = True
    elif damage == 'unproven':
        row['provenance'] = {}
    elif damage == 'forged':
        row['classification_regression_consistent'] = True
    else:
        row = {'smiles': 'CCO', 'status': 'partial', 'success': False, 'value': 6.2}
    state.update(rows=[row], status='partial')
    b = setup_loop([tool('activity_predictor'), finish_last], [activity])
    result = asyncio.run(b.loop.run(AgentContext('预测PDE5A活性；SMILES: CCO', 'non-review'),
        request_kind='scientific', allowed_tools={'activity_predictor'},
        required_tools={'activity_predictor'}, event_bus=b.bus))
    assert not result.success
    assert result.metadata['stop_reason'] != 'prediction_needs_review'
    assert '6.2' not in result.final_answer


@pytest.mark.parametrize('cite_review', [False, True])
@pytest.mark.parametrize('count,metrics', [(2, ('molecular_weight',)), (1, ('qed',))])
def test_only_cited_review_can_finish_and_other_constraints_remain_unsatisfied(setup_loop, family_boundary, cite_review, count, metrics):
    from test_decision_requirements import requirements
    activity, calls, state = family_boundary
    state.update(rows=[conflict_row()], status='partial')
    def finish_selected(messages):
        ids = [last_observation(messages)['quality']['evidence_id']]
        if cite_review:
            observations = [json.loads(m['content']) for m in messages if m.get('role') == 'tool']
            ids.append(observations[0]['quality']['evidence_id'])
        return finish(ids)
    b = setup_loop([tool('activity_predictor'), tool(), finish_selected], [activity, CountingTool()])
    # A complete typed property result necessarily includes QED. Keep an
    # independently unmet subject requirement instead of omitting a descriptor.
    criteria = requirements(count, metrics, **({'expected_smiles': ['CCN']} if count == 1 else {}))
    result = asyncio.run(b.loop.run(AgentContext('预测PDE5A活性；SMILES: CCO', 'cited-review'),
        request_kind='scientific', allowed_tools={'activity_predictor', 'property_calculator'},
        required_tools={'activity_predictor', 'property_calculator'} if cite_review else {'property_calculator'},
        requirements=criteria, event_bus=b.bus))
    assert not result.success and result.outcome == RunOutcome.PARTIAL
    assert result.metadata['stop_reason'] == 'task_requirements_unfulfilled'
    assert result.error is not None
    assert result.error.details['reason'] == 'task_requirements_unfulfilled'
    assert '需复核' in result.final_answer and '6.2' in result.final_answer
    report = result.metadata['task_acceptance']
    assert not report['satisfied'] and not report['checks'][0]['passed']
    assert len(calls) == 1


def test_cited_review_cannot_excuse_missing_other_required_tool(setup_loop, family_boundary):
    activity, calls, state = family_boundary
    state.update(rows=[conflict_row()], status='partial')
    b = setup_loop([tool('activity_predictor'), finish_last], [activity, CountingTool()])
    result = asyncio.run(b.loop.run(AgentContext('预测PDE5A活性；SMILES: CCO', 'missing-other'),
        request_kind='scientific', allowed_tools={'activity_predictor', 'property_calculator'},
        required_tools={'activity_predictor', 'property_calculator'}, event_bus=b.bus))
    assert result.outcome == RunOutcome.PARTIAL and not result.success
    assert result.metadata['stop_reason'] == 'task_requirements_unfulfilled'
    assert result.error is not None
    assert 'property_calculator' in result.metadata['task_acceptance']['missing_required_tools']
    assert '需复核' in result.final_answer and '6.2' in result.final_answer
    assert len(calls) == 1


def test_review_exception_rejects_executed_forbidden_tool_report(setup_loop, family_boundary, monkeypatch):
    from src.agent.harness import decision_loop
    activity, calls, state = family_boundary
    state.update(rows=[conflict_row()], status='partial')
    evaluate = decision_loop.evaluate_requirements
    def report(*args):
        result = evaluate(*args)
        result.update(satisfied=False, executed_forbidden_tools=['property_calculator'])
        return result
    monkeypatch.setattr(decision_loop, 'evaluate_requirements', report)
    b = setup_loop([tool('activity_predictor'), finish_last], [activity])
    result = asyncio.run(b.loop.run(AgentContext('预测PDE5A活性；SMILES: CCO', 'forbidden-report'),
        request_kind='scientific', allowed_tools={'activity_predictor'},
        required_tools={'activity_predictor'}, event_bus=b.bus))
    assert result.outcome == RunOutcome.PARTIAL and not result.success
    assert result.metadata['stop_reason'] == 'task_requirements_unfulfilled'
    assert result.error is not None
    assert result.metadata['task_acceptance']['executed_forbidden_tools'] == ['property_calculator']
    assert '需复核' in result.final_answer and '6.2' in result.final_answer
    assert len(calls) == 1


@pytest.mark.parametrize('damage', ['demo', 'fallback', 'quality_demo', 'quality_fallback', 'error', 'legacy', 'null', 'forged'])
def test_review_renderer_rechecks_evidence_and_keeps_other_partial_data_unusable(damage):
    from src.agent.contracts import ObservationStatus, ToolProvenance, AgentExecutionError
    from src.agent.harness.decision_policy import scientific_answer, family_review_observation, usable
    observation = ToolResult('activity_predictor', False, '', data=[conflict_row()],
        status=ObservationStatus.PARTIAL, provenance=ToolProvenance(tool_name='activity_predictor'))
    if damage == 'demo': observation.provenance = replace(observation.provenance, demo_mode=True)
    elif damage == 'fallback': observation.provenance = replace(observation.provenance, fallback_used=True)
    elif damage == 'quality_demo': observation.quality['demo_mode'] = True
    elif damage == 'quality_fallback': observation.quality['fallback_used'] = True
    elif damage == 'error': observation.error = AgentExecutionError(AgentErrorCode.INVALID_OUTPUT, 'invalid')
    elif damage == 'legacy': observation.data = [{'value': 6.2}]
    elif damage == 'null': observation.data[0]['predicted_pIC50'] = None
    else: observation.data[0]['classification_regression_consistent'] = True
    assert not family_review_observation(observation) and not usable(observation)
    answer = json.loads(scientific_answer([observation]).removeprefix('```json\n').removesuffix('\n```'))
    assert answer['data'] is None and not answer['scientific_usable']


@pytest.mark.parametrize('changes', [
    {'errors': {}}, {'errors': {'unknown': 'failure'}}, {'errors': {'regression': 'failure'}},
    {'errors': {'classification': False}}, {'classification_regression_consistent': False},
    {'predicted_pIC50': 9.9}, {'success': True}, {'execution_status': 'passed'},
])
def test_review_batch_does_not_admit_forged_failed_siblings(changes):
    from src.agent.contracts import ObservationStatus, ToolProvenance
    from src.agent.harness.decision_policy import family_review_observation, scientific_answer, usable
    from tests.agent.test_family_activity_tool import family_row
    sibling = family_row(smiles='CCN', success=False, status='failed', execution_status='failed',
                         predicted_pIC50=None, activity_class=None, activity_probability=None,
                         classification_regression_consistent=None, provenance={},
                         errors={'classification': 'synthetic_failure'})
    sibling.update(changes)
    result = ToolResult('activity_predictor', False, '', data=[conflict_row(), sibling],
                        status=ObservationStatus.PARTIAL,
                        provenance=ToolProvenance(tool_name='activity_predictor'))
    assert not family_review_observation(result) and not usable(result)
    answer = json.loads(scientific_answer([result]).removeprefix('```json\n').removesuffix('\n```'))
    assert answer['data'] is None and not answer['scientific_usable']


def test_duplicate_family_review_does_not_rerun_inference(setup_loop, family_boundary):
    activity, calls, state = family_boundary
    state.update(rows=[conflict_row()], status='partial')
    b = setup_loop([tool('activity_predictor'), tool('activity_predictor'), finish_last], [activity])
    result = asyncio.run(b.loop.run(AgentContext('预测PDE5A活性；SMILES: CCO', 'review-duplicate'),
        request_kind='scientific', allowed_tools={'activity_predictor'},
        required_tools={'activity_predictor'}, event_bus=b.bus))
    assert result.outcome == RunOutcome.PARTIAL and not result.success
    assert len(calls) == 1 and result.metadata['reused_decisions'] == 1


def test_duplicate_action_reuses_observation_without_reexecuting(setup_loop):
    b = setup_loop([tool(), tool(), finish_last])
    result = run(b)
    assert result.success
    assert len(b.tools[0].inputs) == 1
    assert result.metadata['reused_decisions'] == 1


def test_budget_and_existing_trace_do_not_replay(setup_loop):
    b = setup_loop([tool(), tool()], max_model_requests=1)
    result = run(b)
    assert not result.success
    assert result.metadata['stop_reason'] == 'model_budget_exhausted'
    before = b.store.get_run('trace-test')
    again = run(b)
    assert not again.success
    assert len(b.tools[0].inputs) == 1
    assert b.store.get_run('trace-test') == before


def test_chat_calls_model_but_never_scientific_tools(setup_loop):
    b = setup_loop([finish(text='你好，有什么可以帮助你？', kind='chat')])
    result = run(b, request_kind='chat', required_tools=set())
    assert result.success and '你好' in result.final_answer
    assert len(b.model.messages) == 1 and not b.tools[0].inputs
    b = setup_loop([tool()])
    assert not run(b, request_kind='chat', required_tools=set()).success
    assert not b.tools[0].inputs


def test_scientific_task_cannot_escape_as_chat_finish(setup_loop):
    b = setup_loop([finish(text='pIC50=9.9 docking -10 kcal/mol', kind='chat')])
    result = run(b)
    assert not result.success
    assert '9.9' not in result.final_answer


def test_model_cancellation_records_one_terminal(setup_loop):
    b = setup_loop([])
    async def cancel(*a, **kw):
        raise asyncio.CancelledError()
    b.model.decide = cancel
    result = run(b)
    assert result.outcome == RunOutcome.CANCELLED
    assert [e.event.value for e in b.bus.events].count('task_cancelled') == 1
    assert not b.tools[0].inputs


@pytest.mark.parametrize('mode', ['native', 'json'])
def test_real_rdkit_through_existing_http_model_protocol(setup_loop, mode):
    import httpx
    from src.agent.openai_compatible_model import OpenAICompatibleModel
    from src.agent.tools.property_calculator import PropertyCalculator
    b = setup_loop([], [PropertyCalculator()], mode=mode)
    calls = []

    def respond(request):
        payload = json.loads(request.content)
        calls.append(payload)
        decision = tool() if len(calls) == 1 else finish_last(payload['messages'])
        if mode == 'native':
            message = {'role': 'assistant', 'content': None, 'tool_calls': [{
                'id': f'call-{len(calls)}', 'type': 'function', 'function': {
                    'name': 'agent_decision',
                    'arguments': json.dumps({'decision': decision.model_dump()})}}]}
            reason = 'tool_calls'
        else:
            message = {'role': 'assistant', 'content': json.dumps({'decision': decision.model_dump()})}
            reason = 'stop'
        return httpx.Response(200, json={'choices': [{'finish_reason': reason, 'message': message}],
                                         'usage': {'prompt_tokens': 10, 'completion_tokens': 20, 'total_tokens': 30}})

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            b.loop.model = OpenAICompatibleModel('fake-test-key', 'protocol-test',
                                                'https://example.invalid', client=client)
            return await b.loop.run(AgentContext('SMILES: CCO', 'trace-rdkit'),
                request_kind='scientific', allowed_tools={'property_calculator'},
                required_tools={'property_calculator'}, event_bus=b.bus)
    result = asyncio.run(exercise())
    assert result.success, result.metadata
    assert len(calls) == 2
    assert result.tool_results[0].quality['validated']
    from rdkit import Chem
    from rdkit.Chem import Descriptors
    displayed = json.loads(result.final_answer.removeprefix('```json\n').removesuffix('\n```'))
    assert displayed['data'][0]['properties']['molecular_weight'] == pytest.approx(
        Descriptors.MolWt(Chem.MolFromSmiles('CCO')), abs=0.01)
    rounds = result.metadata['model_calls']
    assert [r['model'] for r in rounds] == ['protocol-test', 'protocol-test']
    assert rounds[1]['usage']['total'] == 30
    assert rounds[1]['usage_unit'] == 'tokens'
    assert b.store.get_run('trace-rdkit')['metadata']['decision_loop']['model_calls'] == rounds


def test_failed_observation_warnings_are_persisted(setup_loop):
    b = setup_loop([tool(), clarify()], [CountingTool(fail=True)])
    run(b)
    execution = b.store.get_tool_executions('trace-test')[0]
    assert execution['output']['warnings']
    assert execution['output']['provenance']['input_digest']


def test_invalid_real_smiles_never_yields_properties(setup_loop):
    from src.agent.tools.property_calculator import PropertyCalculator
    b = setup_loop([tool(), clarify()], [PropertyCalculator()])
    result = asyncio.run(b.loop.run(AgentContext('SMILES: CC(C)((', 'invalid'),
        request_kind='scientific', allowed_tools={'property_calculator'},
        required_tools={'property_calculator'}, event_bus=b.bus))
    assert not result.success
    assert not any(r.success for r in result.tool_results)
    assert '46.069' not in result.final_answer


def test_tool_budget_reserves_internal_retries(setup_loop):
    b = setup_loop([tool()], max_tool_attempts=1)
    adapter = b.registry.resolve('property_calculator')
    adapter.spec = replace(adapter.spec, retry_policy=replace(adapter.spec.retry_policy, max_attempts=2))
    result = run(b)
    assert result.metadata['stop_reason'] == 'tool_budget_exhausted'
    assert not b.tools[0].inputs


def test_deadline_stops_before_another_model_request(setup_loop):
    import time
    class SlowTool(CountingTool):
        def execute(self, query):
            time.sleep(0.1)
            return super().execute(query)
    b = setup_loop([tool(), finish_last], [SlowTool()], timeout_seconds=0.04)
    result = run(b)
    assert not result.success
    assert len(b.model.messages) <= 1
    assert result.metadata['stop_reason'] in {'tool_execution_state_unconfirmed', 'task_deadline_exceeded'}


def test_model_cannot_drop_capability_restriction(setup_loop):
    b = setup_loop([tool()])
    result = asyncio.run(b.loop.run(AgentContext('CCO', 'disabled',
        metadata={'capabilities': {'scientific_tools': False}}),
        request_kind='scientific', allowed_tools={'property_calculator'},
        required_tools={'property_calculator'}, event_bus=b.bus))
    assert not result.success and not b.tools[0].inputs


def test_native_call_id_is_validated_before_dispatch(setup_loop):
    b = setup_loop([])
    async def missing_id(*a, **kw):
        return DecisionResponse(tool(), None, None, {})
    b.model.decide = missing_id
    result = run(b)
    assert not result.success
    assert b.tools[0].inputs == []


def test_tool_configuration_cannot_change_after_authorization(setup_loop):
    b = setup_loop([])
    adapter = b.registry.resolve('property_calculator')
    async def change_spec(*a, **kw):
        adapter.spec = replace(adapter.spec, side_effects='filesystem')
        return DecisionResponse(tool(), None, 'call-1', {})
    b.model.decide = change_spec
    result = run(b)
    assert not result.success and not b.tools[0].inputs


def test_tool_events_and_provenance_have_dispatch_identity(setup_loop):
    b = setup_loop([tool(), finish_last])
    result = run(b)
    assert result.tool_results[0].provenance.tool_version == 'test-1'
    for event in b.bus.events:
        if event.event.value in {'tool_started', 'tool_completed'}:
            assert event.payload['decision_id']
            assert event.payload['tool_call_id'] == 'call-1'
            assert event.payload['round'] == 1


def test_mismatched_tool_identity_is_not_trusted(setup_loop):
    class ImpersonatingTool(CountingTool):
        def execute(self, query):
            result = super().execute(query)
            result.tool_name = 'activity_predictor'
            return result
    b = setup_loop([tool(), clarify()], [ImpersonatingTool()])
    result = run(b)
    assert not result.tool_results[0].success
    assert result.tool_results[0].tool_name == 'property_calculator'


@pytest.mark.parametrize('event_name', ['task_started', 'tool_started', 'tool_completed', 'task_completed'])
def test_broken_notification_callback_cannot_break_authoritative_run(setup_loop, event_name):
    b = setup_loop([tool(), finish_last])
    deliveries = []
    def broken(event):
        deliveries.append(event.event.value)
        if event.event.value == event_name:
            raise RuntimeError('private notification failure')
    b.bus.on_event = broken
    result = run(b)
    assert result.success
    assert len(b.tools[0].inputs) == 1
    assert deliveries.count(event_name) == 1
    assert b.store.get_run('trace-test')['status'] == 'succeeded'
    assert 'private notification' not in repr(result)


def test_double_cancellation_during_tool_settles_before_terminal(setup_loop):
    import threading
    entered, release = threading.Event(), threading.Event()
    class Blocked(CountingTool):
        timeout_seconds = 2
        def execute(self, query):
            entered.set()
            assert release.wait(2)
            return super().execute(query)
    b = setup_loop([tool(), finish_last], [Blocked()])
    async def exercise():
        task = asyncio.create_task(b.loop.run(AgentContext('CCO', 'cancel-twice'),
            request_kind='scientific', allowed_tools={'property_calculator'},
            required_tools={'property_calculator'}, event_bus=b.bus))
        try:
            for _ in range(1000):
                if entered.is_set():
                    break
                await asyncio.sleep(0.005)
            assert entered.is_set()
            task.cancel()
            await asyncio.sleep(0.02)
            task.cancel()
            await asyncio.sleep(0.02)
            release.set()
            return await task
        finally:
            release.set()
    result = asyncio.run(exercise())
    assert result.outcome == RunOutcome.CANCELLED
    assert b.store.get_run('cancel-twice')['status'] == 'cancelled'
    assert b.bus.events[-1].event.value == 'task_cancelled'
    assert len(b.model.messages) == 1


@pytest.mark.parametrize('permanent', [False, True])
def test_execution_record_failure_never_reexecutes_tool(setup_loop, permanent):
    b = setup_loop([tool(), finish_last])
    original = b.store.record_tool_execution
    calls = []
    def fail_after_commit(record):
        calls.append(record['id'])
        original(record)
        if permanent or len(calls) == 1:
            raise OSError('private storage failure')
    b.store.record_tool_execution = fail_after_commit
    result = run(b)
    assert len(b.tools[0].inputs) == 1
    assert len(set(calls)) == 1
    assert result.success is (not permanent)
    assert b.store.get_run('trace-test')['status'] != 'running'
    assert b.bus.events[-1].event.value.startswith('task_')
    assert 'private storage failure' not in repr(result)


def test_adapter_cannot_retry_after_outer_deadline(setup_loop):
    import threading
    entered, release = threading.Event(), threading.Event()
    class LateFailure(CountingTool):
        timeout_seconds = 2
        def execute(self, query):
            self.inputs.append(query)
            entered.set()
            assert release.wait(2)
            return ToolResult.error_result(self.name, AgentErrorCode.PROVIDER_ERROR, 'test provider error')
    source = LateFailure()
    b = setup_loop([tool(), clarify()], [source], timeout_seconds=0.5)
    adapter = b.registry.resolve(source.name)
    adapter.spec = replace(adapter.spec, retry_policy=replace(adapter.spec.retry_policy,
        max_attempts=2, retryable_error_codes={'provider_error'}))
    async def exercise():
        result = await b.loop.run(AgentContext('CCO', 'late-retry'),
            request_kind='scientific', allowed_tools={source.name}, required_tools={source.name}, event_bus=b.bus)
        release.set()
        await asyncio.sleep(0.1)
        return result
    try:
        result = asyncio.run(exercise())
        assert not result.success
        assert len(source.inputs) <= 1
    finally:
        release.set()


@pytest.mark.parametrize('failure', ['metadata', 'status_before', 'status_after', 'waiting_status'])
def test_terminal_persistence_recovers_without_restarting_graph(setup_loop, failure):
    b = setup_loop([tool(), clarify() if failure == 'waiting_status' else finish_last])
    metadata_write, status_write = b.store.update_run_metadata, b.store.update_run_status
    injected = []
    def write_metadata(trace, metadata):
        metadata_write(trace, metadata)
        if failure == 'metadata' and metadata['decision_loop']['phase'] == 'terminal' and not injected:
            injected.append(True)
            raise OSError('private finalization error')
    def write_status(trace, status):
        match = failure.startswith('status_') or failure == 'waiting_status' and status == 'waiting_for_input'
        if match and not injected:
            injected.append(True)
            if failure != 'status_before':
                status_write(trace, status)
            raise OSError('private finalization error')
        return status_write(trace, status)
    b.store.update_run_metadata = write_metadata
    b.store.update_run_status = write_status
    result = run(b)
    assert injected
    assert result.success is (failure != 'waiting_status')
    assert len(b.tools[0].inputs) == 1 and len(b.model.messages) == 2
    assert sum(e.event.value in {'task_completed', 'task_partial', 'task_rejected', 'task_failed'}
               for e in b.bus.events) == 1
    assert b.store.get_run('trace-test')['status'] == ('waiting_for_input' if failure == 'waiting_status' else 'succeeded')
