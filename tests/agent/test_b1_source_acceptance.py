"""Portable Pi acceptance with test-owned FAISS, reverse fingerprints and SQLite.

Embedding bytes and model decisions use offline transports.
"""
import asyncio
from contextlib import closing, contextmanager, ExitStack, nullcontext
import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.agent.tools import get_core_tools
from src.agent.tools.rag_search_tool import RAGSearchTool
from src.agent.tools.reverse_target_tool import ReverseTargetTool
from src.rag.service import RAGSystem
from src.reverse_target.predictor import ReverseTargetPredictor
from tests.agent import test_rag_receipt_consumption as rag_fixture
from tests.agent.test_pi_web_business import compiled_decision
from tests.agent.test_web_decision_runtime import ActualSocket, actual_app, cookie_for, result_of
from tests.test_reverse_target_invocation_receipts import close_fixture_mmaps, make_writer_database
from test_decision_binding_acceptance import requirements
from test_decision_binding_loop import loop_case, run
from test_decision_loop import finish, last_observation, tool


@contextmanager
def temporary_sources(directory, monkeypatch, *, empty=False):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError('Initialize sources outside the ASGI event loop')
    directory.mkdir(parents=True, exist_ok=True)
    with ExitStack() as owners:
        patch = owners.enter_context(monkeypatch.context())

        def create_rag(config):
            source = RAGSystem(config)

            def retire():
                if source.index_status != 'closed':
                    source.close()

            owners.callback(retire)
            return source

        # The shared builder initializes before returning. Register ownership
        # at construction so an exception inside initialize still closes it.
        patch.setattr(rag_fixture, 'RAGSystem', create_rag)
        rag, requests = rag_fixture.initialized_service(directory, patch, empty=empty)
        reverse = make_writer_database(directory / 'reverse', smiles=() if empty else ('CCO', 'CCC'))
        owners.callback(close_fixture_mmaps, reverse)
        owners.callback(reverse.close_strict)
        reverse.initialize_strict()
        reverse_tool = ReverseTargetTool(reverse)
        owners.callback(reverse_tool.close)
        yield SimpleNamespace(rag=rag, reverse=reverse, rag_tool=RAGSearchTool(rag),
                              reverse_tool=reverse_tool, requests=requests)


@contextmanager
def mounted_sources(actual_app, sources, monkeypatch, *, wire='native', fail_setup=False):
    from src.agent import tools
    from src.web import app as module

    def factory(model, *, rag_system):
        assert rag_system is sources.rag
        if fail_setup:
            raise RuntimeError('fixture assembly fault')
        return [*get_core_tools(model), sources.rag_tool, sources.reverse_tool]

    with monkeypatch.context() as patch:
        patch.setattr(module, 'RAGSystem', lambda config: sources.rag)
        patch.setattr(tools, 'get_all_tools', factory)
        yield actual_app(mode='pi', ordinary_policy='medchat-web-v1', wire=wire,
                         async_assembly=True, respond=compiled_decision)


def count_producers(monkeypatch):
    # Install on both classes before setup, including calls made in Pi workers.
    calls = []
    for cls, method in ((RAGSearchTool, 'execute'), (RAGSystem, 'search_similar_molecules_sync_with_receipt'),
                        (ReverseTargetTool, 'execute'), (ReverseTargetPredictor, 'predict_with_receipt')):
        original = getattr(cls, method)

        def observe(self, *args, original=original, cls=cls, method=method, **kwargs):
            calls.append((cls, method))
            return original(self, *args, **kwargs)

        monkeypatch.setattr(cls, method, observe)
    return calls


