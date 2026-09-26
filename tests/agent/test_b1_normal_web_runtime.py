"""Task1 gap regressions and Task4 mounted B1 entry; HTTP is offline, not live inference."""
import asyncio
from copy import deepcopy
from contextlib import nullcontext
import json
import sys

import pytest

from ordinary_chat_fixtures import actual_app, ActualSocket, cookie_for, result_of
from src.rag.service import RAGSystem as ActualRAGSystem
from b1_normal_web_fixtures import temporary_b1_sources
from src.agent.contracts.decision_bindings import B1_PROFILE_REVISION, B1_TOOLS


def actual_b1_factory(sources):
    def factory(model, *, rag_system):
        from src.agent.tools import get_core_tools
        from src.agent.tools.activity_predictor_tool import ActivityPredictorTool
        from src.agent.tools.target_database_tool import TargetDatabaseTool
        assert rag_system is sources.rag
        return [*get_core_tools(model), ActivityPredictorTool(), TargetDatabaseTool(),
                sources.rag_tool, sources.reverse_tool]
    return factory


@pytest.mark.parametrize('wire', ['native', 'json'])
def test_b1_ready_advertises_installed_not_ready(actual_app, tmp_path, monkeypatch, wire):
    with temporary_b1_sources(tmp_path / 'sources', monkeypatch) as sources:
        async def run():
            async with actual_app(mode='decision_a2', wire=wire, ordinary_policy='semantic_v1',
                    binding_profile=B1_PROFILE_REVISION, tool_factory=actual_b1_factory(sources),
                    rag_service=sources.rag) as b:
                async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                    ready = await socket.ready()
                    assert ready['normal_chat_mode'] == 'decision_a2'
                    assert ready['capabilities']['rag_retrieval'] is True
                    assert ready['capabilities']['rag_count'] == 3
                    assert set(ready['capabilities']['scientific_tools']) == B1_TOOLS
                assert all(f.readiness == 'unknown' for f in b.app.ordinary_capability_base.features)
                assert not b.calls and not sources.embedding_requests
        asyncio.run(run())


@pytest.mark.parametrize('wire', ['native', 'json'])
@pytest.mark.parametrize('kind', ['property', 'rag', 'chat'])
def test_b1_actual_mounted_entry(actual_app, tmp_path, monkeypatch, wire, kind):
    query = {'property': '计算性质；SMILES: CCO',
             'rag': '使用 RAG 检索本地分子库中与乙醇相关的分子',
             'chat': '你好，我想了解一下这个系统能做什么？'}[kind]
    _actual_entry_scenario(actual_app, tmp_path, monkeypatch, wire, kind, query)


def _actual_entry_scenario(actual_app, tmp_path, monkeypatch, wire, kind, query):
    from ordinary_chat_fixtures import intent_http_response, chat_decision
    from test_decision_loop import tool, finish
    with temporary_b1_sources(tmp_path / 'sources', monkeypatch) as sources:
        rag_invocations = []
        rag_outputs = []
        execute_rag = sources.rag_tool.execute

        def observe_rag(*args, **kwargs):
            rag_invocations.append((args, kwargs))
            output = execute_rag(*args, **kwargs)
            rag_outputs.append(deepcopy(output))
            return output

        monkeypatch.setattr(sources.rag_tool, 'execute', observe_rag)
        async def run():
            count = 0
            async def respond(payload):
                nonlocal count
                count += 1
                if kind == 'chat':
                    return (intent_http_response(wire=wire) if count == 1 else
                            chat_decision('本系统支持分子性质等功能；分子性质可用性未知。'))
                role = 'tool' if wire == 'native' else 'user'
                observations = [json.loads(m['content']) for m in payload['messages']
                                if m['role'] == role and m['content'].startswith('{')]
                if not observations:
                    decision = tool().model_dump()
                    decision['tool_name'] = 'rag_search' if kind == 'rag' else 'property_calculator'
                    return decision
                return finish([o['quality']['evidence_id'] for o in observations]).model_dump()
            async with actual_app(mode='decision_a2', wire=wire, ordinary_policy='semantic_v1',
                    binding_profile=B1_PROFILE_REVISION, tool_factory=actual_b1_factory(sources),
                    rag_service=sources.rag, respond=respond) as b:
                async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                    await socket.ready()
                    result = result_of(await socket.turn(dict(message=query,
                        enable_tools=kind == 'property', enable_rag=kind != 'property', rag_count=3)))
                    if b.protocol_errors:
                        raise b.protocol_errors[0]
                    assert result['success'] is True, result['metadata']
                    assert result['status'] == 'completed'
                    assert len(b.calls) == 2
                    executions = b.app.agent_state_store.get_tool_executions(result['trace_id'])
                    assert len(executions) == (0 if kind == 'chat' else 1)
                    durable = b.app.agent_state_store.get_run(result['trace_id'])
                    binding = durable['metadata']['ordinary_admission']['binding']
                    assert binding['profile_revision'] == B1_PROFILE_REVISION
                    if kind == 'rag':
                        assert len(rag_invocations) == 1
                        assert len(sources.embedding_requests) == 1
                        assert sources.embedding_requests[0]['prompt'] == query
                        assert executions[0]['tool_name'] == 'rag_search'
                        assert executions[0]['input'] == {'query': query}
                        observed, = result['tool_result_sequence']
                        assert observed['tool_name'] == 'rag_search'
                        assert observed['quality']['validated'] is True
                        receipt = observed['evidence'][0]['retrieval_receipt']
                        assert receipt['diagnostics']['status'] == 'valid_hits'
                        assert receipt['diagnostics']['index_search_executed'] is True
                        assert receipt['diagnostics']['requested_k'] == 3
                        assert observed['data']
                        # SQLite retains full producer evidence independently of
                        # the Web transport's existing display sanitization.
                        persisted = executions[0]['output']
                        produced, = rag_outputs
                        assert produced['success'] is True
                        for field in ('data', 'evidence'):
                            assert persisted[field] == produced[field]
                        assert persisted['quality']['validated'] is True
                        assert persisted['quality']['evidence_id'] == observed['quality']['evidence_id']
                    else:
                        assert not rag_invocations
                        assert not sources.embedding_requests
                    return result
        return asyncio.run(run())


