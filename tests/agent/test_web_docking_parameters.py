"""Explicit parameter confirmation on the same synthetic prepared draft."""
import asyncio
from copy import deepcopy
import threading

import pytest

from src.docking import source_preparation
from tests.agent.test_web_docking_consent import target_case
from tests.agent.test_web_docking_sources import source_rig, assert_not_ready, invalidate_source
from tests.agent.test_web_docking_selection import _complete_source_child, select


async def prepared(rig, pointer, coordinator):
    initial = await rig.begin(coordinator, structure_context=pointer)
    result = await select(coordinator, initial)
    assert result['reason_code'] == 'parameters_confirmation_required', result
    return result


def confirmation_args(view):
    preview = view['prepared_preview']
    return dict(root_id='root-1', owner_session_id='owner-session',
        expected_revision=preview['refinement_revision'], receptor_ref=preview['receptor_ref'],
        ligand_ref=preview['ligand_ref'], parameters=deepcopy(preview['parameters_suggestion']))


def method(coordinator):
    result = getattr(coordinator, 'confirm_parameters', None)
    assert callable(result), 'same-root explicit parameter confirmation is missing'
    return result


def test_confirmation_fills_pending_slots_once_without_c1_or_nonce(tmp_path, target_case, monkeypatch):
    rig, pointer = source_rig(tmp_path, target_case)
    monkeypatch.setattr(source_preparation, '_source_candidate', _complete_source_child)
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            confirm = method(coordinator)
            before = await prepared(rig, pointer, coordinator)
            initial = rig.tasks.get_chat_docking_draft(preparation_id=before['preparation_id'], owner_session_id='owner-session')
            args = confirmation_args(before)
            args['parameters']['exhaustiveness'] = 16  # Explicit choice, not a hidden default.
            result = await confirm(**args)
            assert_not_ready(result)
            assert result['reason_code'] == 'inputs_confirmed_staging_required', result
            preview = result['prepared_preview']
            assert preview['refinement_revision'] == 2
            assert preview['confirmed_parameters'] == args['parameters']
            current = rig.tasks.get_chat_docking_draft(preparation_id=result['preparation_id'], owner_session_id='owner-session')
            assert current['state'] == 'AWAITING_INPUT' and current['version'] == initial['version'] + 1
            assert current['identity']['source_refs'] == {key: args[key] for key in ('receptor_ref', 'ligand_ref')}
            assert current['draft']['budget'] == initial['draft']['budget']
            assert current['expires_at_ms'] == initial['expires_at_ms']
            assert current['monotonic_expires'] == initial['monotonic_expires']
            assert all(row['state'] == 'fixed' for row in current['draft']['slots'].values())
            assert current['approval_nonce_hash'] is None
            assert (await confirm(**args))['prepared_preview'] == preview
            later = rig.tasks.get_chat_docking_draft(preparation_id=result['preparation_id'], owner_session_id='owner-session')
            assert later == current
            assert len(rig.calls) == 1
            await coordinator.close_root(root_id='root-1', owner_session_id='owner-session')
    asyncio.run(run())


@pytest.mark.parametrize('change', ['owner', 'revision', 'bool_revision', 'receptor', 'ligand', 'center',
                                   'size', 'bool_exhaustiveness', 'extra', 'missing', 'source', 'deadline'])
def test_bad_confirmation_never_fixes_pending_numeric_slots(tmp_path, target_case, monkeypatch, change):
    rig, pointer = source_rig(tmp_path, target_case)
    monkeypatch.setattr(source_preparation, '_source_candidate', _complete_source_child)
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            confirm = method(coordinator)
            before = await prepared(rig, pointer, coordinator)
            args = confirmation_args(before)
            if change == 'owner': args['owner_session_id'] = 'foreign'
            if change == 'revision': args['expected_revision'] = 0
            if change == 'bool_revision': args['expected_revision'] = True
            if change == 'receptor': args['receptor_ref'] = 'sha256:' + 'a'*64
            if change == 'ligand': args['ligand_ref'] = 'sha256:' + 'b'*64
            if change == 'center': args['parameters']['center'][0] += 1
            if change == 'size': args['parameters']['size'][0] += 1
            if change == 'bool_exhaustiveness': args['parameters']['exhaustiveness'] = True
            if change == 'extra': args['parameters']['backend'] = 'remote'
            if change == 'missing': args['parameters'].pop('num_modes')
            if change in {'source', 'deadline'}: invalidate_source(rig, change)
            result = await confirm(**args)
            assert_not_ready(result)
            assert result['reason_code'] != 'inputs_confirmed_staging_required'
            row = rig.tasks.get_chat_docking_draft(preparation_id=before['preparation_id'], owner_session_id='owner-session')
            assert row['draft']['slots']['center'] == {'state': 'pending'}
            assert row['identity']['refinement_revision'] == 1
            await coordinator.close_root(root_id='root-1', owner_session_id='owner-session')
    asyncio.run(run())


