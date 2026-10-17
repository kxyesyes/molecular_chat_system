"""D5 private C output assembly; synthetic bytes/hooks, never Vina evidence."""
from __future__ import annotations

import asyncio
from dataclasses import replace
import inspect
import json
import os
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from src.task_runtime.config import PROJECT_ROOT, TaskRuntimeConfig
from src.task_runtime.docking_consent import DockingConsentError
from src.task_runtime.docking_execution import DockingExecution
from src.task_runtime.runtime import TaskRuntime
from src.task_runtime.store import TaskStore
from tests.task_runtime.test_docking_consent import (
    Clock, c1_api, identity, policy, prepare, reserve,
    _c2_rig, _c2_ready, _c2_approve, _c2_terminal, _c2_finish,
)


@pytest.fixture(autouse=True)
def no_external(monkeypatch):
    import subprocess
    import httpx

    def denied(*args, **kwargs):
        raise AssertionError("D5 forbids processes and network")

    monkeypatch.setattr(subprocess, "Popen", denied)
    monkeypatch.setattr(httpx.Client, "send", denied)
    monkeypatch.setattr(httpx.AsyncClient, "send", denied)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", denied)


def rig_at(tmp_path, **kwargs):
    clock = Clock()
    selected_policy = policy(c1_api()[0])
    config = TaskRuntimeConfig(backend="local", canary_percent=0,
        temporal_address="unused.invalid:7233", temporal_namespace="test",
        docking_queue="test", docking_concurrency=1, staging_root=tmp_path / "stage")
    store = TaskStore(tmp_path / "tasks.sqlite")
    runtime = TaskRuntime(config=config, store=store, docking_consent_policy=selected_policy,
        consent_wall_time_ms=clock.wall_time_ms, consent_monotonic=clock.monotonic, **kwargs)
    return SimpleNamespace(runtime=runtime, store=store, policy=selected_policy, clock=clock)


def private_rig(tmp_path, root=None, **kwargs):
    assert "docking_consent_output_root" in inspect.signature(TaskRuntime).parameters, \
        "missing trusted constructor-only C output seam"
    return rig_at(tmp_path, docking_consent_output_root=root or tmp_path / "private", **kwargs)


async def ready(rig):
    reserve(rig)
    return (await prepare(rig)).to_dict()


async def approve(rig, proof):
    return await rig.runtime.approve_docking_consent(
        preparation_id=proof["preparation_id"], owner_session_id="owner-1",
        approval_nonce=proof["approval_nonce"], binding_digest=proof["binding_digest"], confirm=True)


async def settled(rig):
    for _ in range(500):
        view = await rig.runtime.get_docking_consent_view(task_id="c-task-1", owner_session_id="owner-1")
        if view["cleanup_status"] == "settled" and view["view_status"] != "ACTIVE":
            return view
        await asyncio.sleep(.01)
    pytest.fail("private C execution did not settle")


def test_default_is_prepare_only_and_rejects_before_submit(tmp_path, monkeypatch):
    rig = rig_at(tmp_path)
    submits = []
    original_submit = rig.runtime.local_backend.submit

    async def denied(submission):
        submits.append(submission)
        return await original_submit(submission)

    monkeypatch.setattr(rig.runtime.local_backend, "submit", denied)
    monkeypatch.setattr(rig.runtime.docking_execution, "run_verified", lambda *a, **k: {"success": False})

    async def run():
        try:
            proof = await ready(rig)
            with pytest.raises(DockingConsentError, match="^consent_policy_unavailable$"):
                await approve(rig, proof)
            assert submits == []
        finally:
            await rig.runtime.close()
    asyncio.run(run())


def test_trusted_constructor_separates_c_and_legacy(tmp_path):
    before = Path.cwd()
    rig = private_rig(tmp_path)
    try:
        assert rig.runtime.docking_execution._allowed_output_root == PROJECT_ROOT / "temp_docking"
        executor = rig.runtime._docking_consent_execution
        assert type(executor) is DockingExecution
        assert executor is not rig.runtime.docking_execution
        assert executor._allowed_output_root == tmp_path / "private"
        assert executor._execution_backend == "local"
        assert Path.cwd() == before
    finally:
        asyncio.run(rig.runtime.close())


@pytest.mark.parametrize("kind", ["relative", "legacy", "legacy-child", "legacy-parent",
    "static", "static-child", "static-parent", "cwd-legacy", "cwd-static", "stage", "file"])
