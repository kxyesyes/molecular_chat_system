"""Delegated execution must use the same authoritative step lifecycle."""
from src.agent.runtime.run_session import WorkflowRunSession
from src.agent.supervisor import SupervisorAgent, _DelegatedWorkflowExecutor
from src.agent.specialists import build_default_specialists
from test_supervisor_delegation import build_registry
import pytest
from src.agent.persistence import SQLiteAgentStateStore
from src.agent.orchestrators import WorkflowOrchestrator, WorkflowStep
from src.agent.planning import WorkflowPlan
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock


class SinglePlanner:
    def plan(self, context):
        return WorkflowPlan(workflow_name='comprehensive_evaluation', steps=[
            WorkflowStep('evaluate', 'property_calculator', input_data='CCO')])


@pytest.mark.parametrize('corruption', ['invalid_json', 'invalid_status'])
def test_corrupt_checkpoint_never_replays_non_idempotent_tool(tmp_path, corruption):
    from dataclasses import replace
    store_path = tmp_path / 'nonidempotent.sqlite'
    store = SQLiteAgentStateStore(store_path)
    registry, tools = build_registry()
    adapter = registry.resolve('property_calculator')
    adapter.spec = replace(adapter.spec, idempotent=False)
    try:
        supervisor = SupervisorAgent(tools={}, planner=SinglePlanner(), tool_registry=registry,
                                     specialists=build_default_specialists(), state_store=store)
        first = supervisor.run('CCO', skill_name='comprehensive_evaluation', trace_id='same')
        assert first['status'] == 'succeeded'
        checkpoint = store.latest_checkpoint('same', 'evaluate')
        output = dict(checkpoint['output'])
        output['status'] = 'bad'
        with sqlite3.connect(store_path) as connection:
            connection.execute('UPDATE agent_checkpoints SET output_json = ? WHERE id = ?',
                               ('{bad-json' if corruption == 'invalid_json' else json.dumps(output), checkpoint['id']))
        second = supervisor.run('CCO', skill_name='comprehensive_evaluation', trace_id='same')
        assert second['status'] != 'succeeded'
        assert tools['property_calculator'].calls == ['CCO']
        assert second['result']['warnings']
        assert store.get_run('same')['status'] == 'rejected'
        third = supervisor.run('CCO', skill_name='comprehensive_evaluation', trace_id='same')
        assert third['status'] != 'succeeded'
        assert tools['property_calculator'].calls == ['CCO']
    finally:
        registry.close()


@pytest.mark.parametrize('status', ['cancelled', 'rejected', 'running'])
def test_existing_terminal_or_uncertain_run_is_not_blindly_replayed(tmp_path, status):
    store = SQLiteAgentStateStore(tmp_path / 'existing.sqlite')
    store.start_run({'trace_id': 'existing', 'status': status, 'query': 'CCO'})
    registry, tools = build_registry()
    try:
        supervisor = SupervisorAgent(tools={}, planner=SinglePlanner(), tool_registry=registry,
                                     specialists=build_default_specialists(), state_store=store)
        result = supervisor.run('CCO', skill_name='comprehensive_evaluation', trace_id='existing')
        assert result['status'] != 'succeeded'
        assert tools['property_calculator'].calls == []
        assert store.get_run('existing')['status'] == status
    finally:
        registry.close()


@pytest.mark.parametrize('separate_stores', [False, True])
def test_same_trace_parallel_prepare_has_one_authoritative_claim(tmp_path, separate_stores):
    from dataclasses import replace

    read_barrier = Barrier(2)
    read_lock = Lock()
    read_count = [0]

    class SimultaneousReadStore(SQLiteAgentStateStore):
        def get_run(self, trace_id):
            row = super().get_run(trace_id)
            if trace_id == 'same':
                with read_lock:
                    read_count[0] += 1
                    ordinal = read_count[0]
                if ordinal <= 2:
                    read_barrier.wait(timeout=10)
            return row

    path = tmp_path / 'claim.sqlite'
    store = SimultaneousReadStore(path)
    second_store = SimultaneousReadStore(path) if separate_stores else store
    registry, tools = build_registry()
    adapter = registry.resolve('property_calculator')
    adapter.spec = replace(adapter.spec, max_concurrency=2)
    supervisors = [SupervisorAgent(tools={}, planner=SinglePlanner(), tool_registry=registry,
                                   specialists=build_default_specialists(), state_store=item)
                   for item in (store, second_store)]
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(
                lambda index: supervisors[index].run(
                    'CCO', skill_name='comprehensive_evaluation',
                    trace_id='same', session_id='browser-a',
                ),
                range(2),
            ))
        assert sorted(item['status'] for item in responses) == ['failed', 'succeeded']
        assert tools['property_calculator'].calls == ['CCO']
        assert store.get_run('same')['status'] == 'succeeded'
    finally:
        registry.close()


