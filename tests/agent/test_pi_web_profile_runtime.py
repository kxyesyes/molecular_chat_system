"""Explicit profile composition; doubles do not claim scientific success."""
from types import SimpleNamespace
import pytest
from src.agent.contracts import AgentContext
from src.agent.contracts.decision_bindings import B1_PROFILE_REVISION, B1_TOOLS
from src.agent.pi_runtime.runtime import PiAgentRuntime
from src.agent.scientific_session.decision_execution import DecisionEvents
from src.agent.scientific_session.decision_policy import authorized_catalog, decision_system_message
from tests.agent.test_decision_loop import setup_loop
from tests.agent.test_web_profile_bindings import make as web_case

WEB = 'medchat-web-v1'

def test_web_runtime_and_events_accept_only_explicit_profile():
    assert PiAgentRuntime(None, None, object(), binding_profile=WEB).binding_profile == WEB
    assert DecisionEvents(None, binding_profile=WEB).binding_profile == WEB

@pytest.mark.parametrize('profile', ['', 'medchat-web-v2', 'ordinary-semantic-b2-v1', True, {}])
def test_unknown_profile_rejected(profile):
    with pytest.raises(ValueError, match='invalid_binding_profile'):
        PiAgentRuntime(None, None, object(), binding_profile=profile)

def test_profile_revisions_and_b1_permissions_stay_separate():
    from src.agent.scientific_session.binding_policy import get_binding_policy
    b1, web = get_binding_policy(B1_PROFILE_REVISION), get_binding_policy(WEB)
    assert (b1.checkpoint_revision, web.checkpoint_revision) == (11, 13)
    assert b1.tools == B1_TOOLS and len(b1.tools) == 7
    assert {'llm_molecular_generator', 'candidate_ranker'} <= web.tools
    assert 'molecular_docking' not in web.tools
    assert get_binding_policy(None) is None

def test_web_prompt_never_instructs_input_ref():
    prompt = decision_system_message('scientific', (), [], {}, binding_profile=WEB)['content']
    assert 'step_ref' in prompt and 'input_ref' not in prompt


def test_web_prompt_separates_agent_and_generator_roles_and_evidence():
    prompt = decision_system_message('scientific', (), [], {}, binding_profile=WEB)['content']
    assert 'configured user-facing Agent model is separate' in prompt
    assert 'llm_molecular_generator tool model' in prompt
    assert 'unvalidated output as a scientific fact' in prompt
    assert 'ordinary conversation history is not scientific evidence' in prompt
    assert 'gmm-llama' not in prompt

def test_web_catalog_only_lists_compiled_actions_with_step_arguments():
    class Registry:
        def resolve(self, name, **kwargs):
            return SimpleNamespace(spec=SimpleNamespace(idempotent=True, side_effects='none',
                description=name, version='1'), health=lambda: {'available': None})
    req = SimpleNamespace(steps=(SimpleNamespace(name='compute', tool_name='property_calculator'),))
    catalog, adapters = authorized_catalog(Registry(), AgentContext('SMILES: CCO', 'web-catalog'),
        'scientific', {'property_calculator', 'llm_molecular_generator'}, binding_profile=WEB, requirements=req)
    assert set(adapters) == {'property_calculator'}
    assert catalog[0]['arguments'] == {'step_ref': 'compute'}

def test_replay_hook_is_profile_selected_not_quality_selected():
    from src.agent.scientific_session.binding_policy import get_binding_policy
    calls = []
    resolver = SimpleNamespace(prepare_observation=lambda r, s: calls.append('b1'),
        prepare_restored_observation=lambda r, s: calls.append('web'))
    result = SimpleNamespace(quality={'candidate_alignment': {'untrusted': True}})
    get_binding_policy(B1_PROFILE_REVISION).prepare_restored_observation(resolver, result, None)
    get_binding_policy(WEB).prepare_restored_observation(resolver, result, None)
    assert calls == ['b1', 'web']

@pytest.mark.parametrize('bad', [float('nan'), float('inf'), 0, -1, True])
def test_compiled_timeout_cannot_escape_remaining_deadline(bad):
    from src.agent.scientific_session.binding_policy import get_binding_policy
    resolver = SimpleNamespace(step_timeout=lambda action, remaining: bad)
    with pytest.raises(ValueError):
        get_binding_policy(WEB).step_timeout(resolver, None, 5)

def test_compiled_metadata_cannot_override_authority_or_candidate_source():
    from src.agent.scientific_session.binding_policy import get_binding_policy
    policy = get_binding_policy(WEB)
    for name in ('candidate_source', 'binding_proof', 'request_input_digest'):
        resolver = SimpleNamespace(step_metadata=lambda action: {name: 'untrusted'})
        with pytest.raises(ValueError):
            policy.step_metadata(resolver, None)


