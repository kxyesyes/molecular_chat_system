"""Task 3 Web execution contract: server-owned property obligations."""

import asyncio
from dataclasses import replace
import json
from pathlib import Path
import tempfile

import pytest

from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
from src.agent.contracts.decision import FinishDecision, ToolDecision
from src.agent.tools.admet_predictor import ADMETPredictor
from src.agent.tools.property_calculator import PropertyCalculator
from test_admet_ai_integration import FakeBackend
from test_web_decision_runtime import ActualSocket, actual_app, cookie_for, result_of
from pi_web_support import PlannedWebModel, WebSocket, make_web_handler, run_web_turn
from src.web.decision_runtime import _Sender, _Turn, _resumable_scientific_clarification
from src.web.scientific_obligations import ScientificObligationCompiler


@pytest.fixture
def web_handler():
    state_dir = Path(tempfile.mkdtemp(prefix="medchat-task3-", dir=Path.cwd()))
    store = SQLiteAgentStateStore(state_dir / "agent.sqlite")
    handler = make_web_handler([PropertyCalculator()], store, model=PlannedWebModel())
    try:
        yield handler
    finally:
        assert not handler.decision_runtime.active_owners
        handler.agent_system.tool_registry.close()


@pytest.fixture
def admet_web_handler():
    state_dir = Path(tempfile.mkdtemp(prefix="medchat-admet-task3-", dir=Path.cwd()))
    store = SQLiteAgentStateStore(state_dir / "agent.sqlite")
    handler = make_web_handler(
        [PropertyCalculator(), ADMETPredictor(backend=FakeBackend())],
        store,
        model=PlannedWebModel(),
    )
    try:
        yield handler
    finally:
        assert not handler.decision_runtime.active_owners
        handler.agent_system.tool_registry.close()


def test_ready_property_plan_executes_real_rdkit_tool_and_publishes_evidence(web_handler):
    _, frames = run_web_turn(
        web_handler,
        "请分析 CCO 的分子性质，返回 LogP 和 TPSA。",
    )
    result = result_of(frames)

    assert result["status"] == "completed", result
    assert result["metadata"]["required_tools"] == ["property_calculator"]
    assert result["metadata"]["workflow_version"] == "web-property-v1"
    assert result["tool_result_sequence"][0]["status"] == "succeeded"
    assert result["tool_result_sequence"][0]["provenance"]
    assert result["tool_result_sequence"][0]["quality"]["evidence_id"]
    assert "CCO" in json.dumps(result, ensure_ascii=False)
    assert "46.07" in json.dumps(result, ensure_ascii=False) or "46.069" in json.dumps(result, ensure_ascii=False)
    assert "gmm-llama:latest" not in json.dumps(result, ensure_ascii=False)
    assert not any(frame.get("type") == "agent_event" and
                   frame.get("event", {}).get("event") == "tool_failed"
                   for frame in frames)


@pytest.mark.parametrize('query', ['请计算这个分子的 LogP', '计算分子的分子量和 logP。'])
def test_missing_smiles_waits_for_input_without_scientific_tool(web_handler, query):
    _, frames = run_web_turn(web_handler, query)
    result = result_of(frames)

    assert result["status"] == "waiting_for_input", result
    assert result["metadata"]["stop_reason"] == "clarification_required"
    assert result["tool_result_sequence"] == []
    assert not any(frame.get("type") == "agent_event" and
                   frame.get("event", {}).get("event") == "tool_started"
                   for frame in frames)


@pytest.mark.parametrize('reconnect', [False, True])
@pytest.mark.parametrize('query', ['请计算这个分子的 LogP', '计算分子的分子量和 logP。'])
def test_missing_smiles_continuation_resumes_same_scientific_obligation(web_handler, reconnect, query):
    async def drive_two_turns():
        session = "missing-smiles-continuation"
        sender = _Sender(WebSocket(session))
        sender._runtime = web_handler.decision_runtime

        first = _Turn(dict(message=query,
                           enable_tools=True, enable_rag=False))
        web_handler.decision_runtime.active_owners.add(first)
        await web_handler.decision_runtime._execute(web_handler, sender, first)
        waiting_frames = sender.websocket.messages[:]
        waiting = result_of(waiting_frames)
        continuation_id = waiting["metadata"].get("continuation_id")
        assert waiting["status"] == "waiting_for_input"
        assert isinstance(continuation_id, str) and len(continuation_id) == 32
        assert sender.waiting is not None
        assert sender.waiting.context.query == query
        assert sender.waiting.prepared.required_tools == {'property_calculator'}
        assert waiting['tool_result_sequence'] == []
        runtime = web_handler.decision_runtime
        assert runtime._restore_durable_waiting(
            _Sender(WebSocket('another-owner')), first.trace_id, continuation_id) is None
        if reconnect:
            sender = _Sender(WebSocket(session))
            sender._runtime = runtime
            sender.waiting = runtime._restore_durable_waiting(sender, first.trace_id, continuation_id)
            assert sender.waiting is not None

        second = _Turn(dict(message="CCO", enable_tools=True, enable_rag=False),
                       trace_id=sender.waiting.context.trace_id,
                       waiting=sender.waiting)
        web_handler.decision_runtime.active_owners.add(second)
        start = len(sender.websocket.messages)
        await web_handler.decision_runtime._execute(web_handler, sender, second)
        assert runtime._restore_durable_waiting(sender, first.trace_id, continuation_id) is None
        return waiting, continuation_id, sender.websocket.messages[start:]

    waiting, continuation_id, resumed_frames = asyncio.run(drive_two_turns())

    assert waiting["status"] == "waiting_for_input"
    assert isinstance(continuation_id, str) and len(continuation_id) == 32
    resumed = result_of(resumed_frames)

    assert resumed["status"] == "completed", resumed
    assert len(resumed["tool_result_sequence"]) == 1
    assert resumed["tool_result_sequence"][0]["tool_name"] == "property_calculator"
    assert "46.07" in json.dumps(resumed, ensure_ascii=False)


