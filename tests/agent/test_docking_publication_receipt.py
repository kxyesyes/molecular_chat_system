"""Private D4 receipt/latch tests. All science is SYNTHETIC, not real Vina."""
import asyncio
import inspect
import sqlite3
import threading
from copy import copy
from contextlib import asynccontextmanager
from dataclasses import replace
import json

import pytest

from tests.task_runtime.test_chat_docking_execution_context import execution_case, approve, target_case
from tests.agent.test_docking_publication_candidate import publication_api, settle
from tests.agent.test_web_docking_sources import invalidate_source


def publisher():
    entry = getattr(publication_api(), 'publish_docking_result', None)
    assert callable(entry), 'D4 private publication callable is absent'
    return entry


async def publish(case, **kwargs):
    return await publisher()(case.coordinator, root_id=case.root.root_id,
        owner_session_id=kwargs.pop('owner', case.root.owner), **kwargs)


def test_private_publication_callable_accepts_only_owned_live_identity():
    assert set(inspect.signature(publisher()).parameters) == {
        'coordinator', 'root_id', 'owner_session_id'}


def test_one_confirmed_private_publication_and_duplicate(tmp_path, target_case, monkeypatch):
    publisher()
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            before = case.rig.tasks.get_docking_consent(case.root.preparation_id)
            first, duplicate = await asyncio.gather(publish(case), publish(case))
            assert first is duplicate
            assert first.status == 'SUCCEEDED'
            assert first.projection.energy_kcal_mol == -7.2
            assert first.projection.pose_count == 1
            assert await publish(case) is first
            assert case.raw == [True]
            with sqlite3.connect(case.rig.tasks.db_path) as db:
                assert db.execute('SELECT count(*) FROM docking_publications').fetchone() == (1,)
            assert case.rig.tasks.get_docking_consent(case.root.preparation_id) == before
            assert case.rig.gate._readers == 0
    asyncio.run(run())


async def checkpoint(predicate):
    for _ in range(400):
        if predicate():
            return
        await asyncio.sleep(.005)
    assert predicate(), 'retained publication checkpoint not reached'


def test_premature_publication_without_c2_preserves_ready_owner(tmp_path, target_case, monkeypatch):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            before = case.root.close_state
            with pytest.raises(publication_api().DockingPublicationUnavailable):
                await publish(case)
            assert case.root.publication is None and not case.root.closed
            assert case.root.close_state == before
            assert case.rig.tasks.get_docking_consent(case.root.preparation_id)['state'] == 'READY'
            await settle(case)
            assert (await publish(case)).status == 'SUCCEEDED'
    asyncio.run(run())


@pytest.mark.parametrize('held_owner', ['raw', 'claim_reader'])
def test_premature_publication_preserves_actual_c2_close(
        tmp_path, target_case, monkeypatch, held_owner):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            entered, release = threading.Event(), threading.Event()
            def hold():
                entered.set()
                assert release.wait(20)
            if held_owner == 'raw':
                case.raw_hook = hold
            else:
                original = case.rig.tasks._claim_docking_consent
                def claim(*args, **kwargs):
                    hold()
                    return original(*args, **kwargs)
                monkeypatch.setattr(case.rig.tasks, '_claim_docking_consent', claim)
            pending = asyncio.create_task(approve(case, chat_input_context=case.context))
            closing = None
            try:
                assert await asyncio.to_thread(entered.wait, 8)
                operation = case.context._execution
                assert not operation.settled
                before = case.root.close_state
                with pytest.raises(publication_api().DockingPublicationUnavailable):
                    await publish(case)
                assert case.root.publication is None and not case.root.closed
                assert case.root.close_state == before
                if held_owner == 'claim_reader':
                    assert case.rig.gate._readers == 1 and not operation.admission.done()
                closing = asyncio.create_task(case.coordinator.close_root(
                    root_id=case.root.root_id, owner_session_id=case.root.owner))
                await checkpoint(operation.signal.is_set)
                assert not closing.done() and not operation.settled
                assert case.coordinator._roots[case.root.root_id] is case.root
                assert case.root.input_context is case.context
            finally:
                release.set()
                await asyncio.gather(pending, return_exceptions=True)
                if closing is not None:
                    await closing
                case.unresolved = bool(case.runtime._consent_preparations)
            assert operation.settled and case.rig.gate._readers == 0
            assert case.root.root_id not in case.coordinator._roots
            assert case.raw == ([True] if held_owner == 'raw' else [])
            with sqlite3.connect(case.rig.tasks.db_path) as db:
                assert db.execute('SELECT count(*) FROM docking_publications').fetchone() == (0,)
                assert db.execute('SELECT count(*) FROM docking_publication_suppressions').fetchone() == (0,)
    asyncio.run(run())