@pytest.mark.parametrize('changed', ['tool_version', 'adapter_version', 'authorization'])
def test_checkpoint_cannot_bypass_version_or_authorization_changes(tmp_path, changed):
    from dataclasses import replace
    registry, tools = build_registry()
    store = SQLiteAgentStateStore(tmp_path / 'run.sqlite')
    supervisor = SupervisorAgent(tools={}, planner=SinglePlanner(), tool_registry=registry,
                                 state_store=store, specialists=build_default_specialists())
    try:
        supervisor.run('CCO', skill_name='comprehensive_evaluation', trace_id='same')
        adapter = registry.resolve('property_calculator')
        if changed == 'tool_version':
            adapter.spec = replace(adapter.spec, version='2')
        elif changed == 'adapter_version':
            adapter.adapter_version = '2'
        else:
            supervisor.specialists['property_admet'].allowed_tools = set()
        result = supervisor.run('CCO', skill_name='comprehensive_evaluation', trace_id='same')
        if changed == 'authorization':
            assert result['status'] != 'succeeded'
            assert len(tools['property_calculator'].calls) == 1
        else:
            assert len(tools['property_calculator'].calls) == 2
    finally:
        registry.close()


@pytest.mark.parametrize('metadata_version', [None, '1'], ids=['unpinned', 'pinned'])
@pytest.mark.parametrize('idempotent', [True, False], ids=['idempotent', 'nonidempotent'])
def test_delegated_checkpoint_uses_registered_tool_version(tmp_path, metadata_version, idempotent):
    from dataclasses import replace

    class VersionPlanner(SinglePlanner):
        def plan(self, context):
            plan = super().plan(context)
            plan.steps[0].metadata['model_version'] = 'model-pin'
            if metadata_version is not None:
                plan.steps[0].metadata['tool_version'] = metadata_version
            return plan

    registry, tools = build_registry()
    adapter = registry.resolve('property_calculator')
    adapter.spec = replace(adapter.spec, version='1', idempotent=idempotent)
    tool = tools['property_calculator']
    tool.output = {'fixture_version': '1'}
    store = SQLiteAgentStateStore(tmp_path / 'registered-version.sqlite')
    supervisor = SupervisorAgent(
        tools={}, planner=VersionPlanner(), tool_registry=registry,
        specialists=build_default_specialists(), state_store=store,
    )
    try:
        first = supervisor.run('CCO', skill_name='comprehensive_evaluation', trace_id='versioned')
        assert first['status'] == 'succeeded'
        assert store.latest_checkpoint('versioned', 'evaluate')['tool_version'] == '1'
        adapter.spec = replace(adapter.spec, version='2')
        tool.output = {'fixture_version': '2'}
        second = supervisor.run('CCO', skill_name='comprehensive_evaluation', trace_id='versioned')
        observation = second['result']['tool_result_sequence'][0]
        if idempotent:
            assert second['status'] == 'succeeded'
            assert observation['data'] == {'fixture_version': '2'}
            assert tool.calls == ['CCO', 'CCO']
        else:
            assert second['status'] == 'rejected'
            assert observation['error']['details']['reason'] == 'uncertain_prior_execution'
            assert tool.calls == ['CCO']
        checkpoint = store.latest_checkpoint('versioned', 'evaluate')
        assert checkpoint['tool_version'] == '2'
        assert checkpoint['model_version'] == 'model-pin'
        with sqlite3.connect(store.db_path) as connection:
            versions = connection.execute(
                'SELECT tool_version, model_version FROM agent_checkpoints WHERE trace_id = ?',
                ('versioned',),
            ).fetchall()
        assert set(versions) == {('1', 'model-pin'), ('2', 'model-pin')}
    finally:
        registry.close()