@pytest.mark.parametrize('empty', [False, True], ids=['hits', 'empty'])
def test_temporary_sources_are_cold_until_invoked(tmp_path, monkeypatch, empty):
    calls = count_producers(monkeypatch)
    with temporary_sources(tmp_path, monkeypatch, empty=empty) as sources:
        sources.rag.capture_retrieval_eligibility()
        sources.reverse.capture_prediction_source()
        assert calls == [] and sources.requests == []
        rag = sources.rag_tool.execute('synthetic query')
        rev = sources.reverse_tool.execute('CCO')
        assert rag['success'] and rev['success']
        assert bool(rag['data']) is not empty and bool(rev['data']) is not empty
        assert calls == [(RAGSearchTool, 'execute'), (RAGSystem, 'search_similar_molecules_sync_with_receipt'),
                         (ReverseTargetTool, 'execute'), (ReverseTargetPredictor, 'predict_with_receipt')]
        diagnostics = rag['evidence'][0]['retrieval_receipt']['diagnostics']
        assert diagnostics['status'] == ('valid_empty' if empty else 'valid_hits')
        assert diagnostics['index_search_executed'] is not empty
        assert [item['prompt'] for item in sources.requests] == ['synthetic query']
    assert sources.rag.index_status == 'closed'
    with pytest.raises(ValueError):
        sources.reverse.capture_prediction_source()


@pytest.mark.parametrize('fault', ['rag_setup', 'reverse_setup', 'body', 'cancel'])
def test_source_owners_close_on_failure(tmp_path, monkeypatch, fault):
    closed = []
    for cls, name in ((RAGSystem, 'close'), (ReverseTargetPredictor, 'close_strict')):
        original = getattr(cls, name)

        def observe(source, original=original):
            original(source)
            closed.append(source)

        monkeypatch.setattr(cls, name, observe)
    if fault == 'rag_setup':
        initialize = RAGSystem.initialize

        async def fail(source):
            await initialize(source)
            raise AssertionError('rag_setup')

        monkeypatch.setattr(RAGSystem, 'initialize', fail)
    elif fault == 'reverse_setup':
        initialize = ReverseTargetPredictor.initialize_strict

        def fail(source):
            initialize(source)
            raise AssertionError('reverse_setup')

        monkeypatch.setattr(ReverseTargetPredictor, 'initialize_strict', fail)
    error = asyncio.CancelledError if fault == 'cancel' else AssertionError
    with pytest.raises(error, match=f'^{fault}$'):
        with temporary_sources(tmp_path, monkeypatch):
            assert fault in ('body', 'cancel')
            raise error(fault)
    assert [type(s) for s in closed] == ([RAGSystem] if fault == 'rag_setup' else [ReverseTargetPredictor, RAGSystem])
    assert closed[-1].index_status == 'closed'
    if fault != 'rag_setup':
        with pytest.raises(ValueError):
            closed[0].capture_prediction_source()


def test_source_setup_rejects_nested_asgi_loop(tmp_path, monkeypatch):
    directory = tmp_path / 'sources'

    async def scenario():
        with pytest.raises(RuntimeError, match='outside the ASGI event loop'):
            with temporary_sources(directory, monkeypatch):
                pytest.fail('entered nested setup')
        assert not directory.exists()

    asyncio.run(scenario())


@pytest.mark.parametrize('wire', ['native', 'json'])
def test_mounted_sources_stay_cold_through_property_turn(actual_app, tmp_path, monkeypatch, wire):
    calls = count_producers(monkeypatch)
    with temporary_sources(tmp_path, monkeypatch) as sources:
        initial = sources.reverse.capture_prediction_source()

        async def scenario():
            with mounted_sources(actual_app, sources, monkeypatch, wire=wire) as application:
                async with application as browser:
                    assert browser.app.rag_system is sources.rag
                    async with ActualSocket(browser.app, await cookie_for(browser.app)) as ws:
                        ready = await ws.ready()
                        assert ready['capabilities']['rag_retrieval'] is True
                        assert 'reverse_target_predictor' in ready['capabilities']['scientific_tools']
                        assert calls == [] and not browser.calls
                        result = result_of(await ws.turn({'message': '只计算 CCO 的分子量和 logP', 'enable_rag': False}))
                    assert not browser.protocol_errors, browser.protocol_errors
                    assert result['status'] == 'completed' and result['success']
                    stored = browser.app.agent_state_store.get_tool_executions(result['trace_id'])
                    assert [row['tool_name'] for row in stored] == ['property_calculator']
                    assert not result['metadata']['retrieval_performed']
            assert calls == [] and sources.requests == []
            assert sources.rag.index_status == 'closed'
            assert sources.reverse.capture_prediction_source() == initial

        asyncio.run(scenario())