def test_rejects_unsafe_constructor_roots(tmp_path, monkeypatch, kind):
    monkeypatch.chdir(tmp_path)
    file = tmp_path / "not-directory"
    file.write_bytes(b"synthetic")
    roots = {"relative": Path("private"), "legacy": PROJECT_ROOT / "temp_docking",
        "legacy-child": PROJECT_ROOT / "temp_docking" / "private", "legacy-parent": PROJECT_ROOT,
        "static": PROJECT_ROOT / "src/web/static", "static-child": PROJECT_ROOT / "src/web/static/private",
        "static-parent": PROJECT_ROOT / "src/web", "cwd-legacy": tmp_path / "temp_docking/private",
        "cwd-static": tmp_path / "src/web/static/private", "stage": tmp_path / "stage/raw", "file": file}
    with pytest.raises(DockingConsentError, match="^consent_policy_unavailable$") as caught:
        private_rig(tmp_path, roots[kind])
    assert str(roots[kind]) not in str(caught.value)


def test_rejects_existing_reparse_component(tmp_path, monkeypatch):
    # Platform-independent lstat metadata injection, also exercises missing leaf.
    parent = tmp_path / "linked"
    parent.mkdir()
    original = Path.lstat

    def lstat(path, *args, **kwargs):
        value = original(path, *args, **kwargs)
        if path == parent:
            return SimpleNamespace(st_mode=value.st_mode, st_file_attributes=0x400)
        return value

    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(DockingConsentError, match="^consent_policy_unavailable$"):
        private_rig(tmp_path, parent / "raw")


def test_c_rejects_nonlocal_environment_before_backend_factory(tmp_path, monkeypatch):
    from src.docking.sandbox_runner import SandboxDockingRunner
    calls = []
    def factory(*args, **kwargs):
        calls.append(True)
        raise AssertionError("must not build sandbox for C")
    monkeypatch.setattr(SandboxDockingRunner, "from_env", factory)
    monkeypatch.setenv("MEDCHAT_DOCKING_EXECUTION_BACKEND", "opensandbox")
    with pytest.raises(DockingConsentError, match="^consent_policy_unavailable$"):
        private_rig(tmp_path)
    assert calls == []
    assert os.environ["MEDCHAT_DOCKING_EXECUTION_BACKEND"] == "opensandbox"


def test_normal_non_c_backend_selection_unchanged(tmp_path, monkeypatch):
    from src.docking.sandbox_runner import SandboxDockingRunner
    calls = []
    raw = lambda *args, **kwargs: None
    def factory(root):
        calls.append(root)
        return SimpleNamespace(execute=raw)
    monkeypatch.setattr(SandboxDockingRunner, "from_env", factory)
    monkeypatch.setenv("MEDCHAT_DOCKING_EXECUTION_BACKEND", "opensandbox")
    rig = rig_at(tmp_path)
    try:
        assert calls == [PROJECT_ROOT / "temp_docking"]
        assert rig.runtime.docking_execution.raw_executor is raw
    finally:
        asyncio.run(rig.runtime.close())


def test_injected_concrete_private_executor_keeps_c2_compatibility(tmp_path, monkeypatch):
    rig = _c2_rig(tmp_path, monkeypatch)
    async def run():
        try:
            proof = await _c2_ready(rig)
            await _c2_approve(rig, proof)
            await _c2_terminal(rig, "SUCCEEDED")
            assert len(rig.raw) == 1
            assert rig.first.runtime._docking_consent_execution is rig.first.execution
        finally:
            await _c2_finish(rig)
    asyncio.run(run())


@pytest.mark.parametrize("fault", ["root", "executor", "policy", "backend", "reparse"])
@pytest.mark.parametrize("phase", ["approval", "dispatch"])
def test_bound_assembly_rechecked_before_approval_and_raw(tmp_path, monkeypatch, fault, phase):
    rig = private_rig(tmp_path)
    runtime = rig.runtime
    raw_calls = []
    from src.task_runtime import docking_execution as module
    monkeypatch.setattr(module, "execute_tool_compat", lambda *a, **k: raw_calls.append(True))
    original_lstat = Path.lstat

    def tamper():
        if fault == "root":
            runtime._docking_consent_execution._allowed_output_root = PROJECT_ROOT / "temp_docking"
        elif fault == "executor":
            runtime._docking_consent_execution = runtime.docking_execution
        elif fault == "policy":
            runtime._docking_consent_policy = replace(rig.policy)
        elif fault == "backend":
            runtime._docking_consent_execution._execution_backend = "opensandbox"
        else:
            target = tmp_path / "private"
            target.mkdir(exist_ok=True)
            def lstat(path, *args, **kwargs):
                value = original_lstat(path, *args, **kwargs)
                if path == target:
                    return SimpleNamespace(st_mode=value.st_mode, st_file_attributes=0x400)
                return value
            monkeypatch.setattr(Path, "lstat", lstat)

    original_guard = runtime._consent_execution_guard
    def guard(operation, inputs, current_phase):
        if current_phase == "dispatch":
            tamper()
        return original_guard(operation, inputs, current_phase)

    async def run():
        try:
            proof = await ready(rig)
            if phase == "approval":
                tamper()
                with pytest.raises(DockingConsentError):
                    await approve(rig, proof)
                row = rig.store._owned_docking_consent(owner_session_id="owner-1", preparation_id="preparation-1")
                assert row["state"] == "READY"
            else:
                monkeypatch.setattr(runtime, "_consent_execution_guard", guard)
                await approve(rig, proof)
                view = await settled(rig)
                assert view["view_status"] == "FAILED"
            assert raw_calls == []
        finally:
            await runtime.close()
    asyncio.run(run())