@pytest.mark.parametrize('query', ['计算阿司匹林的分子性质', '计算乙醇的分子性质',
                                  '请计算分子阿司匹林的分子性质'])
def test_browser_cannot_enable_missing_structure_admission(web_handler, query):
    _, frames = run_web_turn(web_handler, query,
                            allow_molecular_clarification=True)
    result = result_of(frames)
    assert result['status'] == 'rejected'
    assert not result['tool_result_sequence']
    assert not result['metadata'].get('continuation_id')


@pytest.mark.parametrize('query', [
    '计算分子的分子量和 logP。分子为阿司匹林。',
    '计算分子的分子量和 logP。对象为患者。',
    '计算分子的分子量和 logP；分子为阿司匹林。',
    '计算分子的分子量和 logP\n对象为患者。',
    '计算分子的分子量和 logP。这个分子为阿司匹林。',
])
def test_bare_subject_clarification_requires_the_entire_generic_request(query):
    plan = ScientificObligationCompiler().compile(query)
    assert not _resumable_scientific_clarification(plan)


@pytest.mark.parametrize('query', [
    '计算分子的分子量和 logP。分子为阿司匹林。',
    '计算分子的分子量和 logP。对象为患者。',
])
@pytest.mark.parametrize('reconnect', [False, True])
def test_later_subject_has_no_admission_or_authenticated_cco_continuation(actual_app, query, reconnect):
    async def run():
        async with actual_app(mode='pi', ordinary_policy='medchat-web-v1') as b:
            cookie = await cookie_for(b.app)

            async def reject_resume(socket, result):
                await socket.send({'type': 'resume', 'trace_id': result['trace_id'],
                    'continuation_id': result['metadata'].get('continuation_id', 'a' * 32),
                    'message': 'CCO'})
                assert await socket.receive() == {'type': 'error', 'code': 'continuation_unavailable'}

            async with ActualSocket(b.app, cookie) as socket:
                await socket.ready()
                result = result_of(await socket.turn({'message': query,
                    'enable_tools': True, 'enable_rag': False}))
                if not reconnect:
                    await reject_resume(socket, result)
            if reconnect:
                async with ActualSocket(b.app, cookie) as socket:
                    await socket.ready()
                    await reject_resume(socket, result)

            assert result['status'] == 'rejected'
            assert not result['metadata'].get('continuation_id')
            assert result['tool_result_sequence'] == []
            assert b.app.agent_state_store.get_run(result['trace_id']) is None
            assert b.app.agent_state_store.get_tool_executions(result['trace_id']) == []
            assert not b.calls and not b.claims

    asyncio.run(run())


@pytest.mark.parametrize('query,capability', [
    ('请分析这个分子的分子性质，返回LogP和LogD', 'metric:logd'),
    ('计算分子的分子量和 LogD。', 'metric:logd'),
    ('计算分子的 FooMetric 和 logP。', 'metric:unreviewed'),
    ('计算分子的分子量和 FooMetric。', 'metric:unreviewed'),
    ('计算分子的量子轨道能量和 logP。', 'metric:unreviewed'),
])
def test_missing_structure_cannot_drop_unsupported_metric_on_continuation(web_handler, query, capability):
    _, frames = run_web_turn(web_handler, query)
    result = result_of(frames)
    assert result['status'] == 'rejected'
    assert result['metadata']['missing_capabilities'] == [capability]
    assert not result['tool_result_sequence']
    assert not result['metadata'].get('continuation_id')


def test_exclusion_cannot_drop_a_positive_unsupported_metric(web_handler):
    _, frames = run_web_turn(web_handler, '只计算 CCO 的 LogP 和 LogD，不要 ADMET')
    result = result_of(frames)
    assert result['status'] == 'rejected'
    assert 'metric:logd' in result['metadata']['missing_capabilities']
    assert not result['tool_result_sequence']


