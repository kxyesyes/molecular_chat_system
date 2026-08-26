from __future__ import annotations

import importlib.util
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 deployment baseline
    import tomli as tomllib


ROOT = Path(__file__).parents[2]
DEPLOYMENT = ROOT / "deployment" / "opensandbox"
SANDBOX_TOML = DEPLOYMENT / "sandbox.toml"
OPENSANDBOX_SERVICE = DEPLOYMENT / "medchat-opensandbox.service"
BROKER_SERVICE = DEPLOYMENT / "medchat-sandbox-broker.service"
ENV_EXAMPLE = DEPLOYMENT / "opensandbox.env.example"
INSTALLER = DEPLOYMENT / "install.sh"
TEMPORAL_WORKER_SERVICE = ROOT / "deployment" / "medchat-temporal-worker.service"
VALIDATOR = ROOT / "scripts" / "validate_opensandbox_deployment.py"


def _unit_sections(path: Path) -> dict[str, dict[str, list[str]]]:
    sections: dict[str, dict[str, list[str]]] = {}
    current: dict[str, list[str]] | None = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = sections.setdefault(line[1:-1], {})
            continue
        assert current is not None, f"directive outside section in {path.name}"
        key, separator, value = line.partition("=")
        assert separator, f"malformed directive in {path.name}"
        current.setdefault(key, []).append(value)
    return sections


def _values(unit: dict[str, dict[str, list[str]]], key: str) -> list[str]:
    return unit["Service"].get(key, [])


def _words(unit: dict[str, dict[str, list[str]]], key: str) -> set[str]:
    return {
        word
        for value in _values(unit, key)
        for word in value.split()
    }