@pytest.mark.parametrize('wire', ['native', 'json'])
@pytest.mark.parametrize('shape', ['unicode', 'escaped', 'exact', 'over'])
def test_b1_intent_prefix_preserves_validated_content(actual_app, tmp_path, monkeypatch, wire, shape):
    from ordinary_chat_fixtures import intent_http_response, chat_decision
    from src.web import decision_runtime
    from src.agent.harness.decision_policy import DecisionBoundaryError
    original_prefix = decision_runtime.history_prefix
    prefix_errors = []
    def observe_prefix(*args, **kwargs):
        try:
            return original_prefix(*args, **kwargs)
        except DecisionBoundaryError as exc:
            prefix_errors.append(str(exc))
            raise
    monkeypatch.setattr(decision_runtime, 'history_prefix', observe_prefix)
    queries = {'unicode': '你' * 5461, 'escaped': '\\' * 9000,
               'exact': 'a' * 16384, 'over': 'a' * 16385}
    query = queries[shape]
    assert len(json.dumps({'message': query}, ensure_ascii=False).encode()) < 24 * 1024
    if shape != 'over':
        assert len(query.encode()) <= 16 * 1024
        assert len(json.dumps(query).encode()) > 16 * 1024
    with temporary_b1_sources(tmp_path / 'sources', monkeypatch) as sources:
        async def run():
            async def respond(payload):
                if any('Classify the entire unchanged user request' in m['content']
                       for m in payload['messages'] if m['role'] == 'system'):
                    return intent_http_response('conversation', wire=wire)
                return chat_decision('你好，很高兴与你交流。')
            async with actual_app(mode='decision_a2', wire=wire, ordinary_policy='semantic_v1',
                    binding_profile=B1_PROFILE_REVISION, tool_factory=actual_b1_factory(sources),
                    rag_service=sources.rag, respond=respond) as b:
                async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                    await socket.ready()
                    prior = result_of(await socket.turn({'message': '你好'}))
                    assert prior['success'] is True, prior['metadata']
                    before = len(b.calls)
                    result = result_of(await socket.turn({'message': query}))
                    if shape == 'over':
                        assert result['status'] == 'rejected'
                        assert len(b.calls) == before
                    else:
                        assert prefix_errors == [], {'prefix_errors': prefix_errors,
                                                      'new_http_requests': len(b.calls) - before}
                        assert len(b.calls) == before + 2
                        for call in b.calls[before:]:
                            assert [m for m in call['messages'] if m['role'] != 'system'] == [
                                {'role': 'user', 'content': '你好'},
                                {'role': 'assistant', 'content': prior['final_answer']},
                                {'role': 'user', 'content': query}]
                        assert not any(m['content'] == '' for call in b.calls for m in call['messages'])
                        assert result['success'] is True, {
                            'stop_reason': result['metadata'].get('stop_reason'),
                            'new_http_requests': len(b.calls) - before}
                    assert not sources.embedding_requests
        asyncio.run(run())


@pytest.mark.parametrize('wire', ['native', 'json'])
def test_b1_unavailable_sources_do_not_block_ordinary_chat(actual_app, tmp_path, monkeypatch, wire):
    from ordinary_chat_fixtures import intent_http_response, chat_decision
    from test_b1_normal_web_lifecycle import b1_pool
    source = ActualRAGSystem({'rag': {'csv_path': str(tmp_path / 'absent.csv'),
                                   'vector_store_path': str(tmp_path / 'absent-index')}})
    async def run():
        async def respond(payload):
            if any('Classify the entire unchanged user request' in m['content']
                   for m in payload['messages'] if m['role'] == 'system'):
                return intent_http_response('capability', wire=wire)
            return chat_decision('本系统支持分子性质等功能；分子性质可用性未知。')
        async with actual_app(mode='decision_a2', wire=wire, ordinary_policy='semantic_v1',
                binding_profile=B1_PROFILE_REVISION, tool_factory=b1_pool,
                rag_service=source, respond=respond) as b:
            assert not source.is_initialized
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                result = result_of(await socket.turn({'message': '你好，我想了解一下这个系统能做什么？',
                                                      'enable_rag': True, 'enable_tools': False}))
                assert result['success'] is True, result['metadata']
                assert len(b.calls) == 2
                assert not source.is_initialized
                assert b.app.agent_system.tools['activity_predictor']._predictor is None
                assert b.app.agent_system.tools['reverse_target_predictor']._predictor is None
                assert b.app.agent_state_store.get_tool_executions(result['trace_id']) == []
        assert source.index_status == 'closed'
    asyncio.run(run())


