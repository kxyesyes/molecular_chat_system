"""Explicit scientific-choice integration; SYNTHETIC providers, never S10."""
import asyncio
from copy import deepcopy
import hashlib
import json
import threading

import pytest

from src.docking import chat_preparation, source_preparation
from src.task_runtime.database import connection
from tests.agent.test_web_docking_consent import target_case
from tests.agent.test_web_docking_sources import source_rig, invalidate_source, assert_not_ready
from tests.test_rcsb_source import sample
from tests.test_docking_pocket_evidence import atom_line, residue


def _complete_source_child(pdb_id, accession, taxonomy, deadline):
    """Independently authored complete synthetic coordinates in BOTH formats."""
    from src.docking.source_preparation import _source_candidate
    from src.target_search import downloader
    pdb, cif, components = sample.__wrapped__()
    # Fill the synthetic fixture, not a real scientific structure. The original
    # sample deliberately lacks three heavy atoms and cannot be projected.
    lines = pdb.decode('ascii').splitlines()
    lines.insert(5, atom_line(5, 'O', 'O', (2, 4, 6), residue('ALA', 10), 'ATOM'))
    lines.insert(8, atom_line(6, 'C', 'C', (4, 6, 8), residue('ALA', 11), 'ATOM'))
    lines.insert(9, atom_line(10, 'O', 'O', (4, 6, 9), residue('ALA', 11), 'ATOM'))
    pdb = ('\n'.join(lines) + '\n').encode('ascii')
    cif = cif.replace(
        b'ATOM 3 N N . ALA',
        b'ATOM 8 O O . ALA A 1 1 ? 2.000 4.000 6.000 1.00 20.00 ? 10 ALA A O 1\nATOM 3 N N . ALA')
    cif = cif.replace(
        b'HETATM 5 C C1 . LIG',
        b'ATOM 9 C C . ALA A 1 2 ? 4.000 6.000 8.000 1.00 20.00 ? 11 ALA A C 1\n'
        b'ATOM 10 O O . ALA A 1 2 ? 4.000 6.000 9.000 1.00 20.00 ? 11 ALA A O 1\nHETATM 5 C C1 . LIG')
    downloader.fetch_rcsb_source_pair = lambda selected, **kw: dict(pdb_bytes=pdb, cif_bytes=cif, pdb_id=selected)
    downloader.fetch_rcsb_component_files = lambda ids, **kw: components
    return _source_candidate(pdb_id, accession, taxonomy, deadline)


def choices():
    return dict(protein_selection=dict(model=1, chains=[dict(chain='A', entity_id='1')]),
        reference_residue=residue('LIG', 20),
        hetero_choices=[dict(residue=residue('HOH', 40, 'Z'), action='remove')],
        conformer_choices=[], reviewed_limitations=[])


async def select(coordinator, view, **changes):
    method = getattr(coordinator, 'select_source', None)
    assert callable(method), 'same-root explicit scientific selection is absent'
    args = dict(root_id='root-1', owner_session_id='owner-session',
        source_sha256=view['source_preview']['source_sha256'], selection=choices())
    args.update(changes)
    return await method(**args)