@pytest.mark.parametrize('ending', ['close', 'caller_cancel'])
def test_cancel_before_publication_worker_starts_finishes_cleanup(
        tmp_path, target_case, monkeypatch, ending):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            pending = asyncio.create_task(publish(case))

            async def interrupt_before_worker():
                state = case.root.publication
                assert state is not None and state.candidate is None
                assert state.task.get_coro().cr_await is None
                if ending == 'close':
                    await case.coordinator.close_root(
                        root_id=case.root.root_id, owner_session_id=case.root.owner)
                else:
                    pending.cancel()

            closer = asyncio.create_task(interrupt_before_worker())
            results = await asyncio.gather(pending, closer, return_exceptions=True)
            assert results[1] is None
            state = case.root.publication
            await asyncio.gather(state.task, return_exceptions=True)
            assert state.outcome.status == 'CANCELLED' and state.outcome.projection is None
            assert case.rig.gate._readers == 0 and case.context._execution.settled
            assert state.candidate is None and state.receipt is None
            with sqlite3.connect(case.rig.tasks.db_path) as db:
                assert db.execute('SELECT count(*) FROM docking_publications').fetchone() == (0,)
                assert db.execute('SELECT count(*) FROM docking_publication_suppressions').fetchone() == (0,)
            for _ in range(2):
                await case.coordinator.close_root(
                    root_id=case.root.root_id, owner_session_id=case.root.owner)
                status = await case.coordinator.status(
                    root_id=case.root.root_id, owner_session_id=case.root.owner)
                assert not status.get('cleanup_pending', False)
                assert status['phase'] == 'CANCELLED'
            assert case.root.close_state == 'confirmed'
            assert await publish(case) is state.outcome
    asyncio.run(run())


@pytest.mark.parametrize('barrier', ['before', 'after'])
@pytest.mark.parametrize('ending', ['cancel', 'close', 'wall', 'mono', 'generation'])
def test_commit_barriers_never_promote_late_success(
        tmp_path, target_case, monkeypatch, barrier, ending):
    publisher()
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            before = case.rig.tasks.get_docking_consent(case.root.preparation_id)
            entered, release = threading.Event(), threading.Event()
            actual = case.rig.tasks._commit_docking_publication
            calls = []
            def commit(conn):
                calls.append(True)
                if barrier == 'after':
                    actual(conn)
                entered.set()
                assert release.wait(20)
                if barrier == 'before':
                    actual(conn)
            monkeypatch.setattr(case.rig.tasks, '_commit_docking_publication', commit)
            pending = asyncio.create_task(publish(case))
            closer = None
            try:
                assert await asyncio.to_thread(entered.wait, 8)
                physical = case.root.publication.task
                assert physical in case.rig.gate._background
                assert case.rig.gate._readers == 1
                with sqlite3.connect(case.rig.tasks.db_path) as db:
                    assert db.execute('SELECT count(*) FROM docking_publications').fetchone() == (
                        1 if barrier == 'after' else 0,)
                if ending == 'cancel':
                    pending.cancel()
                    await checkpoint(lambda: case.root.publication.outcome is not None)
                    for _ in range(3):
                        physical.cancel()
                        pending.cancel()
                        await asyncio.sleep(.01)
                elif ending == 'close':
                    closer = asyncio.create_task(case.coordinator.close_root(
                        root_id=case.root.root_id, owner_session_id=case.root.owner))
                    await checkpoint(lambda: case.root.publication.outcome is not None)
                elif ending == 'wall':
                    case.rig.wall = case.context._execution.row['operation_deadline_ms']
                elif ending == 'mono':
                    case.rig.mono = case.context._execution.row['operation_monotonic_expires']
                else:
                    case.rig.context['policy'] = replace(case.rig.policy, runtime_generation='changed')
                assert not physical.done()
                assert case.root.input_context is case.context
                assert case.coordinator._roots[case.root.root_id] is case.root
                assert case.rig.gate._readers == 1
            finally:
                release.set()
                await asyncio.gather(*(t for t in (pending, closer) if t is not None), return_exceptions=True)
                await asyncio.wait_for(asyncio.shield(case.root.publication.task), 10)
            outcome = case.root.publication.outcome
            assert outcome.status != 'SUCCEEDED' and outcome.projection is None
            assert calls == [True] and case.raw == [True]
            with sqlite3.connect(case.rig.tasks.db_path) as db:
                assert db.execute('SELECT count(*) FROM docking_publications').fetchone() == (1,)
                assert db.execute('SELECT count(*) FROM docking_publication_suppressions').fetchone() == (1,)
            assert case.rig.tasks.get_docking_consent(case.root.preparation_id) == before
            assert case.rig.gate._readers == 0
            assert await publish(case) is outcome
    asyncio.run(run())