@pytest.mark.parametrize('wire', ['native', 'json'])
def test_b1_one_captured_view_survives_queued_writer(actual_app, tmp_path, monkeypatch, wire):
    from ordinary_chat_fixtures import intent_http_response, chat_decision
    from src.agent.contracts.ordinary_admission import capability_digest
    from src.web import decision_request, decision_runtime
    from src.agent.harness import decision_loop
    with temporary_b1_sources(tmp_path / 'sources', monkeypatch) as sources:
        async def run():
            entered, release = asyncio.Event(), asyncio.Event()
            async def respond(payload):
                if any('Classify the entire unchanged user request' in m['content']
                       for m in payload['messages'] if m['role'] == 'system'):
                    entered.set()
                    await release.wait()
                    return intent_http_response(wire=wire)
                return chat_decision('本系统支持分子性质等功能；分子性质可用性未知。')
            async with actual_app(mode='decision_a2', wire=wire, ordinary_policy='semantic_v1',
                    binding_profile=B1_PROFILE_REVISION, tool_factory=actual_b1_factory(sources),
                    rag_service=sources.rag, respond=respond) as b:
                old = b.app.ordinary_capability_base
                snapshots, preparations, loops = [], [], []
                previous = sys.getprofile()
                def observe(frame, event, value):
                    if event == 'return' and frame.f_code is decision_runtime.WebDecisionRuntime._snapshot.__code__:
                        snapshots.append(value)
                    if event == 'call' and frame.f_code is decision_request.prepare_with_intent.__code__:
                        preparations.append(frame.f_locals['capability_snapshot'])
                    if event == 'call' and frame.f_code is decision_loop.ModelDecisionLoop.__init__.__code__:
                        loops.append(frame.f_locals['binding_profile'])
                    if previous:
                        previous(frame, event, value)
                sys.setprofile(observe)
                writer = turn = None
                try:
                    async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                        await socket.ready()
                        turn = asyncio.create_task(socket.turn({'message': '你好，我想了解一下这个系统能做什么？',
                                                               'enable_tools': False, 'enable_rag': True}))
                        await asyncio.wait_for(entered.wait(), 3)
                        writer = asyncio.create_task(b.app._replace_ordinary_capability_base(old))
                        await asyncio.sleep(0)
                        assert b.app.model_request_gate._writers == 1
                        assert b.app.model_request_gate._readers == 1
                        assert not writer.done()
                        release.set()
                        result = result_of(await turn)
                        assert result['success'] is True, result['metadata']
                        await asyncio.wait_for(writer, 3)
                        assert len(snapshots) == len(preparations) == 1
                        view = snapshots[0]
                        assert preparations[0] is view and loops == [B1_PROFILE_REVISION]
                        assert view.capability_generation == old.capability_generation
                        assert b.app.capability_generation != old.capability_generation
                        assert {f.id for f in view.features if f.permitted} == {'ordinary_chat', 'rag_retrieval'}
                        for call, marker in zip(b.calls, [
                                'Trusted product/runtime facts (registration is not readiness): ',
                                'Frozen ordinary capabilities: ']):
                            content = next(m['content'] for m in call['messages']
                                           if m['role'] == 'system' and marker in m['content']).split(marker, 1)[1]
                            encoded, _ = json.JSONDecoder().raw_decode(content)
                            assert encoded == view.model_dump(mode='json')
                        saved = b.app.agent_state_store.get_run(result['trace_id'])
                        assert saved['metadata']['ordinary_admission']['binding']['capability_digest'] == capability_digest(view)
                finally:
                    release.set()
                    await asyncio.gather(*(t for t in (writer, turn) if t), return_exceptions=True)
                    sys.setprofile(previous)
        asyncio.run(run())


@pytest.mark.parametrize('wire', ['native', 'json'])
def test_normal_entry_b1_retrieval_gap(actual_app, tmp_path, monkeypatch, wire):
    """The original gap expectation now uses the approved explicit B1 assembly."""
    result = _actual_entry_scenario(
        actual_app, tmp_path, monkeypatch, wire, 'rag',
        '使用 RAG 检索本地分子库中与乙醇相关的分子')
    assert result['metadata'].get('stop_reason') != 'unsupported_scientific_request'


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
