"""D4 private capture contracts. Synthetic raw execution, NOT real Vina."""
import asyncio
from copy import copy
from dataclasses import FrozenInstanceError, asdict
import hashlib
import importlib
import json
import sqlite3
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.task_runtime.test_chat_docking_execution_context import execution_case, approve, target_case
from tests.agent.test_web_docking_sources import invalidate_source


def publication_api():
    name = 'src.web.docking_publication'
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name != name:
            raise
        pytest.fail('D4 private candidate capture entry is absent', pytrace=False)


async def settle(case):
    await approve(case, chat_input_context=case.context)
    await asyncio.wait_for(asyncio.shield(case.context._execution.settlement), 10)
    assert case.context._execution.settled
    assert case.context._execution.status == 'SUCCEEDED'


async def capture(case, **kwargs):
    return await publication_api().capture_docking_publication_candidate(
        case.coordinator, root_id=case.root.root_id,
        owner_session_id=kwargs.get('owner', case.root.owner))


def test_capture_verified_private_candidate_without_publication(tmp_path, target_case, monkeypatch):
    publication_api()
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            before = case.rig.tasks.get_docking_consent(case.root.preparation_id)
            candidate = await capture(case)
            projection = candidate.projection
            assert projection.energy_kcal_mol == -7.2
            assert projection.pose_count == 1
            assert projection.score_kind == 'computed_score_not_experimental'
            assert projection.tool_name == 'molecular_docking'
            assert projection.execution_backend == 'local'
            assert projection.tool_version == 'vina-test-1'
            completion = case.runtime._docking_private_output.completions
            manifest = completion.load_verified(case.root.task_id,
                input_hash=projection.input_hash, config_hash=projection.config_hash)
            digest = hashlib.sha256(json.dumps(manifest.to_dict(), sort_keys=True,
                separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('ascii')).hexdigest()
            assert candidate.completion_digest == digest
            pose = completion.read_verified_artifact(case.root.task_id,
                input_hash=projection.input_hash, config_hash=projection.config_hash,
                completion_digest=digest, artifact_id='pose')
            assert projection.pose_sha256 == hashlib.sha256(pose['content']).hexdigest()
            assert projection.pose_size == len(pose['content'])
            assert projection.pose_artifact_id == 'pose'
            assert (await capture(case)).projection == projection
            assert case.raw == [True]
            assert case.rig.tasks.get_docking_consent(case.root.preparation_id) == before
            assert case.root.phase != 'SUCCEEDED'
            assert case.rig.gate._readers == 0
            assert not isinstance(candidate, dict)
            assert repr(candidate) == 'DockingPublicationCandidate(<private; not authority>)'
            with pytest.raises(FrozenInstanceError):
                candidate.completion_digest = 'forged'
            with pytest.raises(FrozenInstanceError):
                projection.pose_count = 2
            assert set(asdict(projection)) == {
                'tool_name', 'execution_backend', 'tool_version', 'energy_kcal_mol',
                'pose_count', 'score_kind', 'pose_artifact_id', 'pose_size', 'pose_sha256',
                'input_hash', 'config_hash', 'tool_policy_digest', 'source_digest',
                'original_receptor_digest', 'ligand_identity_digest', 'projection_digest'}
    asyncio.run(run())


@pytest.mark.parametrize('replacement', ['root', 'input_context', 'closed_original_root'])
def test_capture_rejects_reconstructed_owner(tmp_path, target_case, monkeypatch, replacement):
    api = publication_api()
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            baseline = await capture(case)
            assert baseline.projection.energy_kcal_mol == -7.2
            original_root, original_context = case.root, case.context
            # Restore reciprocal pointers AND closed state before fixture cleanup,
            # including when the expected denial fails on the pre-fix code.
            with monkeypatch.context() as patch:
                if replacement in {'root', 'closed_original_root'}:
                    duplicate = copy(original_root)
                    assert duplicate is not original_root
                    patch.setitem(case.coordinator._roots, original_root.root_id, duplicate)
                    patch.setattr(original_context, '_root', duplicate)
                    if replacement == 'closed_original_root':
                        patch.setattr(original_root, 'closed', True)
                else:
                    duplicate = copy(original_context)
                    assert duplicate is not original_context
                    patch.setattr(original_root, 'input_context', duplicate)
                    patch.setattr(original_context._execution, 'input_context', duplicate)
                with pytest.raises(api.DockingPublicationUnavailable):
                    candidate = await capture(case)
                    assert candidate.projection == baseline.projection
            assert case.raw == [True]
    asyncio.run(run())


@pytest.mark.parametrize('mutation', ['foreign', 'no_execution', 'unsettled', 'failed',
    'cancelled', 'closed', 'source', 'root', 'model', 'policy', 'private_binding',
    'stager', 'completion_store', 'pending', 'uncertain', 'cleanup', 'context',
    'accepted_input', 'descriptor', 'parameters', 'runtime_closed', 'generation',
    'execution_replaced', 'runtime_replaced', 'origin_model_replaced', 'origin_generation_replaced',
    'root_cancel_requested', 'input_stager_root', 'preparation_pending'])
def test_capture_denies_changed_live_authority_before_artifact_reads(
        tmp_path, target_case, monkeypatch, mutation):
    api = publication_api()
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            operation = case.context._execution
            with monkeypatch.context() as patch:
                if mutation in {'source', 'root', 'model'}:
                    invalidate_source(case.rig, mutation)
                elif mutation == 'no_execution': patch.setattr(case.context, '_execution', None)
                elif mutation == 'unsettled': patch.setattr(operation, 'settled', False)
                elif mutation == 'failed': patch.setattr(operation, 'status', 'FAILED')
                elif mutation == 'cancelled':
                    patch.setattr(operation, 'signal', threading.Event())
                    operation.signal.set()
                elif mutation == 'closed': patch.setattr(case.root, 'closed', True)
                elif mutation == 'root_cancel_requested': patch.setattr(case.root, 'cancel_requested', True)
                elif mutation == 'policy':
                    patch.setattr(case.runtime, '_docking_consent_policy', replace(case.rig.policy, policy_generation='new'))
                elif mutation == 'private_binding':
                    patch.setattr(case.runtime, '_docking_private_output', replace(case.runtime._docking_private_output))
                elif mutation == 'stager': patch.setattr(case.runtime, 'stager', object())
                elif mutation == 'input_stager_root': patch.setattr(case.runtime.stager, '_root', tmp_path / 'replacement')
                elif mutation == 'preparation_pending': patch.setattr(case.context._operation, 'physical_pending', True)
                elif mutation == 'completion_store':
                    patch.setattr(case.runtime._docking_consent_execution, '_completions', object())
                elif mutation == 'pending': patch.setattr(operation, 'pending', True)
                elif mutation == 'uncertain': patch.setattr(operation, 'claim_uncertain', True)
                elif mutation == 'cleanup': patch.setattr(operation, 'physical_unresolved', 'command_ownership_unresolved')
                elif mutation == 'context': patch.setattr(case.root, 'input_context', {})
                elif mutation == 'accepted_input': patch.setitem(case.root.ligand, 'sdf_bytes', b'changed')
                elif mutation == 'descriptor': patch.setitem(case.root.ligand_descriptor, 'sha256', '0' * 64)
                elif mutation == 'parameters': patch.setitem(case.root.accepted_slots, 'num_modes', dict(state='fixed', value=99))
                elif mutation == 'runtime_closed': patch.setattr(case.runtime, '_closed', True)
                elif mutation == 'generation': patch.setitem(case.rig.context, 'capability_generation', 'replacement')
                elif mutation == 'execution_replaced': patch.setattr(case.context, '_execution', replace(operation))
                elif mutation == 'runtime_replaced': patch.setattr(case.context, '_runtime', copy(case.runtime))
                elif mutation in {'origin_model_replaced', 'origin_generation_replaced'}:
                    key = 'model' if mutation == 'origin_model_replaced' else 'model_generation'
                    value = object() if key == 'model' else 'changed-together'
                    patch.setitem(case.root.current, key, value)
                    if key == 'model': patch.setattr(case.rig, 'model', value)
                    else: patch.setitem(case.rig.context, key, value)
                def forbidden(*args, **kwargs):
                    pytest.fail('invalid authority reached physical artifact read')
                patch.setattr(case.runtime._docking_private_output.completions, 'load_verified', forbidden)
                with pytest.raises(api.DockingPublicationUnavailable):
                    await capture(case, owner='foreign' if mutation == 'foreign' else case.root.owner)
            assert case.raw == [True]
    asyncio.run(run())


@pytest.mark.parametrize('column,value', [
    ('execution_token', 'changed'), ('cleanup_state', 'pending'), ('cancel_requested', 1),
    ('execution_occupied', 1), ('view_status', 'FAILED'), ('state', 'READY'),
    ('version', 1000), ('policy_json', '{}'), ('runtime_generation', 'changed'),
    ('binding_json', '{}'), ('operation_deadline_ms', 9999999999999),
    ('operation_monotonic_expires', 999999999.0)])
def test_capture_requires_exact_owned_settled_receipt(tmp_path, target_case, monkeypatch, column, value):
    api = publication_api()
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            with sqlite3.connect(case.rig.tasks.db_path) as db:
                db.execute(f'UPDATE docking_consents SET {column}=?', (value,))
            with pytest.raises(api.DockingPublicationUnavailable):
                await capture(case)
    asyncio.run(run())


@pytest.mark.parametrize('mutation', ['malformed', 'pose', 'invalid_marker', 'input_hash',
    'config_hash', 'energy_bool', 'energy_nan', 'count_bool', 'count_zero', 'count_float',
    'staged_input', 'staged_config', 'task_failed', 'finite_energy_tamper', 'positive_count_tamper'])
def test_capture_reverifies_real_files_and_scientific_terminal(tmp_path, target_case, monkeypatch, mutation):
    api = publication_api()
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            store = case.runtime._docking_private_output.completions
            path = store.manifest_path(case.root.task_id)
            payload = json.loads(path.read_text(encoding='utf-8'))
            if mutation == 'malformed': path.write_text('{', encoding='utf-8')
            elif mutation == 'pose': (path.parent / payload['pose']['path']).write_bytes(b'tampered')
            elif mutation == 'invalid_marker': (path.parent / 'completion_invalid.marker').write_bytes(b'invalid')
            elif mutation in {'staged_input', 'staged_config'}:
                manifest_path = path.parent / 'input_manifest.json'
                manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
                if mutation == 'staged_input':
                    (path.parent / manifest['ligand']['path']).write_bytes(b'tampered')
                else:
                    manifest['config']['num_modes'] = 99
                    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
            elif mutation == 'task_failed':
                with sqlite3.connect(case.rig.tasks.db_path) as db:
                    db.execute("UPDATE tasks SET status='failed'")
            else:
                key, value = {
                    'input_hash': ('input_hash', '0' * 64), 'config_hash': ('config_hash', '0' * 64),
                    'energy_bool': ('best_energy', True), 'energy_nan': ('best_energy', float('nan')),
                    'count_bool': ('pose_count', True), 'count_zero': ('pose_count', 0),
                    'count_float': ('pose_count', 1.0), 'finite_energy_tamper': ('best_energy', -9.9),
                    'positive_count_tamper': ('pose_count', 2)}[mutation]
                payload[key] = value
                path.write_text(json.dumps(payload), encoding='utf-8')
            with pytest.raises(api.DockingPublicationUnavailable):
                await capture(case)
    asyncio.run(run())


@pytest.mark.parametrize('elapsed,allowed', [(301, True), (419, True), (420, False)])
def test_capture_uses_original_operation_not_raw_or_consent_clock(
        tmp_path, target_case, monkeypatch, elapsed, allowed):
    api = publication_api()
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            case.rig.wall += 890000
            case.rig.mono += 890
            await settle(case)
            case.rig.wall += elapsed * 1000
            case.rig.mono += elapsed
            if allowed:
                assert (await capture(case)).projection.energy_kcal_mol == -7.2
            else:
                with pytest.raises(api.DockingPublicationUnavailable):
                    await capture(case)
            assert case.raw == [True]
    asyncio.run(run())


@pytest.mark.parametrize('mutation', ['closed', 'model', 'input', 'private_binding', 'expired', 'marker'])
def test_capture_rechecks_after_actual_read(tmp_path, target_case, monkeypatch, mutation):
    api = publication_api()
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            store = case.runtime._docking_private_output.completions
            original = store.read_verified_artifact
            with monkeypatch.context() as patch:
                def read(*args, **kwargs):
                    result = original(*args, **kwargs)
                    if mutation == 'closed': patch.setattr(case.root, 'closed', True)
                    elif mutation == 'model': patch.setitem(case.rig.context, 'model_generation', 'changed')
                    elif mutation == 'input': patch.setitem(case.root.ligand, 'sdf_bytes', b'changed')
                    elif mutation == 'private_binding':
                        patch.setattr(case.runtime, '_docking_private_output', replace(case.runtime._docking_private_output))
                    elif mutation == 'expired': case.rig.mono += 421
                    else: (store.manifest_path(case.root.task_id).parent / 'completion_invalid.marker').write_bytes(b'invalid')
                    return result
                patch.setattr(store, 'read_verified_artifact', read)
                with pytest.raises(api.DockingPublicationUnavailable):
                    await capture(case)
    asyncio.run(run())


def test_cancelled_capture_retains_actual_read_and_gate_until_return(tmp_path, target_case, monkeypatch):
    publication_api()
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            entered, release = threading.Event(), threading.Event()
            store = case.runtime._docking_private_output.completions
            original = store.read_verified_artifact
            def read(*args, **kwargs):
                entered.set()
                assert release.wait(15)
                return original(*args, **kwargs)
            monkeypatch.setattr(store, 'read_verified_artifact', read)
            pending = asyncio.create_task(capture(case))
            try:
                assert await asyncio.to_thread(entered.wait, 8)
                pending.cancel()
                await asyncio.sleep(0.05)
                assert not pending.done()
                assert case.rig.gate._readers == 1
            finally:
                release.set()
                await asyncio.gather(pending, return_exceptions=True)
            with pytest.raises(asyncio.CancelledError):
                await pending
            assert case.rig.gate._readers == 0
            assert case.raw == [True]
    asyncio.run(run())


@pytest.mark.parametrize('mutation', ['task_owner', 'submission_digest', 'root_alias', 'task_manifest'])
def test_capture_never_borrows_another_owned_identity(tmp_path, target_case, monkeypatch, mutation):
    api = publication_api()
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            root_id = case.root.root_id
            if mutation == 'root_alias':
                root_id = 'alias'
                case.coordinator._roots[root_id] = SimpleNamespace(owner=case.root.owner, input_context=case.context)
            else:
                column = {'task_owner': 'owner_session_id', 'submission_digest': 'submission_digest',
                          'task_manifest': 'input_manifest_path'}[mutation]
                with sqlite3.connect(case.rig.tasks.db_path) as db:
                    db.execute(f'UPDATE tasks SET {column}=?', ('foreign',))
            try:
                with pytest.raises(api.DockingPublicationUnavailable):
                    await api.capture_docking_publication_candidate(case.coordinator,
                        root_id=root_id, owner_session_id=case.root.owner)
            finally:
                if mutation == 'root_alias': case.coordinator._roots.pop(root_id)
    asyncio.run(run())


def test_verified_completion_reuse_keeps_claimed_without_raw_reservation(tmp_path, target_case, monkeypatch):
    publication_api()
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            # Seed an actual verified synthetic completion, not a mocked loader.
            # It is not C publication authority until the real approval settles.
            result = await asyncio.to_thread(case.runtime._docking_consent_execution.run_verified,
                case.root.task_id, case.stager.resolve_manifest_locator(case.root.task_id, 'input_manifest.json'))
            assert result['success'] is True and case.raw == [True]
            await settle(case)
            row = case.rig.tasks.get_docking_consent(case.root.preparation_id)
            assert row['state'] == 'CLAIMED'
            assert case.context._execution.raw_expires is None
            assert (await capture(case)).projection.energy_kcal_mol == -7.2
            assert case.raw == [True]
    asyncio.run(run())


@pytest.mark.parametrize('value', [None, True, 1, [], {}, '', '../root', 'a' * 129])
def test_native_identifiers_reject_before_owner_or_physical_lookup(value, monkeypatch):
    api = publication_api()
    from src.web.docking_coordinator import DockingRequestCoordinator
    coordinator = object.__new__(DockingRequestCoordinator)
    def forbidden(*args, **kwargs):
        pytest.fail('invalid identifier reached owner lookup')
    monkeypatch.setattr(coordinator, '_owned', forbidden)
    async def run():
        for kwargs in (dict(root_id=value, owner_session_id='owner'),
                       dict(root_id='root', owner_session_id=value)):
            with pytest.raises(api.DockingPublicationUnavailable):
                await api.capture_docking_publication_candidate(coordinator, **kwargs)
    asyncio.run(run())


@pytest.mark.parametrize('owner', ['-owner', '_owner'])
def test_urlsafe_owner_reaches_actual_lookup_without_borrowing_authority(owner, monkeypatch):
    api = publication_api()
    from src.web.docking_coordinator import DockingRequestCoordinator
    coordinator = object.__new__(DockingRequestCoordinator)
    owned_root = SimpleNamespace(owner=owner, input_context=None)
    foreign_root = SimpleNamespace(owner='other-owner', input_context=None)
    coordinator._roots = {'root': owned_root, 'foreign-root': foreign_root}
    actual_owned = coordinator._owned
    observed = []
    def lookup(root_id, owner_session_id):
        result = actual_owned(root_id, owner_session_id)
        observed.append((root_id, owner_session_id, result))
        return result
    monkeypatch.setattr(coordinator, '_owned', lookup)
    async def run():
        # Valid owner syntax reaches the real ownership check, but neither a
        # foreign root nor an owned root without live C2 context is authority.
        for root_id in ('root', 'foreign-root'):
            with pytest.raises(api.DockingPublicationUnavailable):
                await api.capture_docking_publication_candidate(coordinator,
                    root_id=root_id, owner_session_id=owner)
        assert len(observed) == 2
        assert observed[0][:2] == ('root', owner) and observed[0][2] is owned_root
        assert observed[1] == ('foreign-root', owner, None)
        # The owner-only grammar must not relax the root identifier boundary.
        with pytest.raises(api.DockingPublicationUnavailable):
            await api.capture_docking_publication_candidate(coordinator,
                root_id=owner, owner_session_id=owner)
        assert len(observed) == 2
    asyncio.run(run())


@pytest.mark.parametrize('fake', [None, {}, SimpleNamespace(_roots={})])
def test_duck_coordinator_cannot_supply_capture_authority(fake):
    api = publication_api()
    async def run():
        with pytest.raises(api.DockingPublicationUnavailable):
            await api.capture_docking_publication_candidate(fake, root_id='root', owner_session_id='owner')
    asyncio.run(run())


def test_persisted_completion_and_consent_without_live_root_cannot_recover(tmp_path, target_case, monkeypatch):
    api = publication_api()
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            case.coordinator._roots.pop(case.root.root_id)
            try:
                with pytest.raises(api.DockingPublicationUnavailable):
                    await capture(case)
            finally:
                case.coordinator._roots[case.root.root_id] = case.root
    asyncio.run(run())