def test_commit_then_raise_is_unknown_without_readback_retry(tmp_path, target_case, monkeypatch):
    publisher()
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            actual = case.rig.tasks._commit_docking_publication
            calls = []
            def commit(conn):
                calls.append(True)
                actual(conn)
                raise OSError('synthetic lost acknowledgement')
            monkeypatch.setattr(case.rig.tasks, '_commit_docking_publication', commit)
            first = await publish(case)
            assert first.status == 'UNKNOWN' and first.projection is None
            assert await publish(case) is first
            assert calls == [True]
            with sqlite3.connect(case.rig.tasks.db_path) as db:
                assert db.execute('SELECT count(*) FROM docking_publications').fetchone() == (1,)
                assert db.execute('SELECT count(*) FROM docking_publication_suppressions').fetchone() == (1,)
            assert case.raw == [True]
    asyncio.run(run())


@pytest.mark.parametrize('mutation', ['source', 'manifest', 'pose', 'generation'])
def test_postcommit_fresh_validation_is_required(tmp_path, target_case, monkeypatch, mutation):
    publisher()
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            actual = case.rig.tasks._commit_docking_publication
            def commit(conn):
                actual(conn)
                if mutation == 'source':
                    invalidate_source(case.rig, 'source')
                elif mutation == 'generation':
                    case.rig.context['capability_generation'] = 'changed'
                else:
                    store = case.runtime._docking_private_output.completions
                    path = store.manifest_path(case.root.task_id)
                    if mutation == 'pose':
                        path = path.parent / json.loads(path.read_text(encoding='utf-8'))['pose']['path']
                    path.write_bytes(b'tampered')
            monkeypatch.setattr(case.rig.tasks, '_commit_docking_publication', commit)
            result = await publish(case)
            assert result.status != 'SUCCEEDED' and result.projection is None
            assert await publish(case) is result
            assert case.raw == [True]
    asyncio.run(run())


@pytest.mark.parametrize('kind', ['foreign', 'missing', 'candidate', 'dict', 'root', 'context'])
def test_no_supplied_or_reconstructed_authority_before_io(tmp_path, target_case, monkeypatch, kind):
    publisher()
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            from tests.agent.test_docking_publication_candidate import capture
            candidate = await capture(case)
            with monkeypatch.context() as patch:
                if kind == 'root':
                    duplicate = copy(case.root)
                    patch.setitem(case.coordinator._roots, case.root.root_id, duplicate)
                    patch.setattr(case.context, '_root', duplicate)
                elif kind == 'context':
                    duplicate = copy(case.context)
                    patch.setattr(case.root, 'input_context', duplicate)
                    patch.setattr(case.context._execution, 'input_context', duplicate)
                elif kind == 'missing':
                    patch.delitem(case.coordinator._roots, case.root.root_id)
                def forbidden(*args, **kwargs):
                    pytest.fail('invalid publication authority reached SQL/file work')
                patch.setattr(case.coordinator, '_sql', forbidden)
                if kind in {'candidate', 'dict'}:
                    with pytest.raises(TypeError):
                        await publish(case, candidate=candidate if kind == 'candidate' else {})
                else:
                    with pytest.raises(publication_api().DockingPublicationUnavailable):
                        await publish(case, owner='foreign' if kind == 'foreign' else case.root.owner)
    asyncio.run(run())