@pytest.mark.parametrize('profile,saved_profile,revision', [
    (WEB, B1_PROFILE_REVISION, 11), (B1_PROFILE_REVISION, WEB, 12),
    (WEB, WEB, 11), (B1_PROFILE_REVISION, B1_PROFILE_REVISION, 12),
])
def test_cross_profile_revision_rejected_before_projection_or_writes(monkeypatch, profile, saved_profile, revision):
    from src.agent.evidence import EvidenceLedger
    from src.agent.scientific_session import decision_binding_continuation as codec
    from src.agent.scientific_session.binding_policy import BindingPolicy
    context = AgentContext('SMILES: CCO', 'cross-profile', user_id='u', session_id='s')
    snapshot = dict(decision_protocol_revision=revision, binding_profile=saved_profile,
        task_requirements={}, binding_input_journal={}, binding_records={}, observation_seals={},
        model_requests=1, protocol_repairs=0, tool_budget_reserved=0, reused_decisions=0,
        model_calls=[], messages=[], proposals=[], call_ids=[], current_query=context.query,
        input_queries=[context.query], remaining_seconds=75, pre_finalization_remaining_seconds=100,
        finalization_reserve_seconds=25, tool_attempt_count=0, task_acceptance={},
        checkpoint_warnings=[], results=[], pi_protocol_revision=1, pi_entries=[])
    payload = dict(schema=1, id='a' * 32, configuration='same', snapshot=snapshot)
    payload['checksum'] = EvidenceLedger.output_digest(payload)
    record = dict(status='waiting_for_input', user_id='u', session_id='s', skill_name=context.active_skill,
        query=context.query, workflow_version='pi-scientific-1',
        metadata={'decision_continuation': payload, 'task_requirements': {}})
    class ReadOnlyStore:
        def get_run(self, trace):
            assert trace == context.trace_id
            return record
        def __getattr__(self, name):
            pytest.fail('preclaim rejection must not call store.' + name)
    loop = SimpleNamespace(binding_profile=profile, store=ReadOnlyStore())
    session = SimpleNamespace(context=context, orchestrator=SimpleNamespace(workflow_version='pi-scientific-1'))
    monkeypatch.setattr(BindingPolicy, 'resolver', lambda *a, **k: pytest.fail('must reject before resolver'))
    with pytest.raises(ValueError, match='invalid profile revision requirements'):
        codec.validate_continuation(loop, session, 'same', payload['id'], 'continue',
            requirements=SimpleNamespace(model_dump=lambda **k: {}), adapters={}, required_tools=set(),
            prefix=[], cap_deadline=lambda _: pytest.fail('must reject before deadline mutation'),
            check_deadline=lambda: pytest.fail('must reject before replay'))


@pytest.mark.parametrize('profile,revision', [(B1_PROFILE_REVISION, 11), (WEB, 13)])
def test_snapshot_commits_exact_profile_and_revision(profile, revision):
    from src.agent.scientific_session.decision_binding_continuation import snapshot_payload
    session = SimpleNamespace(_decision_binding_profile=profile, results=[],
        _decision_observation_seals={}, context=SimpleNamespace(query='hello'),
        input_queries=['hello'], tool_attempt_count=0)
    state = SimpleNamespace(deadline=100, pi_entries=[], counters=lambda: {}, messages=[],
        proposals=[], call_ids=set(), task_acceptance={})
    resolver = SimpleNamespace(verify_binding_closure=lambda: None,
        requirements=SimpleNamespace(model_dump=lambda **k: {}), export_records=lambda: {})
    payload, tail = snapshot_payload(state, session, 'config', resolver=resolver,
        journal=SimpleNamespace(export=lambda: {}), created_at=20)
    assert payload['snapshot']['binding_profile'] == profile
    assert payload['snapshot']['decision_protocol_revision'] == revision
    assert tail == 40


def test_web_pending_terminal_never_exposes_candidate_payload():
    from src.agent.runtime.task_state import TaskEventType
    emitted = []
    events = DecisionEvents(SimpleNamespace(emit=lambda **kw: emitted.append(kw)), binding_profile=WEB)
    events.emit('trace', TaskEventType.TASK_COMPLETED, 'scientific content',
        payload={'data': 'not released'}, event_id='terminal-1')
    assert emitted[0]['frozen'] is True
    assert emitted[0]['payload'] == dict(terminal_attempt_id='terminal-1', observed_outcome='completed',
        publication_stage='pending_source_check', answer_released=False)
    assert 'scientific content' not in repr(emitted)


def test_web_timeout_is_capped_and_b1_does_not_use_web_hooks():
    from src.agent.scientific_session.binding_policy import get_binding_policy
    resolver = SimpleNamespace(step_timeout=lambda action, remaining: 99)
    assert get_binding_policy(WEB).step_timeout(resolver, None, 5) == 5
    assert get_binding_policy(B1_PROFILE_REVISION).step_timeout(object(), None, 5) == 5
    assert get_binding_policy(B1_PROFILE_REVISION).step_metadata(object(), None) == {}