@pytest.mark.parametrize('delegated', [False, True], ids=['nondelegated', 'delegated'])
def test_custom_workflow_version_persists_and_controls_resume(tmp_path, delegated):
    store_path = tmp_path / 'workflow-version.sqlite'
    registry, tools = build_registry()
    tool = tools['property_calculator']
    tool.output = {'fixture_revision': 'original'}
    try:
        for version, reused_steps, call_count, expected_output in (
            ('custom-workflow-v7', [], 1, 'original'),
            ('custom-workflow-v7', ['evaluate'], 1, 'original'),
            ('custom-workflow-v8', [], 2, 'fresh'),
        ):
            # Reopen persistence and rebuild the entrypoint, as after a restart.
            store = SQLiteAgentStateStore(store_path)
            supervisor = SupervisorAgent(
                tools={} if delegated else {'property_calculator': tool},
                planner=SinglePlanner(),
                orchestrator=WorkflowOrchestrator(
                    state_store=store, workflow_version=version,
                ),
                tool_registry=registry if delegated else None,
                specialists=build_default_specialists() if delegated else None,
                state_store=store,
            )
            response = supervisor.run(
                'CCO', skill_name='comprehensive_evaluation', trace_id='workflow-version',
            )
            assert response['status'] == 'succeeded'
            assert response['result']['metadata']['reused_steps'] == reused_steps
            assert response['result']['tool_result_sequence'][0]['data'] == {
                'fixture_revision': expected_output,
            }
            assert tool.calls == ['CCO'] * call_count
            run = store.get_run('workflow-version')
            checkpoint = store.latest_checkpoint('workflow-version', 'evaluate')
            assert run['status'] == checkpoint['status'] == 'succeeded'
            assert run['workflow_version'] == checkpoint['workflow_version'] == version
            with sqlite3.connect(store_path) as connection:
                assert connection.execute(
                    'SELECT workflow_version FROM agent_runs WHERE trace_id = ?',
                    ('workflow-version',),
                ).fetchall() == [(version,)]
                checkpoint_versions = connection.execute(
                    'SELECT DISTINCT workflow_version FROM agent_checkpoints WHERE trace_id = ?',
                    ('workflow-version',),
                ).fetchall()
            assert set(checkpoint_versions) == (
                {('custom-workflow-v7',)} if call_count == 1
                else {('custom-workflow-v7',), ('custom-workflow-v8',)}
            )
            # A compatible restart must restore the old observation, not execute.
            tool.output = {'fixture_revision': 'fresh'}
    finally:
        registry.close()


def test_nondelegated_checkpoint_preserves_legacy_metadata_versions(tmp_path):
    class VersionPlanner(SinglePlanner):
        def plan(self, context):
            plan = super().plan(context)
            plan.steps[0].metadata.update(tool_version='legacy-pin', model_version='model-pin')
            return plan

    registry, tools = build_registry()
    tool = tools['property_calculator']
    tool.version = '1'
    tool.output = {'fixture_version': '1'}
    store = SQLiteAgentStateStore(tmp_path / 'legacy-version.sqlite')
    supervisor = SupervisorAgent(
        tools={'property_calculator': tool}, planner=VersionPlanner(), state_store=store,
    )
    try:
        first = supervisor.run('CCO', skill_name='comprehensive_evaluation', trace_id='legacy')
        assert first['status'] == 'succeeded'
        tool.version = '2'
        tool.output = {'fixture_version': '2'}
        second = supervisor.run('CCO', skill_name='comprehensive_evaluation', trace_id='legacy')
        assert second['status'] == 'succeeded'
        assert second['result']['tool_result_sequence'][0]['data'] == {'fixture_version': '1'}
        assert tool.calls == ['CCO']
        checkpoint = store.latest_checkpoint('legacy', 'evaluate')
        assert checkpoint['tool_version'] == 'legacy-pin'
        assert checkpoint['model_version'] == 'model-pin'
    finally:
        registry.close()