def test_confirmed_success_survives_cancel_status_but_generation_removes_access(
        tmp_path, target_case, monkeypatch):
    publisher()
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            result = await publish(case)
            status = await case.coordinator.status(root_id=case.root.root_id, owner_session_id=case.root.owner)
            closed = await case.coordinator.close_root(root_id=case.root.root_id, owner_session_id=case.root.owner)
            assert status['phase'] == closed['phase'] == 'SUCCEEDED'
            assert await publish(case) is result
            from src.web.routes.docking_consent_routes import _receipt
            public = _receipt(case.root.task_id, case.root.preparation_id, status['phase'])
            assert public['phase'] == 'UNKNOWN'
            assert not {'projection', 'energy_kcal_mol', 'artifact_grant', 'url'} & public.keys()
            case.rig.context['policy'] = replace(case.rig.policy, runtime_generation='changed')
            with pytest.raises(publication_api().DockingPublicationUnavailable):
                await publish(case)
            assert case.root.publication.outcome is result
    asyncio.run(run())


def test_new_coordinator_never_recovers_committed_publication(tmp_path, target_case, monkeypatch):
    publisher()
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            await publish(case)
            other = copy(case.coordinator)
            other._roots = {}
            def forbidden(*args, **kwargs):
                pytest.fail('restart attempted publication recovery')
            monkeypatch.setattr(other, '_sql', forbidden)
            with pytest.raises(publication_api().DockingPublicationUnavailable):
                await publisher()(other, root_id=case.root.root_id, owner_session_id=case.root.owner)
    asyncio.run(run())


@pytest.mark.parametrize('column,value', [('approval_nonce_hash', 'changed-without-version'),
    ('manifest_locator', 'other.json'), ('operation_deadline_ms', 9999999999999)])
def test_full_consent_snapshot_cas_not_just_version(tmp_path, target_case, monkeypatch, column, value):
    publisher()
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            actual = case.rig.tasks._record_docking_publication
            def record(**kwargs):
                with sqlite3.connect(case.rig.tasks.db_path) as db:
                    db.execute(f'UPDATE docking_consents SET {column}=?', (value,))
                return actual(**kwargs)
            monkeypatch.setattr(case.rig.tasks, '_record_docking_publication', record)
            result = await publish(case)
            assert result.status == 'UNKNOWN'
            with sqlite3.connect(case.rig.tasks.db_path) as db:
                assert db.execute('SELECT count(*) FROM docking_publications').fetchone() == (0,)
    asyncio.run(run())


def test_store_single_insert_no_overwrite_and_additive_idempotency(tmp_path, target_case, monkeypatch):
    publisher()
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            actual = case.rig.tasks._record_docking_publication
            arguments = []
            def record(**kwargs):
                arguments.append(kwargs)
                return actual(**kwargs)
            monkeypatch.setattr(case.rig.tasks, '_record_docking_publication', record)
            await publish(case)
            with pytest.raises(ValueError):
                actual(**arguments[0])
            changed = dict(arguments[0]['receipt'], completion_digest='0' * 64)
            with pytest.raises(ValueError):
                actual(**dict(arguments[0], receipt=changed))
            from src.task_runtime.database import init_db
            with sqlite3.connect(case.rig.tasks.db_path) as db:
                before = db.execute('SELECT * FROM docking_publications').fetchall()
            init_db(case.rig.tasks.db_path)
            init_db(case.rig.tasks.db_path)
            with sqlite3.connect(case.rig.tasks.db_path) as db:
                assert db.execute('SELECT * FROM docking_publications').fetchall() == before
                assert not db.execute('PRAGMA foreign_key_list(docking_publications)').fetchall()
            assert case.raw == [True]
    asyncio.run(run())


