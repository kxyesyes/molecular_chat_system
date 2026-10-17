"""Trusted, constructor-only local tool identity; never probes executables.

This binds configured command files, not their transitive DLL/package dependencies.
Validation is a pre-dispatch fence, not an OS-level executable/file lock.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import sys

from .molecular_docking_adapter import MOLECULAR_DOCKING_ADAPTER_VERSION
from .molecular_docking_service import MolecularDockingService
from src.task_runtime.docking_consent import DockingConsentError


MAX_TOOL_FILE_BYTES = 256 * 1024 * 1024
MAX_TOOLCHAIN_BYTES = 512 * 1024 * 1024
_CHUNK_BYTES = 1024 * 1024
_PATH_FIELDS = ("vina_exe", "prepare_receptor_cmd", "prepare_ligand_cmd",
                "reduce_cmd", "adfrsuite_path", "python_exe")


def _closed_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def _unavailable():
    return DockingConsentError("consent_policy_unavailable")


def _path(value):
    if type(value) is not str:
        raise _unavailable()
    if not value:
        return ""
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise _unavailable()
    # Do not resolve away symlinks/junctions before checking each component.
    for component in (path, *path.parents):
        try:
            status = component.lstat()
        except FileNotFoundError:
            continue
        if (stat.S_ISLNK(status.st_mode)
                or getattr(status, "st_file_attributes", 0) & 0x400):
            raise _unavailable()
    return str(path.resolve(strict=False))


def _stamp(status):
    # Windows lstat infers executable permission bits from the filename while
    # fstat does not. Compare file type, identity and mutation metadata instead.
    return (status.st_dev, status.st_ino, stat.S_IFMT(status.st_mode), status.st_nlink,
            status.st_size, status.st_mtime_ns, status.st_ctime_ns)


def _file_identity(value, *, required, budget):
    resolved = _path(value)
    if not resolved:
        if required:
            raise _unavailable()
        return {"present": False, "path": ""}
    path = Path(resolved)
    try:
        before = path.lstat()
    except FileNotFoundError:
        if required:
            raise _unavailable() from None
        return {"present": False, "path": resolved}
    if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
            or before.st_size > min(MAX_TOOL_FILE_BYTES, budget[0])):
        raise _unavailable()
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(path, flags)
    try:
        if _stamp(os.fstat(descriptor)) != _stamp(before):
            raise _unavailable()
        digest, size = hashlib.sha256(), 0
        while True:
            # Bound even a concurrently growing file, including its EOF probe.
            chunk = os.read(descriptor, min(_CHUNK_BYTES, before.st_size - size + 1))
            if not chunk:
                break
            size += len(chunk)
            if size > before.st_size:
                raise _unavailable()
            digest.update(chunk)
        _path(value)
        if (size != before.st_size or _stamp(os.fstat(descriptor)) != _stamp(before)
                or _stamp(path.lstat()) != _stamp(before)):
            raise _unavailable()
    finally:
        os.close(descriptor)
    budget[0] -= size
    return {"present": True, "path": resolved, "sha256": digest.hexdigest(),
            "size": size, "identity": list(_stamp(before))}


def _service_config(service):
    if type(service) is not MolecularDockingService:
        raise _unavailable()
    paths = {name: _path(getattr(service, name)) for name in _PATH_FIELDS}
    timeout = service.vina_timeout_seconds
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise _unavailable()
    adapters = {}
    for name, attr, expected in (
        ("vina", "vina_adapter", "vina_exe"),
        ("receptor", "receptor_adapter", "prepare_receptor_cmd"),
        ("ligand", "ligand_adapter", "prepare_ligand_cmd"),
        ("openbabel", "openbabel_adapter", None),
    ):
        adapters[name] = _path(getattr(service, attr).executable)
        if expected is not None and adapters[name] != paths[expected]:
            raise _unavailable()
    reduce_adapter = service.reduce_adapter
    if reduce_adapter is not None and _path(reduce_adapter.executable) != paths["reduce_cmd"]:
        raise _unavailable()
    return {"paths": paths, "adapters": adapters, "vina_timeout_seconds": float(timeout)}


def _runtime_config(config):
    paths = config["paths"]
    return {"docking": {
        "vina_exe": paths["vina_exe"], "prepare_receptor_cmd": paths["prepare_receptor_cmd"],
        "prepare_ligand_cmd": paths["prepare_ligand_cmd"], "reduce_cmd": paths["reduce_cmd"],
        "adfrsuite_bin": paths["adfrsuite_path"], "root_dir": "",
        "vina_timeout_seconds": config["vina_timeout_seconds"],
    }}


def _resolve(config):
    # configure is the concrete service's actual resolver. Avoid __init__ here:
    # it creates output directories. Resolution itself invokes no command.
    service = object.__new__(MolecularDockingService)
    service._command_scope = None
    service.python_exe = sys.executable  # configure cannot override this field.
    MolecularDockingService.configure(service, config)
    return service


def _descriptor(config):
    budget = [MAX_TOOLCHAIN_BYTES]
    tools = {}
    for name, value, required in (
        ("vina", config["paths"]["vina_exe"], True),
        ("receptorprep", config["paths"]["prepare_receptor_cmd"], True),
        ("ligandprep", config["paths"]["prepare_ligand_cmd"], True),
        ("python", config["paths"]["python_exe"], True),
        ("reduce", config["paths"]["reduce_cmd"], False),
        ("openbabel", config["adapters"]["openbabel"], False),
    ):
        tools[name] = _file_identity(value, required=required, budget=budget)
    return {"schema": "chat-docking-toolchain-v1",
            "adapter_contract_version": MOLECULAR_DOCKING_ADAPTER_VERSION,
            "config": config, "tools": tools}


@dataclass(frozen=True, init=False, repr=False, slots=True)
class ChatDockingToolchain:
    """Capture once from a concrete service; drift fails closed, never reseals.

    runtime_config()/public_descriptor() return detached JSON values. The source
    service remains a private identity anchor, checked for later configure calls.
    """
    _service: MolecularDockingService = field(repr=False)
    _json: bytes = field(repr=False)
    digest: str

    def __init__(self, service):
        try:
            config = _service_config(service)
            if _service_config(_resolve(_runtime_config(config))) != config:
                raise _unavailable()
            descriptor = _closed_json(_descriptor(config))
            object.__setattr__(self, "_service", service)
            object.__setattr__(self, "_json", descriptor)
            object.__setattr__(self, "digest", hashlib.sha256(descriptor).hexdigest())
            self.require_current()
        except (OSError, ValueError, TypeError, AttributeError, RuntimeError):
            raise _unavailable() from None

    def __repr__(self):
        return "ChatDockingToolchain(<private>)"

    def runtime_config(self):
        return _runtime_config(json.loads(self._json)["config"])

    def public_descriptor(self):
        descriptor = json.loads(self._json)
        return {"schema": descriptor["schema"],
                "adapter_contract_version": descriptor["adapter_contract_version"],
                "tools": {name: {key: value for key, value in item.items()
                                  if key in {"present", "sha256", "size"}}
                          for name, item in descriptor["tools"].items()}}

    def assert_service(self, service):
        """Require exact resolved configuration, including optional absence."""
        try:
            if _service_config(service) != json.loads(self._json)["config"]:
                raise _unavailable()
        except (OSError, ValueError, TypeError, AttributeError, RuntimeError):
            raise _unavailable() from None

    def require_current(self):
        try:
            self.assert_service(self._service)
            resolved = _resolve(self.runtime_config())
            self.assert_service(resolved)
            if _closed_json(_descriptor(_service_config(resolved))) != self._json:
                raise _unavailable()
        except (OSError, ValueError, TypeError, AttributeError, RuntimeError):
            raise _unavailable() from None
