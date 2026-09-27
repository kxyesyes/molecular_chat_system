"""C1 prepare-only contracts: real private SQLite/staging, no scientific execution.

New APIs are resolved inside test bodies. Missing API is not collection failure.
Literal bytes are synthetic file fixtures, never valid-science/result evidence.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import importlib
import importlib.util
import inspect
import json
from pathlib import Path
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest


PARAMETERS = {"center": [1, -2, 3.5], "size": [20, 21, 22], "exhaustiveness": 4, "num_modes": 3}
RECEPTOR = b"ATOM SYNTHETIC C1 RECEPTOR\nEND\n"
LIGAND = b"SYNTHETIC C1 LIGAND\n$$$$\n"
TTL_MS = 15 * 60 * 1000
FILE_LIMIT = 25 * 1024 * 1024


def c1_api():
    from src.task_runtime.runtime import TaskRuntime
    from src.task_runtime.store import TaskStore
    assert inspect.iscoroutinefunction(getattr(TaskRuntime, "prepare_docking_consent", None)), \
        "C1 missing TaskRuntime.prepare_docking_consent"
    for name in ("reserve_docking_consent_draft", "get_docking_consent", "expire_docking_consents"):
        assert callable(getattr(TaskStore, name, None)), "C1 missing TaskStore." + name
    name = "src.task_runtime.docking_consent"
    assert importlib.util.find_spec(name) is not None, "C1 missing docking_consent contract module"
    api = importlib.import_module(name)
    for name in ("DockingConsentPolicy", "DockingConsentPreview", "DockingConsentError"):
        assert isinstance(getattr(api, name, None), type), "C1 missing " + name
    return api, TaskRuntime, TaskStore


def test_c1_prepare_api_present():
    c1_api()


class Clock:
    """Server TTL clocks only; does not shorten the actual 30s preparation wait."""
    def __init__(self):
        self.wall = 1_800_000_000_000
        self.mono = 500.0

    def wall_time_ms(self):
        return self.wall

    def monotonic(self):
        return self.mono


def identity(index=1, *, owner=None):
    return {
        "preparation_id": f"preparation-{index}", "task_id": f"c-task-{index}",
        "owner_session_id": owner or f"owner-{index}", "trace_id": f"trace-{index}",
        "origin_turn_id": f"turn-{index}", "query_digest": hashlib.sha256(b"synthetic whole query").hexdigest(),
        "refinement_revision": 0, "admission_revision": "docking-entry-v1",
        "source_refs": {"receptor_ref": f"owned-receptor-{index}", "ligand_ref": f"owned-ligand-{index}"},
    }


def policy(api, **changes):
    values = dict(tool_policy_digest="a" * 64, adapter_contract_version="docking-adapter-v1",
                  execution_backend="local", policy_generation="policy-1", runtime_generation="runtime-1",
                  vina_limit_seconds=300, operation_limit_seconds=420)
    values.update(changes)
    return api.DockingConsentPolicy(**values)


def make_rig(tmp_path, monkeypatch, *, consent_policy="default", clock=None):
    api, Runtime, Store = c1_api()
    from src.task_runtime.backends.local import LocalTaskBackend
    from src.task_runtime.config import TaskRuntimeConfig
    from src.task_runtime.staging import DockingInputStager
    from src.agent.openai_compatible_model import OpenAICompatibleModel
    import httpx
    import subprocess

    clock = clock or Clock()
    selected_policy = policy(api) if consent_policy == "default" else consent_policy
    store = Store(tmp_path / "tasks.sqlite")
    stage_root = tmp_path / "stage"
    stager = DockingInputStager(stage_root)
    calls, stages = [], []

    def denied(name):
        calls.append(name)
        raise AssertionError("C1 attempted forbidden boundary: " + name)

    async def backend_boundary(*args, **kwargs):
        denied("backend")

    async def model_boundary(*args, **kwargs):
        denied("model")

    async def worker(*args, **kwargs):
        denied("scientific-worker")

    local = LocalTaskBackend(store, {"docking": worker})
    for name in ("submit", "get", "list", "cancel", "health"):
        monkeypatch.setattr(local, name, backend_boundary)
    for name in ("generate", "decide", "propose_ordinary_intent", "propose_docking_preparation"):
        monkeypatch.setattr(OpenAICompatibleModel, name, model_boundary)
    monkeypatch.setattr(httpx.AsyncClient, "send", model_boundary)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: denied("process"))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", model_boundary)
    original_stage = stager.stage

    def observed_stage(*args, **kwargs):
        stages.append(args[0] if args else kwargs["task_id"])
        return original_stage(*args, **kwargs)

    monkeypatch.setattr(stager, "stage", observed_stage)
    config = TaskRuntimeConfig(backend="local", canary_percent=0, temporal_address="unused.invalid:7233",
                               temporal_namespace="test", docking_queue="test", docking_concurrency=1,
                               staging_root=stage_root)
    runtime = Runtime(
        config=config, store=store, stager=stager, local_backend=local,
        selector=SimpleNamespace(select=lambda *a, **k: denied("selector")),
        temporal_backend_factory=lambda: denied("broker-factory"),
        uuid_factory=lambda: denied("replacement-task-id"),
        docking_execution=SimpleNamespace(run_verified=lambda *a, **k: denied("raw-executor")),
        docking_consent_policy=selected_policy,
        consent_wall_time_ms=clock.wall_time_ms, consent_monotonic=clock.monotonic,
    )
    return SimpleNamespace(api=api, Store=Store, store=store, stager=stager, runtime=runtime,
                           policy=selected_policy, clock=clock, calls=calls, stages=stages,
                           local=local, root=stage_root, db=tmp_path / "tasks.sqlite")


def reserve(rig, draft=None, *, store=None):
    return (store or rig.store).reserve_docking_consent_draft(
        identity=copy.deepcopy(draft or identity()), policy=rig.policy,
        now_ms=rig.clock.wall, monotonic_now=rig.clock.mono,
    )


async def prepare(rig, draft=None, **changes):
    draft = draft or identity()
    values = dict(preparation_id=draft["preparation_id"], owner_session_id=draft["owner_session_id"],
                  revision=draft["refinement_revision"], receptor_name="receptor.pdb", receptor_bytes=RECEPTOR,
                  ligand_name="ligand.sdf", ligand_bytes=LIGAND, parameters=copy.deepcopy(PARAMETERS))
    values.update(changes)
    return await rig.runtime.prepare_docking_consent(**values)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("utf-8")


def expected_binding(draft, *, issued_ms, selected_policy, parameters=None,
                     receptor=RECEPTOR, ligand=LIGAND):
    parameters = copy.deepcopy(parameters or PARAMETERS)
    parameters["center"] = [float(x) for x in parameters["center"]]
    parameters["size"] = [float(x) for x in parameters["size"]]
    r_hash, l_hash = hashlib.sha256(receptor).hexdigest(), hashlib.sha256(ligand).hexdigest()
    config_hash = hashlib.sha256(canonical(parameters)).hexdigest()
    inputs = {"receptor": {"size": len(receptor), "sha256": r_hash},
              "ligand": {"mode": "file", "size": len(ligand), "sha256": l_hash}}
    input_hash = hashlib.sha256(canonical(inputs)).hexdigest()
    request = {"config_hash": config_hash, "ligand_mode": "file",
               "ligand_sha256": l_hash, "receptor_sha256": r_hash}
    request_digest = hashlib.sha256(b"medchat-task-submission-v1\0" + canonical(request)).hexdigest()
    return {
        "schema": "DockingConsent@1", **copy.deepcopy(draft),
        "receptor": {"name": "receptor.pdb", "size": len(receptor), "sha256": r_hash},
        "ligand": {"name": "ligand.sdf", "size": len(ligand), "sha256": l_hash},
        "ligand_mode": "file", **parameters, "energy_range": 3.0,
        "config_hash": config_hash, "input_hash": input_hash, "request_digest": request_digest,
        "tool_name": "molecular_docking", "adapter_contract_version": selected_policy.adapter_contract_version,
        "tool_policy_digest": selected_policy.tool_policy_digest,
        "execution_backend": selected_policy.execution_backend,
        "policy_generation": selected_policy.policy_generation, "runtime_generation": selected_policy.runtime_generation,
        "issued_at_ms": issued_ms, "expires_at_ms": issued_ms + TTL_MS,
        "vina_limit_seconds": selected_policy.vina_limit_seconds,
        "operation_limit_seconds": selected_policy.operation_limit_seconds,
    }


def no_execution(rig):
    assert rig.calls == [] and rig.local.background_task_count == 0
    with sqlite3.connect(rig.db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM task_events").fetchone()[0] == 0


def expire(rig, **changes):
    values = dict(now_ms=rig.clock.wall, monotonic_now=rig.clock.mono,
                  runtime_generation=rig.policy.runtime_generation, policy_generation=rig.policy.policy_generation)
    values.update(changes)
    return rig.store.expire_docking_consents(**values)


def test_c1_real_sqlite_copies_hashes_and_seals_existing_identity(tmp_path, monkeypatch):
    rig = make_rig(tmp_path, monkeypatch)
    draft = identity()
    frozen = copy.deepcopy(draft)
    reserve(rig, draft)
    draft["source_refs"]["ligand_ref"] = "changed-after-reservation"
    initial = rig.Store(rig.db).get_docking_consent(frozen["preparation_id"])
    assert initial["identity"] == frozen and initial["state"] == "AWAITING_INPUT"
    assert initial["binding_digest"] is initial["approval_nonce_hash"] is None
    assert not (rig.root / frozen["task_id"]).exists()
    no_execution(rig)

    async def run():
        try:
            return await prepare(rig, frozen)
        finally:
            await rig.runtime.close()

    preview = asyncio.run(run())
    assert isinstance(preview, rig.api.DockingConsentPreview)
    record = rig.Store(rig.db).get_docking_consent(frozen["preparation_id"])
    assert record["state"] == "READY" and record["identity"] == frozen and record["version"] > initial["version"]
    assert record["manifest_locator"] == "input_manifest.json"
    manifest = rig.stager.load_verified_locator(frozen["task_id"], record["manifest_locator"])
    for key, content in (("receptor", RECEPTOR), ("ligand", LIGAND)):
        copied = (rig.root / frozen["task_id"] / manifest[key]["path"]).read_bytes()
        assert copied == content and hashlib.sha256(copied).hexdigest() == manifest[key]["sha256"]
    expected = expected_binding(frozen, issued_ms=rig.clock.wall, selected_policy=rig.policy)
    digest = hashlib.sha256(b"medchat-docking-consent-v1\0" + canonical(expected)).hexdigest()
    assert record["binding"] == expected and record["binding_digest"] == digest
    assert manifest["config_hash"] == expected["config_hash"]
    public = preview.to_dict()
    assert set(public) == {"schema", "preparation_id", "task_id", "trace_id", "receptor", "ligand",
                           "parameters", "tool_policy", "binding_digest", "expires_at_ms", "approval_nonce"}
    assert public["schema"] == "DockingConsent@1" and public["preparation_id"] == frozen["preparation_id"]
    assert public["task_id"] == frozen["task_id"] and public["trace_id"] == frozen["trace_id"]
    assert public["binding_digest"] == digest and public["expires_at_ms"] == rig.clock.wall + TTL_MS
    assert public["parameters"] == {**manifest["config"], "energy_range": 3.0}
    assert public["receptor"] == expected["receptor"] and public["ligand"] == expected["ligand"]
    assert public["tool_policy"] == {
        "label": "molecular_docking/local", "digest": rig.policy.tool_policy_digest,
        "execution_backend": "local", "adapter_contract_version": rig.policy.adapter_contract_version,
        "vina_limit_seconds": 300, "operation_limit_seconds": 420,
    }
    nonce = public["approval_nonce"]
    assert type(nonce) is str and len(nonce) == 64 and len(bytes.fromhex(nonce)) == 32
    assert record["approval_nonce_hash"] == hashlib.sha256(nonce.encode("ascii")).hexdigest()
    with sqlite3.connect(rig.db) as conn:
        dump = "\n".join(conn.iterdump())
        assert conn.execute("SELECT COUNT(*) FROM docking_consents").fetchone()[0] == 1
    assert nonce not in dump + repr(preview) + repr(record)
    assert str(rig.root) not in json.dumps(public) and frozen["owner_session_id"] not in json.dumps(public)
    assert RECEPTOR.decode().splitlines()[0] not in dump and LIGAND.decode().splitlines()[0] not in dump
    public["parameters"]["center"][0] = 999
    record["binding"]["center"][0] = 999
    assert preview.to_dict()["parameters"]["center"] == expected["center"]
    assert rig.Store(rig.db).get_docking_consent(frozen["preparation_id"])["binding"] == expected
    assert rig.stages == [frozen["task_id"]]
    no_execution(rig)


@pytest.mark.parametrize("fault", ["missing", "foreign-owner", "revision", "runtime-generation", "policy-generation"])
def test_c1_prepare_rejects_unowned_or_stale_draft_before_staging(tmp_path, monkeypatch, fault):
    rig = make_rig(tmp_path, monkeypatch)
    if fault != "missing":
        reserve(rig)
    changes = {}
    if fault == "foreign-owner":
        changes["owner_session_id"] = "other-owner"
    elif fault == "revision":
        changes["revision"] = 1
    elif fault in {"runtime-generation", "policy-generation"}:
        expire(rig, **{fault.replace("-", "_"): "replacement-generation"})

    async def run():
        try:
            with pytest.raises(rig.api.DockingConsentError) as caught:
                await prepare(rig, **changes)
            assert caught.value.reason_code == (
                "consent_not_found" if fault in {"missing", "foreign-owner"} else
                "consent_revision_conflict" if fault == "revision" else "consent_expired")
        finally:
            await rig.runtime.close()

    asyncio.run(run())
    assert rig.stages == []
    no_execution(rig)


@pytest.mark.parametrize("field,value", [
    ("center", None), ("center", [True, 0, 0]), ("center", ["1", 0, 0]),
    ("center", [float("nan"), 0, 0]), ("center", [float("inf"), 0, 0]), ("center", [0, 0]),
    ("center", (0, 0, 0)),
    ("size", [0, 1, 1]), ("size", [-1, 1, 1]), ("size", [100.01, 1, 1]),
    ("exhaustiveness", 0), ("exhaustiveness", 65), ("exhaustiveness", True),
    ("exhaustiveness", "4"), ("num_modes", 0), ("num_modes", 51), ("num_modes", 3.0),
    ("energy_range", 3.0), ("backend", "local"), ("timeout", 300),
])
def test_c1_closed_native_parameters_reject_without_staging(tmp_path, monkeypatch, field, value):
    rig = make_rig(tmp_path, monkeypatch)
    reserve(rig)
    params = copy.deepcopy(PARAMETERS)
    params[field] = value

    async def run():
        try:
            with pytest.raises(rig.api.DockingConsentError) as caught:
                await prepare(rig, parameters=params)
            assert caught.value.reason_code == "consent_invalid_input"
        finally:
            await rig.runtime.close()

    asyncio.run(run())
    assert rig.stages == [] and not (rig.root / identity()["task_id"]).exists()
    assert rig.store.get_docking_consent(identity()["preparation_id"])["binding_digest"] is None
    no_execution(rig)


@pytest.mark.parametrize("missing", list(PARAMETERS))
def test_c1_all_four_parameters_are_explicit_no_stager_defaults(tmp_path, monkeypatch, missing):
    rig = make_rig(tmp_path, monkeypatch)
    reserve(rig)
    params = copy.deepcopy(PARAMETERS)
    del params[missing]

    async def run():
        try:
            with pytest.raises(rig.api.DockingConsentError, match="consent_invalid_input"):
                await prepare(rig, parameters=params)
        finally:
            await rig.runtime.close()

    asyncio.run(run())
    assert rig.stages == []
    no_execution(rig)


@pytest.mark.parametrize("changes", [
    {"receptor_bytes": b""}, {"ligand_bytes": b""}, {"receptor_bytes": "ATOM"},
    {"ligand_bytes": bytearray(b"ligand")}, {"ligand_bytes": None}, {"ligand_name": None},
    {"receptor_name": "../escape.pdb"}, {"ligand_name": "C:\\private\\input.sdf"},
    {"receptor_name": "https://example.invalid/r.pdb"}, {"ligand_name": "CON.sdf"},
    {"ligand_name": "RECEPTOR.PDB"},
])
def test_c1_file_backed_native_names_and_bytes_only(tmp_path, monkeypatch, changes):
    rig = make_rig(tmp_path, monkeypatch)
    reserve(rig)

    async def run():
        try:
            with pytest.raises(rig.api.DockingConsentError) as caught:
                await prepare(rig, **changes)
            assert caught.value.reason_code == "consent_invalid_input"
            assert str(tmp_path) not in str(caught.value)
        finally:
            await rig.runtime.close()

    asyncio.run(run())
    assert not (rig.root / identity()["task_id"]).exists()
    no_execution(rig)


@pytest.mark.parametrize("field", ["receptor_bytes", "ligand_bytes"])
@pytest.mark.parametrize("extra", [0, 1], ids=["exact-25MiB", "over-25MiB"])
def test_c1_actual_file_byte_boundary(tmp_path, monkeypatch, field, extra):
    rig = make_rig(tmp_path, monkeypatch)
    reserve(rig)
    content = b"X" * (FILE_LIMIT + extra)

    async def run():
        try:
            if extra:
                with pytest.raises(rig.api.DockingConsentError, match="consent_invalid_input"):
                    await prepare(rig, **{field: content})
            else:
                result = await prepare(rig, **{field: content})
                record = rig.store.get_docking_consent(result.to_dict()["preparation_id"])
                file_record = record["binding"][field.removesuffix("_bytes")]
                assert file_record["size"] == FILE_LIMIT
                assert file_record["sha256"] == hashlib.sha256(content).hexdigest()
        finally:
            await rig.runtime.close()

    asyncio.run(run())
    no_execution(rig)


@pytest.mark.parametrize("parameters", [
    {"center": [0, 0, 0], "size": [100, 100, 100], "exhaustiveness": 64, "num_modes": 50},
    {"center": [-1.5, 0, 2], "size": [0.5, 1, 1], "exhaustiveness": 1, "num_modes": 1},
])
def test_c1_explicit_parameter_boundaries_and_same_task_immutable(tmp_path, monkeypatch, parameters):
    rig = make_rig(tmp_path, monkeypatch)
    reserve(rig)

    async def run():
        try:
            preview = await prepare(rig, parameters=parameters)
            before = rig.store.get_docking_consent(identity()["preparation_id"])
            for changes in ({}, {"ligand_bytes": b"changed scientific input"}):
                with pytest.raises(rig.api.DockingConsentError, match="consent_not_waiting"):
                    await prepare(rig, parameters=parameters, **changes)
            assert rig.store.get_docking_consent(identity()["preparation_id"]) == before
            assert preview.to_dict()["task_id"] == identity()["task_id"]
            expected = expected_binding(identity(), issued_ms=rig.clock.wall, selected_policy=rig.policy,
                                        parameters=parameters)
            assert before["binding"] == expected
        finally:
            await rig.runtime.close()

    asyncio.run(run())
    assert rig.stages == [identity()["task_id"]]
    no_execution(rig)


def test_c1_missing_server_policy_is_unavailable_not_probe_or_fallback(tmp_path, monkeypatch):
    rig = make_rig(tmp_path, monkeypatch, consent_policy=None)
    # Reserve trusted identity under a policy, but do not give it to this runtime.
    rig.store.reserve_docking_consent_draft(identity=identity(), policy=policy(rig.api),
                                           now_ms=rig.clock.wall, monotonic_now=rig.clock.mono)

    async def run():
        try:
            with pytest.raises(rig.api.DockingConsentError, match="consent_policy_unavailable"):
                await prepare(rig)
        finally:
            await rig.runtime.close()

    asyncio.run(run())
    assert rig.stages == []
    no_execution(rig)


@pytest.mark.parametrize("boundary", ["same-owner", "global-sixteenth"])
def test_c1_capacity_reservation_is_transactional_across_actual_sqlite_connections(tmp_path, monkeypatch, boundary):
    rig = make_rig(tmp_path, monkeypatch)
    if boundary == "global-sixteenth":
        for index in range(15):
            reserve(rig, identity(index))
    # Separate stores/connections, no fake SQL/CAS implementation or replaced lock.
    stores = [rig.Store(rig.db), rig.Store(rig.db)]
    barrier = threading.Barrier(2)
    drafts = [identity(100 + index, owner="same-owner" if boundary == "same-owner" else None)
              for index in range(2)]

    def contender(index):
        barrier.wait(timeout=5)
        try:
            reserve(rig, drafts[index], store=stores[index])
            return "reserved"
        except rig.api.DockingConsentError as exc:
            return exc.reason_code

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(contender, index) for index in range(2)]
            results = [future.result(timeout=10) for future in futures]
        assert sorted(results) == sorted([
            "reserved", "consent_owner_busy" if boundary == "same-owner" else "consent_capacity_full"])
        with sqlite3.connect(rig.db) as conn:
            assert conn.execute("SELECT COUNT(*) FROM docking_consents").fetchone()[0] == (
                1 if boundary == "same-owner" else 16)
    finally:
        asyncio.run(rig.runtime.close())
    assert rig.stages == []
    no_execution(rig)


@pytest.mark.parametrize("phase", ["AWAITING_INPUT", "READY"])
@pytest.mark.parametrize("expiry", ["before", "equal", "monotonic-rollback", "runtime-generation", "policy-generation"])
def test_c1_non_sliding_expiry_and_generation_invalidation(tmp_path, monkeypatch, phase, expiry):
    rig = make_rig(tmp_path, monkeypatch)
    reserve(rig)

    async def run():
        try:
            if phase == "READY":
                # READY TTL starts on issuance, not at earlier draft reservation.
                rig.clock.wall += 10_000
                rig.clock.mono += 10
                await prepare(rig)
            before = rig.store.get_docking_consent(identity()["preparation_id"])
            assert before["expires_at_ms"] == rig.clock.wall + TTL_MS
            for _ in range(3):
                assert rig.Store(rig.db).get_docking_consent(identity()["preparation_id"]) == before
            kwargs = {}
            if expiry == "before":
                rig.clock.wall += TTL_MS - 1
                rig.clock.mono += (TTL_MS - 1) / 1000
            elif expiry == "equal":
                rig.clock.wall += TTL_MS
                rig.clock.mono += TTL_MS / 1000
            elif expiry == "monotonic-rollback":
                rig.clock.wall -= 1_000
                rig.clock.mono += TTL_MS / 1000
            else:
                kwargs[expiry.replace("-", "_")] = "generation-2"
            expire(rig, **kwargs)
            after = rig.Store(rig.db).get_docking_consent(identity()["preparation_id"])
            assert after["expires_at_ms"] == before["expires_at_ms"]
            assert after["state"] == (phase if expiry == "before" else "EXPIRED")
            if phase == "READY":
                assert after["binding"] == before["binding"] and after["binding_digest"] == before["binding_digest"]
            elif expiry != "before":
                with pytest.raises(rig.api.DockingConsentError, match="consent_expired"):
                    await prepare(rig)
                if expiry in {"equal", "monotonic-rollback"}:
                    # No writer existed; a new explicit request can reserve this
                    # slot. Do not reuse an invalidated generation in this control.
                    reserve(rig, identity(2, owner=identity()["owner_session_id"]))
        finally:
            await rig.runtime.close()

    asyncio.run(run())
    no_execution(rig)


def test_c1_ready_staging_is_not_an_orphan_despite_absent_task_row(tmp_path, monkeypatch):
    rig = make_rig(tmp_path, monkeypatch)
    reserve(rig)
    foreign = rig.stager.stage("legacy-owned", "r.pdb", b"legacy receptor", "l.sdf", b"legacy ligand", None,
                               copy.deepcopy(PARAMETERS))
    rig.store.create("legacy-owned", "docking", {}, input_manifest_path=str(foreign))
    unowned = rig.root / "unowned-neighbor"
    unowned.mkdir()
    marker = unowned / "keep.txt"
    marker.write_bytes(b"not owned by C")

    async def run():
        try:
            await prepare(rig)
            before = rig.store.get_docking_consent(identity()["preparation_id"])
            removed = await rig.runtime.cleanup_staging_once(cutoff_epoch=10**12)
            assert identity()["task_id"] not in removed and "legacy-owned" not in removed
            assert rig.store.get_docking_consent(identity()["preparation_id"]) == before
            assert rig.stager.load_verified_locator(identity()["task_id"], before["manifest_locator"])
            assert foreign.exists() and marker.read_bytes() == b"not owned by C"
            assert rig.calls == [] and rig.local.background_task_count == 0
            with pytest.raises(KeyError):
                rig.store.get(identity()["task_id"])
        finally:
            await rig.runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize("interrupt", ["cancel", "repeat-cancel", "deadline", "cleanup-failure"])
def test_c1_interrupted_preparation_retains_actual_writer_capacity_and_owned_cleanup(tmp_path, monkeypatch, interrupt):
    rig = make_rig(tmp_path, monkeypatch)
    reserve(rig)
    entered, release, exited = threading.Event(), threading.Event(), threading.Event()
    original_stage = rig.stager.stage
    writer_reads, deletes = [], []
    import src.task_runtime.staging as staging_module
    original_delete = staging_module._safe_delete_owned_tree
    revoke_completed = threading.Event()
    original_revoke = rig.store._revoke_docking_preparation

    def observed_revoke(*args, **kwargs):
        result = original_revoke(*args, **kwargs)
        revoke_completed.set()
        return result

    monkeypatch.setattr(rig.store, "_revoke_docking_preparation", observed_revoke)

    def held_writer(*args, **kwargs):
        try:
            path = original_stage(*args, **kwargs)
            entered.set()
            if not release.wait(40):
                raise RuntimeError("test writer barrier not released")
            # Actual late file access: a detached/cancelled wrapper is not enough.
            writer_reads.append((path.parent / "inputs" / "receptor.pdb").read_bytes())
            return path
        finally:
            exited.set()

    def observed_delete(path):
        deletes.append(exited.is_set())
        if interrupt == "cleanup-failure":
            return False
        return original_delete(path)

    monkeypatch.setattr(rig.stager, "stage", held_writer)
    monkeypatch.setattr(staging_module, "_safe_delete_owned_tree", observed_delete)
    neighbor = rig.root / "not-c-owned"
    neighbor.mkdir()
    (neighbor / "keep").write_bytes(b"unrelated")

    async def run():
        caller = asyncio.create_task(prepare(rig))
        closer = None
        try:
            assert await asyncio.wait_for(asyncio.to_thread(entered.wait, 5), 6)
            assert not exited.is_set()
            if interrupt != "deadline":
                assert caller.cancel()
                if interrupt == "repeat-cancel":
                    asyncio.get_running_loop().call_soon(caller.cancel)
            # Deadline case exercises the actual fixed 30s policy, not a shorter
            # patched constant. 35s is solely a test watchdog, never request credit.
            outcome = (await asyncio.wait_for(asyncio.gather(caller, return_exceptions=True),
                                              35 if interrupt == "deadline" else 5))[0]
            if interrupt == "deadline":
                assert isinstance(outcome, rig.api.DockingConsentError)
                assert outcome.reason_code == "consent_preparation_timeout"
            else:
                assert isinstance(outcome, asyncio.CancelledError)
            # Caller completion is local revocation, not proof of a SQL receipt.
            # Preserve the historical REVOKED assertion after the real method
            # has returned, without waiting for/releasing the actual writer.
            assert await asyncio.to_thread(revoke_completed.wait, 5)
            record = rig.Store(rig.db).get_docking_consent(identity()["preparation_id"])
            assert record["state"] == "REVOKED" and record["cleanup_state"] == "pending"
            assert record["binding_digest"] is record["approval_nonce_hash"] is None
            assert not exited.is_set() and deletes == [] and (rig.root / identity()["task_id"]).exists()
            with pytest.raises(rig.api.DockingConsentError, match="consent_owner_busy"):
                reserve(rig, identity(2, owner=identity()["owner_session_id"]))
            for index in range(10, 25):
                reserve(rig, identity(index))
            with pytest.raises(rig.api.DockingConsentError, match="consent_capacity_full"):
                reserve(rig, identity(100))
            removed = await rig.runtime.cleanup_staging_once(cutoff_epoch=10**12)
            assert identity()["task_id"] not in removed and deletes == []
            closer = asyncio.create_task(rig.runtime.close())
            done, _ = await asyncio.wait({closer}, timeout=0.05)
            assert not done, "runtime released a still-live staging writer"
            release.set()
            if interrupt == "cleanup-failure":
                with pytest.raises(rig.api.DockingConsentError, match="consent_cleanup_unresolved"):
                    await asyncio.wait_for(asyncio.shield(closer), 5)
            else:
                await asyncio.wait_for(asyncio.shield(closer), 5)
            assert exited.is_set() and writer_reads == [RECEPTOR]
            assert deletes and all(deletes)
            final = rig.Store(rig.db).get_docking_consent(identity()["preparation_id"])
            assert final["state"] == "REVOKED"
            assert final["cleanup_state"] == ("unresolved" if interrupt == "cleanup-failure" else "settled")
            assert final["binding_digest"] is final["approval_nonce_hash"] is None
            assert (neighbor / "keep").read_bytes() == b"unrelated"
            if interrupt == "cleanup-failure":
                with pytest.raises(rig.api.DockingConsentError, match="consent_owner_busy"):
                    reserve(rig, identity(2, owner=identity()["owner_session_id"]))
                with pytest.raises(rig.api.DockingConsentError, match="consent_capacity_full"):
                    reserve(rig, identity(100))
            else:
                assert not (rig.root / identity()["task_id"]).exists()
                reserve(rig, identity(2, owner=identity()["owner_session_id"]))
            no_execution(rig)
        finally:
            release.set()
            if not caller.done():
                caller.cancel()
            await asyncio.gather(caller, return_exceptions=True)
            assert await asyncio.to_thread(exited.wait, 5)
            if closer is not None:
                await asyncio.gather(closer, return_exceptions=True)
            if interrupt == "cleanup-failure":
                # Keep the failure fact; remove the injected failure only for
                # fixture teardown, never to satisfy the assertions above.
                monkeypatch.setattr(staging_module, "_safe_delete_owned_tree", original_delete)
                await asyncio.gather(rig.runtime.close(), return_exceptions=True)
            else:
                await rig.runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize("name,value", [
    ("smiles", "CCO"), ("receptor_path", "/private/receptor.pdb"),
    ("runtime_config", {}), ("backend", "local"), ("timeout", 420),
    ("task_id", "replacement-task"), ("approval_nonce", "not-consent"),
])
def test_c1_prepare_signature_has_no_execution_or_authority_overrides(tmp_path, monkeypatch, name, value):
    rig = make_rig(tmp_path, monkeypatch)
    reserve(rig)

    async def run():
        try:
            with pytest.raises(TypeError):
                await prepare(rig, **{name: value})
        finally:
            await rig.runtime.close()

    asyncio.run(run())
    assert rig.stages == []
    no_execution(rig)


@pytest.mark.parametrize("basename_bytes", [100, 101])
def test_c1_reuses_existing_portable_filename_byte_limit(tmp_path_factory, monkeypatch, basename_bytes):
    rig = make_rig(tmp_path_factory.mktemp("c1-name"), monkeypatch)
    reserve(rig)
    name = "r" * (basename_bytes - 4) + ".pdb"
    assert len(name.encode("utf-8")) == basename_bytes
    import src.task_runtime.staging as staging_module
    # Isolate the basename boundary from the separate full-path limit. The
    # temporary component has the actual stager's prefix and16 hex characters;
    # its random value does not affect path length. No stager limit is patched.
    for directory in (identity()["task_id"], staging_module._STAGE_PREFIX + "0" * 16):
        candidate = rig.root / directory / "inputs" / name
        path_chars = len(str(candidate))
        assert path_chars <= staging_module._MAX_WINDOWS_PATH_CHARS

    async def run():
        try:
            if basename_bytes == 101:
                with pytest.raises(rig.api.DockingConsentError, match="consent_invalid_input"):
                    await prepare(rig, receptor_name=name)
            else:
                result = await prepare(rig, receptor_name=name)
                record = rig.store.get_docking_consent(result.to_dict()["preparation_id"])
                assert record["binding"]["receptor"]["name"] == name
        finally:
            await rig.runtime.close()

    asyncio.run(run())
    no_execution(rig)


def test_c1_unique_task_identity_and_cross_owner_equal_inputs_never_share_authority(tmp_path, monkeypatch):
    rig = make_rig(tmp_path, monkeypatch)
    left, right = identity(1), identity(2)
    reserve(rig, left)
    collision = {**right, "task_id": left["task_id"]}
    with pytest.raises(rig.api.DockingConsentError, match="consent_identity_conflict"):
        reserve(rig, collision, store=rig.Store(rig.db))
    reserve(rig, right, store=rig.Store(rig.db))

    async def run():
        try:
            return await prepare(rig, left), await prepare(rig, right)
        finally:
            await rig.runtime.close()

    previews = [item.to_dict() for item in asyncio.run(run())]
    records = [rig.Store(rig.db).get_docking_consent(item["preparation_id"]) for item in (left, right)]
    assert records[0]["binding"]["input_hash"] == records[1]["binding"]["input_hash"]
    assert records[0]["binding"]["request_digest"] == records[1]["binding"]["request_digest"]
    assert records[0]["binding_digest"] != records[1]["binding_digest"]
    assert previews[0]["approval_nonce"] != previews[1]["approval_nonce"]
    assert [item["task_id"] for item in previews] == [left["task_id"], right["task_id"]]
    assert rig.stages == [left["task_id"], right["task_id"]]
    no_execution(rig)


def _blocked_revocation_case(tmp_path, monkeypatch, interrupt, release_order):
    """Hold real store calls, not a fake SQL algorithm or a SQLite-lock claim."""
    rig = make_rig(tmp_path, monkeypatch)
    reserve(rig)
    other_store = rig.Store(rig.db)
    draft = identity()
    writer_entered, writer_release, writer_exited = (
        threading.Event(), threading.Event(), threading.Event()
    )
    revoke_entered, revoke_release = threading.Event(), threading.Event()
    calls_lock = threading.Lock()
    revoke_calls, seal_calls, writer_reads, deletes, barrier_errors = [], [], [], [], []
    original_stage = rig.stager.stage
    original_revoke = rig.Store._revoke_docking_preparation
    original_seal = rig.Store._seal_docking_preparation
    import src.task_runtime.staging as staging_module
    original_delete = staging_module._safe_delete_owned_tree
    neighbor = rig.root / "unowned-sql-barrier-neighbor"
    neighbor.mkdir()
    (neighbor / "keep").write_bytes(b"not C owned")

    def held_writer(*args, **kwargs):
        try:
            path = original_stage(*args, **kwargs)
            writer_entered.set()
            # Finite fixture escape only; actual production deadline stays30s.
            if not writer_release.wait(60):
                barrier_errors.append("writer fixture escape")
                raise RuntimeError("writer fixture barrier not released")
            writer_reads.append((path.parent / "inputs" / "receptor.pdb").read_bytes())
            return path
        finally:
            writer_exited.set()

    def held_revoke(store, preparation_id, token):
        call = {"preparation_id": preparation_id,
                "returned": threading.Event(), "exited": threading.Event()}
        with calls_lock:
            revoke_calls.append(call)
        revoke_entered.set()
        try:
            if not revoke_release.wait(60):
                barrier_errors.append("revoke fixture escape")
                raise RuntimeError("revoke fixture barrier not released")
            result = original_revoke(store, preparation_id, token)
            call["returned"].set()  # Only after the actual SQLite method returns.
            return result
        finally:
            call["exited"].set()

    def observed_delete(path):
        with calls_lock:
            receipt_done = bool(revoke_calls) and all(
                call["returned"].is_set() for call in revoke_calls
            )
        deletes.append((writer_exited.is_set(), receipt_done))
        return original_delete(path)

    def observed_seal(store, *args, **kwargs):
        seal_calls.append("seal")
        return original_seal(store, *args, **kwargs)

    monkeypatch.setattr(rig.stager, "stage", held_writer)
    # Class-level wrapping covers caller, settlement and shutdown invocations,
    # including new Store instances. No competing revocation can bypass the hold.
    monkeypatch.setattr(rig.Store, "_revoke_docking_preparation", held_revoke)
    monkeypatch.setattr(rig.Store, "_seal_docking_preparation", observed_seal)
    monkeypatch.setattr(staging_module, "_safe_delete_owned_tree", observed_delete)

    async def run():
        caller = asyncio.create_task(prepare(rig))
        closers = []
        try:
            assert await asyncio.to_thread(writer_entered.wait, 5)
            before = other_store.get_docking_consent(draft["preparation_id"])
            assert before["state"] == "PREPARING" and before["cleanup_state"] == "pending"
            assert before["binding_digest"] is before["approval_nonce_hash"] is None
            assert not writer_exited.is_set()

            if interrupt == "none":
                writer_release.set()
                done, _ = await asyncio.wait({caller}, timeout=5)
                assert caller in done, "normal prepare did not complete after writer release"
                preview = caller.result()
                assert isinstance(preview, rig.api.DockingConsentPreview)
                public = preview.to_dict()
                ready = other_store.get_docking_consent(draft["preparation_id"])
                assert ready["state"] == "READY"
                expected = expected_binding(draft, issued_ms=rig.clock.wall, selected_policy=rig.policy)
                assert ready["binding"] == expected
                assert ready["binding_digest"] == hashlib.sha256(
                    b"medchat-docking-consent-v1\0" + canonical(expected)
                ).hexdigest()
                assert ready["approval_nonce_hash"] == hashlib.sha256(
                    public["approval_nonce"].encode("ascii")
                ).hexdigest()
                await rig.runtime.close()
                assert writer_exited.is_set() and writer_reads == [RECEPTOR]
                assert not revoke_entered.is_set() and revoke_calls == [] and deletes == []
                assert seal_calls == ["seal"]
                assert (rig.root / draft["task_id"] / "input_manifest.json").exists()
                no_execution(rig)
                return

            if interrupt != "deadline":
                assert caller.cancel()
                if interrupt == "repeat-cancel":
                    asyncio.get_running_loop().call_soon(caller.cancel)
            # Observe only: unlike wait_for, timeout cannot cancel caller into
            # an apparent pass. Deadline arm waits the unchanged actual30s path.
            done, _ = await asyncio.wait({caller}, timeout=35 if interrupt == "deadline" else 5)
            assert await asyncio.to_thread(revoke_entered.wait, 5)
            assert not revoke_release.is_set() and not writer_release.is_set()
            with calls_lock:
                observed = tuple(revoke_calls)
            assert observed and all(not call["returned"].is_set() for call in observed)
            assert all(call["preparation_id"] == draft["preparation_id"] for call in observed)
            assert not writer_exited.is_set()
            # Establish actual DB facts before the expected behavioral RED.
            blocked = other_store.get_docking_consent(draft["preparation_id"])
            assert blocked == before
            assert caller in done, "prepare caller awaited blocked durable revocation receipt"
            outcome = (await asyncio.gather(caller, return_exceptions=True))[0]
            if interrupt == "deadline":
                assert isinstance(outcome, rig.api.DockingConsentError)
                assert outcome.reason_code == "consent_preparation_timeout"
            else:
                assert isinstance(outcome, asyncio.CancelledError)

            # Honest independent DB observation while every revoke is held:
            # locally cancelled != committed REVOKED. No new view API is invented.
            assert len(observed) == 1, "revocation must be one shared retained operation"
            with pytest.raises(rig.api.DockingConsentError, match="consent_owner_busy"):
                reserve(rig, identity(2, owner=draft["owner_session_id"]), store=other_store)
            for index in range(10, 25):
                reserve(rig, identity(index), store=other_store)
            with pytest.raises(rig.api.DockingConsentError, match="consent_capacity_full"):
                reserve(rig, identity(100), store=other_store)
            removed = await rig.runtime.cleanup_staging_once(cutoff_epoch=10**12)
            assert draft["task_id"] not in removed and deletes == []

            closer = asyncio.create_task(rig.runtime.close())
            closers.append(closer)
            done, _ = await asyncio.wait({closer}, timeout=0.05)
            assert not done
            if interrupt == "repeat-cancel":
                assert closer.cancel()
                asyncio.get_running_loop().call_soon(closer.cancel)
                closed_outcome = (await asyncio.gather(closer, return_exceptions=True))[0]
                assert isinstance(closed_outcome, asyncio.CancelledError)
                closer = asyncio.create_task(rig.runtime.close())
                closers.append(closer)

            if release_order == "sql-first":
                revoke_release.set()
                assert await asyncio.to_thread(observed[0]["returned"].wait, 5)
                middle = other_store.get_docking_consent(draft["preparation_id"])
                assert middle["state"] == "REVOKED" and middle["cleanup_state"] == "pending"
                assert not writer_exited.is_set()
            else:
                writer_release.set()
                assert await asyncio.to_thread(writer_exited.wait, 5)
                assert writer_reads == [RECEPTOR]
                assert other_store.get_docking_consent(draft["preparation_id"]) == before
                assert not observed[0]["returned"].is_set()
            done, _ = await asyncio.wait({closer}, timeout=0.05)
            assert not done, "shutdown returned with an actual writer or SQL owner outstanding"
            assert deletes == [] and (rig.root / draft["task_id"]).exists()
            with pytest.raises(rig.api.DockingConsentError, match="consent_owner_busy"):
                reserve(rig, identity(2, owner=draft["owner_session_id"]), store=other_store)
            with pytest.raises(rig.api.DockingConsentError, match="consent_capacity_full"):
                reserve(rig, identity(100), store=other_store)

            writer_release.set()
            revoke_release.set()
            done, _ = await asyncio.wait({closer}, timeout=5)
            assert closer in done
            closer.result()
            final = other_store.get_docking_consent(draft["preparation_id"])
            assert final["state"] == "REVOKED" and final["cleanup_state"] == "settled"
            assert final["binding"] is final["binding_digest"] is final["approval_nonce_hash"] is None
            assert seal_calls == [], "late staging result attempted READY/nonce publication"
            assert writer_reads == [RECEPTOR] and writer_exited.is_set()
            with calls_lock:
                finished_calls = tuple(revoke_calls)
            assert len(finished_calls) == 1
            assert finished_calls[0]["returned"].is_set() and finished_calls[0]["exited"].is_set()
            assert deletes and all(writer_done and sql_done for writer_done, sql_done in deletes)
            assert not (rig.root / draft["task_id"]).exists()
            assert (neighbor / "keep").read_bytes() == b"not C owned"
            reserve(rig, identity(2, owner=draft["owner_session_id"]), store=other_store)
            no_execution(rig)
        finally:
            # Release BEFORE any join/assertion, including expected RED failures.
            # Runtime.close is the real owner drain, not a mocked success receipt.
            writer_release.set()
            revoke_release.set()
            if not caller.done():
                caller.cancel()
            await asyncio.gather(caller, return_exceptions=True)
            await asyncio.gather(*closers, return_exceptions=True)
            await rig.runtime.close()
            writer_done = await asyncio.to_thread(writer_exited.wait, 5)
            with calls_lock:
                all_calls = tuple(revoke_calls)
            sql_done = [await asyncio.to_thread(call["exited"].wait, 5) for call in all_calls]
            assert writer_done and all(sql_done) and barrier_errors == []

    # asyncio.run also joins the default executor's real threads on exit; the
    # per-call exit events above prove physical functions returned before teardown.
    asyncio.run(run())


@pytest.mark.parametrize("interrupt", ["cancel", "repeat-cancel", "deadline"])
@pytest.mark.parametrize("release_order", ["sql-first", "writer-first"])
def test_c1_blocked_revocation_returns_before_receipt_and_retains_owners(
    tmp_path, monkeypatch, interrupt, release_order,
):
    _blocked_revocation_case(tmp_path, monkeypatch, interrupt, release_order)


def test_c1_blocked_revocation_fixture_preserves_normal_success(tmp_path, monkeypatch):
    _blocked_revocation_case(tmp_path, monkeypatch, "none", None)


@pytest.mark.parametrize("fault", [
    "revoke-pre-throw", "revoke-post-commit-throw", "revoke-zero-match",
    "finish-pre-throw", "finish-post-commit-throw", "delete-failure", "normal",
])
def test_c1_receipt_faults_preserve_actual_sql_and_cleanup_facts(tmp_path, monkeypatch, fault):
    """Faults surround real methods; no owner/receipt mutation or fake False."""
    rig = make_rig(tmp_path, monkeypatch)
    reserve(rig)
    independent = rig.Store(rig.db)
    draft = identity()
    for index in range(10, 25):
        reserve(rig, identity(index), store=independent)
    entered, release, exited = threading.Event(), threading.Event(), threading.Event()
    revoke_exited, finish_exited = threading.Event(), threading.Event()
    original_stage = rig.stager.stage
    original_revoke = rig.Store._revoke_docking_preparation
    original_finish = rig.Store._finish_docking_cleanup
    import src.task_runtime.staging as staging_module
    original_delete = staging_module._safe_delete_owned_tree
    original_unlink = staging_module.os.unlink
    deleting = threading.local()
    revoke_calls, finish_calls, delete_calls = [], [], []
    unlink_faults, writer_reads, barrier_errors = [], [], []
    neighbor = rig.root / "receipt-neighbor"
    neighbor.mkdir()
    (neighbor / "keep").write_bytes(b"not C owned")

    def snapshot():
        # Each getter opens an independent real SQLite connection.
        return independent.get_docking_consent(draft["preparation_id"])

    def held_stage(*args, **kwargs):
        try:
            path = original_stage(*args, **kwargs)
            entered.set()
            if not release.wait(20):
                barrier_errors.append("writer fixture escape")
                raise RuntimeError("receipt test writer barrier not released")
            writer_reads.append((path.parent / "inputs" / "receptor.pdb").read_bytes())
            return path
        finally:
            exited.set()

    def revoke(store, preparation_id, token):
        call = {"before": snapshot(), "delegated": False, "result": None, "after": None}
        revoke_calls.append(call)
        try:
            if fault == "revoke-pre-throw":
                raise RuntimeError("injected revoke pre-call failure")
            supplied_token = token
            if fault == "revoke-zero-match":
                supplied_token = "0" * 64 if token != "0" * 64 else "1" * 64
            call["delegated"] = True
            # A wrong-token UPDATE on the real DB must produce the zero match.
            # Do not replace the store return value or mutate the persisted owner.
            call["result"] = original_revoke(store, preparation_id, supplied_token)
            call["after"] = snapshot()
            if fault == "revoke-post-commit-throw":
                raise RuntimeError("injected revoke post-commit failure")
            return call["result"]
        finally:
            revoke_exited.set()

    def finish(store, preparation_id, token, *, settled):
        call = {"requested_settled": settled, "before": snapshot(),
                "delegated": False, "after": None,
                "delete_results_at_entry": tuple(item.get("result") for item in delete_calls)}
        finish_calls.append(call)
        try:
            if fault == "finish-pre-throw":
                raise RuntimeError("injected finish pre-call failure")
            call["delegated"] = True
            result = original_finish(store, preparation_id, token, settled=settled)
            call["after"] = snapshot()
            if fault == "finish-post-commit-throw":
                raise RuntimeError("injected finish post-commit failure")
            return result
        finally:
            finish_exited.set()

    def observed_delete(path):
        call = {"writer_exited": exited.is_set(), "revoke_exited": revoke_exited.is_set(),
                "own_quarantine": path.parent == rig.root / ".trash"
                and path.name.startswith(draft["task_id"] + "-")}
        delete_calls.append(call)
        deleting.active = call["own_quarantine"]
        try:
            # Always run the actual marker/link checks, rmtree and outcome logic.
            result = original_delete(path)
            call["result"] = result
            call["residue_exists"] = path.exists()
            call["receptor_remains"] = (path / "inputs" / "receptor.pdb").is_file()
            call["ownership_marker"] = (path / ".medchat-staging-owner").is_file()
            return result
        finally:
            deleting.active = False

    def guarded_unlink(path, *args, **kwargs):
        # Both installed shutil rmtree implementations delegate to os.unlink;
        # restrict the fault to this thread's actual owned-tree receptor removal.
        if (fault == "delete-failure" and getattr(deleting, "active", False)
                and Path(path).name == "receptor.pdb"):
            unlink_faults.append("owned-receptor-unlink")
            raise PermissionError("injected owned-file deletion failure")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(rig.stager, "stage", held_stage)
    monkeypatch.setattr(rig.Store, "_revoke_docking_preparation", revoke)
    monkeypatch.setattr(rig.Store, "_finish_docking_cleanup", finish)
    monkeypatch.setattr(staging_module, "_safe_delete_owned_tree", observed_delete)
    monkeypatch.setattr(staging_module.os, "unlink", guarded_unlink)

    async def run():
        caller = asyncio.create_task(prepare(rig))
        closer = None
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            before = snapshot()
            assert before["state"] == "PREPARING" and before["cleanup_state"] == "pending"
            assert caller.cancel()
            done, _ = await asyncio.wait({caller}, timeout=5)
            assert caller in done
            outcome = (await asyncio.gather(caller, return_exceptions=True))[0]
            assert isinstance(outcome, asyncio.CancelledError)
            assert await asyncio.to_thread(revoke_exited.wait, 5)
            assert len(revoke_calls) == 1 and delete_calls == [] and not exited.is_set()
            with pytest.raises(rig.api.DockingConsentError, match="consent_owner_busy"):
                reserve(rig, identity(2, owner=draft["owner_session_id"]), store=independent)
            with pytest.raises(rig.api.DockingConsentError, match="consent_capacity_full"):
                reserve(rig, identity(100), store=independent)
            closer = asyncio.create_task(rig.runtime.close())
            done, _ = await asyncio.wait({closer}, timeout=0.05)
            assert not done, "fault receipt released a still-live actual writer"
            release.set()
            done, _ = await asyncio.wait({closer}, timeout=5)
            assert closer in done
            close_outcome = (await asyncio.gather(closer, return_exceptions=True))[0]
            if fault == "normal":
                assert close_outcome is None
            else:
                assert isinstance(close_outcome, rig.api.DockingConsentError)
                assert close_outcome.reason_code == "consent_cleanup_unresolved"
                assert str(close_outcome) == "consent_cleanup_unresolved"
            assert exited.is_set() and writer_reads == [RECEPTOR]
            assert finish_exited.is_set() and len(finish_calls) == 1 and len(revoke_calls) == 1
            final = snapshot()
            assert final["identity"] == before["identity"]
            assert final["binding"] is final["binding_digest"] is final["approval_nonce_hash"] is None
            assert (neighbor / "keep").read_bytes() == b"not C owned"

            revoked = revoke_calls[0]
            finished = finish_calls[0]
            if fault == "revoke-pre-throw":
                assert revoked["delegated"] is False and revoked["after"] is None
                assert final == before  # No durable revocation or terminal cleanup UPDATE.
            elif fault == "revoke-zero-match":
                assert revoked["delegated"] is True and revoked["result"] is False
                assert revoked["after"] == revoked["before"] == before
                assert final == before
            else:
                assert revoked["delegated"] is True and revoked["result"] is True
                assert revoked["after"]["state"] == "REVOKED"
                assert revoked["after"]["cleanup_state"] == "pending"
                assert final["state"] == "REVOKED"

            if fault.startswith("revoke-"):
                assert delete_calls == [] and finished["requested_settled"] is False
                assert finished["delete_results_at_entry"] == ()
                assert (rig.root / draft["task_id"] / "inputs" / "receptor.pdb").read_bytes() == RECEPTOR
                assert final["cleanup_state"] == (
                    "unresolved" if fault == "revoke-post-commit-throw" else "pending")
            else:
                assert len(delete_calls) == 1
                deletion = delete_calls[0]
                assert deletion["writer_exited"] and deletion["revoke_exited"] and deletion["own_quarantine"]
                if fault == "delete-failure":
                    assert unlink_faults == ["owned-receptor-unlink"]
                    assert deletion["result"] is False and deletion["residue_exists"]
                    assert deletion["receptor_remains"] and deletion["ownership_marker"]
                    assert finished["requested_settled"] is False
                    assert finished["delete_results_at_entry"] == (False,)
                    assert final["cleanup_state"] == "unresolved"
                else:
                    assert unlink_faults == [] and deletion["result"] is True
                    assert not deletion["residue_exists"] and finished["requested_settled"] is True
                    assert finished["delete_results_at_entry"] == (True,)
                    assert not (rig.root / draft["task_id"]).exists()
                    if fault == "finish-pre-throw":
                        assert finished["delegated"] is False and finished["after"] is None
                        assert final["cleanup_state"] == "pending"
                    else:
                        assert finished["delegated"] is True
                        assert finished["after"]["cleanup_state"] == final["cleanup_state"] == "settled"
                        # Real deletion + committed finish legitimately free DB
                        # capacity, even when the post-commit exception makes the
                        # local close uncertain. Never assert a fictional rollback.

            if fault in {"normal", "finish-post-commit-throw"}:
                reserve(rig, identity(2, owner=draft["owner_session_id"]), store=independent)
            else:
                with pytest.raises(rig.api.DockingConsentError, match="consent_owner_busy"):
                    reserve(rig, identity(2, owner=draft["owner_session_id"]), store=independent)
                with pytest.raises(rig.api.DockingConsentError, match="consent_capacity_full"):
                    reserve(rig, identity(100), store=independent)
            no_execution(rig)
        finally:
            # No owner/receipt mutation, manual DB rollback or fake cleanup.
            # Release the actual writer before every join, including failing cases.
            release.set()
            if not caller.done():
                caller.cancel()
            await asyncio.gather(caller, return_exceptions=True)
            if closer is not None:
                await asyncio.gather(closer, return_exceptions=True)
            # Unresolved close may truthfully fail again; its real owners must
            # nevertheless already be physically drained, without retrying revoke.
            await asyncio.gather(rig.runtime.close(), return_exceptions=True)
            assert await asyncio.to_thread(exited.wait, 5)
            if revoke_calls:
                assert revoke_exited.is_set() and len(revoke_calls) == 1
            if finish_calls:
                assert finish_exited.is_set() and len(finish_calls) == 1
            assert barrier_errors == []

    asyncio.run(run())  # Joins default-executor threads after the real owner drain.


def _transaction_boundary_case(tmp_path, monkeypatch, phase, boundary, outcome):
    """Observe real UPDATE/COMMIT/ROLLBACK, not a method-entry fault surrogate."""
    from contextlib import contextmanager
    import src.task_runtime.store as store_module
    import src.task_runtime.staging as staging_module

    rig = make_rig(tmp_path, monkeypatch)
    reserve(rig)
    draft = identity()
    entered, release = threading.Event(), threading.Event()
    original_connection = store_module.connection
    original_stage = rig.stager.stage
    original_delete = staging_module._safe_delete_owned_tree
    transactions, selected, stage_calls, deletes, barrier_errors = [], [], [], [], []
    neighbor = rig.root / "transaction-neighbor"
    neighbor.mkdir()
    (neighbor / "keep").write_bytes(b"not C owned")

    def snapshot():
        # Read-only, independent native connection: never the transaction proxy
        # and never a competing test write while the target write lock is held.
        conn = sqlite3.connect(rig.db)
        try:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """SELECT state,version,cleanup_state,identity_json,binding_json,
                binding_digest,approval_nonce_hash FROM docking_consents WHERE preparation_id=?""",
                (draft["preparation_id"],),
            ).fetchone()
        finally:
            conn.close()
        return dict(row)

    initial = snapshot()

    def hold(tx):
        tx["visible_at_hold"] = snapshot()
        entered.set()
        if not release.wait(60):
            barrier_errors.append("transaction fixture escape")
            raise RuntimeError("transaction fixture barrier not released")
        if outcome == "throw":
            raise RuntimeError("injected transaction boundary exception")

    class ConnectionObserver:
        def __init__(self, actual, tx):
            self.actual, self.tx = actual, tx

        def execute(self, sql, *args, **kwargs):
            cursor = self.actual.execute(sql, *args, **kwargs)
            normalized = " ".join(sql.split())
            kind = None
            if normalized.startswith("UPDATE docking_consents SET state='PREPARING',"):
                kind = "begin"
            elif normalized.startswith("UPDATE docking_consents SET state='READY',"):
                kind = "seal"
            elif normalized.startswith("UPDATE docking_consents SET state='EXPIRED',"):
                kind = "expiry"
            if kind is not None:
                self.tx["kind"] = kind
                self.tx["rowcount"] = cursor.rowcount
                transactions.append(self.tx)
                if kind == phase:
                    selected.append(self.tx)
            return cursor  # The real cursor/result, including the real rowcount.

        def __getattr__(self, name):
            return getattr(self.actual, name)

    @contextmanager
    def observed_connection(*args, **kwargs):
        tx = {"kind": None, "rowcount": None, "trace": [], "committed": False,
              "visible_at_hold": None, "visible_after_commit": None,
              "visible_after_rollback": None, "exited": threading.Event()}
        try:
            # The original context manager performs its own real commit/rollback
            # and close. Never call a fake commit or roll back in test teardown.
            with original_connection(*args, **kwargs) as actual:
                def trace(statement):
                    command = statement.strip().upper()
                    if command in {"COMMIT", "ROLLBACK"}:
                        tx["trace"].append(command)  # No SQL payload/token logging.

                actual.set_trace_callback(trace)
                yield ConnectionObserver(actual, tx)
                if tx["kind"] == phase and boundary == "pre-commit":
                    hold(tx)  # UPDATE executed; original commit has NOT run yet.
            tx["committed"] = True  # Set only after original __exit__ succeeds.
            if tx["kind"] == phase:
                tx["visible_after_commit"] = snapshot()
                if boundary == "post-commit":
                    hold(tx)  # The actual commit/close already returned.
        except BaseException:
            if tx["kind"] == phase and "ROLLBACK" in tx["trace"]:
                tx["visible_after_rollback"] = snapshot()
            raise
        finally:
            tx["exited"].set()

    def observed_stage(*args, **kwargs):
        stage_calls.append("stage")
        return original_stage(*args, **kwargs)

    def observed_delete(path):
        # Cleanup cannot race a live DB operation even if its caller returned.
        entry = {"target_exited": bool(selected) and all(tx["exited"].is_set() for tx in selected)}
        deletes.append(entry)
        result = original_delete(path)
        entry["result"] = result
        entry["residue_exists"] = path.exists()
        return result

    monkeypatch.setattr(store_module, "connection", observed_connection)
    monkeypatch.setattr(rig.stager, "stage", observed_stage)
    monkeypatch.setattr(staging_module, "_safe_delete_owned_tree", observed_delete)

    async def run():
        caller = asyncio.create_task(prepare(rig))
        closer = None
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            assert len(selected) == 1
            tx = selected[0]
            assert tx["kind"] == phase and tx["rowcount"] == 1 and not tx["exited"].is_set()
            assert any(item["kind"] == "expiry" and item["committed"] for item in transactions)
            # The begin expiry transaction is real, but NEVER the held/injected one.
            visible = tx["visible_at_hold"]
            if boundary == "pre-commit":
                assert tx["trace"] == [] and tx["committed"] is False
                assert visible["state"] == ("AWAITING_INPUT" if phase == "begin" else "PREPARING")
                assert visible["binding_digest"] is visible["approval_nonce_hash"] is None
            else:
                assert tx["trace"] == ["COMMIT"] and tx["committed"] is True
                assert visible == tx["visible_after_commit"]
                assert visible["state"] == ("PREPARING" if phase == "begin" else "READY")
                if phase == "seal":
                    assert visible["binding_digest"] is not None and visible["approval_nonce_hash"] is not None
            assert not caller.done() and deletes == []
            assert stage_calls == ([] if phase == "begin" else ["stage"])

            if outcome in {"cancel", "deadline"}:
                if outcome == "cancel":
                    assert caller.cancel()
                done, _ = await asyncio.wait({caller}, timeout=35 if outcome == "deadline" else 5)
                assert caller in done, "caller waited for the held actual begin/seal transaction"
                result = (await asyncio.gather(caller, return_exceptions=True))[0]
                if outcome == "cancel":
                    assert isinstance(result, asyncio.CancelledError)
                else:
                    assert isinstance(result, rig.api.DockingConsentError)
                    assert result.reason_code == "consent_preparation_timeout"
                # A post-commit cancellation may already have durably revoked;
                # do not relabel its earlier actual READY receipt a rollback.
                if boundary == "pre-commit":
                    assert snapshot() == visible  # WAL read; no test writes.
                closer = asyncio.create_task(rig.runtime.close())
                done, _ = await asyncio.wait({closer}, timeout=0.05)
                assert not done and not tx["exited"].is_set() and deletes == []
                if phase == "seal":
                    assert (rig.root / draft["task_id"] / "inputs" / "receptor.pdb").read_bytes() == RECEPTOR
                release.set()
                done, _ = await asyncio.wait({closer}, timeout=5)
                assert closer in done
                closer.result()
            else:
                release.set()
                done, _ = await asyncio.wait({caller}, timeout=5)
                assert caller in done
                result = (await asyncio.gather(caller, return_exceptions=True))[0]
                if outcome == "throw":
                    assert isinstance(result, rig.api.DockingConsentError)
                    assert str(result) == result.reason_code == "consent_invalid_input"
                else:
                    assert isinstance(result, rig.api.DockingConsentPreview)
                await rig.runtime.close()

            assert tx["exited"].is_set() and barrier_errors == []
            final = snapshot()
            assert final["identity_json"] == initial["identity_json"]
            if outcome == "throw" and boundary == "pre-commit":
                assert tx["committed"] is False and tx["trace"] == ["ROLLBACK"]
                assert tx["visible_after_rollback"] == visible
            else:
                assert tx["committed"] is True and tx["trace"] == ["COMMIT"]
                assert tx["visible_after_commit"] is not None
            if outcome == "normal":
                assert final["state"] == "READY" and deletes == []
                public = result.to_dict()
                expected = expected_binding(draft, issued_ms=rig.clock.wall, selected_policy=rig.policy)
                assert json.loads(final["binding_json"]) == expected
                assert final["binding_digest"] == hashlib.sha256(
                    b"medchat-docking-consent-v1\0" + canonical(expected)
                ).hexdigest()
                assert final["approval_nonce_hash"] == hashlib.sha256(
                    public["approval_nonce"].encode("ascii")
                ).hexdigest()
                assert public["binding_digest"] == final["binding_digest"]
            else:
                assert not isinstance(result, rig.api.DockingConsentPreview)
                assert caller.done() and final["approval_nonce_hash"] is None
                if phase == "begin" and outcome == "throw" and boundary == "pre-commit":
                    assert final == initial  # Real rollback: original draft remains.
                    assert stage_calls == [] and deletes == []
                else:
                    assert final["state"] == "REVOKED" and final["cleanup_state"] == "settled"
                    if phase == "begin":
                        assert stage_calls == [] and deletes == []
                        assert final["binding_json"] is final["binding_digest"] is None
                    else:
                        assert stage_calls == ["stage"] and len(deletes) == 1
                        assert deletes[0] == {"target_exited": True, "result": True, "residue_exists": False}
                        assert not (rig.root / draft["task_id"]).exists()
                        if tx["committed"]:
                            # Historical committed binding is not erased to hide
                            # a lost response; it does not authorize a late preview.
                            expected = expected_binding(draft, issued_ms=rig.clock.wall, selected_policy=rig.policy)
                            assert json.loads(final["binding_json"]) == expected
                        else:
                            assert final["binding_json"] is final["binding_digest"] is None
            assert (neighbor / "keep").read_bytes() == b"not C owned"
            no_execution(rig)
        finally:
            # Unblock commit BEFORE joins, including any expected RED path.
            # Never issue competing test writes, fake a receipt or manually roll back.
            release.set()
            if not caller.done():
                caller.cancel()
            await asyncio.gather(caller, return_exceptions=True)
            if closer is not None:
                await asyncio.gather(closer, return_exceptions=True)
            close_result = (await asyncio.gather(rig.runtime.close(), return_exceptions=True))[0]
            assert all(tx["exited"].is_set() for tx in transactions)
            assert barrier_errors == []
            assert close_result is None

    asyncio.run(run())  # Real retained owners drain, then default executor joins.


@pytest.mark.parametrize("phase", ["begin", "seal"])
@pytest.mark.parametrize("boundary", ["pre-commit", "post-commit"])
@pytest.mark.parametrize("outcome", ["cancel", "deadline"])
def test_c1_transaction_boundary_interrupt_retains_actual_owner(tmp_path, monkeypatch, phase, boundary, outcome):
    _transaction_boundary_case(tmp_path, monkeypatch, phase, boundary, outcome)


def test_c1_transaction_boundary_normal_control(tmp_path, monkeypatch):
    _transaction_boundary_case(tmp_path, monkeypatch, "seal", "post-commit", "normal")


@pytest.mark.parametrize("phase", ["begin", "seal"])
@pytest.mark.parametrize("boundary", ["pre-commit", "post-commit"])
def test_c1_transaction_boundary_exception_preserves_real_commit_outcome(tmp_path, monkeypatch, phase, boundary):
    _transaction_boundary_case(tmp_path, monkeypatch, phase, boundary, "throw")


def test_c1_cancel_during_actual_verification_retains_reader_before_cleanup(tmp_path, monkeypatch):
    import src.task_runtime.staging as staging_module

    rig = make_rig(tmp_path, monkeypatch)
    draft = identity()
    reserve(rig)
    for index in range(10, 25):
        reserve(rig, identity(index))
    independent = rig.Store(rig.db)
    receptor = rig.root / draft["task_id"] / "inputs" / "receptor.pdb"
    neighbor = rig.root / "verification-neighbor"
    neighbor.mkdir()
    (neighbor / "keep").write_bytes(b"not C owned")
    stage_returned, entered, release = threading.Event(), threading.Event(), threading.Event()
    verify_started, verify_exited, revoke_exited = threading.Event(), threading.Event(), threading.Event()
    scope = threading.local()
    reads, verified, cleanup_entries, deletions, seals, barrier_errors = [], [], [], [], [], []
    original_stage = rig.stager.stage
    original_verify = rig.stager.load_verified_locator
    original_secure_read = staging_module._secure_read_file
    original_read = staging_module.os.read
    original_discard = rig.stager.discard_unprojected
    original_delete = staging_module._safe_delete_owned_tree
    original_seal = rig.store._seal_docking_preparation
    original_revoke = rig.store._revoke_docking_preparation

    def observed_stage(*args, **kwargs):
        result = original_stage(*args, **kwargs)
        stage_returned.set()
        return result

    def observed_verify(*args, **kwargs):
        assert stage_returned.is_set()
        verify_started.set()
        scope.verifying = True
        try:
            result = original_verify(*args, **kwargs)
            verified.append(result)  # The actual validated manifest, unchanged.
            return result
        finally:
            scope.verifying = False
            verify_exited.set()

    def scoped_secure_read(path, **kwargs):
        previous = getattr(scope, "receptor_read", False)
        scope.receptor_read = getattr(scope, "verifying", False) and path == receptor
        try:
            return original_secure_read(path, **kwargs)
        finally:
            scope.receptor_read = previous

    def held_read(descriptor, size):
        if not getattr(scope, "receptor_read", False):
            return original_read(descriptor, size)
        if not entered.is_set():
            # The real secure reader already opened and checked the receptor
            # descriptor. Hold its actual read, not stage's own verification or
            # a precomputed/fabricated manifest returned by a wrapper.
            entered.set()
            if not release.wait(40):
                barrier_errors.append("verification fixture escape")
                raise RuntimeError("verification fixture barrier not released")
        chunk = original_read(descriptor, size)
        reads.append(chunk)
        return chunk

    def observed_discard(*args, **kwargs):
        # Record BEFORE delegating: an early cleanup call blocked by the stager
        # lock must fail this test, even if no physical deletion happened yet.
        cleanup_entries.append(verify_exited.is_set())
        return original_discard(*args, **kwargs)

    def observed_delete(path):
        result = original_delete(path)
        deletions.append((verify_exited.is_set(), result, path.exists()))
        return result

    def observed_seal(*args, **kwargs):
        seals.append("seal")
        return original_seal(*args, **kwargs)

    def observed_revoke(*args, **kwargs):
        result = original_revoke(*args, **kwargs)
        revoke_exited.set()  # Only after the real durable method returns.
        return result

    monkeypatch.setattr(rig.stager, "stage", observed_stage)
    monkeypatch.setattr(rig.stager, "load_verified_locator", observed_verify)
    monkeypatch.setattr(staging_module, "_secure_read_file", scoped_secure_read)
    monkeypatch.setattr(staging_module.os, "read", held_read)
    monkeypatch.setattr(rig.stager, "discard_unprojected", observed_discard)
    monkeypatch.setattr(staging_module, "_safe_delete_owned_tree", observed_delete)
    monkeypatch.setattr(rig.store, "_seal_docking_preparation", observed_seal)
    monkeypatch.setattr(rig.store, "_revoke_docking_preparation", observed_revoke)

    async def run():
        caller = asyncio.create_task(prepare(rig))
        closer = None
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            assert stage_returned.is_set() and verify_started.is_set() and not verify_exited.is_set()
            assert rig.stages == [draft["task_id"]] and reads == [] and verified == []
            assert caller.cancel()
            done, _ = await asyncio.wait({caller}, timeout=5)
            assert caller in done, "cancel waited for the held physical verification read"
            outcome = (await asyncio.gather(caller, return_exceptions=True))[0]
            assert isinstance(outcome, asyncio.CancelledError)
            assert not isinstance(outcome, rig.api.DockingConsentPreview)
            assert await asyncio.to_thread(revoke_exited.wait, 5)
            record = independent.get_docking_consent(draft["preparation_id"])
            assert record["state"] == "REVOKED" and record["cleanup_state"] == "pending"
            assert record["binding"] is record["binding_digest"] is record["approval_nonce_hash"] is None
            with pytest.raises(rig.api.DockingConsentError, match="consent_owner_busy"):
                reserve(rig, identity(2, owner=draft["owner_session_id"]), store=independent)
            with pytest.raises(rig.api.DockingConsentError, match="consent_capacity_full"):
                reserve(rig, identity(100), store=independent)
            closer = asyncio.create_task(rig.runtime.close())
            done, _ = await asyncio.wait({closer}, timeout=0.05)
            assert not done, "shutdown released a still-live verification worker"
            assert not verify_exited.is_set() and verified == [] and reads == []
            assert cleanup_entries == [] and deletions == [] and seals == []
            assert receptor.read_bytes() == RECEPTOR
            assert (receptor.parent / "ligand.sdf").read_bytes() == LIGAND

            release.set()
            done, _ = await asyncio.wait({closer}, timeout=5)
            assert closer in done
            closer.result()
            assert verify_exited.is_set() and barrier_errors == []
            assert b"".join(reads) == RECEPTOR and reads[-1] == b""
            assert len(verified) == 1
            assert verified[0]["receptor"]["sha256"] == hashlib.sha256(RECEPTOR).hexdigest()
            assert verified[0]["ligand"]["sha256"] == hashlib.sha256(LIGAND).hexdigest()
            assert cleanup_entries == [True] and deletions == [(True, True, False)]
            assert seals == [] and caller.cancelled()
            final = independent.get_docking_consent(draft["preparation_id"])
            assert final["state"] == "REVOKED" and final["cleanup_state"] == "settled"
            assert final["binding"] is final["binding_digest"] is final["approval_nonce_hash"] is None
            assert not (rig.root / draft["task_id"]).exists()
            assert (neighbor / "keep").read_bytes() == b"not C owned"
            reserve(rig, identity(2, owner=draft["owner_session_id"]), store=independent)
            no_execution(rig)
        finally:
            release.set()  # Release the actual descriptor read before all joins.
            if not caller.done():
                caller.cancel()
            await asyncio.gather(caller, return_exceptions=True)
            if closer is not None:
                await asyncio.gather(closer, return_exceptions=True)
            await asyncio.gather(rig.runtime.close(), return_exceptions=True)
            if verify_started.is_set():
                assert await asyncio.to_thread(verify_exited.wait, 5)
            assert barrier_errors == []

    asyncio.run(run())  # Runtime drains the real owner; executor joins its thread.