def test_real_adapter_service_scope_and_executor_roots_agree(tmp_path, monkeypatch):
    from src.docking.molecular_docking_service import MolecularDockingService
    from src.docking.adapters.base import CommandOwnershipScope
    services = []
    monkeypatch.setattr(MolecularDockingService, "configure", lambda *a, **k: None)
    def unavailable(service):
        services.append(service)
        return False  # No dependency probe, scientific calculation or subprocess.
    monkeypatch.setattr(MolecularDockingService, "verify_environment", unavailable)
    monkeypatch.setattr(MolecularDockingService, "env_diagnostics", lambda self: {})
    rig = private_rig(tmp_path)
    before = Path.cwd()
    async def run():
        try:
            proof = await ready(rig)
            assert str(tmp_path) not in json.dumps(proof)
            await approve(rig, proof)
            view = await settled(rig)
            assert view["view_status"] == "FAILED"  # Deliberate tool-unavailable hook.
            assert len(services) == 1
            service = services[0]
            scope = service._command_scope
            assert type(scope) is CommandOwnershipScope
            assert Path(service.work_dir) == Path(scope._output_root) == \
                rig.runtime._docking_consent_execution._allowed_output_root == tmp_path / "private"
            assert service.vina_adapter._ownership_scope is scope
            assert str(tmp_path) not in json.dumps(view)
            assert Path.cwd() == before
        finally:
            await rig.runtime.close()
    asyncio.run(run())


def test_fake_local_backend_does_not_gain_c_authority(tmp_path):
    from src.task_runtime.backends.local import LocalTaskBackend
    store = TaskStore(tmp_path / "fake.sqlite")
    calls = []
    async def fake(*args):
        calls.append(True)
        return {}
    backend = LocalTaskBackend(store, {"docking": fake})
    rig = private_rig(tmp_path, local_backend=backend)
    async def run():
        try:
            proof = await ready(rig)
            with pytest.raises(DockingConsentError, match="^consent_policy_unavailable$"):
                await approve(rig, proof)
            assert calls == []
        finally:
            await rig.runtime.close()
    asyncio.run(run())


def test_scope_root_rechecked_before_injected_raw(tmp_path, monkeypatch):
    rig = _c2_rig(tmp_path, monkeypatch)
    runtime = rig.first.runtime
    original_guard = runtime._consent_execution_guard
    def guard(operation, inputs, phase):
        if phase == "dispatch":
            operation.command_scope._output_root = str(PROJECT_ROOT / "temp_docking")
        return original_guard(operation, inputs, phase)
    monkeypatch.setattr(runtime, "_consent_execution_guard", guard)
    async def run():
        try:
            proof = await _c2_ready(rig)
            await _c2_approve(rig, proof)
            view = await settled(SimpleNamespace(runtime=runtime))
            assert view["view_status"] == "FAILED"
            assert rig.raw == []
        finally:
            await _c2_finish(rig)
    asyncio.run(run())


def test_non_c_handler_still_uses_legacy_executor(tmp_path, monkeypatch):
    from src.task_runtime.models import TaskSubmission
    rig = private_rig(tmp_path)
    calls = []
    sentinel = {"synthetic": "legacy-only"}
    def legacy(*args, **kwargs):
        calls.append((args, kwargs))
        return sentinel
    monkeypatch.setattr(rig.runtime.docking_execution, "run_verified", legacy)
    async def run():
        try:
            submission = TaskSubmission(task_id="legacy-task", task_type="docking", payload={},
                input_manifest_path="synthetic-manifest")
            result = await rig.runtime.local_backend._handlers["docking"](
                submission, threading.Event(), None)
            assert result is sentinel
            assert calls[0][0] == ("legacy-task", "synthetic-manifest")
            assert "command_scope" not in calls[0][1]
        finally:
            await rig.runtime.close()
    asyncio.run(run())