@pytest.mark.parametrize('wire', ['native', 'json'])
@pytest.mark.parametrize('empty', [False, True], ids=['hits', 'empty'])
def test_mounted_rag_turn_preserves_real_receipt(actual_app, tmp_path, monkeypatch, wire, empty):
    with temporary_sources(tmp_path, monkeypatch, empty=empty) as sources:
        observed = []
        retrieve = sources.rag.search_similar_molecules_sync_with_receipt

        def observe_retrieval(*args, **kwargs):
            envelope = retrieve(*args, **kwargs)
            observed.append(envelope)
            return envelope

        monkeypatch.setattr(sources.rag, 'search_similar_molecules_sync_with_receipt', observe_retrieval)

        async def scenario():
            with mounted_sources(actual_app, sources, monkeypatch, wire=wire) as application:
                async with application as browser:
                    async with ActualSocket(browser.app, await cookie_for(browser.app)) as ws:
                        await ws.ready()
                        assert not sources.requests
                        frames = await ws.turn({'message': 'CCO', 'enable_tools': False, 'enable_rag': True,
                                                'rag_count': 5})
                    assert not browser.protocol_errors, browser.protocol_errors
                    result = result_of(frames)
                    assert result['status'] == 'completed' and result['success']
                    assert result['metadata']['retrieval_performed'] is True
                    row, = browser.app.agent_state_store.get_tool_executions(result['trace_id'])
                    assert row['tool_name'] == 'rag_search'
                    raw = row['output']
                    receipt = raw['evidence'][0]['retrieval_receipt']
                    envelope, = observed
                    assert receipt == envelope['receipt'] and raw['data'] == envelope['records']
                    assert receipt['diagnostics']['status'] == ('valid_empty' if empty else 'valid_hits')
                    assert receipt['diagnostics']['requested_k'] == 5
                    assert bool(raw['data']) is not empty
                    assert [request['prompt'] for request in sources.requests] == ['CCO']

        asyncio.run(scenario())


@pytest.mark.parametrize('exit_kind', ['normal', 'body', 'cancel', 'setup'])
def test_web_injections_roll_back(actual_app, tmp_path, monkeypatch, caplog, exit_kind):
    from src.agent import tools
    from src.web import app as module

    old_rag, old_factory, old_profile = module.RAGSystem, tools.get_all_tools, sys.getprofile()
    with temporary_sources(tmp_path, monkeypatch) as sources:
        close = Mock(wraps=sources.rag.close)
        monkeypatch.setattr(sources.rag, 'close', close)

        async def scenario():
            error = {'body': AssertionError, 'cancel': asyncio.CancelledError, 'setup': ValueError}.get(exit_kind)
            # The Pi app logs the failed tool factory, then refuses the empty registry.
            message = '^Agent registration invalid:' if exit_kind == 'setup' else '^fixture body fault$'
            with pytest.raises(error, match=message) if error else nullcontext():
                with mounted_sources(actual_app, sources, monkeypatch, fail_setup=exit_kind == 'setup') as application:
                    async with application as browser:
                        assert browser.app.chat_handler.rag_service is sources.rag
                        assert not browser.calls
                        if error:
                            raise error('fixture body fault')
            if exit_kind == 'setup':
                assert any('fixture assembly fault' in record.getMessage() for record in caplog.records)
            close.assert_called_once_with()
            assert sources.rag.index_status == 'closed'
            assert module.RAGSystem is old_rag and tools.get_all_tools is old_factory
            assert sys.getprofile() is old_profile
            async with actual_app(mode='pi', ordinary_policy='medchat-web-v1') as later:
                assert later.app.rag_system is not sources.rag
                assert 'reverse_target_predictor' not in later.app.agent_system.tools
                assert 'rag_search' not in later.app.agent_system.tools

        asyncio.run(scenario())


