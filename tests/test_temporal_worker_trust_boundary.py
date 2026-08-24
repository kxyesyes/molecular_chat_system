from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = ROOT / "deployment" / "libexec" / "validate-temporal-worker-env.py"
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