@pytest.mark.parametrize("authority", ["runtime_config", "allowed_output_root", "docking_consent_output_root"])
def test_client_parameters_cannot_supply_output_authority(tmp_path, authority):
    from tests.task_runtime.test_docking_consent import PARAMETERS
    rig = rig_at(tmp_path)
    async def run():
        try:
            reserve(rig)
            with pytest.raises(DockingConsentError):
                await prepare(rig, parameters={**PARAMETERS, authority: str(tmp_path / "private")})
        finally:
            await rig.runtime.close()
    asyncio.run(run())


@pytest.mark.parametrize("root", [PROJECT_ROOT / "temp_docking", PROJECT_ROOT / "src/web/static"])
def test_explicit_concrete_unsafe_executor_has_no_c_authority(tmp_path, root):
    execution = DockingExecution(tmp_path / "stage", raw_executor=lambda *a, **k: None,
                                 allowed_output_root=root)
    rig = rig_at(tmp_path, docking_execution=execution)
    async def run():
        try:
            proof = await ready(rig)
            with pytest.raises(DockingConsentError, match="^consent_policy_unavailable$"):
                await approve(rig, proof)
            assert rig.runtime.docking_execution is execution
        finally:
            await rig.runtime.close()
    asyncio.run(run())


@pytest.mark.parametrize("component", ["_stager", "_completions"])
@pytest.mark.parametrize("phase", ["approval", "dispatch", "result"])
def test_same_root_component_replacement_rejected_at_real_boundaries(tmp_path, monkeypatch, component, phase):
    from src.system.scientific_contracts import ToolProvenance, ToolResult

    raw_calls, replacements = [], []
    output = tmp_path / "private"

    def raw(payload, **control):
        raw_calls.append(control["job_id"])
        pose = output / ("docking_" + control["job_id"]) / "result.pdbqt"
        pose.parent.mkdir(parents=True, exist_ok=True)
        pose.write_text(
            "MODEL 1\nREMARK VINA RESULT: -7.200 0.000 0.000\nROOT\n"
            "ATOM      1  C   LIG A   1       0.000   0.000   0.000  0.00  0.00    +0.000 C\n"
            "ENDROOT\nTORSDOF 0\nENDMDL\n", encoding="utf-8")
        # Synthetic fixture provenance only; never evidence of real Vina.
        return ToolResult.success_result("molecular_docking",
            data={"total_poses": 1, "pose_file": str(pose),
                  "best_pose": {"binding_energy": -7.2, "pose_file": str(pose)}},
            quality={"real_execution": True},
            provenance=ToolProvenance(tool_name="molecular_docking", tool_version="vina-test-1"))

    execution = DockingExecution(tmp_path / "stage", raw_executor=raw, allowed_output_root=output)
    rig = rig_at(tmp_path, docking_execution=execution)
    original = getattr(execution, component)
    replacement = type(original)(original._root)
    assert replacement is not original and replacement._root == original._root
    original_guard = rig.runtime._consent_execution_guard

    def swap():
        setattr(execution, component, replacement)
        replacements.append(phase)

    def guard(operation, inputs, current_phase):
        if current_phase == phase and not replacements:
            swap()
        return original_guard(operation, inputs, current_phase)

    async def run():
        try:
            proof = await ready(rig)
            if phase == "approval":
                swap()
                with pytest.raises(DockingConsentError, match="^consent_policy_unavailable$"):
                    await approve(rig, proof)
                row = rig.store._owned_docking_consent(
                    owner_session_id="owner-1", preparation_id="preparation-1")
                assert row["state"] == "READY" and row["dispatch_state"] == "not_reserved"
            else:
                monkeypatch.setattr(rig.runtime, "_consent_execution_guard", guard)
                await approve(rig, proof)
                view = await settled(rig)
                assert view["view_status"] == "FAILED"
                assert view["reason_code"] == "consent_policy_unavailable"
            assert replacements == [phase]
            assert raw_calls == (["c-task-1"] if phase == "result" else [])
        finally:
            await rig.runtime.close()
    asyncio.run(run())


@pytest.mark.parametrize("component", ["_stager", "_completions"])
@pytest.mark.parametrize("mutation", ["duck", "root"])
def test_component_seal_rejects_duck_and_original_root_mutation(tmp_path, component, mutation):
    rig = private_rig(tmp_path)
    execution = rig.runtime._docking_consent_execution
    original = getattr(execution, component)
    try:
        if mutation == "duck":
            setattr(execution, component, SimpleNamespace(_root=original._root))
        else:
            original._root = tmp_path / "different-stage"
        with pytest.raises(DockingConsentError, match="^consent_policy_unavailable$"):
            rig.runtime._require_consent_output()
    finally:
        asyncio.run(rig.runtime.close())