def test_claimed_completion_reuse_publishes_without_second_raw(tmp_path, target_case, monkeypatch):
    publisher()
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            result = await asyncio.to_thread(case.runtime._docking_consent_execution.run_verified,
                case.root.task_id, case.stager.resolve_manifest_locator(case.root.task_id, 'input_manifest.json'))
            assert result['success'] is True
            await settle(case)
            assert case.context._execution.raw_expires is None
            assert (await publish(case)).status == 'SUCCEEDED'
            assert case.raw == [True]
    asyncio.run(run())


def test_copied_publication_latch_cannot_supply_authority(tmp_path, target_case, monkeypatch):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            await publish(case)
            with monkeypatch.context() as patch:
                patch.setattr(case.root, 'publication', copy(case.root.publication))
                def forbidden(*args, **kwargs):
                    pytest.fail('copied publication latch reached physical validation')
                patch.setattr(case.coordinator, '_sql', forbidden)
                with pytest.raises(publication_api().DockingPublicationUnavailable):
                    await publish(case)
    asyncio.run(run())


def test_reconciliation_stays_on_original_store_when_binding_changes(tmp_path, target_case, monkeypatch):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            from src.task_runtime.store import TaskStore
            replacement = TaskStore(tmp_path / 'replacement.sqlite')
            actual = case.rig.tasks._commit_docking_publication
            with monkeypatch.context() as patch:
                def commit(conn):
                    actual(conn)
                    patch.setattr(case.coordinator, '_tasks', replacement)
                patch.setattr(case.rig.tasks, '_commit_docking_publication', commit)
                result = await publish(case)
                assert result.status == 'UNKNOWN'
            for store, expected in ((case.rig.tasks, 1), (replacement, 0)):
                with sqlite3.connect(store.db_path) as db:
                    assert db.execute('SELECT count(*) FROM docking_publication_suppressions').fetchone() == (expected,)
    asyncio.run(run())


@pytest.mark.parametrize('field', ['model_generation', 'capability_generation'])
def test_store_binds_generation_to_exact_consent_draft(tmp_path, target_case, monkeypatch, field):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            actual = case.rig.tasks._record_docking_publication
            def record(**kwargs):
                return actual(**dict(kwargs, receipt=dict(kwargs['receipt'], **{field: 'changed'})))
            monkeypatch.setattr(case.rig.tasks, '_record_docking_publication', record)
            assert (await publish(case)).status == 'UNKNOWN'
            with sqlite3.connect(case.rig.tasks.db_path) as db:
                assert db.execute('SELECT count(*) FROM docking_publications').fetchone() == (0,)
    asyncio.run(run())


def test_failed_initial_runtime_check_is_permanent_not_retryable(tmp_path, target_case, monkeypatch):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            with monkeypatch.context() as patch:
                patch.setattr(case.runtime, '_docking_consent_policy', replace(case.rig.policy, runtime_generation='new'))
                first = await publish(case)
                assert first.status == 'UNKNOWN'
            assert await publish(case) is first
            with sqlite3.connect(case.rig.tasks.db_path) as db:
                assert db.execute('SELECT count(*) FROM docking_publications').fetchone() == (0,)
    asyncio.run(run())


def test_original_deadline_seals_promptly_without_waiting_for_commit(tmp_path, target_case, monkeypatch):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            # Original remaining budget is < the existing five-second feedback.
            case.rig.mono = case.context._execution.row['operation_monotonic_expires'] - 2.0
            entered, release = threading.Event(), threading.Event()
            actual = case.rig.tasks._commit_docking_publication
            def commit(conn):
                entered.set()
                assert release.wait(15)
                actual(conn)
            monkeypatch.setattr(case.rig.tasks, '_commit_docking_publication', commit)
            pending = asyncio.create_task(publish(case))
            try:
                assert await asyncio.to_thread(entered.wait, 8)
                case.rig.mono = case.context._execution.row['operation_monotonic_expires']
                result = await asyncio.wait_for(asyncio.shield(pending), 4)
                assert result.status == 'TIMED_OUT' and result.projection is None
                assert not case.root.publication.task.done()
                assert case.rig.gate._readers == 1
                assert case.root.input_context is case.context
            finally:
                release.set()
                await asyncio.gather(pending, return_exceptions=True)
                await case.root.publication.task
            assert await publish(case) is result
    asyncio.run(run())