def test_delegated_entry_executes_shared_session(monkeypatch):
    registry, tools = build_registry()
    seen = []
    original = WorkflowRunSession.execute_step

    def capture(session, index):
        seen.append(session.steps[index].name)
        return original(session, index)

    monkeypatch.setattr(WorkflowRunSession, 'execute_step', capture)
    try:
        supervisor = SupervisorAgent(tools={}, tool_registry=registry,
                                     specialists=build_default_specialists())
        response = supervisor.run('CCO', skill_name='admet_assessment')
        assert seen
        assert seen == [item['task_id'].split(':')[-1] for item in response['delegations']]
        assert len({event['trace_id'] for event in response['agent_events']}) == 1
        terminal = response['agent_events'][-1]
        assert terminal['event'] == 'task_completed'
        assert terminal['payload']['metadata']['_harness_delegations'] == response['delegations']
    finally:
        registry.close()


def test_delegated_executor_supports_incremental_harness_contract():
    assert callable(getattr(_DelegatedWorkflowExecutor, 'prepare', None))


def test_preparing_delegated_workflow_does_not_claim_run_until_start(tmp_path):
    from src.agent.contracts import AgentContext
    from src.agent.runtime import PreparedWorkflow
    store = SQLiteAgentStateStore(tmp_path / 'deferred-claim.sqlite')
    registry, tools = build_registry()
    try:
        supervisor = SupervisorAgent(tools={}, planner=SinglePlanner(), tool_registry=registry,
                                     specialists=build_default_specialists(), state_store=store)
        prepared = _DelegatedWorkflowExecutor(supervisor).prepare(
            context=AgentContext('CCO', 'abandoned-prepare', active_skill='comprehensive_evaluation'),
            policy=supervisor.catalog.get('comprehensive_evaluation'),
            all_tools=registry.as_mapping(),
        )
        assert isinstance(prepared, PreparedWorkflow)
        assert store.get_run('abandoned-prepare') is None
        assert tools['property_calculator'].calls == []
        session = prepared.create_session()
        assert store.get_run('abandoned-prepare') is None
        session.start()
        assert store.get_run('abandoned-prepare')['status'] == 'running'
        session.execute_step(0)
        assert session.finish().success
    finally:
        registry.close()


def test_direct_langgraph_delegation_rejects_existing_running_trace(tmp_path):
    from src.agent.contracts import AgentContext
    from src.agent.harness.langgraph_execution import LangGraphExecutionHarness
    store = SQLiteAgentStateStore(tmp_path / 'langgraph-claim.sqlite')
    store.start_run({'trace_id': 'occupied', 'status': 'running', 'query': 'CCO'})
    registry, tools = build_registry()
    try:
        supervisor = SupervisorAgent(tools={}, planner=SinglePlanner(), tool_registry=registry,
                                     specialists=build_default_specialists(), state_store=store)
        run = LangGraphExecutionHarness(_DelegatedWorkflowExecutor(supervisor)).execute(
            context=AgentContext('CCO', 'occupied', active_skill='comprehensive_evaluation'),
            policy=supervisor.catalog.get('comprehensive_evaluation'),
            all_tools=registry.as_mapping(),
        )
        assert run.authoritative.result.metadata['run_claim_conflict'] is True
        assert not run.authoritative.result.success
        assert tools['property_calculator'].calls == []
        assert store.get_run('occupied')['status'] == 'running'
    finally:
        registry.close()


@pytest.mark.parametrize('mode', ['legacy', 'shadow', 'langgraph_canary'])
def test_delegated_harness_modes_call_tool_once(monkeypatch, mode):
    from src.agent.harness import HarnessFactory
    monkeypatch.setenv('AGENT_LANGGRAPH_CANARY_PERCENT', '100')
    registry, tools = build_registry()
    try:
        supervisor = SupervisorAgent(
            tools={}, planner=SinglePlanner(), tool_registry=registry,
            specialists=build_default_specialists(),
            harness_factory=HarnessFactory(mode=mode, langgraph_available=lambda: True))
        result = supervisor.run('CCO', skill_name='admet_assessment')
        assert result['status'] == 'succeeded'
        assert tools['property_calculator'].calls == ['CCO']
        assert len(result['delegations']) == 1
        assert [e['event'] for e in result['agent_events']].count('task_completed') == 1
    finally:
        registry.close()