def test_schema_repair_defers_to_profile_catalog_instead_of_forcing_input_ref():
    from src.agent.contracts import AgentErrorCode, AgentExecutionError
    from src.agent.scientific_session.decision_policy import schema_correction
    from src.agent.pi_runtime.provider import _safe_feedback

    response = SimpleNamespace(error=AgentExecutionError(AgentErrorCode.INVALID_OUTPUT,
        'private provider error', {'reason': 'invalid_decision_schema', 'schema_issues': []}))
    feedback = schema_correction(response)
    assert 'input_ref' not in feedback
    assert 'catalog' in feedback
    assert 'private provider error' not in feedback
    assert _safe_feedback(feedback) == feedback


@pytest.mark.parametrize('mode', ['native', 'json'])
def test_web_schema_repair_and_resume_preserve_aligned_observations(setup_loop, mode):
    import asyncio
    from copy import deepcopy
    from src.agent.runtime.worker_ownership import WorkerOwner
    from src.agent.scientific_session.web_requirements import compile_web_requirements
    from src.agent.tools.property_calculator import PropertyCalculator
    from tests.agent.test_decision_loop import tool, finish, clarify, last_observation
    from tests.agent.test_decision_protocol_recovery import ProtocolModel, invalid
    from tests.agent.test_generation_ranking_contract import CountingTool, GEN, output

    context = AgentContext('生成 1 个分子，并计算理化性质', 'web-profile-repair-' + mode,
        user_id='owner', session_id='session', active_skill='molecular_design',
        mol_count=1, metadata={'requested_count': 1})
    allowed = {GEN, 'property_calculator'}
    requirements = compile_web_requirements(context, allowed_tools=allowed)
    names = {step.tool_name: step.name for step in requirements.steps}
    generation = tool(GEN, {'step_ref': names[GEN]})
    properties = tool('property_calculator', {'step_ref': names['property_calculator']})
    ids = []

    def after_generation(messages):
        ids.append(last_observation(messages)['quality']['evidence_id'])
        return properties

    def pause_after_alignment(messages):
        observation = last_observation(messages)
        assert observation['quality']['candidate_alignment']
        ids.append(observation['quality']['evidence_id'])
        return clarify()

    generator = CountingTool(GEN, output(GEN))
    bundle = setup_loop([], [generator, PropertyCalculator()], binding_profile=WEB, mode=mode)
    bundle.model = bundle.loop.model = ProtocolModel([
        invalid(), generation, after_generation, pause_after_alignment,
        generation, properties, lambda _: finish(ids)])
    options = dict(request_kind='scientific', allowed_tools=allowed, required_tools=allowed,
                   requirements=requirements, event_bus=bundle.bus)
    waiting = asyncio.run(bundle.loop.run(context, **options, worker_owner=WorkerOwner()))
    assert waiting.metadata.get('waiting_for_input'), waiting.metadata
    assert waiting.metadata['protocol_repairs'] == 1
    feedback = [message['content'] for message in bundle.model.messages[1]
                if message['role'] == 'system' and 'invalid_decision_schema' in message['content']]
    assert len(feedback) == 1 and 'input_ref' not in feedback[0]
    before = deepcopy([result.to_legacy_dict() for result in waiting.tool_results])
    assert len(before) == 2
    resumed = asyncio.run(bundle.loop.run(context, **options,
        continuation_id=waiting.metadata['continuation_id'], clarified_query='继续',
        worker_owner=WorkerOwner()))
    assert resumed.success, resumed.metadata
    assert resumed.metadata['reused_decisions'] == 2
    assert resumed.metadata['protocol_repairs'] == 1
    assert resumed.metadata['tool_attempt_count'] == 2
    assert generator.calls == 1
    assert [result.to_legacy_dict() for result in resumed.tool_results] == before
    assert len(bundle.model.messages) == 7


@pytest.mark.parametrize('raw_flag', [True, {'discarded_count': 0}])
def test_raw_candidate_alignment_quality_never_skips_fresh_alignment(web_case, monkeypatch, raw_flag):
    from src.agent.contracts import ObservationStatus
    from tests.agent.test_generation_ranking_contract import GEN
    from tests.agent.test_web_profile_bindings import execute

    case = web_case(generate=True)
    generation = next(step for step in case.steps if step.tool_name == GEN)
    properties = next(step for step in case.steps if step.tool_name == 'property_calculator')
    assert execute(case, generation).success
    calculator = case.resolver.adapters[properties.tool_name].tool
    original = calculator.execute

    def untrusted_result(_):
        result = original('CCO\nCCC')
        result.setdefault('quality', {})['candidate_alignment'] = raw_flag
        return result

    monkeypatch.setattr(calculator, 'execute', untrusted_result)
    result = execute(case, properties)
    assert result.status is ObservationStatus.PARTIAL
    assert result.quality['candidate_alignment']['discarded_count'] == 1
    assert [row['smiles'] for row in result.data] == ['CCO']
    assert result.data[0]['candidate_id'] == case.session.results[0].data['candidates'][0]['candidate_id']