def test_repeated_cancel_retains_actual_initial_reader(tmp_path, target_case, monkeypatch):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            entered, release = threading.Event(), threading.Event()
            completions = case.runtime._docking_private_output.completions
            actual = completions.read_verified_artifact
            def read(*args, **kwargs):
                entered.set()
                assert release.wait(15)
                return actual(*args, **kwargs)
            monkeypatch.setattr(completions, 'read_verified_artifact', read)
            pending = asyncio.create_task(publish(case))
            try:
                assert await asyncio.to_thread(entered.wait, 8)
                pending.cancel()
                await checkpoint(lambda: case.root.publication.outcome is not None)
                physical = case.root.publication.task
                for _ in range(4):
                    physical.cancel()
                    await asyncio.sleep(.01)
                assert not physical.done() and physical in case.rig.gate._background
                assert case.rig.gate._readers == 1
                assert case.root.input_context is case.context
                assert case.root.publication.outcome.status == 'CANCELLED'
            finally:
                release.set()
                await asyncio.gather(pending, return_exceptions=True)
                await case.root.publication.task
            assert case.rig.gate._readers == 0
            assert case.raw == [True]
    asyncio.run(run())


def test_caller_cancel_after_confirmed_latch_returns_existing_outcome(tmp_path, target_case, monkeypatch):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            entered, release = asyncio.Event(), asyncio.Event()
            original = case.rig.gate.request
            @asynccontextmanager
            async def held_reader():
                async with original():
                    yield
                    state = case.root.publication
                    if state is not None and state.outcome is not None and state.outcome.status == 'SUCCEEDED':
                        entered.set()
                        await release.wait()
            monkeypatch.setattr(case.rig.gate, 'request', held_reader)
            pending = asyncio.create_task(publish(case))
            try:
                await asyncio.wait_for(entered.wait(), 8)
                expected = case.root.publication.outcome
                pending.cancel()
                assert await pending is expected
                assert not case.root.closed
                assert case.root.publication.task in case.rig.gate._background
            finally:
                release.set()
                await asyncio.gather(pending, return_exceptions=True)
                await case.root.publication.task
    asyncio.run(run())


@pytest.mark.parametrize('elapsed', [301, 419, 420])
def test_publication_uses_whole_original_operation_clock(tmp_path, target_case, monkeypatch, elapsed):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            case.rig.wall += 890000
            case.rig.mono += 890
            await settle(case)
            case.rig.wall += elapsed * 1000
            case.rig.mono += elapsed
            result = await publish(case)
            assert result.status == ('TIMED_OUT' if elapsed == 420 else 'SUCCEEDED')
            if elapsed == 420:
                case.rig.wall -= 1000
                case.rig.mono -= 1
                assert await publish(case) is result
            else:
                assert result.projection.energy_kcal_mol == -7.2
            assert case.raw == [True]
    asyncio.run(run())


@pytest.mark.parametrize('field,value', [('consent_version', True),
    ('operation_monotonic_expires', float('nan')), ('root_id', '../root'),
    ('task_id', 'foreign'), ('runtime_generation', 'changed'),
    ('execution_token', 'changed'), ('binding_digest', '0' * 64),
    ('input_hash', '0' * 64), ('config_hash', '0' * 64), ('unknown', 'value')])
def test_fixed_native_storage_facts_reject_before_insert(tmp_path, target_case, monkeypatch, field, value):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            actual = case.rig.tasks._record_docking_publication
            def record(**kwargs):
                return actual(**dict(kwargs, receipt=dict(kwargs['receipt'], **{field: value})))
            monkeypatch.setattr(case.rig.tasks, '_record_docking_publication', record)
            assert (await publish(case)).status == 'UNKNOWN'
            with sqlite3.connect(case.rig.tasks.db_path) as db:
                assert db.execute('SELECT count(*) FROM docking_publications').fetchone() == (0,)
    asyncio.run(run())


