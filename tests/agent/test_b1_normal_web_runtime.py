"""Task1 normal-entry behavioral RED; offline HTTP is not live inference."""
import asyncio
from contextlib import nullcontext
import json
import sys

import pytest

from ordinary_chat_fixtures import actual_app, ActualSocket, cookie_for, result_of
from src.rag.service import RAGSystem as ActualRAGSystem
from b1_normal_web_fixtures import temporary_b1_sources


@pytest.mark.parametrize('wire', ['native', 'json'])
def test_normal_entry_b1_retrieval_gap(actual_app, wire):
    """Exercise today's mounted /ws without a not-yet-existing B profile."""
    async def scenario():
        async with actual_app(mode='decision_a2', wire=wire,
                              ordinary_policy='semantic_v1') as b:
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                result = result_of(await socket.turn({
                    'message': '使用 RAG 检索本地分子库中与乙醇相关的分子',
                    'enable_tools': False, 'enable_rag': True, 'rag_count': 3}))
                assert result['metadata'].get('stop_reason') != 'unsupported_scientific_request'
    asyncio.run(scenario())


@pytest.mark.parametrize('empty', [False, True])
def test_temporary_sources_have_no_prior_invocations(tmp_path, monkeypatch, empty):
    """Direct producer baseline only, not normal-Web B dispatch acceptance."""
    from src.agent.tools.rag_search_tool import RAGSearchTool
    from src.agent.tools.reverse_target_tool import ReverseTargetTool
    from src.reverse_target.predictor import ReverseTargetPredictor

    watched = {
        RAGSearchTool.execute.__code__: 'rag_tool',
        ReverseTargetTool.execute.__code__: 'reverse_tool',
        ActualRAGSystem.search_similar_molecules_sync_with_receipt.__code__: 'retrieval',
        ReverseTargetPredictor.predict_with_receipt.__code__: 'prediction',
    }
    entries = []
    previous_profile = sys.getprofile()

    def observe(frame, event, arg):
        if event == 'call' and frame.f_code in watched:
            entries.append(watched[frame.f_code])
        if previous_profile is not None:
            previous_profile(frame, event, arg)

    sys.setprofile(observe)
    try:
        with temporary_b1_sources(tmp_path, monkeypatch, empty=empty) as sources:
            assert entries == [] and sources.embedding_requests == []
            sources.rag.capture_retrieval_eligibility()
            sources.reverse.capture_prediction_source()
            assert entries == []
            rag_result = sources.rag_tool.execute('synthetic query')
            reverse_result = sources.reverse_tool.execute('CCO')
            assert entries == ['rag_tool', 'retrieval', 'reverse_tool', 'prediction']
            assert rag_result['success'] is True and reverse_result['success'] is True
            assert bool(rag_result['data']) is not empty
            assert bool(reverse_result['data']) is not empty
            receipt = rag_result['evidence'][0]['retrieval_receipt']
            assert receipt['diagnostics']['status'] == ('valid_empty' if empty else 'valid_hits')
            assert receipt['diagnostics']['index_search_executed'] is not empty
            assert len(sources.embedding_requests) == 1
            assert sources.embedding_requests[0]['prompt'] == 'synthetic query'
        assert sources.rag.index_status == 'closed'
        with pytest.raises(ValueError):
            sources.reverse.capture_prediction_source()
    finally:
        sys.setprofile(previous_profile)


@pytest.mark.parametrize('fault', ['rag_setup', 'reverse_setup', 'body', 'cancel'])
def test_temporary_source_cleanup_on_failure(tmp_path, monkeypatch, fault):
    from src.reverse_target.predictor import ReverseTargetPredictor

    retired = []
    rag_close, reverse_close = ActualRAGSystem.close, ReverseTargetPredictor.close_strict

    def close_rag(source):
        retired.append(('rag', source))
        rag_close(source)

    def close_reverse(source):
        retired.append(('reverse', source))
        reverse_close(source)

    monkeypatch.setattr(ActualRAGSystem, 'close', close_rag)
    monkeypatch.setattr(ReverseTargetPredictor, 'close_strict', close_reverse)
    error = asyncio.CancelledError if fault == 'cancel' else AssertionError
    if fault == 'rag_setup':
        initialize = ActualRAGSystem.initialize

        async def fail(source):
            await initialize(source)
            raise error('after real RAG initialization')

        monkeypatch.setattr(ActualRAGSystem, 'initialize', fail)
    elif fault == 'reverse_setup':
        initialize = ReverseTargetPredictor.initialize_strict

        def fail(source):
            initialize(source)
            raise error('after real reverse initialization')

        monkeypatch.setattr(ReverseTargetPredictor, 'initialize_strict', fail)
    expected_message = {'rag_setup': 'after real RAG initialization',
                        'reverse_setup': 'after real reverse initialization'}.get(
                            fault, 'fixture body fault')
    with pytest.raises(error, match=expected_message):
        with temporary_b1_sources(tmp_path, monkeypatch):
            raise error('fixture body fault')
    assert [kind for kind, _ in retired] == (
        ['rag'] if fault == 'rag_setup' else ['reverse', 'rag'])
    for kind, source in retired:
        if kind == 'rag':
            assert source.index_status == 'closed'
        else:
            with pytest.raises(ValueError):
                source.capture_prediction_source()


def test_temporary_sources_refuse_nested_asgi_loop(tmp_path, monkeypatch):
    async def scenario():
        with pytest.raises(RuntimeError, match='outside the ASGI event loop'):
            with temporary_b1_sources(tmp_path / 'not-created', monkeypatch):
                pytest.fail('source setup entered running loop')
        assert not (tmp_path / 'not-created').exists()
    asyncio.run(scenario())


