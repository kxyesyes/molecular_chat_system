"""Actual mounted Web lifecycle for socket-local scientific presentation."""
import asyncio
import json

from tests.agent.test_web_decision_runtime import actual_app, ActualSocket, cookie_for, result_of


def test_full_web_ready_reports_registered_domain_capabilities(actual_app):
    from src.agent.contracts.web_requirements import WEB_TOOLS
    async def run():
        async with actual_app(ordinary_policy=None) as bundle:
            async with ActualSocket(bundle.app, await cookie_for(bundle.app)) as socket:
                ready = await socket.ready()
                names = set(bundle.app._get_agent_tool_registry().as_mapping()) & WEB_TOOLS
                assert set(ready['capabilities']['scientific_tools']) == names - {'rag_search'}
                assert ready['capabilities']['rag_retrieval'] is ('rag_search' in names)
    asyncio.run(run())


def test_full_web_keeps_configured_question_budget_before_dispatch(actual_app):
    async def run():
        async with actual_app(ordinary_policy=None) as bundle:
            bundle.app.chat_handler.config = {'inference':{'external_input_max_chars':0, 'input_max_chars':0}}
            async with ActualSocket(bundle.app, await cookie_for(bundle.app)) as socket:
                await socket.ready()
                result = result_of(await socket.turn({'message':'hello'}))
                assert result['status'] == 'rejected'
                assert result['metadata']['stop_reason'] == 'input_budget_exceeded'
                assert not bundle.calls
                assert bundle.app.agent_state_store.get_tool_executions(result['trace_id']) == []
    asyncio.run(run())


def test_failed_serialization_cannot_publish_captured_scientific_authority(actual_app, monkeypatch):
    import src.web.decision_chat as presentation
    original = presentation._result_frame
    def fail_scientific(handler, result, changed, **kwargs):
        if result.tool_results:
            raise ValueError('injected invalid scientific display')
        return original(handler, result, changed, **kwargs)
    monkeypatch.setattr(presentation, '_result_frame', fail_scientific)
    async def run():
        async with actual_app(ordinary_policy=None, respond=compiled_decision) as bundle:
            async with ActualSocket(bundle.app, await cookie_for(bundle.app)) as socket:
                await socket.ready()
                failed = await socket.turn({'message': '只计算 CCO 的分子量与 logP', 'enable_rag':False})
                assert failed[-1]['status'] == 'failed'
                sender, = bundle.app.decision_runtime.sockets
                assert not sender.scientific_memory[-1].get('property_evidence')
                assert not sender.scientific_memory[-1]['agent_used']
    asyncio.run(run())


async def compiled_decision(payload):
    marker = '. Immutable result requirements (not a tool sequence): '
    prompt = next(m['content'] for m in payload['messages'] if marker in (m.get('content') or ''))
    requirements = json.loads(prompt.split(marker, 1)[1])
    evidence = []
    for message in payload['messages'][1:]:
        try:
            value = json.loads(message.get('content', ''))
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict) and isinstance(value.get('quality'), dict):
            eid = value['quality'].get('evidence_id')
            if eid and eid not in evidence:
                evidence.append(eid)
    if len(evidence) < len(requirements['steps']):
        step = requirements['steps'][len(evidence)]
        return dict(version='1', action='tool', tool_name=step['tool_name'],
                    arguments={'step_ref': step['name']}, purpose='Perform compiled test action')
    return dict(version='1', action='finish', response_kind='scientific' if evidence else 'chat',
                evidence_ids=evidence, text='Ordinary test chat')


def test_socket_local_property_comparison_never_recalculates_previous(actual_app):
    async def run():
        async with actual_app(ordinary_policy=None, respond=compiled_decision) as bundle:
            cookie = await cookie_for(bundle.app)
            async with ActualSocket(bundle.app, cookie) as socket:
                await socket.ready()
                first = result_of(await socket.turn({'message': '计算乙醇 CCO 的分子量与 logP', 'enable_rag':False}))
                assert not bundle.protocol_errors, bundle.protocol_errors
                assert first['status'] == 'completed', first['metadata']
                query = '把分析对象改为乙胺 CCN，只比较它和刚才乙醇的分子量与 logP，不要再运行 ADMET。'
                second = result_of(await socket.turn({'message': query, 'enable_rag':False}))
                assert second['status'] == 'completed', second['metadata']
                body = second.get('display_answer', second['final_answer'])
                assert '上轮（复用）' in body and 'CCO' in body and 'CCN' in body
                assert '46.07' in body and '45.08' in body
                executions = bundle.app.agent_state_store.get_tool_executions(second['trace_id'])
                assert len(executions) == 1 and executions[0]['tool_name'] == 'property_calculator'
                # A new socket for the SAME owner must not recover prior evidence.
                async with ActualSocket(bundle.app, cookie) as other:
                    await other.ready()
                    third = result_of(await other.turn({'message':query, 'enable_rag':False}))
                    assert third['status'] == 'partial', third
                    assert '缺少当前聊天上一轮' in third['display_answer']
    asyncio.run(run())


def test_invalid_structure_correction_reuses_only_prior_operation(actual_app):
    async def run():
        async with actual_app(ordinary_policy=None, respond=compiled_decision) as bundle:
            async with ActualSocket(bundle.app, await cookie_for(bundle.app)) as socket:
                await socket.ready()
                first = result_of(await socket.turn({'message':'只计算 CC(C)(( 的分子量与 logP', 'enable_rag':False}))
                assert first['status'] == 'rejected'
                assert not bundle.calls
                second = result_of(await socket.turn({'message':'更正为 CCO，请继续', 'enable_rag':False}))
                assert not bundle.protocol_errors, bundle.protocol_errors
                assert second['status'] == 'completed', second['metadata']
                assert second['tool_result_sequence'][0]['tool_name'] == 'property_calculator'
                assert second['tool_result_sequence'][0]['data'][0]['smiles'] == 'CCO'
                assert len(second['tool_result_sequence']) == 1
    asyncio.run(run())
