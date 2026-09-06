from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "deployment/libexec/prepare-temporal-worker-directories.py"
PREPARE_UNIT = ROOT / "deployment/medchat-temporal-worker-prepare.service"
TMPFILES = ROOT / "deployment/tmpfiles/medchat-temporal-worker.conf"


def _load_helper():
    spec = importlib.util.spec_from_file_location("temporal_directory_helper", HELPER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_directory_helper_is_standalone_and_has_fixed_paths() -> None:
    source = HELPER.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        (node.module or "").split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert imports <= set(sys.stdlib_module_names) | {"__future__"}
    assert "O_DIRECTORY" in source
    assert "O_NOFOLLOW" in source
    assert "dir_fd" in source
    helper = _load_helper()
    assert helper.RUNTIME_DIRECTORIES == (
        "opt/medchat/molecular_chat_system/scratch",
        "opt/medchat/molecular_chat_system/scratch/task_inputs",
        "opt/medchat/molecular_chat_system/scratch/temporal_backups",
        "opt/medchat/molecular_chat_system/temp_docking",
    )


def test_directory_helper_cli_rejects_path_override_without_echo() -> None:
    result = subprocess.run(
        [sys.executable, str(HELPER), "/private/root"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == (
        "temporal_worker_directory_preparation=failed "
        "code=directory_arguments_invalid\n"
    )
    assert "private" not in result.stderr.lower()
    assert "traceback" not in result.stderr.lower()


def test_directory_error_sanitizes_unknown_codes() -> None:
    helper = _load_helper()
    error = helper.DirectoryPreparationError("/private/sentinel")
    assert error.code == "directory_preparation_failed"
    assert str(error) == "directory_preparation_failed"


def test_prepare_unit_uses_only_installed_generation_helpers() -> None:
    source = PREPARE_UNIT.read_text(encoding="utf-8")
    exec_starts = [
        line.split("=", 1)[1]
        for line in source.splitlines()
        if line.startswith("ExecStart=")
    ]
    assert exec_starts == [
        "/usr/lib/medchat/temporal-worker/current/libexec/validate-temporal-worker-env",
        "/usr/lib/medchat/temporal-worker/current/libexec/prepare-temporal-worker-directories",
    ]
    assert "systemd-tmpfiles" not in source
    assert "EnvironmentFile" not in source
    assert "/opt/medchat" not in source
    assert "/opt/conda" not in source
    assert not TMPFILES.exists()
