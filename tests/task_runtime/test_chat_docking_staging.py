"""Chat draft -> existing C1 staging; synthetic bytes, never scientific results."""
import asyncio
import copy
import hashlib
import threading

import pytest

from src.task_runtime.database import connection
from tests.task_runtime.test_docking_consent import make_rig, prepare, PARAMETERS, canonical
from tests.task_runtime.test_docking_consent_draft import identity, metadata


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
def test_chat_ready_starts_separate_consent_ttl_without_resetting_waiting_early(tmp_path, monkeypatch, wall_offset):
    rig = make_rig(tmp_path, monkeypatch)
    before = reserve_chat(rig)
    rig.clock.wall += wall_offset
    rig.clock.mono += 40
    async def run():
        preview = (await prepare(rig, before["identity"])).to_dict()
        row = rig.store.get_chat_docking_draft(preparation_id="draft-1", owner_session_id="owner-1")
        assert row["state"] == "READY"
        assert row["expires_at_ms"] == rig.clock.wall + 900000
        assert row["monotonic_expires"] == rig.clock.mono + 900
        assert preview["expires_at_ms"] == rig.clock.wall + 900000
        assert row["binding"]["expires_at_ms"] == rig.clock.wall + 900000
        assert row["binding"]["issued_at_ms"] == rig.clock.wall
        assert row["binding_digest"] == hashlib.sha256(
            b"medchat-docking-consent-v1\0" + canonical(row["binding"])).hexdigest()
        assert row["identity"] == before["identity"]
        assert row["draft"] == before["draft"]
        await rig.runtime.close()
    asyncio.run(run())
    assert rig.calls == [] and rig.stages == ["task-1"]


@pytest.mark.parametrize("field", ["center", "size", "exhaustiveness", "num_modes"])
def test_chat_fixed_parameter_drift_rejected_before_staging(tmp_path, monkeypatch, field):
    rig = make_rig(tmp_path, monkeypatch)
    before = reserve_chat(rig)
    params = copy.deepcopy(PARAMETERS)
    if field in {"center", "size"}:
        params[field][0] += 1
    else:
        params[field] += 1
    async def run():
        with pytest.raises(rig.api.DockingConsentError) as caught:
            await prepare(rig, before["identity"], parameters=params)
        assert caught.value.reason_code == "consent_binding_mismatch"
        await rig.runtime.close()
    asyncio.run(run())
    assert rig.calls == [] and rig.stages == []
    row = rig.store.get_docking_consent("draft-1")
    assert row["binding"] is None and row["approval_nonce_hash"] is None


@pytest.mark.parametrize("clock", ["wall", "mono", "mono_wall_rollback"])
def test_chat_crossing_original_expiry_during_staging_never_seals(tmp_path, monkeypatch, clock):
    rig = make_rig(tmp_path, monkeypatch)
    before = reserve_chat(rig)
    original = rig.stager.stage
    def stage(*args, **kwargs):
        value = original(*args, **kwargs)
        if clock == "wall":
            rig.clock.wall = before["expires_at_ms"]
        else:
            rig.clock.mono = before["monotonic_expires"]
            if clock == "mono_wall_rollback":
                rig.clock.wall -= 10000
        return value
    monkeypatch.setattr(rig.stager, "stage", stage)
    async def run():
        with pytest.raises(rig.api.DockingConsentError) as caught:
            await prepare(rig, before["identity"])
        assert caught.value.reason_code == "consent_expired"
        await rig.runtime.close()
    asyncio.run(run())
    row = rig.store.get_docking_consent("draft-1")
    assert row["binding"] is None and row["approval_nonce_hash"] is None
    assert row["expires_at_ms"] == before["expires_at_ms"]
    with connection(rig.db) as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM tasks").fetchone()["n"] == 0
    assert rig.calls == []


def test_chat_constraints_cancel_keeps_actual_worker_until_settlement(tmp_path, monkeypatch):
    rig = make_rig(tmp_path, monkeypatch)
    before = reserve_chat(rig)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    original = rig.store._validate_docking_preparation_parameters
    def held(*args, **kwargs):
        entered.set()
        try:
            assert release.wait(10), "test barrier was not released"
            return original(*args, **kwargs)
        finally:
            finished.set()
    monkeypatch.setattr(rig.store, "_validate_docking_preparation_parameters", held)
    async def run():
        call = asyncio.create_task(prepare(rig, before["identity"]))
        close = None
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            call.cancel()
            with pytest.raises(asyncio.CancelledError):
                await call
            assert not finished.is_set() and rig.stages == []
            assert len(rig.runtime._consent_preparations) == 1
            operation = next(iter(rig.runtime._consent_preparations))
            assert not operation.settled and not operation.worker.done()
            close = asyncio.create_task(rig.runtime.close())
            await asyncio.sleep(0)
            assert not close.done()
        finally:
            release.set()
            if close is not None:
                await asyncio.wait_for(close, 5)
            else:
                await rig.runtime.close()
        assert finished.is_set() and not rig.runtime._consent_preparations
    asyncio.run(run())
    assert rig.stages == [] and rig.calls == []
    row = rig.store.get_docking_consent("draft-1")
    assert row["binding"] is None and row["cleanup_state"] == "settled"


def test_chat_seal_rechecks_fixed_parameters_under_store_lock(tmp_path, monkeypatch):
    import src.task_runtime.runtime as runtime
    rig = make_rig(tmp_path, monkeypatch)
    before = reserve_chat(rig)
    original = runtime.seal_binding
    def altered(*args, **kwargs):
        binding, digest = original(*args, **kwargs)
        binding["num_modes"] += 1
        digest = hashlib.sha256(b"medchat-docking-consent-v1\0" + canonical(binding)).hexdigest()
        return binding, digest
    monkeypatch.setattr(runtime, "seal_binding", altered)
    async def run():
        with pytest.raises(rig.api.DockingConsentError) as caught:
            await prepare(rig, before["identity"])
        assert caught.value.reason_code == "consent_binding_mismatch"
        await rig.runtime.close()
    asyncio.run(run())
    row = rig.store.get_docking_consent("draft-1")
    assert row["binding"] is None and row["approval_nonce_hash"] is None
    assert row["expires_at_ms"] == before["expires_at_ms"]
    assert rig.calls == []