def test_lost_suppression_ack_stays_unresolved_without_retry(tmp_path, target_case, monkeypatch):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            actual_commit = case.rig.tasks._commit_docking_publication
            def commit(conn):
                actual_commit(conn)
                raise OSError('synthetic lost candidate acknowledgement')
            actual_suppress = case.rig.tasks._suppress_docking_publication
            calls = []
            def suppress(**kwargs):
                calls.append(True)
                actual_suppress(**kwargs)
                raise OSError('synthetic lost suppression acknowledgement')
            monkeypatch.setattr(case.rig.tasks, '_commit_docking_publication', commit)
            monkeypatch.setattr(case.rig.tasks, '_suppress_docking_publication', suppress)
            result = await publish(case)
            assert result.status == 'UNKNOWN'
            assert case.root.publication.reconciliation == 'unconfirmed'
            view = await case.coordinator.status(root_id=case.root.root_id, owner_session_id=case.root.owner)
            assert view['cleanup_pending'] is True
            assert await publish(case) is result
            closed = await case.coordinator.close_root(
                root_id=case.root.root_id, owner_session_id=case.root.owner)
            assert closed['cleanup_pending'] is True
            assert case.root.close_state == 'unconfirmed'
            assert calls == [True]
    asyncio.run(run())


def test_confirmed_duplicate_observations_coalesce_under_actual_root_owner(tmp_path, target_case, monkeypatch):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            expected = await publish(case)
            entered, release = threading.Event(), threading.Event()
            store = case.runtime._docking_private_output.completions
            actual = store.read_verified_artifact
            calls = []
            def read(*args, **kwargs):
                calls.append(True)
                entered.set()
                assert release.wait(15)
                return actual(*args, **kwargs)
            monkeypatch.setattr(store, 'read_verified_artifact', read)
            first = asyncio.create_task(publish(case))
            second = None
            try:
                assert await asyncio.to_thread(entered.wait, 8)
                second = asyncio.create_task(publish(case))
                await asyncio.sleep(.05)
                assert calls == [True]
                assert not case.root.task.done()
                assert case.root.task in case.rig.gate._background
            finally:
                release.set()
                results = await asyncio.gather(*(t for t in (first, second) if t is not None), return_exceptions=True)
            assert all(result is expected for result in results)
            assert calls == [True]
            assert case.raw == [True]
    asyncio.run(run())


def test_invalid_pending_duplicate_seals_original_operation(tmp_path, target_case, monkeypatch):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            entered, release = threading.Event(), threading.Event()
            actual = case.rig.tasks._commit_docking_publication
            def commit(conn):
                entered.set()
                assert release.wait(15)
                actual(conn)
            monkeypatch.setattr(case.rig.tasks, '_commit_docking_publication', commit)
            first = asyncio.create_task(publish(case))
            try:
                assert await asyncio.to_thread(entered.wait, 8)
                with monkeypatch.context() as patch:
                    patch.setattr(case.runtime, '_docking_consent_policy', replace(case.rig.policy, runtime_generation='changed'))
                    denied = await publish(case)
                    assert denied.status == 'UNKNOWN'
                assert not case.root.publication.task.done()
            finally:
                release.set()
                await asyncio.gather(first, return_exceptions=True)
                await case.root.publication.task
            assert await first is denied
            assert await publish(case) is denied
    asyncio.run(run())


def test_observed_generation_invalidation_does_not_restore_confirmed_access(tmp_path, target_case, monkeypatch):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            original = await publish(case)
            with monkeypatch.context() as patch:
                patch.setitem(case.rig.context, 'policy', replace(case.rig.policy, runtime_generation='changed'))
                with pytest.raises(publication_api().DockingPublicationUnavailable):
                    await publish(case)
            with pytest.raises(publication_api().DockingPublicationUnavailable):
                await publish(case)
            assert case.root.publication.outcome is original
    asyncio.run(run())