def test_timed_out_adapter_retains_inflight_capacity_until_worker_finishes(tmp_path):
    from threading import Event
    from dataclasses import replace
    from src.agent.contracts import ToolResult
    registry, tools = build_registry()
    release, exited = Event(), Event()
    adapter = registry.resolve('property_calculator')
    adapter.spec = replace(adapter.spec, timeout_seconds=0.03)
    def blocked(query):
        try:
            release.wait(5)
            return ToolResult.success_result('property_calculator', data={'fixture': True})
        finally:
            exited.set()
    tools['property_calculator'].execute = blocked
    supervisor = SupervisorAgent(tools={}, planner=SinglePlanner(), tool_registry=registry,
                                 specialists=build_default_specialists())
    try:
        first = supervisor.run('CCO', skill_name='comprehensive_evaluation')
        second = supervisor.run('CCO', skill_name='comprehensive_evaluation')
        timed = first['result']['tool_result_sequence'][0]
        busy = second['result']['tool_result_sequence'][0]
        assert timed['error']['code'] == 'tool_timeout'
        assert timed['quality']['invocation_may_still_be_running'] is True
        assert busy['quality']['capacity_exhausted'] is True
    finally:
        release.set()
        exited.wait(5)
        registry.close()


def test_timed_out_non_idempotent_tool_is_not_replayed_after_worker_exits(tmp_path):
    from dataclasses import replace
    from threading import Event
    from src.agent.contracts import ToolResult

    store = SQLiteAgentStateStore(tmp_path / 'uncertain-timeout.sqlite')
    registry, tools = build_registry()
    release, exited = Event(), Event()
    adapter = registry.resolve('property_calculator')
    adapter.spec = replace(adapter.spec, timeout_seconds=0.03, idempotent=False)
    calls = []

    def slow_tool(query):
        calls.append(query)
        try:
            release.wait(5)
            return ToolResult.success_result('property_calculator', data={'computed': True})
        finally:
            exited.set()

    tools['property_calculator'].execute = slow_tool
    supervisor = SupervisorAgent(tools={}, planner=SinglePlanner(), tool_registry=registry,
                                 specialists=build_default_specialists(), state_store=store)
    try:
        first = supervisor.run('CCO', skill_name='comprehensive_evaluation', trace_id='uncertain')
        assert first['status'] == 'failed'
        assert first['result']['tool_result_sequence'][0]['error']['code'] == 'tool_timeout'
        release.set()
        assert exited.wait(5)
        second = supervisor.run('CCO', skill_name='comprehensive_evaluation', trace_id='uncertain')
        assert second['status'] == 'rejected'
        assert second['result']['tool_result_sequence'][0]['error']['details']['reason'] == 'uncertain_prior_execution'
        assert calls == ['CCO']
        assert store.get_run('uncertain')['status'] == 'rejected'
    finally:
        release.set()
        exited.wait(5)
        registry.close()


def test_delegated_adapter_rejects_contradictory_raw_legacy_result(tmp_path):
    store = SQLiteAgentStateStore(tmp_path / 'raw-result.sqlite')
    registry, tools = build_registry()
    tools['property_calculator'].execute = lambda query: {
        'success': True,
        'status': 'failed',
        'data': {'molecular_weight': 100},
        'formatted': 'Already calculated',
    }
    supervisor = SupervisorAgent(tools={}, planner=SinglePlanner(), tool_registry=registry,
                                 specialists=build_default_specialists(), state_store=store)
    try:
        response = supervisor.run('CCO', skill_name='comprehensive_evaluation', trace_id='raw-conflict')
        assert response['status'] == 'failed'
        observation = response['result']['tool_result_sequence'][0]
        assert observation['success'] is False
        assert observation['error'] is not None, observation
        assert observation['error']['code'] == 'invalid_output'
        assert observation['formatted'] == ''
        assert store.latest_checkpoint('raw-conflict', 'evaluate')['status'] == 'failed'
    finally:
        registry.close()


