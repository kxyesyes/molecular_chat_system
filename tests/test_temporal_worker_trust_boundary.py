from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.task_runtime.production_worker import (
    ProductionWorkerValidationError,
    validate_production_worker_config,
)


ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = ROOT / "deployment" / "libexec" / "validate-temporal-worker-env.py"
SYSTEMD_INTEGRATION_PATH = ROOT / "tests" / "test_temporal_systemd_integration.py"
DEPLOYMENT_README = ROOT / "deployment" / "README.md"
WORKER_ENV_EXAMPLE = ROOT / "deployment" / "temporal-worker.env.example"
GIT_ATTRIBUTES = ROOT / ".gitattributes"
ALLOWED_KEYS = {
    "MEDCHAT_TASK_BACKEND",
    "MEDCHAT_TEMPORAL_CANARY_PERCENT",
    "MEDCHAT_TEMPORAL_ADDRESS",
    "MEDCHAT_TEMPORAL_NAMESPACE",
    "MEDCHAT_TEMPORAL_DOCKING_QUEUE",
    "MEDCHAT_TEMPORAL_DOCKING_CONCURRENCY",
    "MEDCHAT_TASK_STAGING_ROOT",
    "MEDCHAT_TASK_DB_PATH",
    "MEDCHAT_TEMPORAL_WORKER_METRICS_ADDRESS",
    "MEDCHAT_TEMPORAL_WORKER_METRICS_PORT",
    "MEDCHAT_TEMPORAL_BASELINE_P95_SECONDS",
    "MEDCHAT_TEMPORAL_BACKUP_STATE",
    "MEDCHAT_DOCKING_EXECUTION_BACKEND",
    "MEDCHAT_SANDBOX_BROKER_SOCKET",
    "MOLECULAR_DOCKING_ROOT",
    "MOLECULAR_DOCKING_VINA",
    "MOLECULAR_DOCKING_ADFR_BIN",
    "MOLECULAR_DOCKING_PREPARE_RECEPTOR",
    "MOLECULAR_DOCKING_PREPARE_LIGAND",
    "MOLECULAR_DOCKING_VINA_TIMEOUT_SECONDS",
}