def test_explicit_choices_materialize_once_and_fix_only_produced_refs(tmp_path, target_case, monkeypatch):
    rig, pointer = source_rig(tmp_path, target_case)
    monkeypatch.setattr(source_preparation, '_source_candidate', _complete_source_child)
    calls, owners = [], []
    actual = chat_preparation.run_preparation
    async def observed(operation, **kwargs):
        calls.append(operation)
        owners.append(kwargs['owner'])
        assert rig.gate._readers == 0
        return await actual(operation, **kwargs)
    monkeypatch.setattr(chat_preparation, 'run_preparation', observed)
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            assert callable(getattr(coordinator, 'select_source', None)), 'same-root scientific selection missing'
            initial = await rig.begin(coordinator, structure_context=pointer)
            assert initial['reason_code'] == 'scientific_selection_required', initial
            result = await select(coordinator, initial)
            assert_not_ready(result)
            assert result['reason_code'] == 'parameters_confirmation_required', result
            preview = result['prepared_preview']
            assert preview['input_kind'] == 'explicit-residue-conformer-v1'
            assert preview['parameters_suggestion'] == dict(center=[5.5, 6.5, 7.5], size=[10.0]*3,
                exhaustiveness=8, num_modes=9)
            assert preview['ligand_provenance']['input_kind'] == 'modeled_input_not_experimental_pose'
            assert preview['ligand_provenance']['embedding_attempts'] == 1
            assert preview['source_sha256'] == initial['source_preview']['source_sha256']
            assert preview['derived_sha256'] != preview['source_sha256']
            assert 'pdb_bytes' not in json.dumps(result)
            assert len(json.dumps(result).encode()) < 48*1024
            record = rig.tasks.get_chat_docking_draft(preparation_id=result['preparation_id'],
                                                       owner_session_id='owner-session')
            assert record['identity']['refinement_revision'] == 1
            assert record['version'] == 3
            for key in ('receptor_ref', 'ligand_ref'):
                assert record['draft']['slots'][key] == dict(state='fixed', value=preview[key])
            for key in ('center', 'size', 'exhaustiveness', 'num_modes'):
                assert record['draft']['slots'][key] == {'state': 'pending'}
            assert 'source_refs' not in record['identity']
            root = coordinator._roots['root-1']
            assert hashlib.sha256(root.projected['pdb_bytes']).hexdigest() == preview['derived_sha256']
            assert hashlib.sha256(root.ligand['sdf_bytes']).hexdigest() == preview['ligand_sha256']
            # All later observations reuse the immutable modeled input.
            original = deepcopy(preview)
            preview['parameters_suggestion']['center'][0] = 999
            assert (await select(coordinator, initial))['prepared_preview'] == original
            assert (await coordinator.status(root_id='root-1', owner_session_id='owner-session'))['prepared_preview'] == original
            assert (await rig.begin(coordinator, structure_context=pointer))['prepared_preview'] == original
            assert calls == ['projection_inspect', 'projection', 'ligand']
            assert all(owner.snapshot()['settled'] for owner in owners)
            with connection(rig.tasks.db_path) as conn:
                assert conn.execute('SELECT COUNT(*) AS n FROM tasks').fetchone()['n'] == 0
            await coordinator.close_root(root_id='root-1', owner_session_id='owner-session')
    asyncio.run(run())


@pytest.mark.parametrize('change', ['owner', 'hash', 'missing', 'water', 'reference', 'source', 'deadline'])
def test_invalid_or_stale_choice_never_yields_prepared_input(tmp_path, target_case, monkeypatch, change):
    rig, pointer = source_rig(tmp_path, target_case)
    monkeypatch.setattr(source_preparation, '_source_candidate', _complete_source_child)
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            assert callable(getattr(coordinator, 'select_source', None)), 'same-root scientific selection missing'
            initial = await rig.begin(coordinator, structure_context=pointer)
            replacement = choices()
            changes = {}
            if change == 'owner': changes['owner_session_id'] = 'foreign'
            if change == 'hash': changes['source_sha256'] = '0'*64
            if change == 'missing': replacement.pop('hetero_choices')
            if change == 'water': replacement['hetero_choices'][0]['action'] = 'retain'
            if change == 'reference': replacement['reference_residue'] = residue('HOH', 40, 'Z')
            if change in {'source', 'deadline'}: invalidate_source(rig, change)
            result = await select(coordinator, initial, selection=replacement, **changes)
            assert_not_ready(result)
            assert 'prepared_preview' not in result
            await coordinator.close_root(root_id='root-1', owner_session_id='owner-session')
    asyncio.run(run())


@pytest.mark.parametrize('operation', ['projection', 'ligand'])
@pytest.mark.parametrize('mutation', ['source', 'draft', 'model', 'deadline'])
def test_physical_result_after_invalidation_is_cleanup_only(tmp_path, target_case, monkeypatch, operation, mutation):
    rig, pointer = source_rig(tmp_path, target_case)
    monkeypatch.setattr(source_preparation, '_source_candidate', _complete_source_child)
    actual = chat_preparation.run_preparation
    async def changed(name, **kwargs):
        result = await actual(name, **kwargs)
        if name == operation:
            invalidate_source(rig, mutation)
        return result
    monkeypatch.setattr(chat_preparation, 'run_preparation', changed)
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            assert callable(getattr(coordinator, 'select_source', None)), 'same-root scientific selection missing'
            initial = await rig.begin(coordinator, structure_context=pointer)
            result = await select(coordinator, initial)
            assert_not_ready(result)
            assert result['phase'] == 'UNAVAILABLE'
            assert 'prepared_preview' not in result
            await coordinator.close_root(root_id='root-1', owner_session_id='owner-session')
    asyncio.run(run())


