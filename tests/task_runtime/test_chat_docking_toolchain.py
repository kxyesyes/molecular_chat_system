"""Synthetic file identities only: no scientific executables or network."""
from dataclasses import FrozenInstanceError, replace
import hashlib
import importlib
import inspect
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.docking import molecular_docking_service as service_module
from src.docking.molecular_docking_adapter import MOLECULAR_DOCKING_ADAPTER_VERSION
from src.task_runtime.docking_consent import DockingConsentError
from src.task_runtime.docking_execution import DockingExecution
from src.task_runtime.docking_private_output import PrivateDockingOutput
from tests.task_runtime.test_docking_consent import c1_api, policy
from tests.task_runtime.test_docking_execution import _stage


@pytest.fixture(autouse=True)
def no_external(monkeypatch):
    import subprocess
    import socket
    def denied(*args, **kwargs):
        raise AssertionError("synthetic toolchain tests forbid commands/network")
    monkeypatch.setattr(subprocess, "Popen", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)


def api():
    assert importlib.util.find_spec("src.docking.chat_toolchain") is not None, \
        "missing trusted toolchain binding"
    return importlib.import_module("src.docking.chat_toolchain")


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for key in tuple(os.environ):
        if key.startswith("MOLECULAR_DOCKING_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("MEDCHAT_DOCKING_EXECUTION_BACKEND", "local")
    monkeypatch.setattr(service_module.shutil, "which", lambda name: None)
    paths = {}
    for name in ("vina_exe", "prepare_receptor_cmd", "prepare_ligand_cmd", "python"):
        paths[name] = tmp_path / (name + ".exe")
        paths[name].write_bytes(("synthetic-" + name).encode())
    monkeypatch.setattr(service_module.sys, "executable", str(paths["python"]))
    config = {key: str(path) for key, path in paths.items() if key != "python"}
    config["vina_timeout_seconds"] = 120.0
    service = service_module.MolecularDockingService(config={"docking": config})
    return SimpleNamespace(service=service, config=config, paths=paths, root=tmp_path)


def capture(rig):
    return api().ChatDockingToolchain(rig.service)


def test_actual_hashes_closed_config_and_detached_public_descriptor(rig):
    binding = capture(rig)
    public = binding.public_descriptor()
    assert public["adapter_contract_version"] == MOLECULAR_DOCKING_ADAPTER_VERSION
    assert public["tools"]["vina"]["sha256"] == hashlib.sha256(rig.paths["vina_exe"].read_bytes()).hexdigest()
    assert public["tools"]["vina"]["size"] == rig.paths["vina_exe"].stat().st_size
    assert public["tools"]["reduce"] == {"present": False}
    assert public["tools"]["openbabel"] == {"present": False}
    assert str(rig.root) not in json.dumps(public)
    assert str(rig.root) not in repr(binding)
    assert len(binding.digest) == 64
    original = binding.runtime_config()
    detached = binding.runtime_config()
    detached["docking"]["vina_exe"] = "changed"
    public["tools"]["vina"]["sha256"] = "changed"
    rig.config["vina_exe"] = "changed source dict alias"
    assert binding.runtime_config() == original
    binding.require_current()
    with pytest.raises((FrozenInstanceError, AttributeError)):
        binding.digest = "0" * 64


@pytest.mark.parametrize("key", ["vina_exe", "prepare_receptor_cmd", "prepare_ligand_cmd", "python"])
def test_file_tamper_fails_closed_without_reseal(rig, key):
    binding = capture(rig)
    digest = binding.digest
    rig.paths[key].write_bytes(b"tampered")
    with pytest.raises(DockingConsentError, match="^consent_policy_unavailable$"):
        binding.require_current()
    assert binding.digest == digest


@pytest.mark.parametrize("field", ["vina_exe", "vina_timeout_seconds", "python_exe"])
def test_source_service_config_mutation_rejected(rig, field):
    binding = capture(rig)
    setattr(rig.service, field, 99 if field.endswith("seconds") else "different")
    with pytest.raises(DockingConsentError):
        binding.require_current()


@pytest.mark.parametrize("kind", ["reduce", "openbabel"])
def test_absent_optional_ambient_fallback_cannot_appear(rig, monkeypatch, kind):
    binding = capture(rig)
    other = rig.root / "optional.exe"
    other.write_bytes(b"synthetic optional")
    if kind == "reduce":
        monkeypatch.setenv("MOLECULAR_DOCKING_REDUCE", str(other))
    else:
        monkeypatch.setattr(service_module.shutil, "which", lambda name: str(other) if name == "obabel" else None)
    with pytest.raises(DockingConsentError):
        binding.require_current()


def test_explicit_required_config_wins_over_later_environment(rig, monkeypatch):
    binding = capture(rig)
    other = rig.root / "other.exe"
    other.write_bytes(b"different synthetic executable")
    monkeypatch.setenv("MOLECULAR_DOCKING_VINA", str(other))
    monkeypatch.setenv("MOLECULAR_DOCKING_VINA_TIMEOUT_SECONDS", "12")
    binding.require_current()
    assert binding.runtime_config()["docking"]["vina_exe"] == str(rig.paths["vina_exe"])


def test_capture_rejects_nonconcrete_service(rig):
    with pytest.raises(DockingConsentError):
        api().ChatDockingToolchain(SimpleNamespace(**vars(rig.service)))


@pytest.mark.parametrize("kind", ["missing", "directory", "reparse", "oversize", "aggregate"])
def test_invalid_files_and_bounded_reads(rig, monkeypatch, kind):
    module = api()
    target = rig.paths["vina_exe"]
    if kind == "missing":
        target.unlink()
    elif kind == "directory":
        target.unlink()
        target.mkdir()
    elif kind == "reparse":
        original = Path.lstat
        def lstat(path, *args, **kwargs):
            value = original(path, *args, **kwargs)
            return SimpleNamespace(st_mode=value.st_mode, st_file_attributes=0x400) if path == target.parent else value
        monkeypatch.setattr(Path, "lstat", lstat)
    else:
        monkeypatch.setattr(module, "MAX_TOOL_FILE_BYTES" if kind == "oversize" else "MAX_TOOLCHAIN_BYTES", 1)
    with pytest.raises(DockingConsentError):
        capture(rig)


def test_private_output_pins_pointer_and_policy_digest(rig):
    binding = capture(rig)
    selected = replace(policy(c1_api()[0]), tool_policy_digest=binding.digest,
        adapter_contract_version=MOLECULAR_DOCKING_ADAPTER_VERSION)
    execution = DockingExecution(rig.root / "stage", allowed_output_root=rig.root / "private", chat_toolchain=binding)
    with pytest.raises(DockingConsentError):
        PrivateDockingOutput.bind(execution, replace(selected, tool_policy_digest="0" * 64), rig.root / "stage")
    frozen = PrivateDockingOutput.bind(execution, selected, rig.root / "stage")
    assert frozen.require(execution, selected) is execution
    execution._chat_toolchain = capture(rig)
    with pytest.raises(DockingConsentError):
        frozen.require(execution, selected)


def test_legacy_default_has_no_runtime_config_and_raw_signature_unchanged(rig):
    calls = []
    def raw(payload, *, job_id, progress_callback, cancel_event):
        calls.append(dict(payload))
    execution = DockingExecution(rig.root / "stage", raw, allowed_output_root=rig.root / "private")
    execution.run_verified("task-1", _stage(rig.root / "stage"))
    assert len(calls) == 1 and "runtime_config" not in calls[0]


def test_toolchain_check_precedes_consent_raw_dispatch(rig):
    binding = capture(rig)
    calls, phases = [], []
    execution = DockingExecution(rig.root / "stage", lambda *a, **k: calls.append(True),
        allowed_output_root=rig.root / "private", chat_toolchain=binding)
    def guard(inputs, phase):
        phases.append(phase)
        if phase == "reserve":
            rig.paths["vina_exe"].write_bytes(b"tampered")
    with pytest.raises(DockingConsentError):
        execution.run_verified("task-1", _stage(rig.root / "stage"), consent_guard=guard)
    assert "dispatch" not in phases and calls == []


@pytest.mark.parametrize("scoped", [False, True])
def test_default_actual_adapter_receives_bound_runtime_config(rig, monkeypatch, scoped):
    from src.docking.adapters.base import CommandOwnershipScope
    binding = capture(rig)
    seen = []
    instances = []
    original = service_module.MolecularDockingService.__init__
    def init(service, config=None, **kwargs):
        seen.append(json.loads(json.dumps(config)))
        original(service, config, **kwargs)
        instances.append(service)
    monkeypatch.setattr(service_module.MolecularDockingService, "__init__", init)
    monkeypatch.setattr(service_module.MolecularDockingService, "verify_environment", lambda self: False)
    monkeypatch.setattr(service_module.MolecularDockingService, "env_diagnostics", lambda self: {})
    execution = DockingExecution(rig.root / "stage", allowed_output_root=rig.root / "private", chat_toolchain=binding)
    scope = CommandOwnershipScope(task_id="task-1", output_root=rig.root / "private") if scoped else None
    execution.run_verified("task-1", _stage(rig.root / "stage"), command_scope=scope)
    assert seen and all(config == binding.runtime_config() for config in seen)
    assert len(instances) == 1
    assert instances[0].reduce_cmd == ""
    assert instances[0].openbabel_adapter.executable == ""
    if scoped:
        assert instances[0]._command_scope is scope
        assert Path(instances[0].work_dir) == rig.root / "private"
    assert inspect.signature(DockingExecution).parameters["chat_toolchain"].default is None


@pytest.mark.parametrize("name", ["reduce", "openbabel"])
def test_present_optional_identity_and_tamper(rig, monkeypatch, name):
    path = rig.root / (name + ".exe")
    path.write_bytes(b"synthetic optional")
    if name == "reduce":
        rig.config["reduce_cmd"] = str(path)
    else:
        monkeypatch.setattr(service_module.shutil, "which", lambda tool: str(path) if tool == "obabel" else None)
    rig.service.configure(rig.config)
    binding = capture(rig)
    assert binding.public_descriptor()["tools"][name] == {
        "present": True, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "size": path.stat().st_size,
    }
    path.unlink()
    with pytest.raises(DockingConsentError):
        binding.require_current()


def test_nonexistent_configured_reduce_is_absent_not_fabricated(rig):
    path = rig.root / "missing-reduce.exe"
    rig.config["reduce_cmd"] = str(path)
    rig.service.configure(rig.config)
    binding = capture(rig)
    assert binding.public_descriptor()["tools"]["reduce"] == {"present": False}
    path.write_bytes(b"newly installed optional")
    with pytest.raises(DockingConsentError):
        binding.require_current()


def test_same_size_bytes_changed_with_restored_mtime_rejected(rig):
    binding = capture(rig)
    path = rig.paths["vina_exe"]
    before = path.stat()
    path.write_bytes(b"X" * before.st_size)
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    with pytest.raises(DockingConsentError):
        binding.require_current()


def test_file_change_during_bounded_read_is_rejected(rig, monkeypatch):
    module = api()
    original = module.os.read
    reads = []
    def read(fd, size):
        reads.append(size)
        chunk = original(fd, size)
        if len(reads) == 1:
            with rig.paths["vina_exe"].open("ab") as stream:
                stream.write(b"changed during capture")
        return chunk
    monkeypatch.setattr(module.os, "read", read)
    with pytest.raises(DockingConsentError):
        capture(rig)
    assert reads and max(reads) <= 1024 * 1024


def test_hardlinked_executable_rejected(rig):
    os.link(rig.paths["vina_exe"], rig.root / "hardlink.exe")
    with pytest.raises(DockingConsentError):
        capture(rig)


def test_symlink_executable_rejected(rig):
    path = rig.root / "symlink.exe"
    try:
        path.symlink_to(rig.paths["vina_exe"])
    except OSError:
        pytest.skip("host lacks symlink creation permission; reparse metadata covered separately")
    rig.service.vina_exe = str(path)
    rig.service.vina_adapter.executable = str(path)
    with pytest.raises(DockingConsentError):
        capture(rig)


def test_detached_config_per_injected_raw_invocation(rig):
    binding = capture(rig)
    calls = []
    def raw(payload, *, job_id, progress_callback, cancel_event):
        calls.append(json.loads(json.dumps(payload["runtime_config"])))
        payload["runtime_config"]["docking"]["vina_exe"] = "mutated by injected raw"
    execution = DockingExecution(rig.root / "stage", raw,
        allowed_output_root=rig.root / "private", chat_toolchain=binding)
    for task in ("task-1", "task-2"):
        execution.run_verified(task, _stage(rig.root / "stage", task_id=task))
    assert calls == [binding.runtime_config(), binding.runtime_config()]


def test_dispatch_callback_tamper_cannot_reach_raw(rig):
    binding = capture(rig)
    calls = []
    execution = DockingExecution(rig.root / "stage", lambda *a, **k: calls.append(True),
        allowed_output_root=rig.root / "private", chat_toolchain=binding)
    def guard(inputs, phase):
        if phase == "dispatch":
            rig.paths["vina_exe"].write_bytes(b"changed at last callback")
    with pytest.raises(DockingConsentError):
        execution.run_verified("task-1", _stage(rig.root / "stage"), consent_guard=guard)
    assert calls == []


@pytest.mark.parametrize("adapter", ["vina_adapter", "receptor_adapter", "ligand_adapter", "openbabel_adapter", "reduce_adapter"])
def test_adapter_identity_alias_mutation_rejected(rig, adapter):
    binding = capture(rig)
    # Source adapters are mutable aliases; a cached reduce adapter is also a
    # consumer even though its construction is lazy.
    if adapter == "reduce_adapter":
        rig.service.reduce_adapter = SimpleNamespace(executable="changed")
    else:
        getattr(rig.service, adapter).executable = "changed"
    with pytest.raises(DockingConsentError):
        binding.require_current()


def test_policy_cannot_claim_different_adapter_version(rig):
    binding = capture(rig)
    selected = replace(policy(c1_api()[0]), tool_policy_digest=binding.digest,
        adapter_contract_version="different-adapter")
    execution = DockingExecution(rig.root / "stage", allowed_output_root=rig.root / "private", chat_toolchain=binding)
    with pytest.raises(DockingConsentError):
        PrivateDockingOutput.bind(execution, selected, rig.root / "stage")


def test_binding_has_no_mutable_field_dictionary(rig):
    assert not hasattr(capture(rig), "__dict__")


@pytest.mark.parametrize("phase_to_remove", ["before", "reserve", "dispatch"])
def test_dispatch_callback_cannot_remove_toolchain(rig, phase_to_remove):
    binding = capture(rig)
    calls = []
    execution = DockingExecution(rig.root / "stage", lambda *a, **k: calls.append(True),
        allowed_output_root=rig.root / "private", chat_toolchain=binding)
    if phase_to_remove == "before":
        execution._chat_toolchain = None
    def guard(inputs, phase):
        if phase == phase_to_remove:
            execution._chat_toolchain = None
    with pytest.raises(DockingConsentError):
        execution.run_verified("task-1", _stage(rig.root / "stage"), consent_guard=guard)
    assert calls == []


@pytest.mark.parametrize("scoped", [False, True])
@pytest.mark.parametrize("optional", ["reduce", "openbabel"])
def test_actual_constructor_drift_denied_before_probe(rig, monkeypatch, scoped, optional):
    """Restore ambient state before returning: only the actual instance differs."""
    from src.docking.adapters.base import CommandOwnershipScope
    binding = capture(rig)
    other = rig.root / "unexpected-optional.exe"
    other.write_bytes(b"synthetic unexpected optional executable")
    original = service_module.MolecularDockingService.__init__
    constructed, probes = [], []
    def init(service, config=None, **kwargs):
        with monkeypatch.context() as ambient:
            if optional == "reduce":
                ambient.setenv("MOLECULAR_DOCKING_REDUCE", str(other))
            else:
                ambient.setattr(service_module.shutil, "which", lambda name: str(other) if name == "obabel" else None)
            original(service, config, **kwargs)
        constructed.append(service)
    def probe(service):
        probes.append(service)
        return False
    monkeypatch.setattr(service_module.MolecularDockingService, "__init__", init)
    monkeypatch.setattr(service_module.MolecularDockingService, "verify_environment", probe)
    monkeypatch.setattr(service_module.MolecularDockingService, "env_diagnostics", lambda self: {})
    execution = DockingExecution(rig.root / "stage", allowed_output_root=rig.root / "private", chat_toolchain=binding)
    scope = CommandOwnershipScope(task_id="task-1", output_root=rig.root / "private") if scoped else None
    result = execution.run_verified("task-1", _stage(rig.root / "stage"), command_scope=scope)
    assert len(constructed) == 1
    assert (constructed[0].reduce_cmd if optional == "reduce" else constructed[0].openbabel_adapter.executable) == str(other)
    assert probes == [], "actual drifted service reached environment probe"
    assert result["success"] is False
    binding.require_current()  # A temporary resolver alone cannot detect this drift.


def _direct_adapter_payload(rig):
    receptor, ligand = rig.root / "receptor.pdb", rig.root / "ligand.sdf"
    receptor.write_bytes(b"synthetic receptor")
    ligand.write_bytes(b"synthetic ligand")
    return {"receptor_path": str(receptor), "ligand_path": str(ligand),
            "center": [1, 2, 3], "size": [20, 20, 20]}


def test_adapter_constructor_requires_concrete_current_toolchain(rig):
    from src.docking.molecular_docking_adapter import MolecularDocking
    assert "chat_toolchain" in inspect.signature(MolecularDocking).parameters
    with pytest.raises(DockingConsentError):
        MolecularDocking(chat_toolchain=SimpleNamespace(require_current=lambda: None))
    binding = capture(rig)
    rig.paths["vina_exe"].write_bytes(b"tampered before adapter construction")
    with pytest.raises(DockingConsentError):
        MolecularDocking(chat_toolchain=binding)


def test_bound_adapter_ignores_payload_toolchain_and_runtime_config(rig, monkeypatch):
    from src.docking.molecular_docking_adapter import MolecularDocking
    assert "chat_toolchain" in inspect.signature(MolecularDocking).parameters
    binding = capture(rig)
    probes, configs = [], []
    original = service_module.MolecularDockingService.__init__
    def init(service, config=None, **kwargs):
        configs.append(json.loads(json.dumps(config)))
        original(service, config, **kwargs)
    def probe(service):
        probes.append(service)
        return False
    monkeypatch.setattr(service_module.MolecularDockingService, "__init__", init)
    monkeypatch.setattr(service_module.MolecularDockingService, "verify_environment", probe)
    monkeypatch.setattr(service_module.MolecularDockingService, "env_diagnostics", lambda self: {})
    payload = _direct_adapter_payload(rig)
    payload.update(runtime_config={"docking": {"vina_exe": "caller-controlled", "vina_timeout_seconds": 9999}},
                   chat_toolchain={"digest": "caller-controlled"})
    adapter = MolecularDocking(chat_toolchain=binding)
    result = adapter.execute(payload)
    assert result["success"] is False
    assert configs == [binding.runtime_config()]
    assert len(probes) == 1 and probes[0].vina_exe == str(rig.paths["vina_exe"])
    assert probes[0].reduce_cmd == "" and probes[0].openbabel_adapter.executable == ""
    assert payload["runtime_config"]["docking"]["vina_exe"] == "caller-controlled"


def test_adapter_rechecks_current_before_constructing_service(rig, monkeypatch):
    from src.docking.molecular_docking_adapter import MolecularDocking
    assert "chat_toolchain" in inspect.signature(MolecularDocking).parameters
    binding = capture(rig)
    adapter = MolecularDocking(chat_toolchain=binding)
    constructed = []
    monkeypatch.setattr(service_module.MolecularDockingService, "__init__", lambda *a, **k: constructed.append(True))
    rig.paths["vina_exe"].write_bytes(b"tampered after adapter construction")
    result = adapter.execute(_direct_adapter_payload(rig))
    assert constructed == [] and result["success"] is False


def test_bound_execution_keeps_default_raw_identity(rig):
    binding = capture(rig)
    execution = DockingExecution(rig.root / "stage", allowed_output_root=rig.root / "private", chat_toolchain=binding)
    assert execution.raw_executor is DockingExecution._execute_production_tool
    assert execution.raw_executor is DockingExecution._execute_local_tool