def test_changed_fixed_confirmation_requires_new_request(tmp_path, target_case, monkeypatch):
    rig, pointer = source_rig(tmp_path, target_case)
    monkeypatch.setattr(source_preparation, '_source_candidate', _complete_source_child)
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            confirm = method(coordinator)
            before = await prepared(rig, pointer, coordinator)
            args = confirmation_args(before)
            first = await confirm(**args)
            assert first['reason_code'] == 'inputs_confirmed_staging_required'
            changed = deepcopy(args)
            changed['expected_revision'] = first['prepared_preview']['refinement_revision']
            changed['parameters']['num_modes'] = 10
            result = await confirm(**changed)
            assert result['reason_code'] == 'new_root_required'
            assert 'prepared_preview' not in result
            await coordinator.close_root(root_id='root-1', owner_session_id='owner-session')
    asyncio.run(run())


def test_stale_changed_confirmation_has_no_authority_to_close_current_root(tmp_path, target_case, monkeypatch):
    rig, pointer = source_rig(tmp_path, target_case)
    monkeypatch.setattr(source_preparation, '_source_candidate', _complete_source_child)
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            before = await prepared(rig, pointer, coordinator)
            args = confirmation_args(before)
            accepted = await method(coordinator)(**args)
            changed = deepcopy(args)  # Old revision is only valid for an identical retry.
            changed['parameters']['num_modes'] += 1
            denied = await method(coordinator)(**changed)
            assert denied['reason_code'] == 'parameters_unavailable'
            assert (await coordinator.status(root_id='root-1', owner_session_id='owner-session'))['prepared_preview'] == accepted['prepared_preview']
            await coordinator.close_root(root_id='root-1', owner_session_id='owner-session')
    asyncio.run(run())


def test_numerically_identical_signed_zero_retry_reuses_confirmation(tmp_path, target_case, monkeypatch):
    from tests.agent.test_web_docking_coordinator import proposal, VALUES
    rig, pointer = source_rig(tmp_path, target_case)
    rig.args['query'] = '用 CCO 与 1ABC 对接。center=[0,2,3]; size=[20,20,20]; exhaustiveness=8; num_modes=9.'
    monkeypatch.setitem(VALUES, 'center', [0, 2, 3])
    rig.output = proposal(rig.args['query'], unresolved=True)
    monkeypatch.setattr(source_preparation, '_source_candidate', _complete_source_child)
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            before = await prepared(rig, pointer, coordinator)
            args = confirmation_args(before)
            def forbidden(**kwargs):
                pytest.fail('all-fixed explicit inputs or duplicates must not perform a CAS')
            monkeypatch.setattr(rig.tasks, 'refine_docking_consent_draft', forbidden)
            accepted = await method(coordinator)(**args)
            assert accepted['reason_code'] == 'inputs_confirmed_staging_required'
            args['parameters']['center'][0] = -0.0
            duplicate = await method(coordinator)(**args)
            assert duplicate.get('prepared_preview') == accepted['prepared_preview']
            await coordinator.close_root(root_id='root-1', owner_session_id='owner-session')
    asyncio.run(run())


@pytest.mark.parametrize('change', [False, True])
def test_confirmation_never_rounds_distinct_integer_coordinates_together(tmp_path, target_case, monkeypatch, change):
    from tests.agent.test_web_docking_coordinator import proposal, VALUES
    rig, pointer = source_rig(tmp_path, target_case)
    first = 2**53 if change else 2**53 + 1
    rig.args['query'] = f'用 CCO 与 1ABC 对接。center=[{first},2,3]; size=[20,20,20]; exhaustiveness=8; num_modes=9.'
    monkeypatch.setitem(VALUES, 'center', [first, 2, 3])
    rig.output = proposal(rig.args['query'], unresolved=True)
    monkeypatch.setattr(source_preparation, '_source_candidate', _complete_source_child)
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            before = await prepared(rig, pointer, coordinator)
            args = confirmation_args(before)
            accepted = await method(coordinator)(**args)
            assert accepted['reason_code'] == 'inputs_confirmed_staging_required'
            assert accepted['prepared_preview']['confirmed_parameters']['center'][0] == first
            if change:
                args['expected_revision'] = accepted['prepared_preview']['refinement_revision']
                args['parameters']['center'][0] += 1
                assert (await method(coordinator)(**args))['reason_code'] == 'new_root_required'
            await coordinator.close_root(root_id='root-1', owner_session_id='owner-session')
    asyncio.run(run())