def test_selection_capture_is_native_and_detached_before_await(tmp_path, target_case, monkeypatch):
    rig, pointer = source_rig(tmp_path, target_case)
    monkeypatch.setattr(source_preparation, '_source_candidate', _complete_source_child)
    called = []
    class Hook(dict):
        def items(self):
            called.append(True)
            raise AssertionError('untrusted hook')
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            assert callable(getattr(coordinator, 'select_source', None)), 'same-root scientific selection missing'
            initial = await rig.begin(coordinator, structure_context=pointer)
            assert 'prepared_preview' not in await select(coordinator, initial, selection=Hook(choices()))
            assert called == []
            choice = choices()
            actual = coordinator._read_source_reference
            def mutate(**kwargs):
                choice['hetero_choices'].clear()
                return actual(**kwargs)
            monkeypatch.setattr(coordinator, '_read_source_reference', mutate)
            result = await select(coordinator, initial, selection=choice)
            assert result['reason_code'] == 'parameters_confirmation_required', result
            assert choice['hetero_choices'] == []
            await coordinator.close_root(root_id='root-1', owner_session_id='owner-session')
    asyncio.run(run())


def test_original_numeric_values_are_preserved_instead_of_suggested_defaults(tmp_path, target_case, monkeypatch):
    from tests.agent.test_web_docking_coordinator import proposal
    rig, pointer = source_rig(tmp_path, target_case)
    rig.args['query'] += ' exhaustiveness=8; num_modes=9.'
    rig.output = proposal(rig.args['query'], unresolved=True)
    # Independent text/value spans for a nondefault value.
    old = rig.args['query']
    rig.args['query'] = old.replace('exhaustiveness=8', 'exhaustiveness=16')
    out = rig.output
    out['source_spans'][0]['end'] += 1
    out['fields']['exhaustiveness']['value'] = 16
    for span in out['source_spans'][1:]:
        if old[span['start']:span['end']] == '8':
            span['end'] += 1
        else:
            span['start'] += 1
            span['end'] += 1
    monkeypatch.setattr(source_preparation, '_source_candidate', _complete_source_child)
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            assert callable(getattr(coordinator, 'select_source', None)), 'same-root scientific selection missing'
            initial = await rig.begin(coordinator, structure_context=pointer)
            result = await select(coordinator, initial)
            assert result['prepared_preview']['parameters_suggestion']['exhaustiveness'] == 16
            record = rig.tasks.get_chat_docking_draft(preparation_id=result['preparation_id'], owner_session_id='owner-session')
            assert record['draft']['slots']['exhaustiveness'] == dict(state='fixed', value=16)
            assert record['draft']['slots']['num_modes'] == dict(state='fixed', value=9)
            assert record['draft']['slots']['center'] == {'state': 'pending'}
            await coordinator.close_root(root_id='root-1', owner_session_id='owner-session')
    asyncio.run(run())


def test_lost_refinement_receipt_cannot_be_recovered_by_sql_readback(tmp_path, target_case, monkeypatch):
    rig, pointer = source_rig(tmp_path, target_case)
    monkeypatch.setattr(source_preparation, '_source_candidate', _complete_source_child)
    actual = rig.tasks.refine_docking_consent_draft
    calls = []
    def uncertain(**kwargs):
        actual(**kwargs)
        calls.append(1)
        raise RuntimeError('simulated lost commit receipt')
    monkeypatch.setattr(rig.tasks, 'refine_docking_consent_draft', uncertain)
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            assert callable(getattr(coordinator, 'select_source', None)), 'same-root scientific selection missing'
            initial = await rig.begin(coordinator, structure_context=pointer)
            result = await select(coordinator, initial)
            assert result['phase'] == 'UNAVAILABLE' and 'prepared_preview' not in result
            later = await select(coordinator, initial)
            assert 'prepared_preview' not in later
            assert calls == [1]
            assert 'prepared_preview' not in await coordinator.status(root_id='root-1', owner_session_id='owner-session')
            await coordinator.close_root(root_id='root-1', owner_session_id='owner-session')
    asyncio.run(run())


def test_duplicate_selection_coalesces_and_different_choice_cannot_reprepare(tmp_path, target_case, monkeypatch):
    rig, pointer = source_rig(tmp_path, target_case)
    monkeypatch.setattr(source_preparation, '_source_candidate', _complete_source_child)
    actual = chat_preparation.run_preparation
    entered, release = None, None
    calls = []
    async def held(name, **kwargs):
        calls.append(name)
        if name == 'projection':
            entered.set()
            await release.wait()
        return await actual(name, **kwargs)
    monkeypatch.setattr(chat_preparation, 'run_preparation', held)
    async def run():
        nonlocal entered, release
        entered, release = asyncio.Event(), asyncio.Event()
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            assert callable(getattr(coordinator, 'select_source', None)), 'same-root scientific selection missing'
            initial = await rig.begin(coordinator, structure_context=pointer)
            first = asyncio.create_task(select(coordinator, initial))
            await asyncio.wait_for(entered.wait(), 10)
            duplicate = await select(coordinator, initial)
            assert 'prepared_preview' not in duplicate
            release.set()
            assert (await first)['reason_code'] == 'parameters_confirmation_required'
            changed = choices()
            changed['hetero_choices'][0]['action'] = 'retain'
            later = await select(coordinator, initial, selection=changed)
            assert later['reason_code'] == 'new_root_required'
            assert 'prepared_preview' not in later
            assert calls.count('projection') == 1 and calls.count('ligand') == 1
            await coordinator.close_root(root_id='root-1', owner_session_id='owner-session')
    asyncio.run(run())


