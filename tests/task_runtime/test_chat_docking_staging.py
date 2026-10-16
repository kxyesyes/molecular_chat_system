"""Chat draft -> C1 with live synthetic source ownership, never real results."""
import asyncio
import copy
import hashlib
import threading

import pytest

from src.task_runtime.database import connection
from tests.task_runtime.test_docking_consent import PARAMETERS, canonical
from tests.task_runtime.test_docking_consent_draft import identity, metadata
from tests.task_runtime.test_chat_docking_input_context import authenticated_rig, target_case


def reserve_chat(rig):
    draft = metadata()
    values = dict(PARAMETERS, receptor_ref="sha256:" + "a" * 64,
                  ligand_ref="sha256:" + "b" * 64)
    draft["slots"] = {key: dict(state="fixed", value=value) for key, value in values.items()}
    context = dict(policy=rig.policy, model_generation="model-1", capability_generation="capability-1")
    return rig.store.reserve_chat_docking_draft(identity=identity(), draft=draft, policy=rig.policy,
        wall_time_ms=rig.clock.wall_time_ms, monotonic=rig.clock.monotonic,
        current_context=lambda: context)


@pytest.mark.parametrize("wall_offset", [40000, -40000])
def test_chat_ready_starts_separate_consent_ttl_without_resetting_waiting_early(tmp_path, target_case, monkeypatch, wall_offset):
    async def run():
        async with authenticated_rig(tmp_path, target_case, monkeypatch) as case:
            before = copy.deepcopy(case.root.draft)
            case.rig.wall += wall_offset
            case.rig.mono += 40
            preview = (await case.runtime.prepare_docking_consent(**case.args)).to_dict()
            row = case.rig.tasks.get_chat_docking_draft(preparation_id=case.root.preparation_id,
                                                       owner_session_id=case.root.owner)
            assert row["state"] == "READY"
            assert row["expires_at_ms"] == case.rig.wall + 900000
            assert row["monotonic_expires"] == case.rig.mono + 900
            assert preview["expires_at_ms"] == case.rig.wall + 900000
            assert row["binding"]["expires_at_ms"] == case.rig.wall + 900000
            assert row["binding"]["issued_at_ms"] == case.rig.wall
            assert row["binding_digest"] == hashlib.sha256(
                b"medchat-docking-consent-v1\0" + canonical(row["binding"])).hexdigest()
            assert row["identity"] == before["identity"] and row["draft"] == before["draft"]
            assert not case.calls and case.stages == [case.root.task_id]
    asyncio.run(run())


@pytest.mark.parametrize("field", ["center", "size", "exhaustiveness", "num_modes"])
def test_chat_fixed_parameter_drift_rejected_before_staging(tmp_path, target_case, monkeypatch, field):
    async def run():
        async with authenticated_rig(tmp_path, target_case, monkeypatch) as case:
            params = copy.deepcopy(case.args['parameters'])
            if field in {"center", "size"}: params[field][0] += 1
            else: params[field] += 1
            with pytest.raises(ValueError, match='consent_proof_mismatch'):
                await case.runtime.prepare_docking_consent(**dict(case.args, parameters=params))
            row = case.rig.tasks.get_docking_consent(case.root.preparation_id)
            assert row["binding"] is None and row["approval_nonce_hash"] is None
            assert not case.calls and not case.stages
    asyncio.run(run())


@pytest.mark.parametrize("clock", ["wall", "mono", "mono_wall_rollback"])
def test_chat_crossing_original_expiry_during_staging_never_seals(tmp_path, target_case, monkeypatch, clock):
    async def run():
        async with authenticated_rig(tmp_path, target_case, monkeypatch) as case:
            before = copy.deepcopy(case.root.draft)
            original = case.stager.stage
            def stage(*args, **kwargs):
                value = original(*args, **kwargs)
                if clock == "wall": case.rig.wall = before["expires_at_ms"]
                else:
                    case.rig.mono = before["monotonic_expires"]
                    if clock == "mono_wall_rollback": case.rig.wall -= 10000
                return value
            monkeypatch.setattr(case.stager, "stage", stage)
            with pytest.raises(ValueError, match='consent_expired'):
                await case.runtime.prepare_docking_consent(**case.args)
            await case.runtime.close()
            row = case.rig.tasks.get_docking_consent(case.root.preparation_id)
            assert row["binding"] is None and row["approval_nonce_hash"] is None
            assert row["expires_at_ms"] == before["expires_at_ms"]
            with connection(case.rig.tasks.db_path) as conn:
                assert conn.execute("SELECT COUNT(*) AS n FROM tasks").fetchone()["n"] == 0
            assert not case.calls
    asyncio.run(run())


def test_chat_constraints_cancel_keeps_actual_worker_until_settlement(tmp_path, target_case, monkeypatch):
    async def run():
        async with authenticated_rig(tmp_path, target_case, monkeypatch) as case:
            entered, release, finished = threading.Event(), threading.Event(), threading.Event()
            original = case.rig.tasks._validate_docking_preparation_parameters
            def held(*args, **kwargs):
                entered.set()
                try:
                    assert release.wait(15), "test barrier was not released"
                    return original(*args, **kwargs)
                finally: finished.set()
            monkeypatch.setattr(case.rig.tasks, "_validate_docking_preparation_parameters", held)
            call = asyncio.create_task(case.runtime.prepare_docking_consent(**case.args))
            close = None
            try:
                assert await asyncio.to_thread(entered.wait, 8)
                call.cancel()
                with pytest.raises(asyncio.CancelledError): await call
                assert not finished.is_set() and not case.stages
                operation, = case.runtime._consent_preparations
                assert not operation.settled and not operation.worker.done()
                close = asyncio.create_task(case.runtime.close())
                await asyncio.sleep(0)
                assert not close.done()
            finally:
                release.set()
                if close is not None: await asyncio.wait_for(close, 8)
                else: await case.runtime.close()
            assert finished.is_set() and not case.runtime._consent_preparations
            assert not case.stages and not case.calls
            row = case.rig.tasks.get_docking_consent(case.root.preparation_id)
            assert row["binding"] is None and row["cleanup_state"] == "settled"
    asyncio.run(run())


def test_chat_seal_rechecks_fixed_parameters_under_store_lock(tmp_path, target_case, monkeypatch):
    import src.task_runtime.runtime as runtime
    original = runtime.seal_binding
    def altered(*args, **kwargs):
        binding, digest = original(*args, **kwargs)
        binding["num_modes"] += 1
        digest = hashlib.sha256(b"medchat-docking-consent-v1\0" + canonical(binding)).hexdigest()
        return binding, digest
    monkeypatch.setattr(runtime, "seal_binding", altered)
    async def run():
        async with authenticated_rig(tmp_path, target_case, monkeypatch) as case:
            before = copy.deepcopy(case.root.draft)
            with pytest.raises(ValueError, match='consent_binding_mismatch'):
                await case.runtime.prepare_docking_consent(**case.args)
            await case.runtime.close()
            row = case.rig.tasks.get_docking_consent(case.root.preparation_id)
            assert row["binding"] is None and row["approval_nonce_hash"] is None
            assert row["expires_at_ms"] == before["expires_at_ms"]
            assert not case.calls
    asyncio.run(run())