@pytest.mark.parametrize('fail_admet', [False, True])
def test_resumed_compound_obligation_cannot_finish_after_only_one_tool(admet_web_handler, fail_admet):
    class EarlyFinishModel(PlannedWebModel):
        async def decide(self, messages, **kwargs):
            response = await super().decide(messages, **kwargs)
            if isinstance(response.decision, ToolDecision):
                ids = [identity for m in messages if m.get('role') == 'tool'
                       if (identity := json.loads(m['content']).get('quality', {}).get('evidence_id'))]
                if ids:
                    return replace(response, decision=FinishDecision(version='1', action='finish',
                        response_kind='scientific', text='Protocol-only early finish', evidence_ids=ids))
            return response

    handler = admet_web_handler
    if fail_admet:
        handler.agent_system.tools['admet_predictor'].backend.fail_ids.add('molecule-001')
    runtime = handler.decision_runtime
    handler.model = runtime.application.model = EarlyFinishModel()

    async def run():
        sender = _Sender(WebSocket('compound-clarification'))
        sender._runtime = runtime
        first = _Turn(dict(message='请计算这个分子的 LogP 并预测 ADMET',
                           enable_tools=True, enable_rag=False))
        runtime.active_owners.add(first)
        await runtime._execute(handler, sender, first)
        assert sender.waiting is not None, sender.websocket.messages
        assert sender.waiting.prepared.required_tools == {'property_calculator', 'admet_predictor'}
        start = len(sender.websocket.messages)
        second = _Turn(dict(message='CCO'), trace_id=first.trace_id, waiting=sender.waiting)
        runtime.active_owners.add(second)
        await runtime._execute(handler, sender, second)
        return result_of(sender.websocket.messages[start:])

    result = asyncio.run(run())
    assert result['status'] == ('partial' if fail_admet else 'completed'), result
    assert [item['tool_name'] for item in result['tool_result_sequence']] == [
        'property_calculator', 'admet_predictor']


def test_other_mixed_unsupported_request_is_rejected_before_execution(web_handler):
    _, frames = run_web_turn(web_handler, "请分析 CCO 的 docking 和 LogP")
    result = result_of(frames)

    assert result["status"] == "rejected"
    assert result["metadata"]["missing_capabilities"] == ["docking"]
    assert result["tool_result_sequence"] == []
    assert not any(frame.get("type") == "agent_event" and
                   frame.get("event", {}).get("event") == "tool_started"
                   for frame in frames)


def test_unavailable_property_tool_is_not_reported_as_success(web_handler):
    web_handler.decision_runtime.registry.resolve(
        "property_calculator", require_available=False
    ).set_available(False, "test tool unavailable")

    _, frames = run_web_turn(
        web_handler,
        "请分析 CCO 的分子性质，返回 LogP 和 TPSA。",
    )
    result = result_of(frames)

    assert result["status"] in {"unavailable", "failed", "partial"}
    assert result["success"] is False
    observation = result["tool_result_sequence"][0]
    assert observation["status"] != "succeeded"
    assert not result.get("evidence")


def test_ready_admet_plan_executes_real_tool_and_publishes_model_provenance(
    admet_web_handler,
):
    _, frames = run_web_turn(admet_web_handler, "请预测 CCO 的 ADMET 特征")
    result = result_of(frames)

    assert result["status"] == "completed", result
    assert result["metadata"]["workflow_version"] == "web-admet-v1"
    assert result["metadata"]["required_tools"] == ["admet_predictor"]
    observation = result["tool_result_sequence"][0]
    assert observation["tool_name"] == "admet_predictor"
    assert observation["status"] == "succeeded"
    assert observation["provenance"]["tool_name"] == "admet_predictor"
    assert observation["provenance"]["model_version"] == "1.4.0"
    assert "gmm-llama:latest" not in json.dumps(result, ensure_ascii=False)


def test_mixed_property_admet_requires_both_observations(admet_web_handler):
    _, frames = run_web_turn(
        admet_web_handler,
        "请分析 CCO 的分子性质和 ADMET 特征",
    )
    result = result_of(frames)

    assert result["metadata"]["required_tools"] == [
        "property_calculator",
        "admet_predictor",
    ]
    assert [item["tool_name"] for item in result["tool_result_sequence"]] == [
        "property_calculator",
        "admet_predictor",
    ]
    assert all(
        item["status"] == "succeeded"
        for item in result["tool_result_sequence"]
    )


def test_admet_failure_is_not_reported_as_a_completed_scientific_result(
    admet_web_handler,
):
    admet_web_handler.agent_system.tools["admet_predictor"].backend.fail_ids.add(
        "molecule-001"
    )

    _, frames = run_web_turn(admet_web_handler, "请预测 CCO 的 ADMET 特征")
    result = result_of(frames)

    assert result["status"] in {"partial", "failed", "unavailable"}
    assert result["success"] is False
    assert result["tool_result_sequence"][0]["status"] != "succeeded"
