"""Live chat authority at C2; every raw executor here is synthetic, never Vina."""
import asyncio
from contextlib import asynccontextmanager
import inspect
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from src.task_runtime.docking_consent import DockingConsentError
from src.task_runtime.docking_execution import DockingExecution
from src.task_runtime.runtime import TaskRuntime
from src.system.scientific_contracts import ToolResult, ToolProvenance
from tests.task_runtime.test_chat_docking_input_context import authenticated_rig, target_case
from tests.agent.test_web_docking_staging import prepare_inputs
from tests.agent.test_web_docking_sources import invalidate_source


@asynccontextmanager
async def execution_case(tmp_path, target_case, monkeypatch):
    async with authenticated_rig(tmp_path, target_case, monkeypatch) as case:
        case.raw = []
        case.raw_hook = lambda: None
        output = tmp_path / 'private-output'
        def synthetic_raw(*args, **kwargs):
            assert case.rig.gate._readers == 0, 'raw science must not retain a model reader'
            case.raw.append(True)
            case.raw_hook()
            pose = output / ('docking_' + kwargs['job_id']) / 'result.pdbqt'
            pose.parent.mkdir(parents=True, exist_ok=True)
            pose.write_text(
                'MODEL 1\nREMARK VINA RESULT: -7.200 0.000 0.000\nROOT\n'
                'ATOM      1  C   LIG A   1       0.000   0.000   0.000  0.00  0.00    +0.000 C\n'
                'ENDROOT\nTORSDOF 0\nENDMDL\n', encoding='utf-8')
            return ToolResult.success_result('molecular_docking',
                data={'total_poses': 1, 'pose_file': str(pose),
                      'best_pose': {'binding_energy': -7.2, 'pose_file': str(pose)}},
                quality={'real_execution': True},  # Synthetic validator fixture only.
                provenance=ToolProvenance(tool_name='molecular_docking', tool_version='vina-test-1'))
        execution = DockingExecution(case.runtime.config.staging_root,
            raw_executor=synthetic_raw, allowed_output_root=output)
        runtime = TaskRuntime(config=case.runtime.config, store=case.rig.tasks,
            stager=case.stager, docking_execution=execution,
            docking_consent_policy=case.rig.policy,
            consent_wall_time_ms=lambda: case.rig.wall, consent_monotonic=lambda: case.rig.mono)
        case.runtime = runtime
        case.unresolved = False
        try:
            view = await prepare_inputs(case)
            assert view['phase'] == 'READY'
            case.proof = view['consent_preview']
            yield case
        finally:
            if case.unresolved:
                with pytest.raises(DockingConsentError, match='consent_cleanup_unresolved'):
                    await runtime.close()
                await runtime.local_backend.close()
            else:
                await runtime.close()


async def approve(case, **kwargs):
    return await case.runtime.approve_docking_consent(
        preparation_id=case.root.preparation_id, owner_session_id=case.root.owner,
        approval_nonce=case.proof['approval_nonce'], binding_digest=case.proof['binding_digest'],
        confirm=True, **kwargs)


def test_chat_c2_cannot_execute_without_the_original_live_input_owner(tmp_path, target_case, monkeypatch):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            with pytest.raises(DockingConsentError, match='consent_proof_mismatch'):
                await approve(case)
            row = case.rig.tasks.get_docking_consent(case.root.preparation_id)
            assert row['state'] == 'READY' and not case.raw
    asyncio.run(run())


@pytest.mark.parametrize('mutation', ['source', 'root', 'model'])
def test_chat_c2_rechecks_live_source_before_claim(tmp_path, target_case, monkeypatch, mutation):
    assert 'chat_input_context' in inspect.signature(TaskRuntime.approve_docking_consent).parameters
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            invalidate_source(case.rig, mutation)
            with pytest.raises(DockingConsentError):
                await approve(case, chat_input_context=case.context)
            assert case.rig.tasks.get_docking_consent(case.root.preparation_id)['state'] == 'READY'
            assert not case.raw
    asyncio.run(run())