@pytest.mark.parametrize('mutation', ['source', 'deadline'])
def test_final_preview_encoding_cannot_publish_after_invalidation(tmp_path, target_case, monkeypatch, mutation):
    from src.web import docking_coordinator as module
    rig, pointer = source_rig(tmp_path, target_case)
    monkeypatch.setattr(source_preparation, '_source_candidate', _complete_source_child)
    actual = module._small_json
    def changed(value, *args):
        result = actual(value, *args)
        if type(value) is dict and value.get('input_kind') == 'explicit-residue-conformer-v1':
            invalidate_source(rig, mutation)
        return result
    monkeypatch.setattr(module, '_small_json', changed)
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            initial = await rig.begin(coordinator, structure_context=pointer)
            result = await select(coordinator, initial)
            assert result['phase'] == 'UNAVAILABLE'
            assert 'prepared_preview' not in result
            await coordinator.close_root(root_id='root-1', owner_session_id='owner-session')
    asyncio.run(run())


@pytest.mark.parametrize('location', ['receipt', 'same_version_read'])
def test_refinement_never_borrows_a_refunded_proposal_budget(tmp_path, target_case, monkeypatch, location):
    rig, pointer = source_rig(tmp_path, target_case)
    monkeypatch.setattr(source_preparation, '_source_candidate', _complete_source_child)
    actual = rig.tasks.refine_docking_consent_draft
    def changed(**kwargs):
        result = actual(**kwargs)
        if location == 'receipt':
            result['draft']['budget']['spent_ms'] = 0
        else:
            with connection(rig.tasks.db_path) as conn:
                row = conn.execute('SELECT draft_json FROM docking_consents').fetchone()
                draft = json.loads(row['draft_json'])
                draft['budget']['spent_ms'] = 0
                conn.execute('UPDATE docking_consents SET draft_json=?', (json.dumps(draft),))
        return result
    monkeypatch.setattr(rig.tasks, 'refine_docking_consent_draft', changed)
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            initial = await rig.begin(coordinator, structure_context=pointer)
            result = await select(coordinator, initial)
            assert result['phase'] == 'UNAVAILABLE'
            assert 'prepared_preview' not in result
            await coordinator.close_root(root_id='root-1', owner_session_id='owner-session')
    asyncio.run(run())


def test_changed_choice_propagates_repeated_cancel_after_actual_child_close(tmp_path, target_case, monkeypatch):
    rig, pointer = source_rig(tmp_path, target_case)
    monkeypatch.setattr(source_preparation, '_source_candidate', _complete_source_child)
    original = chat_preparation.IsolatedProcess
    entered, release, finished = (threading.Event() for _ in range(3))
    class HeldClose(original):
        def close(self):
            super().close()
            entered.set()
            try:
                assert release.wait(15)
            finally:
                finished.set()
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            initial = await rig.begin(coordinator, structure_context=pointer)
            # Only the actual projection process close is held, after source inspection.
            monkeypatch.setattr(chat_preparation, 'IsolatedProcess', HeldClose)
            first = asyncio.create_task(select(coordinator, initial))
            changed = None
            try:
                for _ in range(1200):
                    if entered.is_set(): break
                    await asyncio.sleep(.005)
                assert entered.is_set() and not finished.is_set()
                replacement = choices()
                replacement['hetero_choices'][0]['action'] = 'retain'
                changed = asyncio.create_task(select(coordinator, initial, selection=replacement))
                await asyncio.sleep(.01)
                changed.cancel()
                await asyncio.sleep(.01)
                changed.cancel()
                await asyncio.sleep(.01)
                assert not changed.done() and not first.done()
                assert 'root-1' in coordinator._roots and not finished.is_set()
            finally:
                release.set()
                await asyncio.gather(*(task for task in (first, changed) if task is not None),
                                     return_exceptions=True)
            assert finished.is_set()
            assert changed.cancelled(), 'changed selection swallowed caller cancellation'
            await coordinator.close_root(root_id='root-1', owner_session_id='owner-session')
            assert 'root-1' not in coordinator._roots
    asyncio.run(run())
