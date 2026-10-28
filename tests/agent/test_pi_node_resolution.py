"""Avoid repeated PATH scans while keeping dependency recovery and isolation."""
import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from src.agent.pi_runtime import bridge


@pytest.fixture
def node_lookup(tmp_path, monkeypatch):
    executable = tmp_path / "node.exe"
    executable.write_bytes(b"synthetic executable; never launched")
    executable.chmod(0o700)
    calls = []

    def find(name):
        calls.append(name)
        return str(executable)

    monkeypatch.setattr(bridge, "_NODE_LOOKUP", None, raising=False)
    monkeypatch.setattr(bridge.shutil, "which", find)
    return executable, calls


def test_bridge_instances_reuse_successful_node_discovery(node_lookup):
    executable, calls = node_lookup
    first, second = bridge.PiRuntimeBridge(), bridge.PiRuntimeBridge()
    assert first.node == second.node == str(executable)
    assert first is not second and first.active_pid is second.active_pid is None
    assert calls == ["node"]


def test_concurrent_bridge_construction_only_discovers_node_once(node_lookup):
    executable, calls = node_lookup
    with ThreadPoolExecutor(max_workers=4) as pool:
        instances = list(pool.map(lambda _: bridge.PiRuntimeBridge(), range(8)))
    assert all(instance.node == str(executable) for instance in instances)
    assert calls == ["node"]


@pytest.mark.parametrize("variable", ["PATH", "PATHEXT"])
def test_search_environment_change_forces_new_discovery(node_lookup, monkeypatch, variable):
    _, calls = node_lookup
    bridge.PiRuntimeBridge()
    monkeypatch.setenv(variable, os.environ.get(variable, "") + os.pathsep + "synthetic")
    bridge.PiRuntimeBridge()
    assert calls == ["node", "node"]


def test_working_directory_change_forces_new_discovery(node_lookup, tmp_path, monkeypatch):
    _, calls = node_lookup
    bridge.PiRuntimeBridge()
    monkeypatch.chdir(tmp_path)
    bridge.PiRuntimeBridge()
    assert calls == ["node", "node"]


def test_missing_cached_executable_is_discovered_again(node_lookup, monkeypatch, tmp_path):
    executable, _ = node_lookup
    bridge.PiRuntimeBridge()
    executable.unlink()
    replacement = tmp_path / "replacement.exe"
    replacement.write_bytes(b"synthetic; never launched")
    replacement.chmod(0o700)
    monkeypatch.setattr(bridge.shutil, "which", lambda _: str(replacement))
    assert bridge.PiRuntimeBridge().node == str(replacement)


def test_missing_node_does_not_cache_failure(node_lookup, monkeypatch):
    executable, _ = node_lookup
    monkeypatch.setattr(bridge.shutil, "which", lambda _: None)
    assert bridge.PiRuntimeBridge().node is None
    monkeypatch.setattr(bridge.shutil, "which", lambda _: str(executable))
    assert bridge.PiRuntimeBridge().node == str(executable)


def test_cached_executable_losing_execute_access_is_discovered_again(node_lookup, monkeypatch, tmp_path):
    executable, _ = node_lookup
    bridge.PiRuntimeBridge()
    original_access = bridge.os.access
    monkeypatch.setattr(bridge.os, "access", lambda path, mode: (
        False if path == str(executable) else original_access(path, mode)
    ))
    replacement = tmp_path / "replacement.exe"
    replacement.write_bytes(b"synthetic; never launched")
    replacement.chmod(0o700)
    monkeypatch.setattr(bridge.shutil, "which", lambda _: str(replacement))
    assert bridge.PiRuntimeBridge().node == str(replacement)


def test_explicit_node_override_avoids_search(node_lookup):
    _, calls = node_lookup
    assert bridge.PiRuntimeBridge(node="explicit-node").node == "explicit-node"
    assert calls == []