def test_browser_session_cannot_claim_another_sessions_trace(tmp_path):
    store = SQLiteAgentStateStore(tmp_path / 'owner-bound.sqlite')
    registry, tools = build_registry()
    supervisor = SupervisorAgent(
        tools={}, planner=SinglePlanner(), tool_registry=registry,
        specialists=build_default_specialists(), state_store=store,
    )
    try:
        first = supervisor.run(
            'CCO', skill_name='comprehensive_evaluation', trace_id='owned-trace',
            session_id='browser-a',
        )
        before = store.get_run('owned-trace')
        checkpoint_before = store.latest_checkpoint('owned-trace', 'evaluate')
        second = supervisor.run(
            'CCO', skill_name='comprehensive_evaluation', trace_id='owned-trace',
            session_id='browser-b',
        )

        assert first['status'] == 'succeeded'
        assert second['status'] in {'failed', 'rejected'}
        assert tools['property_calculator'].calls == ['CCO']
        persisted = store.get_run('owned-trace')
        assert persisted == before
        assert store.latest_checkpoint('owned-trace', 'evaluate') == checkpoint_before
    finally:
        registry.close()


def test_browser_session_cannot_claim_legacy_unowned_trace(tmp_path):
    store = SQLiteAgentStateStore(tmp_path / 'legacy-unowned.sqlite')
    store.start_run({
        'trace_id': 'legacy-trace',
        'status': 'succeeded',
        'query': 'CCO',
        'skill_name': 'comprehensive_evaluation',
    })
    registry, tools = build_registry()
    supervisor = SupervisorAgent(
        tools={}, planner=SinglePlanner(), tool_registry=registry,
        specialists=build_default_specialists(), state_store=store,
    )
    try:
        result = supervisor.run(
            'CCO', skill_name='comprehensive_evaluation', trace_id='legacy-trace',
            session_id='browser-a',
        )
        assert result['status'] in {'failed', 'rejected'}
        assert tools['property_calculator'].calls == []
        assert store.get_run('legacy-trace')['session_id'] is None
    finally:
        registry.close()


def test_local_caller_cannot_claim_browser_owned_trace(tmp_path):
    store = SQLiteAgentStateStore(tmp_path / 'local-boundary.sqlite')
    registry, tools = build_registry()
    supervisor = SupervisorAgent(
        tools={}, planner=SinglePlanner(), tool_registry=registry,
        specialists=build_default_specialists(), state_store=store,
    )
    try:
        first = supervisor.run(
            'CCO', skill_name='comprehensive_evaluation', trace_id='browser-trace',
            session_id='browser-a',
        )
        second = supervisor.run(
            'CCO', skill_name='comprehensive_evaluation', trace_id='browser-trace',
        )
        assert first['status'] == 'succeeded'
        assert second['status'] in {'failed', 'rejected'}
        assert tools['property_calculator'].calls == ['CCO']
        assert store.get_run('browser-trace')['session_id'] == 'browser-a'
    finally:
        registry.close()


def test_raw_idempotency_key_is_scoped_and_not_persisted(tmp_path):
    store_path = tmp_path / 'idempotency-scope.sqlite'
    store = SQLiteAgentStateStore(store_path)
    registry, _ = build_registry()
    supervisor = SupervisorAgent(
        tools={}, planner=SinglePlanner(), tool_registry=registry,
        specialists=build_default_specialists(), state_store=store,
    )
    try:
        first = supervisor.run(
            'CCO', skill_name='comprehensive_evaluation', session_id='browser-a',
            metadata={'idempotency_key': 'same-client-key'},
        )
        second = supervisor.run(
            'CCO', skill_name='comprehensive_evaluation', session_id='browser-b',
            metadata={'idempotency_key': 'same-client-key'},
        )
        assert first['status'] == second['status'] == 'succeeded'
        assert first['trace_id'] != second['trace_id']
        with sqlite3.connect(store_path) as connection:
            keys = [row[0] for row in connection.execute(
                'SELECT idempotency_key FROM agent_runs ORDER BY trace_id'
            ).fetchall()]
        assert len(keys) == 2
        assert len(set(keys)) == 2
        assert 'same-client-key' not in keys
    finally:
        registry.close()