def test_current_chat_context_permits_one_synthetic_raw_call(tmp_path, target_case, monkeypatch):
    assert 'chat_input_context' in inspect.signature(TaskRuntime.approve_docking_consent).parameters
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await approve(case, chat_input_context=case.context)
            operation = case.context._execution
            if operation.settlement is not None:
                await asyncio.wait_for(asyncio.shield(operation.settlement), 10)
            assert case.raw == [True]
            assert operation.settled and operation.status == 'SUCCEEDED'
    asyncio.run(run())


@pytest.mark.parametrize('fake', [None, {}, SimpleNamespace(valid=True)])
def test_chat_context_duck_objects_cannot_authorize_execution(tmp_path, target_case, monkeypatch, fake):
    assert 'chat_input_context' in inspect.signature(TaskRuntime.approve_docking_consent).parameters
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            with pytest.raises(DockingConsentError, match='consent_proof_mismatch'):
                await approve(case, chat_input_context=fake)
            assert not case.raw
    asyncio.run(run())


@pytest.mark.parametrize('phase', ['check', 'reserve', 'dispatch', 'result'])
def test_source_invalidation_after_claim_still_blocks_raw(tmp_path, target_case, monkeypatch, phase):
    assert 'chat_input_context' in inspect.signature(TaskRuntime.approve_docking_consent).parameters
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            original = case.runtime._consent_execution_guard
            def guard(operation, inputs, current):
                if phase == current:
                    invalidate_source(case.rig, 'source')
                return original(operation, inputs, current)
            monkeypatch.setattr(case.runtime, '_consent_execution_guard', guard)
            await approve(case, chat_input_context=case.context)
            operation = case.context._execution
            await asyncio.wait_for(asyncio.shield(operation.settlement), 10)
            assert case.raw == ([True] if phase == 'result' else [])
            assert operation.status != 'SUCCEEDED'
            assert operation.settled
    asyncio.run(run())


def test_claim_waits_agent_first_then_rechecks_model_under_actual_task_writer(tmp_path, target_case, monkeypatch):
    assert 'chat_input_context' in inspect.signature(TaskRuntime.approve_docking_consent).parameters
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            entered = threading.Event()
            original = case.rig.tasks._claim_docking_consent
            def claim(*args, **kwargs):
                entered.set()
                return original(*args, **kwargs)
            monkeypatch.setattr(case.rig.tasks, '_claim_docking_consent', claim)
            blocker = sqlite3.connect(case.rig.tasks.db_path, timeout=0)
            blocker.execute('BEGIN IMMEDIATE')
            pending = asyncio.create_task(approve(case, chat_input_context=case.context))
            try:
                assert await asyncio.to_thread(entered.wait, 8)
                guard = sqlite3.connect(case.rig.state.db_path, timeout=0)
                try:
                    with pytest.raises(sqlite3.OperationalError, match='locked'):
                        guard.execute('BEGIN IMMEDIATE')
                finally:
                    guard.close()
                case.rig.context['model_generation'] = 'replaced-under-task-writer'
            finally:
                blocker.rollback()
                blocker.close()
                await asyncio.gather(pending, return_exceptions=True)
            with pytest.raises(DockingConsentError):
                await pending
            assert not case.raw
            assert case.rig.tasks.get_docking_consent(case.root.preparation_id)['state'] == 'READY'
    asyncio.run(run())


def test_valid_dispatch_can_finish_after_consumed_chat_consent_expires(tmp_path, target_case, monkeypatch):
    assert 'chat_input_context' in inspect.signature(TaskRuntime.approve_docking_consent).parameters
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            case.rig.wall += 890000
            case.rig.mono += 890
            def elapsed():
                case.rig.wall += 20000
                case.rig.mono += 20
            case.raw_hook = elapsed
            await approve(case, chat_input_context=case.context)
            operation = case.context._execution
            await asyncio.wait_for(asyncio.shield(operation.settlement), 10)
            assert case.raw == [True]
            assert operation.status == 'SUCCEEDED' and operation.settled
    asyncio.run(run())