def test_parameter_input_is_captured_and_concurrent_duplicate_does_not_refine_twice(tmp_path, target_case, monkeypatch):
    rig, pointer = source_rig(tmp_path, target_case)
    monkeypatch.setattr(source_preparation, '_source_candidate', _complete_source_child)
    entered, release = threading.Event(), threading.Event()
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            confirm = method(coordinator)
            before = await prepared(rig, pointer, coordinator)
            args = confirmation_args(before)
            expected = deepcopy(args)
            actual = rig.tasks.refine_docking_consent_draft
            calls = []
            def held(**kwargs):
                calls.append(1)
                entered.set()
                assert release.wait(10)
                return actual(**kwargs)
            monkeypatch.setattr(rig.tasks, 'refine_docking_consent_draft', held)
            first = asyncio.create_task(confirm(**args))
            try:
                for _ in range(400):
                    if entered.is_set(): break
                    await asyncio.sleep(.005)
                assert entered.is_set()
                args['parameters']['center'][0] = 999
                duplicate = await confirm(**expected)
                assert duplicate['phase'] == 'BUSY'
                assert 'prepared_preview' not in duplicate
            finally:
                release.set()
                result = await first
            assert result['prepared_preview']['confirmed_parameters'] == expected['parameters']
            assert calls == [1]
            await coordinator.close_root(root_id='root-1', owner_session_id='owner-session')
    asyncio.run(run())


def test_original_fixed_parameter_cannot_be_changed_by_confirmation(tmp_path, target_case, monkeypatch):
    from tests.agent.test_web_docking_coordinator import proposal
    rig, pointer = source_rig(tmp_path, target_case)
    rig.args['query'] += ' exhaustiveness=8; num_modes=9.'
    rig.output = proposal(rig.args['query'], unresolved=True)
    monkeypatch.setattr(source_preparation, '_source_candidate', _complete_source_child)
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            confirm = method(coordinator)
            before = await prepared(rig, pointer, coordinator)
            args = confirmation_args(before)
            args['parameters']['exhaustiveness'] = 16
            result = await confirm(**args)
            assert result['reason_code'] == 'new_root_required'
            assert 'prepared_preview' not in result
            row = rig.tasks.get_chat_docking_draft(preparation_id=before['preparation_id'], owner_session_id='owner-session')
            assert row['draft']['slots']['exhaustiveness'] == {'state': 'fixed', 'value': 8}
            assert row['draft']['slots']['center'] == {'state': 'pending'}
            await coordinator.close_root(root_id='root-1', owner_session_id='owner-session')
    asyncio.run(run())


@pytest.mark.parametrize('point', ['final_encoding', 'lost_receipt'])
def test_confirmation_does_not_publish_late_or_unconfirmed_inputs(tmp_path, target_case, monkeypatch, point):
    from src.web import docking_coordinator as module
    rig, pointer = source_rig(tmp_path, target_case)
    monkeypatch.setattr(source_preparation, '_source_candidate', _complete_source_child)
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            confirm = method(coordinator)
            before = await prepared(rig, pointer, coordinator)
            args = confirmation_args(before)
            calls = []
            if point == 'final_encoding':
                actual = module._small_json
                def changed(value, *other):
                    result = actual(value, *other)
                    if type(value) is dict and 'confirmed_parameters' in value:
                        invalidate_source(rig, 'source')
                    return result
                monkeypatch.setattr(module, '_small_json', changed)
            else:
                actual = rig.tasks.refine_docking_consent_draft
                def changed(**kwargs):
                    actual(**kwargs)
                    calls.append(1)
                    raise OSError('synthetic lost receipt')
                monkeypatch.setattr(rig.tasks, 'refine_docking_consent_draft', changed)
            result = await confirm(**args)
            assert_not_ready(result)
            assert result['phase'] == 'UNAVAILABLE'
            assert 'prepared_preview' not in result
            assert 'prepared_preview' not in await confirm(**args)
            if point == 'lost_receipt': assert calls == [1]
            await coordinator.close_root(root_id='root-1', owner_session_id='owner-session')
    asyncio.run(run())
