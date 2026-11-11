"""Portable entry point and evidence boundary, with no provider traffic."""
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/run_g3_generation_canary.py"


@pytest.fixture(scope="module")
def offline_child(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("g3-cli")
    private_temp = tmp_path / "temp"
    private_temp.mkdir()
    env = dict(os.environ, TMP=str(private_temp), TEMP=str(private_temp), TMPDIR=str(private_temp))
    child = subprocess.run([sys.executable, str(SCRIPT)], cwd=tmp_path, capture_output=True, env=env,
                           timeout=220, check=False)
    assert child.returncode == 0, child.stdout
    assert not list(private_temp.iterdir())
    assert list(tmp_path.iterdir()) == [private_temp]
    return child


def test_offline_launcher_cold_start(offline_child):
    child = offline_child
    assert not child.stderr
    assert len(child.stdout) < 64 * 1024
    report = json.loads(child.stdout)
    assert report["transport_kind"] == "scripted"
    assert report["remote_state"] == "not_contacted"
    assert report["status"] == "complete10"
    assert report["result"]["unique_count"] == 10
    assert report["local_cleanup"] == "settled"
    assert report["transport"]["counters"]["delegate_entries"] == 1
    assert len(report["source_hashes"]) == 7

    from scripts.run_g3_generation_canary import validated_output
    with pytest.raises(ValueError):
        validated_output(child, real=True)
    relabeled = dict(report, transport_kind="local_ollama", remote_state="provider_terminal_reported")
    with pytest.raises(ValueError):
        validated_output(subprocess.CompletedProcess([], 0, json.dumps(relabeled), ""), real=True)
    for key, value in (("status", "success"), ("unexpected", "private")):
        altered = dict(report, **{key: value})
        invalid = subprocess.CompletedProcess([], 0, json.dumps(altered), "")
        with pytest.raises(ValueError):
            validated_output(invalid, real=False)


def test_no_arbitrary_endpoint_cli():
    child = subprocess.run([sys.executable, str(SCRIPT), "--endpoint", "private.invalid"],
                           capture_output=True, timeout=10)
    assert child.returncode != 0
    assert b"private.invalid" not in child.stdout + child.stderr


@pytest.mark.parametrize("returncode,stdout,stderr", [
    (1, "private", ""), (0, "{}", "private"), (0, "a" * (65536 + 1), ""), (0, "", ""),
], ids=["failed-exit", "stderr", "oversized", "empty"])
def test_invalid_child_frames_are_rejected_before_decoding(returncode, stdout, stderr):
    from scripts.run_g3_generation_canary import validated_output
    child = subprocess.CompletedProcess([], returncode, stdout, stderr)
    with pytest.raises(ValueError, match="^Invalid canary frame$"):
        validated_output(child, real=False)


@pytest.mark.parametrize("settled,finished,expected", [
    (False, 1, "ownership_unconfirmed"), (True, 210.0, "deadline"),
])
def test_valid_report_cannot_override_owner_or_deadline(
        offline_child, tmp_path, monkeypatch, capsys, settled, finished, expected):
    from scripts import run_g3_generation_canary as launcher
    private = tmp_path / "owned"
    private.mkdir()
    scope = SimpleNamespace(seal=lambda: None, settle=lambda: settled)
    adapter = SimpleNamespace(wrap_command=lambda *args: list(args), run=lambda *a, **kw: offline_child)
    owner = SimpleNamespace(CommandOwnershipScope=lambda **kw: scope, CommandAdapter=lambda *a, **kw: adapter)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(launcher, "_command_owner", lambda: owner)
    monkeypatch.setattr(launcher.tempfile, "mkdtemp", lambda **kw: str(private))
    clock = iter([0.0, 0.0, finished])
    monkeypatch.setattr(launcher.time, "monotonic", lambda: next(clock))
    assert launcher.main([]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == expected
    assert private.exists() is (not settled)


@pytest.mark.parametrize("extra", [0, 1])
def test_owner_caps_combined_raw_streams_at_64_kib(tmp_path, extra):
    from scripts import run_g3_generation_canary as launcher
    commands = launcher._command_owner()
    scope = commands.CommandOwnershipScope(task_id="g3-test", output_root=tmp_path)
    adapter = commands.CommandAdapter(sys.executable, ownership_scope=scope)
    code = ("import os; os.write(1, b'x' * 32768); "
            "os.write(2, b'y' * (32768 + " + str(extra) + "))")
    args = adapter.wrap_command("-I", "-c", code)
    try:
        if extra:
            with pytest.raises(commands.CommandOutputLimitError):
                adapter.run(args, cwd=str(tmp_path), timeout=10)
        else:
            child = adapter.run(args, cwd=str(tmp_path), timeout=10)
            assert len(child.stdout) + len(child.stderr) == 65536
    finally:
        scope.seal()
        assert scope.settle()