@pytest.fixture
def helper():
    spec = importlib.util.spec_from_file_location(
        "test_temporal_worker_env_helper",
        HELPER_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_helper_is_standalone_standard_library_source() -> None:
    source = HELPER_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        (node.module or "").split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert "src" not in imported
    assert "scripts" not in imported
    assert "deployment" not in imported
    assert imported <= set(sys.stdlib_module_names) | {"__future__"}


def test_descriptor_traversal_uses_no_follow_dir_fd_contract() -> None:
    source = HELPER_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    os_open_calls = [
        node
        for node in calls
        if isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "os"
        and node.func.attr == "open"
    ]
    assert len(os_open_calls) == 3
    assert sum(
        any(keyword.arg == "dir_fd" for keyword in call.keywords)
        for call in os_open_calls
    ) == 2
    assert "O_DIRECTORY" in source
    assert "O_NOFOLLOW" in source
    assert "O_CLOEXEC" in source
    assert "O_NONBLOCK" in source
    assert not any(
        isinstance(call.func, ast.Name) and call.func.id == "open" for call in calls
    )
    assert not any(
        isinstance(call.func, ast.Attribute)
        and call.func.attr in {"resolve", "stat", "lstat"}
        for call in calls
    )


def test_parser_accepts_only_canonical_allowlisted_assignments(helper) -> None:
    payload = (
        b"# worker only\n"
        b"; alternate comment\n"
        b"\n"
        b"MEDCHAT_TASK_BACKEND=temporal_canary\n"
        b"MEDCHAT_TEMPORAL_CANARY_PERCENT=5\n"
    )
    assert helper.parse_environment_payload(payload) == {
        "MEDCHAT_TASK_BACKEND": "temporal_canary",
        "MEDCHAT_TEMPORAL_CANARY_PERCENT": "5",
    }
    assert helper.ALLOWED_KEYS == frozenset(ALLOWED_KEYS)
    assert helper.MAX_ENVIRONMENT_BYTES == 65_536


def test_real_helper_parses_complete_worker_environment_example(helper) -> None:
    payload = WORKER_ENV_EXAMPLE.read_bytes()

    assert b"\r" not in payload
    parsed = helper.parse_environment_payload(payload)

    assert set(parsed) == ALLOWED_KEYS
    assert parsed["MEDCHAT_DOCKING_EXECUTION_BACKEND"] == "opensandbox"
    assert parsed["MEDCHAT_SANDBOX_BROKER_SOCKET"] == (
        "/run/medchat-sandbox/broker.sock"
    )


def test_worker_environment_example_is_forced_to_lf_by_git_attributes() -> None:
    rules = GIT_ATTRIBUTES.read_text(encoding="utf-8").splitlines()

    assert "deployment/temporal-worker.env.example text eol=lf" in rules


@pytest.mark.parametrize(
    "name",
    [
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "PYTHONHOME",
        "PYTHONPATH",
        "BASH_ENV",
        "ENV",
        "IFS",
        "GCONV_PATH",
        "SSLKEYLOGFILE",
        "OPENAI_COMPATIBLE_API_KEY",
        "MODELSCOPE_API_KEY",
        "SERVICE_TOKEN",
        "DATABASE_PASSWORD",
        "CLIENT_SECRET",
    ],
)
def test_parser_rejects_loader_runtime_and_secret_keys(helper, name: str) -> None:
    with pytest.raises(helper.TrustBoundaryError) as raised:
        helper.parse_environment_payload(f"{name}=private\n".encode("ascii"))
    assert raised.value.code == "environment_key_forbidden"
    assert str(raised.value) == "environment_key_forbidden"


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        (b"UNDECLARED=value\n", "environment_key_not_allowed"),
        (
            b"MEDCHAT_TASK_BACKEND=a\nMEDCHAT_TASK_BACKEND=b\n",
            "environment_key_duplicate",
        ),
        (b" MEDCHAT_TASK_BACKEND=value\n", "environment_line_not_canonical"),
        (b"MEDCHAT_TASK_BACKEND=value \n", "environment_line_not_canonical"),
        (b"MEDCHAT_TASK_BACKEND='value'\n", "environment_line_not_canonical"),
        (b'MEDCHAT_TASK_BACKEND=va"lue\n', "environment_line_not_canonical"),
        (
            b"MEDCHAT_TASK_BACKEND=value\\\ncontinued\n",
            "environment_line_not_canonical",
        ),
        (b"MEDCHAT_TASK_BACKEND\n", "environment_line_not_canonical"),
        (b"MEDCHAT_TASK_BACKEND=a=b\n", "environment_line_not_canonical"),
        (b"MEDCHAT_TASK_BACKEND=bad\tvalue\n", "environment_line_not_canonical"),
        (b"MEDCHAT_TASK_BACKEND=value\r\n", "environment_line_not_canonical"),
        (b" \n", "environment_line_not_canonical"),
        (b"MEDCHAT_TASK_BACKEND=bad\x00value\n", "environment_file_invalid_nul"),
        (b"\xff\n", "environment_file_invalid_utf8"),
        (b"", "environment_file_empty"),
        (b"# comments only\n; still empty\n", "environment_file_empty"),
        (b"x" * 65_537, "environment_file_too_large"),
    ],
    ids=[
        "undeclared",
        "duplicate",
        "leading-space",
        "trailing-space",
        "quoted-value",
        "embedded-quote",
        "continuation",
        "missing-equals",
        "multiple-equals",
        "tab",
        "crlf",
        "whitespace-line",
        "nul",
        "invalid-utf8",
        "empty",
        "comments-only",
        "oversize",
    ],
)
def test_parser_rejects_unsafe_content_without_echo(
    helper,
    payload: bytes,
    code: str,
) -> None:
    with pytest.raises(helper.TrustBoundaryError) as raised:
        helper.parse_environment_payload(payload)
    assert raised.value.code == code
    rendered = str(raised.value).lower()
    assert repr(payload).lower() not in rendered
    assert "traceback" not in rendered


def test_trust_error_sanitizes_unknown_codes(helper) -> None:
    error = helper.TrustBoundaryError("C:/private/secret.env")
    assert error.code == "environment_validation_failed"
    assert "private" not in str(error).lower()
    assert "secret" not in str(error).lower()


