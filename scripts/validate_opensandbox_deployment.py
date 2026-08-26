"""Fail-closed validation for the OpenSandbox deployment bundle."""

from __future__ import annotations

import json
import os
import re
import shlex
import stat
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

try:
    import grp
    import pwd
except ModuleNotFoundError:  # Static validation is supported on Windows.
    grp = None
    pwd = None

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 deployment baseline
    import tomli as tomllib


_ROOT = Path(__file__).resolve().parents[1]
_OUTPUT_LIMIT = 4096
_COMMAND_TIMEOUT_SECONDS = 5.0
_HEALTH_TIMEOUT_SECONDS = 3.0
_SOCKET_PATH = Path("/run/medchat-sandbox/broker.sock")
_RUNTIME_ENV = Path("/etc/medchat/sandbox-broker.env")
_SERVICES = (
    "medchat-opensandbox.service",
    "medchat-sandbox-broker.service",
    "medchat-temporal-worker.service",
)
_DANGEROUS_CAPABILITIES = {
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
_REQUIRED_HARDENING = {
    "UMask": "0077",
    "PrivateTmp": "true",
    "NoNewPrivileges": "true",
    "ProtectSystem": "strict",
    "ProtectHome": "true",
    "PrivateDevices": "true",
    "ProtectKernelTunables": "true",
    "ProtectKernelModules": "true",
    "ProtectControlGroups": "true",
}
_ASSET_PATHS = (
    "deployment/opensandbox/sandbox.toml",
    "deployment/opensandbox/medchat-opensandbox.service",
    "deployment/opensandbox/medchat-sandbox-broker.service",
    "deployment/opensandbox/opensandbox.env.example",
    "deployment/opensandbox/install.sh",
    "deployment/medchat-temporal-worker.service",
)
_STABLE_CODES = frozenset(
    {
        "usage",
        "asset_missing",
        "config_malformed",
        "server_not_loopback",
        "api_key_template_invalid",
        "runtime_not_docker",
        "runtime_not_gvisor",
        "docker_policy_unsafe",
        "host_path_allowlist_unsafe",
        "opensandbox_unit_unsafe",
        "broker_unit_unsafe",
        "worker_unit_unsafe",
        "installer_unsafe",
        "env_template_unsafe",
        "runsc_unavailable",
        "docker_runtime_unregistered",
        "opensandbox_unhealthy",
        "socket_invalid",
        "socket_owner_invalid",
        "socket_mode_invalid",
        "image_reference_invalid",
        "image_digest_missing",
        "service_inactive",
        "runtime_check_failed",
        "validation_internal_error",
    }
)


@dataclass(frozen=True)
class _CommandResult:
    ok: bool
    stdout: str


@dataclass(frozen=True)
class _SocketStatus:
    is_socket: bool
    owner: str
    group: str
    mode: int


def _unit_sections(text: str) -> dict[str, dict[str, list[str]]]:
    sections: dict[str, dict[str, list[str]]] = {}
    current: dict[str, list[str]] | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            name = line[1:-1]
            if not name or name in sections:
                raise ValueError
            current = sections.setdefault(name, {})
            continue
        if current is None:
            raise ValueError
        key, separator, value = line.partition("=")
        if not separator or not key or "\x00" in value:
            raise ValueError
        current.setdefault(key, []).append(value)
    if "Service" not in sections:
        raise ValueError
    return sections


def _service_values(
    unit: dict[str, dict[str, list[str]]], key: str
) -> list[str]:
    return unit["Service"].get(key, [])


def _service_words(
    unit: dict[str, dict[str, list[str]]], key: str
) -> set[str]:
    return {
        word
        for value in _service_values(unit, key)
        for word in value.split()
    }


def _single(unit: dict[str, dict[str, list[str]]], key: str) -> str | None:
    values = _service_values(unit, key)
    return values[0] if len(values) == 1 else None


def _section_words(
    unit: dict[str, dict[str, list[str]]], section: str, key: str
) -> set[str]:
    return {
        word
        for value in unit.get(section, {}).get(key, [])
        for word in value.split()
    }


def _hardened(unit: dict[str, dict[str, list[str]]]) -> bool:
    return all(_single(unit, key) == value for key, value in _REQUIRED_HARDENING.items())


def _load_assets(root: Path) -> dict[str, str] | None:
    assets: dict[str, str] = {}
    try:
        for relative in _ASSET_PATHS:
            path = root / relative
            if not path.is_file() or path.is_symlink():
                return None
            assets[relative] = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    return assets


def _validate_config(text: str) -> str | None:
    try:
        config = tomllib.loads(text)
    except Exception:
        return "config_malformed"
    if config.get("server", {}).get("host") != "127.0.0.1":
        return "server_not_loopback"
    if config.get("server", {}).get("api_key") != "${OPEN_SANDBOX_API_KEY}":
        return "api_key_template_invalid"
    if config.get("runtime") != {
        "type": "docker",
        "execd_image": "opensandbox/execd:v1.0.21",
    }:
        return "runtime_not_docker"
    if config.get("secure_runtime") != {
        "type": "gvisor",
        "docker_runtime": "runsc",
    }:
        return "runtime_not_gvisor"
    docker = config.get("docker", {})
    if (
        docker.get("network_mode") != "bridge"
        or type(docker.get("pids_limit")) is not int
        or docker.get("pids_limit") != 128
        or docker.get("no_new_privileges") is not True
        or set(docker.get("drop_capabilities", ())) != _DANGEROUS_CAPABILITIES
    ):
        return "docker_policy_unsafe"
    if config.get("storage", {}).get("allowed_host_paths") != [
        "/var/lib/opensandbox/approved-empty"
    ]:
        return "host_path_allowlist_unsafe"
    return None


def _validate_opensandbox_unit(unit: dict[str, dict[str, list[str]]]) -> bool:
    return bool(
        _hardened(unit)
        and _single(unit, "User") == "medchat-opensandbox"
        and _single(unit, "Group") == "medchat-opensandbox"
        and _single(unit, "EnvironmentFile") == "/etc/medchat/opensandbox.env"
        and _single(unit, "RuntimeDirectory") == "opensandbox"
        and _single(unit, "RuntimeDirectoryMode") == "0700"
        and _single(unit, "RestrictAddressFamilies") == "AF_UNIX AF_INET AF_INET6"
        and _service_words(unit, "SupplementaryGroups") == {"docker"}
        and "/var/run/docker.sock" in _service_words(unit, "ReadWritePaths")
        and "/var/lib/opensandbox/approved-empty"
        in _service_words(unit, "InaccessiblePaths")
    )


def _validate_broker_unit(unit: dict[str, dict[str, list[str]]]) -> bool:
    return bool(
        _hardened(unit)
        and _single(unit, "User") == "medchat-sandbox"
        and _single(unit, "Group") == "medchat-sandbox"
        and _single(unit, "Type") == "notify"
        and _single(unit, "NotifyAccess") == "main"
        and _single(unit, "RuntimeDirectory") == "medchat-sandbox"
        and _single(unit, "RuntimeDirectoryMode") == "0750"
        and _single(unit, "EnvironmentFile")
        == "/etc/medchat/sandbox-broker.env"
        and _single(unit, "RestrictAddressFamilies") == "AF_UNIX AF_INET AF_INET6"
        and _service_words(unit, "ReadWritePaths")
        == {"/var/lib/medchat-sandbox", "/run/medchat-sandbox"}
        and "/var/run/docker.sock" not in _service_words(unit, "ReadWritePaths")
        and not _service_words(unit, "SupplementaryGroups")
        and "/etc/medchat/opensandbox.env"
        not in _service_values(unit, "EnvironmentFile")
    )


def _validate_worker_unit(unit: dict[str, dict[str, list[str]]]) -> bool:
    exposed_environment = "\n".join(
        _service_values(unit, "EnvironmentFile")
        + _service_values(unit, "Environment")
    )
    return bool(
        _service_words(unit, "SupplementaryGroups") == {"medchat-sandbox"}
        and "/run/medchat-sandbox" in _service_words(unit, "ReadWritePaths")
        and "/var/run/docker.sock" not in _service_words(unit, "ReadWritePaths")
        and "docker" not in _service_words(unit, "SupplementaryGroups")
        and "opensandbox.env" not in exposed_environment
        and "sandbox-broker.env" not in exposed_environment
        and "OPEN_SANDBOX_API_KEY" not in exposed_environment
        and "medchat-sandbox-broker.service"
        in _section_words(unit, "Unit", "After")
        and "medchat-sandbox-broker.service"
        in _section_words(unit, "Unit", "Requires")
        and "/etc/medchat/sandbox-broker.env"
        in _service_words(unit, "InaccessiblePaths")
    )


def _assigns_persistent_broker_membership(text: str) -> bool:
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "medchat-sandbox" not in line:
            continue
        try:
            tokens = shlex.split(line.removesuffix("\\").rstrip(), comments=True, posix=True)
        except ValueError:
            return True
        token_set = set(tokens)
        if not {"medchat", "medchat-sandbox"}.issubset(token_set):
            continue
        commands = {Path(token).name for token in tokens}
        if commands.intersection({"usermod", "adduser"}):
            return True
        if "gpasswd" in commands and token_set.intersection({"-a", "--add"}):
            return True
    return False


def _validate_installer(text: str) -> bool:
    forbidden = (
        "mktemp",
        "install -d",
        "while [ \"$parent\" != / ]",
        "sed ",
        "eval ",
        ". /etc/medchat/opensandbox.env",
        "source /etc/medchat/opensandbox.env",
        "usermod --append --groups medchat-sandbox medchat",
        "usermod -a -G medchat-sandbox medchat",
        "adduser medchat medchat-sandbox",
        "gpasswd --add medchat medchat-sandbox",
        "gpasswd -a medchat medchat-sandbox",
    )
    required = (
        "set +x",
        "set -eu",
        "umask 077",
        "/etc/medchat/opensandbox.env",
        "/etc/medchat/opensandbox.toml",
        'if [ "$#" -eq 0 ]; then',
        "DESTDIR=/",
        "approved-empty",
        "def ensure_directory(",
        "require_empty=True",
        "def read_source(",
        '"OPEN_SANDBOX_API_KEY=" + secret + "\\n"',
        "opensandbox_uid,",
        "opensandbox_gid,",
        "os.lstat",
        "stat.S_ISDIR",
        "O_NOFOLLOW",
        "os.fchmod",
        "os.fsync",
        "systemctl daemon-reload",
        "GROUP_MIGRATION_INTENT=remove-medchat-from-medchat-sandbox",
        "remove_legacy_medchat_membership",
        "gpasswd --delete medchat medchat-sandbox",
        "group_migration_intent = sys.argv[5]",
        'group_migration_intent != "remove-medchat-from-medchat-sandbox"',
    )
    return bool(
        all(value in text for value in required)
        and not any(value in text for value in forbidden)
        and not _assigns_persistent_broker_membership(text)
    )


def _validate_environment_example(text: str) -> bool:
    values: dict[str, str] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        if not separator or name in values:
            return False
        values[name] = value
    return bool(
        values
        == {
            "OPEN_SANDBOX_API_KEY": "replace-with-random-secret",
            "MEDCHAT_SANDBOX_IMAGE": (
                "registry.invalid/medchat-docking:approved@sha256:" + "0" * 64
            ),
        }
    )


def validate_static(root: Path) -> str | None:
    """Return a stable failure code, or ``None`` when static assets are safe."""

    try:
        root = Path(root)
        assets = _load_assets(root)
        if assets is None:
            return "asset_missing"
        config_code = _validate_config(
            assets["deployment/opensandbox/sandbox.toml"]
        )
        if config_code is not None:
            return config_code
        try:
            opensandbox = _unit_sections(
                assets["deployment/opensandbox/medchat-opensandbox.service"]
            )
            broker = _unit_sections(
                assets["deployment/opensandbox/medchat-sandbox-broker.service"]
            )
            worker = _unit_sections(
                assets["deployment/medchat-temporal-worker.service"]
            )
        except ValueError:
            return "broker_unit_unsafe"
        if not _validate_opensandbox_unit(opensandbox):
            return "opensandbox_unit_unsafe"
        if not _validate_broker_unit(broker):
            return "broker_unit_unsafe"
        if not _validate_worker_unit(worker):
            return "worker_unit_unsafe"
        if not _validate_installer(assets["deployment/opensandbox/install.sh"]):
            return "installer_unsafe"
        if not _validate_environment_example(
            assets["deployment/opensandbox/opensandbox.env.example"]
        ):
            return "env_template_unsafe"
        return None
    except Exception:
        return "validation_internal_error"


def _run_command(argv: tuple[str, ...]) -> _CommandResult:
    """Run one fixed command with bounded time and captured output."""

    if not argv or any(not isinstance(part, str) or not part for part in argv):
        return _CommandResult(False, "")
    process: subprocess.Popen[bytes] | None = None
    output = bytearray()
    try:
        process = subprocess.Popen(
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            shell=False,
            close_fds=True,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C"},
        )
        assert process.stdout is not None

        def read_bounded() -> None:
            output.extend(process.stdout.read(_OUTPUT_LIMIT + 1))

        reader = threading.Thread(target=read_bounded, daemon=True)
        reader.start()
        reader.join(_COMMAND_TIMEOUT_SECONDS)
        if reader.is_alive() or len(output) > _OUTPUT_LIMIT:
            process.kill()
            reader.join(1.0)
            process.wait(timeout=1.0)
            return _CommandResult(False, "")
        return_code = process.wait(timeout=1.0)
        decoded = bytes(output).decode("utf-8", errors="replace")
        return _CommandResult(return_code == 0, decoded)
    except (OSError, subprocess.SubprocessError, ValueError):
        if process is not None:
            try:
                process.kill()
                process.wait(timeout=1.0)
            except (OSError, subprocess.SubprocessError):
                pass
        return _CommandResult(False, "")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _opensandbox_health_is_ok() -> bool:
    try:
        opener = urllib.request.build_opener(_NoRedirect)
        request = urllib.request.Request(
            "http://127.0.0.1:8080/health",
            method="GET",
            headers={"Accept": "application/json"},
        )
        with opener.open(request, timeout=_HEALTH_TIMEOUT_SECONDS) as response:
            body = response.read(513)
            return response.status == 200 and len(body) <= 512
    except (OSError, ValueError, urllib.error.URLError):
        return False


def _broker_socket_status() -> _SocketStatus:
    try:
        if pwd is None or grp is None:
            return _SocketStatus(False, "", "", 0)
        metadata = os.lstat(_SOCKET_PATH)
        owner = pwd.getpwuid(metadata.st_uid).pw_name
        group = grp.getgrgid(metadata.st_gid).gr_name
        return _SocketStatus(
            stat.S_ISSOCK(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode),
            owner,
            group,
            stat.S_IMODE(metadata.st_mode),
        )
    except (KeyError, OSError):
        return _SocketStatus(False, "", "", 0)


def _parse_runtime_environment(content: str) -> str | None:
    try:
        assignments: dict[str, str] = {}
        for line in content.splitlines():
            if not line or line.startswith("#"):
                continue
            name, separator, value = line.partition("=")
            if not separator or name in assignments or not value:
                return None
            assignments[name] = value
        if set(assignments) != {
            "OPEN_SANDBOX_API_KEY",
            "MEDCHAT_SANDBOX_IMAGE",
        }:
            return None
        key = assignments["OPEN_SANDBOX_API_KEY"]
        if (
            len(key) < 32
            or len(key) > 256
            or any(ord(character) < 32 or ord(character) == 127 for character in key)
        ):
            return None
        image = assignments["MEDCHAT_SANDBOX_IMAGE"]
        match = re.fullmatch(r"([^\s@]+)@sha256:([0-9a-f]{64})", image)
        if match is None:
            return None
        return image
    except (TypeError, ValueError):
        return None


def _runtime_image_reference() -> str | None:
    try:
        if grp is None:
            return None
        metadata = os.lstat(_RUNTIME_ENV)
        broker_gid = grp.getgrnam("medchat-sandbox").gr_gid
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o640
            or metadata.st_uid != 0
            or metadata.st_gid != broker_gid
        ):
            return None
        content = _RUNTIME_ENV.read_text(encoding="utf-8")
        return _parse_runtime_environment(content)
    except (KeyError, OSError, UnicodeError):
        return None