@pytest.mark.parametrize('wire', ['native', 'json'])
def test_pi_b1_reverse_record_drives_real_local_lookup(loop_case, tmp_path, monkeypatch, wire):
    from src.agent.evidence import EvidenceLedger
    from src.agent.tools.target_database_tool import TargetDatabaseTool
    from src.target_search.database import get_connection
    from src.target_search.service import TargetSearchService

    root = tmp_path / 'targets'
    monkeypatch.setenv('TARGET_DB_PATH', str(root / 'targets.sqlite'))
    monkeypatch.setenv('TARGET_CACHE_DIR', str(root / 'cache'))
    with closing(TargetSearchService(project_root=root, auto_seed=False)) as service:
        with closing(get_connection(root)) as db:
            db.executemany('INSERT INTO targets (gene_symbol, protein_name) VALUES (?, ?)',
                           [('SYNTHETIC_A', 'Synthetic A'), ('SYNTHETIC_B', 'Synthetic B')])
            db.commit()
        monkeypatch.setattr(service, '_search_authoritative', lambda *a, **kw: pytest.fail('expected local lookup'))
        search = Mock(wraps=service.search_targets)
        monkeypatch.setattr(service, 'search_targets', search)
        with temporary_sources(tmp_path / 'sources', monkeypatch) as sources:
            predict = sources.reverse.predict_with_receipt
            envelopes = []

            def observe_prediction(*args, **kwargs):
                envelope = predict(*args, **kwargs)
                envelopes.append(envelope)
                return envelope

            monkeypatch.setattr(sources.reverse, 'predict_with_receipt', observe_prediction)
            for smiles, expected, gene in (('CCO', 'Synthetic A', 'SYNTHETIC_A'), ('CCC', 'Synthetic B', 'SYNTHETIC_B')):
                selected = {}
                target = TargetDatabaseTool()
                target._service = service

                def choose(messages):
                    reverse = last_observation(messages)
                    descriptor = reverse['record_references'][0]
                    projected = reverse['data'][descriptor['row_index']]
                    assert projected['target_name'] == expected
                    selected.update(descriptor=descriptor, evidence_id=reverse['quality']['evidence_id'])
                    return tool(target.name, {'input_ref': selected['evidence_id'], 'record_ref': descriptor['record_ref']})

                def done(messages):
                    observation = last_observation(messages)
                    assert observation['tool_name'] == target.name
                    return finish([observation['quality']['evidence_id']])

                case = loop_case([tool(sources.reverse_tool.name), choose, done],
                                 [sources.reverse_tool, target], mode=wire)
                result = run(case, 'SMILES: ' + smiles,
                             req=requirements(target_result={'input': 'reverse', 'require_resolved': True}))
                assert result.success, result.metadata
                assert [elt.tool_name for elt in result.tool_results] == [sources.reverse_tool.name, target.name]
                rows = case.store.get_tool_executions(result.trace_id)
                envelope, = envelopes
                entry, = [item for item in rows[0]['output']['evidence'] if 'prediction_receipt' in item]
                assert entry['prediction_receipt'] == envelope['receipt']
                index = selected['descriptor']['row_index']
                record = envelope['records'][index]
                assert entry['records'][index] == record and record['target_name'] == expected
                assert selected['descriptor']['selection_sha256'] == EvidenceLedger.output_digest(
                    {'row_index': index, 'row': record})
                output = rows[1]['output']
                hit, = output['data']
                assert (hit['protein_name'], hit['gene_symbol']) == (expected, gene)
                quality = output['quality']
                assert quality['lookup_status'] == 'resolved' and quality['lookup_path'] == ['local']
                assert quality['input_evidence_ids'] == [selected['evidence_id']]
                assert selected['descriptor']['selection_sha256'] in json.dumps(quality['binding_proof'])
                search.assert_called_once_with(expected)
                assert not sources.requests
                envelopes.clear()
                search.reset_mock()
