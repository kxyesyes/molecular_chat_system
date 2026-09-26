"""Load-free consumer checks: real temporary sources, synthetic HTTP only."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from src.agent.tools.rag_search_tool import RAGSearchTool
from src.agent.tools.reverse_target_tool import ReverseTargetTool
from tests.agent.test_rag_receipt_consumption import initialized_service
from tests.test_reverse_target_invocation_receipts import (
    close_fixture_mmaps, make_writer_database,
)


@pytest.fixture
def sources(tmp_path, monkeypatch):
    """Always retire actual sources, including assertion/setup failure paths."""
    cleanup = []

    def make(kind, *, empty=False):
        directory = tmp_path / str(len(cleanup))
        directory.mkdir()
        if kind == 'reverse':
            source = make_writer_database(directory, smiles=() if empty else ('CCO', 'CCC'))
            cleanup.append((kind, source))
            source.initialize_strict()
            tool, query, requests = ReverseTargetTool(source), 'CCO', []
            snapshot = source.capture_prediction_source()
            projection = dict(kind=kind, generation_id=snapshot.generation_id,
                              source_sha256=snapshot.source_sha256,
                              configuration_sha256=snapshot.configuration_sha256)
        else:
            source, requests = initialized_service(directory, monkeypatch, empty=empty)
            cleanup.append((kind, source))
            tool, query = RAGSearchTool(source), 'synthetic query'
            snapshot = source.capture_retrieval_eligibility()
            projection = dict(kind=kind, generation_id=snapshot.generation_id,
                              epoch=snapshot.epoch,
                              configuration_sha256=snapshot.configuration_sha256,
                              source_identity_sha256=snapshot.source_identity_sha256)
        result = tool.execute(query)
        assert result['success'] is True
        assert bool(result['data']) is not empty
        assert len(requests) == (1 if kind == 'rag' else 0)
        return SimpleNamespace(kind=kind, source=source, tool=tool, query=query,
                               result=result, projection=projection, requests=requests)

    yield make
    for kind, source in reversed(cleanup):
        if kind == 'reverse':
            source.close_strict()
            close_fixture_mmaps(source)
        else:
            source.close()


def forbid_work(case, monkeypatch):
    calls = []

    def forbidden(*args, **kwargs):
        calls.append(True)
        pytest.fail('current-source validation reran scientific/load work')

    names = (
        ('initialize_strict', 'load', 'predict', 'predict_with_receipt', 'predict_batch',
         '_predict_core', 'compute_query_fingerprints', 'batch_tanimoto_similarity')
        if case.kind == 'reverse' else
        ('initialize', '_load_candidate', '_load_or_create_index', '_create_index',
         'search_similar_molecules_sync_with_receipt', 'search_similar_molecules_sync',
         'search_similar_molecules', 'get_embedding', 'get_embedding_sync',
         '_embedding_async', '_embedding_sync', '_search_embedding')
    )
    for name in names:
        monkeypatch.setattr(case.source, name, forbidden)
    if case.kind == 'reverse':
        monkeypatch.setattr(case.tool, '_get_predictor', forbidden)
    return calls


def validate(case, **overrides):
    arguments = dict(data=case.result['data'], evidence=case.result['evidence'],
                     input_data=case.query)
    arguments.update(overrides)
    return case.tool.validate_current_observation(**arguments)


@pytest.mark.parametrize('kind', ['reverse', 'rag'])
@pytest.mark.parametrize('empty', [False, True])
def test_actual_observation_revalidated_without_scientific_work(sources, monkeypatch, kind, empty):
    case = sources(kind, empty=empty)
    before = deepcopy(case.result)
    calls = forbid_work(case, monkeypatch)
    actual = validate(case)
    assert type(actual) is dict and actual == case.projection
    assert actual is not case.projection
    assert validate(case, expected_source=actual) == case.projection
    actual['generation_id'] = '0' * 32
    assert validate(case) == case.projection
    assert case.result == before
    assert calls == []
    assert len(case.requests) == (1 if kind == 'rag' else 0)


def unavailable(case, **overrides):
    with pytest.raises(ValueError, match='^current_source_unavailable$') as error:
        validate(case, **overrides)
    assert type(error.value) is ValueError
    assert error.value.__cause__ is None and error.value.__suppress_context__


def provider_methods(case):
    return (('capture_prediction_source', 'validate_prediction_source') if case.kind == 'reverse'
            else ('capture_retrieval_eligibility', 'validate_retrieval_source'))


def forbid_providers(case, monkeypatch):
    calls = []

    def forbidden(*args, **kwargs):
        calls.append(True)
        pytest.fail('malformed boundary reached provider')

    for name in provider_methods(case):
        monkeypatch.setattr(case.source, name, forbidden)
    return calls


@pytest.mark.parametrize('kind', ['reverse', 'rag'])
@pytest.mark.parametrize('bad', ['different', 'leading_space', 'trailing_space', 'batch',
                                 'prompt', 'blank', 'none', 'bool'])
def test_exact_resolved_input_only(sources, monkeypatch, kind, bad):
    case = sources(kind)
    values = dict(different='CCC', leading_space=' ' + case.query,
                  trailing_space=case.query + ' ', batch=[case.query, 'CCC'],
                  prompt='Please search ' + case.query, blank=' ', none=None, bool=True)
    before = deepcopy(case.result)
    forbid_work(case, monkeypatch)
    unavailable(case, input_data=values[bad])
    assert case.result == before


@pytest.mark.parametrize('kind', ['reverse', 'rag'])
@pytest.mark.parametrize('mutation', ['missing', 'duplicate', 'null', 'malformed', 'data', 'raw'])
def test_original_proof_required_without_backfill(sources, monkeypatch, kind, mutation):
    case = sources(kind)
    key = 'prediction_receipt' if kind == 'reverse' else 'retrieval_receipt'
    entry = case.result['evidence'][0]
    if mutation == 'missing':
        del entry[key]
    elif mutation == 'duplicate':
        case.result['evidence'].append(deepcopy(entry))
    elif mutation == 'null':
        entry[key] = None
    elif mutation == 'malformed':
        entry[key]['result_sha256'] = 'private-invalid-proof'
    elif mutation == 'data':
        case.result['data'][0]['extension'] = 'tampered normalized record'
    elif kind == 'reverse':
        entry['records'][0]['target_name'] = 'tampered raw record'
    else:
        entry[key]['source_sha256'] = 'f' * 64
    before = deepcopy(case.result)
    calls = forbid_providers(case, monkeypatch)
    forbid_work(case, monkeypatch)
    unavailable(case)
    assert case.result == before and calls == []


class NonNativeDict(dict):
    def __deepcopy__(self, memo):
        pytest.fail('copied non-native value before bounding')

    def __contains__(self, key):
        pytest.fail('scanned non-native receipt before bounding')


class NonNativeStr(str):
    pass


class NonNativeInt(int):
    pass


def malformed(kind):
    if kind == 'cycle':
        value = []
        value.append(value)
        return value
    if kind == 'depth':
        value = []
        for _ in range(40):
            value = [value]
        return value
    return {'subclass': NonNativeDict(a=1), 'tuple': (1,), 'object': object(),
            'nan': float('nan'), 'inf': float('inf'), 'keys': {1: 'bad'},
            'oversize': 'x' * 65537, 'unicode': '中' * 24000,
            'surrogate': '\ud800', 'nodes': [None] * 17000,
            'integer': 1 << 4097}[kind]


@pytest.mark.parametrize('kind', ['reverse', 'rag'])
@pytest.mark.parametrize('field', ['data', 'evidence', 'input_data', 'expected_source'])
@pytest.mark.parametrize('bad', ['subclass', 'tuple', 'object', 'cycle', 'depth', 'oversize',
                                'nan', 'inf', 'keys', 'unicode', 'surrogate', 'nodes', 'integer'])
def test_native_bounds_before_copy_scan_or_provider(sources, monkeypatch, kind, field, bad):
    case = sources(kind)
    value = malformed(bad)
    # Place hostile values after a valid proof: no early receipt selection may
    # bypass validation of the rest of the caller's observation.
    if field == 'evidence':
        value = case.result['evidence'] + [value]
    calls = forbid_providers(case, monkeypatch)
    forbid_work(case, monkeypatch)
    unavailable(case, **{field: value})
    assert calls == []


@pytest.mark.parametrize('kind', ['reverse', 'rag'])
def test_combined_observation_bound_precedes_receipt_validation(sources, monkeypatch, kind):
    case = sources(kind)
    case.result['data'][0]['padding'] = 'd' * 34000
    case.result['evidence'].append({'padding': 'e' * 34000})
    calls = forbid_providers(case, monkeypatch)
    module = ('src.agent.tools.reverse_target_tool' if kind == 'reverse'
              else 'src.agent.tools.rag_search_tool')
    name = ('validate_prediction_observation' if kind == 'reverse'
            else 'validate_retrieval_envelope')
    def forbidden(*args, **kwargs):
        pytest.fail('copied/scanned oversized observation')
    monkeypatch.setattr(module + '.' + name, forbidden)
    unavailable(case)
    assert calls == []


@pytest.mark.parametrize('kind', ['reverse', 'rag'])
@pytest.mark.parametrize('mutation', ['extra', 'missing', 'kind', 'kind_subclass',
                                     'generation_upper', 'generation_short', 'generation_int',
                                     'digest_upper', 'digest_short', 'digest_int', 'dict_subclass'])
def test_closed_expected_projection_before_provider(sources, monkeypatch, kind, mutation):
    case = sources(kind)
    expected = deepcopy(case.projection)
    if mutation == 'extra':
        expected['unexpected'] = 0
    elif mutation == 'missing':
        del expected['generation_id']
    elif mutation == 'kind':
        expected['kind'] = 'rag' if kind == 'reverse' else 'reverse'
    elif mutation == 'kind_subclass':
        expected['kind'] = NonNativeStr(kind)
    elif mutation.startswith('generation_'):
        expected['generation_id'] = {'upper': 'A' * 32, 'short': 'a' * 31,
                                     'int': 1}[mutation.removeprefix('generation_')]
    elif mutation.startswith('digest_'):
        expected['configuration_sha256'] = {'upper': 'A' * 64, 'short': 'a' * 63,
                                            'int': 1}[mutation.removeprefix('digest_')]
    else:
        expected = NonNativeDict(expected)
    calls = forbid_providers(case, monkeypatch)
    unavailable(case, expected_source=expected)
    assert calls == []


@pytest.mark.parametrize('epoch', [True, False, 0.0, -1, '0', None, NonNativeInt(0)])
def test_rag_expected_epoch_is_exact_nonnegative_int(sources, monkeypatch, epoch):
    case = sources('rag')
    expected = dict(case.projection, epoch=epoch)
    calls = forbid_providers(case, monkeypatch)
    unavailable(case, expected_source=expected)
    assert calls == []


@pytest.mark.parametrize('kind', ['reverse', 'rag'])
def test_well_formed_but_different_expected_identity_rejects(sources, monkeypatch, kind):
    case = sources(kind)
    forbid_work(case, monkeypatch)
    for field in case.projection.keys() - {'kind'}:
        expected = deepcopy(case.projection)
        expected[field] = expected[field] + 1 if field == 'epoch' else '0' * len(expected[field])
        unavailable(case, expected_source=expected)
    assert validate(case) == case.projection


@pytest.mark.parametrize('length', [16384, 16385])
def test_rag_exact_query_utf8_limit(sources, monkeypatch, length):
    case = sources('rag')
    # Real execute creates an original receipt for the exact boundary query.
    case.query = '中' * (length // 3) + 'x' * (length % 3)
    case.result = case.tool.execute(case.query)
    assert case.result['success']
    before = len(case.requests)
    forbid_work(case, monkeypatch)
    if length == 16384:
        assert validate(case) == case.projection
    else:
        calls = forbid_providers(case, monkeypatch)
        unavailable(case)
        assert not calls
    assert len(case.requests) == before


def test_rag_rejects_original_non_three_k(sources, monkeypatch):
    case = sources('rag')
    case.result = case.tool.execute(case.query, k=2)
    assert case.result['success']
    forbid_work(case, monkeypatch)
    calls = forbid_providers(case, monkeypatch)
    unavailable(case)
    assert not calls


def test_rag_validated_partial_still_rejected(sources, monkeypatch):
    from tests.agent.test_rag_receipt_consumption import synthetic_partial
    from src.rag.receipt import validate_retrieval_envelope
    case = sources('rag')
    partial = synthetic_partial(dict(records=case.result['data'],
                                     receipt=case.result['evidence'][0]['retrieval_receipt']))
    assert validate_retrieval_envelope(partial, query=case.query, k=3) == partial
    case.result['data'] = partial['records']
    case.result['evidence'][0]['retrieval_receipt'] = partial['receipt']
    calls = forbid_providers(case, monkeypatch)
    unavailable(case)
    assert not calls


@pytest.mark.parametrize('kind', ['reverse', 'rag'])
@pytest.mark.parametrize('state', ['missing', 'legacy', 'closed', 'uninitialized'])
def test_ineligible_attachment_never_loads(sources, monkeypatch, kind, state):
    case = sources(kind)
    if state in ('missing', 'legacy'):
        replacement = None if state == 'missing' else SimpleNamespace(is_initialized=True)
        setattr(case.tool, '_predictor' if kind == 'reverse' else 'rag_system', replacement)
    elif kind == 'reverse':
        case.source.close_strict()
    elif state == 'closed':
        case.source.close()
    else:
        case.source.is_initialized = False
    calls = forbid_work(case, monkeypatch)
    unavailable(case)
    assert not calls


@pytest.mark.parametrize('state', ['closed', 'loading'])
def test_reverse_tool_state_and_borrowed_ownership(sources, monkeypatch, state):
    case = sources('reverse')
    if state == 'closed':
        case.tool.close()
    else:
        case.tool._loading = True
    snapshot = case.source.capture_prediction_source()
    forbid_work(case, monkeypatch)
    unavailable(case)
    assert case.source.capture_prediction_source() == snapshot


@pytest.mark.parametrize('kind', ['reverse', 'rag'])
def test_reload_rejects_old_receipt_without_revoking_healthy_generation(sources, monkeypatch, kind):
    case = sources(kind)
    if kind == 'reverse':
        case.source.initialize_strict()
    else:
        asyncio.run(case.source.initialize())
    capture = getattr(case.source, provider_methods(case)[0])
    healthy = capture()
    assert healthy.generation_id != case.projection['generation_id']
    forbid_work(case, monkeypatch)
    unavailable(case)
    unavailable(case, expected_source=case.projection)
    assert capture() == healthy
    assert len(case.requests) == (1 if kind == 'rag' else 0)


@pytest.mark.parametrize('field', ['embedding_model', 'embedding_endpoint', 'vector_store_path', 'csv_bytes'])
def test_rag_current_full_source_and_configuration(sources, monkeypatch, field):
    case = sources('rag')
    if field == 'csv_bytes':
        path = case.source.csv_path
        original = path.read_bytes()
        stat = path.stat()
        path.write_bytes(original.replace(b'retained', b'modified'))
        # Same length and mtime defeat a stat-only freshness shortcut.
        import os
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    else:
        case.source.config['rag'][field] += '-changed'
    calls = forbid_work(case, monkeypatch)
    unavailable(case)
    assert not calls and len(case.requests) == 1


def test_reverse_configuration_drift(sources, monkeypatch):
    case = sources('reverse')
    monkeypatch.setenv('REVERSE_TARGET_MORGAN_WEIGHT', '0.2')
    calls = forbid_work(case, monkeypatch)
    unavailable(case)
    assert not calls


@pytest.mark.parametrize('kind', ['reverse', 'rag'])
@pytest.mark.parametrize('phase', [0, 1])
def test_attachment_replacement_during_provider_call(sources, monkeypatch, kind, phase):
    case, replacement = sources(kind), sources(kind)
    name = provider_methods(case)[phase]
    original = getattr(case.source, name)
    before = deepcopy(case.result)
    def replace(*args, **kwargs):
        value = original(*args, **kwargs)
        setattr(case.tool, '_predictor' if kind == 'reverse' else 'rag_system', replacement.source)
        return value
    monkeypatch.setattr(case.source, name, replace)
    forbid_work(case, monkeypatch)
    forbid_work(replacement, monkeypatch)
    unavailable(case)
    assert case.result == before
    # Stale consumer rejection owns neither source; both remain usable.
    assert validate(replacement) == replacement.projection
    setattr(case.tool, '_predictor' if kind == 'reverse' else 'rag_system', case.source)
    monkeypatch.setattr(case.source, name, original)
    assert validate(case) == case.projection


@pytest.mark.parametrize('phase', [0, 1])
def test_reverse_provider_callback_may_take_tool_lock(sources, monkeypatch, phase):
    case = sources('reverse')
    name = provider_methods(case)[phase]
    original = getattr(case.source, name)
    entered, release, acquired = threading.Event(), threading.Event(), threading.Event()
    def callback(*args, **kwargs):
        entered.set()
        assert release.wait(10), 'callback release missing'
        # Timed lock admission bounds a broken implementation without sleeps.
        assert case.tool._state_lock.acquire(timeout=10), 'provider called under tool lock'
        try:
            acquired.set()
        finally:
            case.tool._state_lock.release()
        return original(*args, **kwargs)
    monkeypatch.setattr(case.source, name, callback)
    forbid_work(case, monkeypatch)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(validate, case)
        try:
            assert entered.wait(10)
        finally:
            release.set()
        assert future.result(timeout=15) == case.projection
    assert acquired.is_set()


@pytest.mark.parametrize('kind', ['reverse', 'rag'])
@pytest.mark.parametrize('phase', [0, 1])
def test_close_during_provider_callback_is_rechecked(sources, monkeypatch, kind, phase):
    case = sources(kind)
    name = provider_methods(case)[phase]
    original = getattr(case.source, name)
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()
    def callback(*args, **kwargs):
        result = original(*args, **kwargs)
        entered.set()
        assert release.wait(10), 'callback release missing'
        return result
    def close():
        (case.tool.close if kind == 'reverse' else case.source.close)()
        closed.set()
    monkeypatch.setattr(case.source, name, callback)
    forbid_work(case, monkeypatch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(validate, case)
        closing = None
        try:
            assert entered.wait(10)
            closing = pool.submit(close)
            assert closed.wait(10), 'close blocked by hook tool lock'
        finally:
            release.set()
        if closing is not None:
            closing.result(timeout=10)
        with pytest.raises(ValueError, match='^current_source_unavailable$'):
            future.result(timeout=10)
    if kind == 'reverse':
        assert case.source.capture_prediction_source().generation_id == case.projection['generation_id']


@pytest.mark.parametrize('kind', ['reverse', 'rag'])
@pytest.mark.parametrize('phase', [0, 1])
@pytest.mark.parametrize('error_type', [RuntimeError, ValueError, TypeError, asyncio.CancelledError])
def test_provider_failure_sanitization_and_cancellation(sources, monkeypatch, kind, phase, error_type):
    case = sources(kind)
    before = deepcopy(case.result)
    error = error_type('private source details')
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(case.source, provider_methods(case)[phase], fail)
    forbid_work(case, monkeypatch)
    if error_type is asyncio.CancelledError:
        with pytest.raises(asyncio.CancelledError) as caught:
            validate(case)
        assert caught.value is error
    else:
        unavailable(case)
    assert case.result == before


@pytest.mark.parametrize('kind', ['reverse', 'rag'])
def test_generic_evidence_extensions_do_not_require_result_authority(sources, monkeypatch, kind):
    case = sources(kind)
    case.result['evidence'].append({'diagnostic': {'nested': [True, None, 1, 1.5]}})
    # Status, seals and owner are deliberately outside this component's API.
    case.result.update(success=False, status='failed', owner='not-a-real-owner')
    before = deepcopy(case.result)
    forbid_work(case, monkeypatch)
    assert validate(case, expected_source=case.projection) == case.projection
    assert case.result == before