def _load_validator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("opensandbox_deployment_validator", VALIDATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _asset_root(tmp_path: Path) -> Path:
    for source in (
        SANDBOX_TOML,
        OPENSANDBOX_SERVICE,
        BROKER_SERVICE,
        ENV_EXAMPLE,
        INSTALLER,
        TEMPORAL_WORKER_SERVICE,
    ):
        relative = source.relative_to(ROOT)
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    return tmp_path


def test_server_config_is_authenticated_loopback_gvisor_and_mount_safe() -> None:
    config = tomllib.loads(SANDBOX_TOML.read_text(encoding="utf-8"))

    assert config["server"]["host"] == "127.0.0.1"
    assert config["server"]["api_key"] == "${OPEN_SANDBOX_API_KEY}"
    assert config["runtime"] == {
        "type": "docker",
        "execd_image": "opensandbox/execd:v1.0.21",
    }
    assert config["secure_runtime"] == {
        "type": "gvisor",
        "docker_runtime": "runsc",
    }
    assert config["docker"]["pids_limit"] == 128
    assert config["docker"]["network_mode"] == "bridge"
    assert config["docker"]["no_new_privileges"] is True
    assert set(config["docker"]["drop_capabilities"]) == {
        "AUDIT_WRITE",
        "MKNOD",
        "NET_ADMIN",
        "NET_RAW",
        "SYS_ADMIN",
        "SYS_MODULE",
        "SYS_PTRACE",
        "SYS_TIME",
        "SYS_TTY_CONFIG",
    }
    assert config["storage"]["allowed_host_paths"] == [
        "/var/lib/opensandbox/approved-empty"
    ]


def test_only_opensandbox_service_has_docker_socket_access() -> None:
    opensandbox = _unit_sections(OPENSANDBOX_SERVICE)
    broker = _unit_sections(BROKER_SERVICE)
    worker = _unit_sections(TEMPORAL_WORKER_SERVICE)

    assert "/var/run/docker.sock" in _words(opensandbox, "ReadWritePaths")
    assert "/var/run/docker.sock" not in _words(broker, "ReadWritePaths")
    assert "/var/run/docker.sock" not in _words(worker, "ReadWritePaths")
    assert "docker" in _words(opensandbox, "SupplementaryGroups")
    assert "docker" not in _words(broker, "SupplementaryGroups")
    assert "docker" not in _words(worker, "SupplementaryGroups")


@pytest.mark.parametrize(
    "path",
    [OPENSANDBOX_SERVICE, BROKER_SERVICE],
)
def test_new_services_have_required_systemd_hardening(path: Path) -> None:
    service = _unit_sections(path)["Service"]
    assert service["UMask"] == ["0077"]
    assert service["PrivateTmp"] == ["true"]
    assert service["NoNewPrivileges"] == ["true"]
    assert service["ProtectSystem"] == ["strict"]
    assert service["ProtectHome"] == ["true"]
    assert service["PrivateDevices"] == ["true"]
    assert service["ProtectKernelTunables"] == ["true"]
    assert service["ProtectKernelModules"] == ["true"]
    assert service["ProtectControlGroups"] == ["true"]
    assert service["ReadWritePaths"]


def test_broker_and_worker_have_only_required_shared_runtime_access() -> None:
    broker = _unit_sections(BROKER_SERVICE)
    worker = _unit_sections(TEMPORAL_WORKER_SERVICE)

    assert _values(broker, "User") == ["medchat-sandbox"]
    assert _values(broker, "Group") == ["medchat-sandbox"]
    assert _words(broker, "ReadWritePaths") == {
        "/var/lib/medchat-sandbox",
        "/run/medchat-sandbox",
    }
    assert _values(broker, "RestrictAddressFamilies") == [
        "AF_UNIX AF_INET AF_INET6"
    ]
    assert "medchat-sandbox" in _words(worker, "SupplementaryGroups")
    assert "/run/medchat-sandbox" in _words(worker, "ReadWritePaths")

    for unit in (broker, worker):
        exposed_environment = "\n".join(
            _values(unit, "EnvironmentFile") + _values(unit, "Environment")
        )
        assert "opensandbox.env" not in exposed_environment
        assert "OPEN_SANDBOX_API_KEY" not in exposed_environment


def test_opensandbox_service_is_loopback_configured_and_key_scoped() -> None:
    unit = _unit_sections(OPENSANDBOX_SERVICE)
    assert _values(unit, "User") == ["medchat-opensandbox"]
    assert _values(unit, "Group") == ["medchat-opensandbox"]
    assert _values(unit, "EnvironmentFile") == ["/etc/medchat/opensandbox.env"]
    assert _values(unit, "RestrictAddressFamilies") == [
        "AF_UNIX AF_INET AF_INET6"
    ]
    assert "/var/lib/opensandbox/approved-empty" in _words(
        unit, "InaccessiblePaths"
    )


def test_environment_example_has_placeholders_only() -> None:
    assignments = {}
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            key, separator, value = line.partition("=")
            assert separator
            assignments[key] = value

    assert assignments["OPEN_SANDBOX_API_KEY"] == "replace-with-random-secret"
    assert assignments["MEDCHAT_SANDBOX_IMAGE"].endswith(
        "@sha256:" + "0" * 64
    )
    assert not any(value.startswith(("sk-", "ghp_", "token-")) for value in assignments.values())


def test_installer_keeps_token_literal_and_uses_safe_rendering_contract() -> None:
    template = SANDBOX_TOML.read_text(encoding="utf-8")
    installer = INSTALLER.read_text(encoding="utf-8")

    assert template.count("${OPEN_SANDBOX_API_KEY}") == 1
    assert "/etc/medchat/opensandbox.toml" in installer
    assert "/etc/medchat/opensandbox.env" in installer
    assert "umask 077" in installer
    assert "set +x" in installer
    assert "approved-empty" in installer
    assert "0o000" in installer or "0000" in installer
    assert "mktemp" not in installer
    assert "sed " not in installer
    assert "eval " not in installer
    assert ". /etc/medchat/opensandbox.env" not in installer
    assert "source /etc/medchat/opensandbox.env" not in installer
    assert "OPEN_SANDBOX_API_KEY=" not in "\n".join(
        line for line in installer.splitlines() if "example" not in line
    )


def test_installer_rejects_symlinked_directories_and_targets() -> None:
    installer = INSTALLER.read_text(encoding="utf-8")

    assert "def ensure_directory(" in installer
    assert "os.lstat" in installer
    assert "stat.S_ISDIR" in installer
    assert "O_NOFOLLOW" in installer
    assert "install -d" not in installer
    assert "while [ \"$parent\" != / ]" in installer


def test_static_validator_passes_and_cli_output_is_exact() -> None:
    validator = _load_validator()
    assert validator.validate_static(ROOT) is None

    result = subprocess.run(
        [sys.executable, str(VALIDATOR), "--static"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0
    assert result.stdout == "opensandbox_deployment_validation=passed\n"
    assert result.stderr == ""


@pytest.mark.parametrize(
    ("relative_path", "replacement", "expected_code"),
    [
        ("deployment/opensandbox/sandbox.toml", "not = [valid", "config_malformed"),
        (
            "deployment/opensandbox/medchat-sandbox-broker.service",
            "[Service]\nUser=root\n",
            "broker_unit_unsafe",
        ),
    ],
)
def test_static_validator_returns_stable_codes_for_unsafe_assets(
    tmp_path: Path,
    relative_path: str,
    replacement: str,
    expected_code: str,
) -> None:
    validator = _load_validator()
    root = _asset_root(tmp_path)
    (root / relative_path).write_text(replacement, encoding="utf-8")

    code = validator.validate_static(root)

    assert code == expected_code
    assert code in validator._STABLE_CODES


def test_static_validator_returns_stable_code_for_missing_asset(tmp_path: Path) -> None:
    validator = _load_validator()
    root = _asset_root(tmp_path)
    (root / "deployment/opensandbox/sandbox.toml").unlink()

    assert validator.validate_static(root) == "asset_missing"


def test_static_validator_rejects_installer_without_directory_guards(
    tmp_path: Path,
) -> None:
    validator = _load_validator()
    root = _asset_root(tmp_path)
    installer = root / "deployment/opensandbox/install.sh"
    installer.write_text(
        installer.read_text(encoding="utf-8").replace(
            "def ensure_directory(", "def unsafe_directory("
        ),
        encoding="utf-8",
    )

    assert validator.validate_static(root) == "installer_unsafe"


def test_validator_never_emits_exception_or_secret_text(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    validator = _load_validator()
    secret = "never-print-this-api-key"

    def fail(_: Path) -> str | None:
        raise RuntimeError(secret)

    monkeypatch.setattr(validator, "validate_static", fail)
    exit_code = validator.main(["--static"])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert captured.out == (
        "opensandbox_deployment_validation=failed code=validation_internal_error\n"
    )
    assert captured.err == ""
    assert secret not in captured.out


def test_safe_mocked_runtime_checks_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    validator = _load_validator()
    calls: list[tuple[str, ...]] = []

    def command(argv: tuple[str, ...]) -> validator._CommandResult:
        calls.append(argv)
        if argv == ("runsc", "--version"):
            return validator._CommandResult(True, "runsc version 20260801")
        if argv[:3] == ("docker", "info", "--format"):
            return validator._CommandResult(True, '{"runsc":{"path":"runsc"}}')
        if argv[:4] == ("docker", "image", "ls", "--digests"):
            return validator._CommandResult(
                True,
                "registry.invalid/medchat-docking@sha256:" + "a" * 64,
            )
        if argv[:3] == ("systemctl", "is-active", "--quiet"):
            return validator._CommandResult(True, "")
        raise AssertionError(f"unexpected fixed command shape: {argv!r}")

    monkeypatch.setattr(validator, "_run_command", command)
    monkeypatch.setattr(validator, "_opensandbox_health_is_ok", lambda: True)
    monkeypatch.setattr(
        validator,
        "_broker_socket_status",
        lambda: validator._SocketStatus(True, "medchat-sandbox", "medchat-sandbox", 0o660),
    )
    monkeypatch.setattr(
        validator,
        "_runtime_image_reference",
        lambda: (
            "registry.invalid/medchat-docking:approved@sha256:" + "a" * 64
        ),
    )

    assert validator.validate_runtime() is None
    assert all(isinstance(call, tuple) and call for call in calls)
    assert not any(call[0] in {"sh", "bash", "cmd", "powershell"} for call in calls)
    assert (
        "docker",
        "image",
        "ls",
        "--digests",
        "--format",
        "{{.Repository}}@{{.Digest}}",
    ) in calls
    assert not any("registry.invalid" in argument for call in calls for argument in call)


def test_runtime_failure_is_allowlisted_and_does_not_echo_command_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validator = _load_validator()
    secret = "api-key-from-stderr"
    monkeypatch.setattr(
        validator,
        "_run_command",
        lambda _argv: validator._CommandResult(False, secret),
    )

    code = validator.validate_runtime()

    assert code == "runsc_unavailable"
    assert code in validator._STABLE_CODES
    assert secret not in code


def test_runtime_socket_contract_rejects_wrong_type_owner_or_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validator = _load_validator()
    def command(argv: tuple[str, ...]) -> validator._CommandResult:
        if argv == ("runsc", "--version"):
            return validator._CommandResult(True, "runsc version 1")
        if argv[:3] == ("docker", "info", "--format"):
            return validator._CommandResult(True, '{"runsc":{"path":"runsc"}}')
        raise AssertionError(argv)

    monkeypatch.setattr(validator, "_run_command", command)
    monkeypatch.setattr(validator, "_opensandbox_health_is_ok", lambda: True)
    monkeypatch.setattr(
        validator,
        "_broker_socket_status",
        lambda: validator._SocketStatus(False, "root", "root", 0o777),
    )

    assert validator.validate_runtime() == "socket_invalid"


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell syntax check")
def test_installer_has_valid_posix_shell_syntax() -> None:
    result = subprocess.run(
        ["sh", "-n", str(INSTALLER)],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=10,
    )
    assert result.returncode == 0


def test_installer_is_not_executable_on_windows_checkout() -> None:
    if os.name == "nt":
        pytest.skip("Windows does not preserve POSIX executable mode")
    assert stat.S_IMODE(INSTALLER.stat().st_mode) & stat.S_IXUSR