def validate_runtime() -> str | None:
    """Return a stable failure code, or ``None`` after safe runtime probes."""

    try:
        runsc = _run_command(("runsc", "--version"))
        if not runsc.ok or "runsc" not in runsc.stdout.lower():
            return "runsc_unavailable"

        docker_info = _run_command(
            ("docker", "info", "--format", "{{json .Runtimes}}")
        )
        if not docker_info.ok:
            return "docker_runtime_unregistered"
        try:
            runtimes = json.loads(docker_info.stdout)
        except (json.JSONDecodeError, TypeError):
            return "docker_runtime_unregistered"
        if not isinstance(runtimes, dict) or "runsc" not in runtimes:
            return "docker_runtime_unregistered"

        if not _opensandbox_health_is_ok():
            return "opensandbox_unhealthy"

        socket_status = _broker_socket_status()
        if not socket_status.is_socket:
            return "socket_invalid"
        if (
            socket_status.owner != "medchat-sandbox"
            or socket_status.group != "medchat-sandbox"
        ):
            return "socket_owner_invalid"
        if socket_status.mode != 0o660:
            return "socket_mode_invalid"

        image = _runtime_image_reference()
        if image is None:
            return "image_reference_invalid"
        image_match = re.fullmatch(
            r"([^\s@]+)@sha256:([0-9a-f]{64})", image
        )
        if image_match is None:
            return "image_reference_invalid"
        image_uri, expected_digest = image_match.groups()
        prefix, separator, leaf = image_uri.rpartition("/")
        repository_leaf = leaf.rsplit(":", 1)[0] if ":" in leaf else leaf
        repository = (
            prefix + "/" + repository_leaf if separator else repository_leaf
        )
        expected_repo_digest = repository + "@sha256:" + expected_digest
        image_result = _run_command(
            (
                "docker",
                "image",
                "ls",
                "--digests",
                "--format",
                "{{.Repository}}@{{.Digest}}",
            )
        )
        if not image_result.ok:
            return "image_digest_missing"
        repo_digests = image_result.stdout.splitlines()
        if expected_repo_digest not in repo_digests:
            return "image_digest_missing"

        for service in _SERVICES:
            active = _run_command(
                ("systemctl", "is-active", "--quiet", service)
            )
            if not active.ok:
                return "service_inactive"
        return None
    except Exception:
        return "runtime_check_failed"


def _emit(code: str | None) -> int:
    if code is None:
        print("opensandbox_deployment_validation=passed")
        return 0
    safe_code = code if code in _STABLE_CODES else "validation_internal_error"
    print(f"opensandbox_deployment_validation=failed code={safe_code}")
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    """Validate without ever serializing raw failure details."""

    try:
        arguments = list(sys.argv[1:] if argv is None else argv)
        if arguments not in (["--static"], ["--runtime"]):
            return _emit("usage")
        code = validate_static(_ROOT)
        if code is None and arguments == ["--runtime"]:
            code = validate_runtime()
        return _emit(code)
    except BaseException:
        return _emit("validation_internal_error")


if __name__ == "__main__":
    raise SystemExit(main())