@pytest.mark.parametrize('wire', ['native', 'json'])
def test_actual_sources_mount_without_preexecuting(actual_app, tmp_path, monkeypatch, wire):
    """Original-profile property control, not B admission/seven-tool acceptance."""
    from src.agent.openai_compatible_model import OpenAICompatibleModel
    from src.agent.tools import get_core_tools
    from src.agent.tools.activity_predictor_tool import ActivityPredictorTool
    from src.agent.tools.target_database_tool import TargetDatabaseTool
    from test_decision_loop import tool, finish

    with temporary_b1_sources(tmp_path / 'sources', monkeypatch) as sources:
        original_reverse = sources.reverse.capture_prediction_source()
        assert sources.embedding_requests == []
        factory_calls = []

        def factory(model, *, rag_system):
            factory_calls.append(rag_system)
            assert rag_system is sources.rag
            # Preserve the existing full-pool registration contract (including
            # generator/ranker); registration is not permission to invoke them.
            # Activity/target are actual lazy constructors, not invoked here.
            return [*get_core_tools(model), ActivityPredictorTool(), TargetDatabaseTool(),
                    sources.rag_tool, sources.reverse_tool]

        async def respond(payload):
            # The actual loop emits native tool messages or JSON user messages.
            observation_role = 'tool' if wire == 'native' else 'user'
            observations = [json.loads(m['content'])
                            for m in payload['messages']
                            if m['role'] == observation_role and m['content'].startswith('{')]
            if not observations:
                return tool().model_dump()
            return finish([o['quality']['evidence_id'] for o in observations]).model_dump()

        async def scenario():
            async with actual_app(mode='decision_a2', wire=wire,
                                  ordinary_policy='semantic_v1', tool_factory=factory,
                                  rag_service=sources.rag, respond=respond) as b:
                assert type(b.model) is OpenAICompatibleModel
                assert b.app.rag_system is sources.rag and b.rag is sources.rag
                assert sources.embedding_requests == []
                assert sources.reverse.capture_prediction_source() == original_reverse
                async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                    await socket.ready()
                    result = result_of(await socket.turn({
                        'message': '计算性质；SMILES: CCO',
                        'enable_tools': True, 'enable_rag': False, 'rag_count': 3}))
                    if b.protocol_errors:
                        raise b.protocol_errors[0]
                    assert result['success'] is True and result['status'] == 'completed', {
                        'stop_reason': result['metadata'].get('stop_reason'),
                        'message_roles': [[m['role'] for m in call['messages']] for call in b.calls]}
                    assert len(b.calls) == 2
                    assert len(result['tool_result_sequence']) == 1
                    assert result['tool_result_sequence'][0]['tool_name'] == 'property_calculator'
                    assert len(b.app.agent_state_store.get_tool_executions(result['trace_id'])) == 1
                    assert result['metadata']['retrieval_performed'] is False
                assert sources.embedding_requests == []
                assert sources.reverse.capture_prediction_source() == original_reverse
        asyncio.run(scenario())
        assert factory_calls == [sources.rag]
        assert sources.rag.index_status == 'closed'
        # The tool/app borrows reverse; only this outer source fixture retires it.
        assert sources.reverse.capture_prediction_source() == original_reverse
    with pytest.raises(ValueError):
        sources.reverse.capture_prediction_source()


@pytest.mark.parametrize('exit_kind', ['normal', 'assertion', 'cancel', 'setup'])
def test_fixture_injections_rollback(actual_app, monkeypatch, tmp_path, exit_kind):
    """Fixture ownership control only; this uninitialized source is not evidence."""
    from src.agent import tools
    from src.web import app as module

    source = ActualRAGSystem({'rag': {'csv_path': str(tmp_path / 'unused.csv')}})
    original_close = source.close
    closed, factory_calls = [], []

    def close():
        closed.append(True)
        original_close()

    monkeypatch.setattr(source, 'close', close)
    old_factory, old_rag = tools.get_all_tools, module.RAGSystem

    def factory(model, *, rag_system):
        factory_calls.append(rag_system)
        if exit_kind == 'setup':
            raise RuntimeError('fixture setup fault')
        return tools.get_core_tools(model)

    async def scenario():
        previous_profile = sys.getprofile()
        error = {'assertion': AssertionError, 'cancel': asyncio.CancelledError,
                 'setup': RuntimeError}.get(exit_kind)
        expected_message = 'fixture setup fault' if exit_kind == 'setup' else 'fixture body fault'
        with (pytest.raises(error, match=expected_message) if error else nullcontext()):
            async with actual_app(mode='decision_a2', ordinary_policy='semantic_v1',
                                  binding_profile=None, tool_factory=factory,
                                  rag_service=source) as b:
                assert b.rag is source and b.app.rag_system is source
                assert b.app.chat_handler.rag_service is source
                assert not b.calls and not b.admission_calls
                if error:
                    raise error('fixture body fault')
        assert closed == [True]
        assert source.index_status == 'closed'
        assert factory_calls == [source]
        assert tools.get_all_tools is old_factory and module.RAGSystem is old_rag
        assert sys.getprofile() is previous_profile
        # A later build must retain the historical default resource/tool pool.
        async with actual_app(mode='decision_a2') as legacy:
            assert legacy.rag is not source
            assert legacy.app.rag_system is legacy.rag
            assert 'rag_search' not in legacy.app.agent_system.tools
            assert 'reverse_target_predictor' not in legacy.app.agent_system.tools
    asyncio.run(scenario())