@pytest.mark.parametrize('column,expression', [('version', 'version+1'), ('cleanup_state', "'settled'")])
def test_claimed_chat_receipt_cannot_change_before_dispatch(tmp_path, target_case, monkeypatch, column, expression):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            original = case.runtime._consent_execution_guard
            def guard(operation, inputs, phase):
                if phase == 'check':
                    with sqlite3.connect(case.rig.tasks.db_path) as db:
                        db.execute(f'UPDATE docking_consents SET {column}={expression}')
                return original(operation, inputs, phase)
            monkeypatch.setattr(case.runtime, '_consent_execution_guard', guard)
            await approve(case, chat_input_context=case.context)
            operation = case.context._execution
            await asyncio.wait_for(asyncio.shield(operation.settlement), 10)
            assert not case.raw
            assert operation.status == 'FAILED'
    asyncio.run(run())


def test_cancelled_claim_keeps_model_and_sql_owner_until_actual_writer_settles(tmp_path, target_case, monkeypatch):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            entered = threading.Event()
            original = case.rig.tasks._claim_docking_consent
            def claim(*args, **kwargs):
                entered.set()
                return original(*args, **kwargs)
            monkeypatch.setattr(case.rig.tasks, '_claim_docking_consent', claim)
            blocker = sqlite3.connect(case.rig.tasks.db_path, timeout=0)
            blocker.execute('BEGIN IMMEDIATE')
            pending = asyncio.create_task(approve(case, chat_input_context=case.context))
            try:
                assert await asyncio.to_thread(entered.wait, 8)
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pending
                operation = case.context._execution
                assert case.runtime._consent_executions[case.root.task_id] is operation
                assert not operation.admission.done()
                assert case.rig.gate._readers == 1 and not case.raw
            finally:
                blocker.rollback()
                blocker.close()
                await asyncio.gather(pending, return_exceptions=True)
            await asyncio.wait_for(asyncio.shield(operation.cancellation), 10)
            await asyncio.wait_for(asyncio.shield(operation.settlement), 10)
            assert operation.settled and not case.raw
            assert case.rig.gate._readers == 0
    asyncio.run(run())


def test_invalidation_after_actual_claim_ack_retains_unknown_claim_without_submit(tmp_path, target_case, monkeypatch):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            original = case.rig.tasks._claim_docking_consent
            def claim(*args, **kwargs):
                result = original(*args, **kwargs)
                case.rig.context['model_generation'] = 'invalid-after-actual-claim'
                return result
            monkeypatch.setattr(case.rig.tasks, '_claim_docking_consent', claim)
            case.unresolved = True
            with pytest.raises(DockingConsentError):
                await approve(case, chat_input_context=case.context)
            operation = case.context._execution
            row = case.rig.tasks._owned_docking_consent(preparation_id=case.root.preparation_id,
                owner_session_id=case.root.owner)
            assert operation.acquired and operation.submission is None
            assert case.runtime._consent_executions[case.root.task_id] is operation
            assert row['execution_occupied'] == 1 and row['view_status'] == 'UNKNOWN'
            assert operation.status == 'UNKNOWN' and not case.raw
    asyncio.run(run())


@pytest.mark.parametrize('phase,mutation', [('dispatch', 'rollback'), ('dispatch', 'policy'),
    ('dispatch', 'generation'), ('result', 'policy'), ('result', 'generation')])
def test_live_dispatch_and_result_require_full_frozen_reserved_receipt(tmp_path, target_case, monkeypatch, phase, mutation):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            original = case.runtime._consent_execution_guard
            def guard(operation, inputs, current):
                if current == phase:
                    with sqlite3.connect(case.rig.tasks.db_path) as db:
                        if mutation == 'rollback':
                            db.execute("UPDATE docking_consents SET state='CLAIMED',version=version-1,dispatch_state='not_reserved'")
                        elif mutation == 'policy':
                            db.execute("UPDATE docking_consents SET policy_json='{}'")
                        else:
                            db.execute("UPDATE docking_consents SET runtime_generation='replacement'")
                return original(operation, inputs, current)
            monkeypatch.setattr(case.runtime, '_consent_execution_guard', guard)
            await approve(case, chat_input_context=case.context)
            operation = case.context._execution
            await asyncio.wait_for(asyncio.shield(operation.settlement), 10)
            assert case.raw == ([True] if phase == 'result' else [])
            assert operation.status != 'SUCCEEDED'
    asyncio.run(run())