def test_cli_output_is_fixed_and_redacted(helper, monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        helper,
        "validate_environment_file",
        lambda: (_ for _ in ()).throw(
            helper.TrustBoundaryError("environment_key_forbidden")
        ),
    )
    assert helper.main([]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == (
        "temporal_worker_environment_validation=failed "
        "code=environment_key_forbidden\n"
    )


def test_cli_success_output_is_fixed(helper, monkeypatch, capsys) -> None:
    monkeypatch.setattr(helper, "validate_environment_file", lambda: {})
    assert helper.main([]) == 0
    output = capsys.readouterr()
    assert output.out == "temporal_worker_environment_validation=passed\n"
    assert output.err == ""


def test_descriptor_validator_rejects_relative_paths_before_open(helper) -> None:
    with pytest.raises(helper.TrustBoundaryError) as raised:
        helper.validate_environment_file(Path("private/temporal-worker.env"))
    assert raised.value.code == "environment_path_invalid"


def test_systemd_integration_cleanup_has_no_numeric_pid_signal_path() -> None:
    source = SYSTEMD_INTEGRATION_PATH.read_text(encoding="utf-8")
    cleanup_source = source[
        source.index("class PosixCleanupBackend"):source.index("class FakeCleanupBackend")
    ]
    assert "os.kill(" not in cleanup_source
    assert "os.killpg(" not in cleanup_source
    assert "ControlGroup" in cleanup_source
    assert "cgroup.procs" in cleanup_source
    assert "cgroup.kill" in cleanup_source
    assert "InvocationID" in cleanup_source
    assert '"--property=Job"' in cleanup_source
    assert "create_runtime_mask" in cleanup_source
    assert "follow_symlinks=False" in cleanup_source
    assert '"systemctl", "stop"' in cleanup_source
    assert '"systemctl", "kill"' not in cleanup_source
    assert '"systemctl", "reset-failed"' in cleanup_source


def test_deployment_docs_prescribe_trusted_worker_install_and_narrow_ownership() -> None:
    documentation = DEPLOYMENT_README.read_text(encoding="utf-8")
    assert "chown -R medchat:medchat /opt/medchat" not in documentation
    assert "deployment/install-temporal-worker.sh --destdir" in documentation
    assert "/opt/medchat/molecular_chat_system" in documentation
    assert "/opt/conda/envs/medchat" in documentation
    assert "root:root" in documentation
    assert "go-w" in documentation
    assert "install -d -o medchat" not in documentation
    assert "systemd-tmpfiles" not in documentation
    assert "prepare-temporal-worker-directories" in documentation
    assert "activate-temporal-worker-generation.sh" in documentation
    assert "/etc/medchat/temporal-worker.env" in documentation
    assert "temporal-worker.env.example" in documentation
    assert "0600" in documentation
    assert "no secrets" in documentation.lower()
    assert "systemctl restart medchat-temporal-worker-prepare.service" in documentation
    assert "systemctl restart medchat-temporal-worker.service" in documentation
    assert "Docker/Compose" in documentation
    assert "operator" in documentation.lower()
    assert "manifest.sha256" in documentation
    assert "releases/<bundle-sha256>" in documentation
    assert "activate-temporal-worker-generation.sh" in documentation
    assert "--migrate-legacy" in documentation
    assert "legacy-tmpfiles.disabled" in documentation
    assert "isolated live-root runner" in documentation
    assert "release blocker" in documentation.lower()


def test_cli_rejects_path_argument_without_echo_or_traceback() -> None:
    result = subprocess.run(
        [sys.executable, str(HELPER_PATH), "C:/private/secret.env"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == (
        "temporal_worker_environment_validation=failed "
        "code=environment_arguments_invalid\n"
    )
    assert "private" not in result.stderr.lower()
    assert "secret" not in result.stderr.lower()
    assert "traceback" not in result.stderr.lower()


def _production_config(project_root: Path) -> SimpleNamespace:
    return SimpleNamespace(
        backend="temporal_canary",
        canary_percent=0,
        temporal_namespace="default",
        docking_queue="medchat-docking",
        docking_concurrency=1,
        warnings=(),
        _temporal_address_configured=True,
        _staging_root_configured=True,
        _worker_metrics_address_configured=True,
        _backup_state_path_configured=True,
        worker_metrics_address="127.0.0.1",
        staging_root=project_root / "scratch" / "task_inputs",
        backup_state_path=project_root / "scratch" / "backups" / "latest.json",
    )


def _validate_production(project_root: Path) -> None:
    validate_production_worker_config(
        _production_config(project_root),
        project_root=project_root,
        task_db_path=project_root / "scratch" / "tasks.sqlite",
        docking_output_root=project_root / "temp_docking",
    )


def test_production_worker_requires_explicit_opensandbox_docking_backend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MEDCHAT_DOCKING_EXECUTION_BACKEND", raising=False)
    monkeypatch.setenv(
        "MEDCHAT_SANDBOX_BROKER_SOCKET", "/run/medchat-sandbox/broker.sock"
    )

    with pytest.raises(ProductionWorkerValidationError) as raised:
        _validate_production(tmp_path)

    assert "docking_backend_not_opensandbox" in raised.value.codes


def test_production_worker_requires_explicit_sandbox_socket(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MEDCHAT_DOCKING_EXECUTION_BACKEND", "opensandbox")
    monkeypatch.delenv("MEDCHAT_SANDBOX_BROKER_SOCKET", raising=False)

    with pytest.raises(ProductionWorkerValidationError) as raised:
        _validate_production(tmp_path)

    assert "sandbox_socket_not_configured" in raised.value.codes


@pytest.mark.parametrize(
    "socket_path",
    [
        "run/medchat-sandbox/broker.sock",
        "/run/medchat-sandbox/../private/broker.sock",
        "/tmp/broker.sock",
        "/run/medchat-sandbox",
    ],
)
def test_production_worker_rejects_socket_outside_fixed_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    socket_path: str,
) -> None:
    monkeypatch.setenv("MEDCHAT_DOCKING_EXECUTION_BACKEND", "opensandbox")
    monkeypatch.setenv("MEDCHAT_SANDBOX_BROKER_SOCKET", socket_path)

    with pytest.raises(ProductionWorkerValidationError) as raised:
        _validate_production(tmp_path)

    assert "sandbox_socket_outside_runtime" in raised.value.codes


def test_worker_environment_example_appends_only_non_secret_broker_settings() -> None:
    lines = WORKER_ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
    assert lines[-2:] == [
        "MEDCHAT_DOCKING_EXECUTION_BACKEND=opensandbox",
        "MEDCHAT_SANDBOX_BROKER_SOCKET=/run/medchat-sandbox/broker.sock",
    ]
    assert "OPEN_SANDBOX_API_KEY" not in WORKER_ENV_EXAMPLE.read_text(encoding="utf-8")