@pytest.mark.parametrize(
    ('second_query', 'second_skill'),
    [
        ('CCC', 'comprehensive_evaluation'),
        ('CCO', 'admet_assessment'),
    ],
)
def test_same_owner_cannot_change_claimed_request_identity(
    tmp_path, second_query, second_skill,
):
    store = SQLiteAgentStateStore(tmp_path / 'request-identity.sqlite')
    registry, tools = build_registry()
    supervisor = SupervisorAgent(
        tools={}, planner=SinglePlanner(), tool_registry=registry,
        specialists=build_default_specialists(), state_store=store,
    )
    try:
        first = supervisor.run(
            'CCO', skill_name='comprehensive_evaluation', trace_id='stable-request',
            session_id='browser-a',
        )
        second = supervisor.run(
            second_query, skill_name=second_skill, trace_id='stable-request',
            session_id='browser-a',
        )
        assert first['status'] == 'succeeded'
        assert second['status'] in {'failed', 'rejected'}
        assert tools['property_calculator'].calls == ['CCO']
        assert store.get_run('stable-request')['query'] == 'CCO'
    finally:
        registry.close()


def test_nondelegated_browser_run_uses_same_owner_boundary(tmp_path):
    store = SQLiteAgentStateStore(tmp_path / 'nondelegated-owner.sqlite')
    registry, tools = build_registry()
    supervisor = SupervisorAgent(
        tools=tools, planner=SinglePlanner(), state_store=store,
    )
    try:
        first = supervisor.run(
            'CCO', skill_name='comprehensive_evaluation', trace_id='plain-trace',
            session_id='browser-a',
        )
        second = supervisor.run(
            'CCO', skill_name='comprehensive_evaluation', trace_id='plain-trace',
            session_id='browser-b',
        )
        assert first['status'] == 'succeeded'
        assert second['status'] in {'failed', 'rejected'}
        assert tools['property_calculator'].calls == ['CCO']
        assert store.get_run('plain-trace')['session_id'] == 'browser-a'
    finally:
        registry.close()


def test_session_owner_cannot_be_supplied_through_metadata(tmp_path):
    store = SQLiteAgentStateStore(tmp_path / 'metadata-owner.sqlite')
    registry, _ = build_registry()
    supervisor = SupervisorAgent(
        tools={}, planner=SinglePlanner(), tool_registry=registry,
        specialists=build_default_specialists(), state_store=store,
    )
    try:
        result = supervisor.run(
            'CCO', skill_name='comprehensive_evaluation', trace_id='metadata-trace',
            session_id='trusted-session',
            metadata={'session_id': 'attacker', 'user_id': 'attacker'},
        )
        assert result['status'] == 'succeeded'
        persisted = store.get_run('metadata-trace')
        assert persisted['session_id'] == 'trusted-session'
        assert persisted['user_id'] is None
        assert 'session_id' not in persisted['metadata']
        assert 'user_id' not in persisted['metadata']
    finally:
        registry.close()


@pytest.mark.parametrize('value', ['', '   ', 123, 'x' * 257])
def test_browser_idempotency_key_validation_fails_before_execution(tmp_path, value):
    store = SQLiteAgentStateStore(tmp_path / 'invalid-key.sqlite')
    registry, tools = build_registry()
    supervisor = SupervisorAgent(
        tools={}, planner=SinglePlanner(), tool_registry=registry,
        specialists=build_default_specialists(), state_store=store,
    )
    try:
        result = supervisor.run(
            'CCO', skill_name='comprehensive_evaluation', session_id='browser-a',
            metadata={'idempotency_key': value},
        )
        assert result['status'] == 'failed'
        assert result['result']['error']['details']['reason'] == 'invalid_idempotency_key'
        assert tools['property_calculator'].calls == []
    finally:
        registry.close()


def test_same_session_idempotent_retry_reuses_trace(tmp_path):
    store = SQLiteAgentStateStore(tmp_path / 'same-owner-key.sqlite')
    registry, tools = build_registry()
    supervisor = SupervisorAgent(
        tools={}, planner=SinglePlanner(), tool_registry=registry,
        specialists=build_default_specialists(), state_store=store,
    )
    try:
        first = supervisor.run(
            'CCO', skill_name='comprehensive_evaluation', session_id='browser-a',
            metadata={'idempotency_key': 'retry-key'},
        )
        second = supervisor.run(
            'CCO', skill_name='comprehensive_evaluation', session_id='browser-a',
            metadata={'idempotency_key': 'retry-key'},
        )
        assert first['status'] == second['status'] == 'succeeded'
        assert first['trace_id'] == second['trace_id']
        assert tools['property_calculator'].calls == ['CCO']
    finally:
        registry.close()