def test_final_result_guard_remains_owned_when_backend_owner_is_cancelled(tmp_path, target_case, monkeypatch):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            entered, release = threading.Event(), threading.Event()
            cls = type(case.context)
            original = cls._execution_step_guarded
            def guard(self, execution, inputs, phase):
                if inputs is None and phase == 'result':
                    entered.set()
                    assert release.wait(20), 'final guard barrier unreleased'
                return original(self, execution, inputs, phase)
            monkeypatch.setattr(cls, '_execution_step_guarded', guard)
            closing = None
            try:
                await approve(case, chat_input_context=case.context)
                assert await asyncio.to_thread(entered.wait, 8)
                operation = case.context._execution
                operation.backend_owner.cancel()
                closing = asyncio.create_task(case.runtime.close())
                await asyncio.wait({closing}, timeout=0.2)
                row = case.rig.tasks._owned_docking_consent(
                    preparation_id=case.root.preparation_id, owner_session_id=case.root.owner)
                assert not operation.settled and row['execution_occupied'] == 1
                assert case.runtime._consent_executions[case.root.task_id] is operation
                assert case.rig.gate._readers == 1 and not closing.done()
            finally:
                release.set()
                if closing is not None:
                    await closing
            assert case.rig.gate._readers == 0
            assert operation.settled and operation.status != 'SUCCEEDED'
    asyncio.run(run())


@pytest.mark.parametrize('phase', ['readback', 'dispatch', 'result'])
@pytest.mark.parametrize('column,increment', [
    ('operation_deadline_ms', 3600000), ('operation_monotonic_expires', 3600)])
def test_chat_execution_deadlines_remain_bound_to_claim_ack(
        tmp_path, target_case, monkeypatch, phase, column, increment):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            def mutate():
                with sqlite3.connect(case.rig.tasks.db_path) as db:
                    db.execute(f'UPDATE docking_consents SET {column}={column}+?', (increment,))
            if phase == 'readback':
                original = case.runtime._submit_claimed_consent
                async def submit(operation):
                    mutate()
                    return await original(operation)
                monkeypatch.setattr(case.runtime, '_submit_claimed_consent', submit)
            else:
                original = case.runtime._consent_execution_guard
                def guard(operation, inputs, current):
                    if current == phase:
                        mutate()
                    return original(operation, inputs, current)
                monkeypatch.setattr(case.runtime, '_consent_execution_guard', guard)
            try:
                await approve(case, chat_input_context=case.context)
            except DockingConsentError:
                assert phase == 'readback'
                case.unresolved = True
            operation = case.context._execution
            if operation.settlement is not None:
                await asyncio.wait_for(asyncio.shield(operation.settlement), 10)
            assert case.raw == ([True] if phase == 'result' else [])
            assert operation.status != 'SUCCEEDED'
    asyncio.run(run())


def test_claim_clock_samples_are_not_replaced_by_later_ack_clocks(tmp_path, target_case, monkeypatch):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            before = case.rig.wall, case.rig.mono
            original = case.rig.tasks._claim_docking_consent
            def claim(*args, **kwargs):
                acquired = original(*args, **kwargs)
                case.rig.wall += 10000
                case.rig.mono += 10
                return acquired
            monkeypatch.setattr(case.rig.tasks, '_claim_docking_consent', claim)
            await approve(case, chat_input_context=case.context)
            operation = case.context._execution
            await asyncio.wait_for(asyncio.shield(operation.settlement), 10)
            assert operation.row['operation_deadline_ms'] == before[0] + 420000
            assert operation.row['operation_monotonic_expires'] == before[1] + 420
            assert case.raw == [True] and operation.status == 'SUCCEEDED'
    asyncio.run(run())
