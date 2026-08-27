"""Fail-closed validation for the OpenSandbox deployment bundle."""

from __future__ import annotations

import hashlib
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
    "medchat-opensandbox-firewall.service",
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
    "deployment/opensandbox/medchat-opensandbox-firewall.service",
    "deployment/opensandbox/configure-firewall.sh",
    "deployment/opensandbox/opensandbox.env.example",
    "deployment/opensandbox/install.sh",
    "deployment/medchat-temporal-worker.service",
)
_PINNED_ASSET_SHA256 = {
    "deployment/opensandbox/sandbox.toml": (
        "03362ff1a7fdea840a23a15397be611c8aca1efeef5b18231b1242368f1f84aa"
    ),
    "deployment/opensandbox/medchat-opensandbox.service": (
        "c9292d8ffb7703a5a23f812719f52b092eba98cca9d289d699ad9bd84b5ffa6f"
    ),
    "deployment/opensandbox/medchat-sandbox-broker.service": (
        "24229d6d5c4181fbcd5162c52515f26e133cf9bf6558788806a1fa43692165fb"
    ),
    "deployment/opensandbox/medchat-opensandbox-firewall.service": (
        "d08a8ed8795d2c505099fd9ddf8660831fc9780d251d46c3f8132d99ce86a3e1"
    ),
    "deployment/opensandbox/configure-firewall.sh": (
        "573a5ca7f4288677189a61cf18a84308be5b713b8368be52abb53eec7344739c"
    ),
    "deployment/opensandbox/opensandbox.env.example": (
        "bdd4b347aacd786c555250f5733566d5fd8723e15c44aeb048af237852d089bf"
    ),
    "deployment/opensandbox/install.sh": (
        "85ac192c30980e4b6fcd13ad1be7de57166bb80c90d3c128bf657458fd599a0d"
    ),
    "deployment/medchat-temporal-worker.service": (
        "15fb8b7faa079b9a33328081c751a75c72a2d68e1b92b676b55150438064f982"
    ),
}
_STABLE_CODES = frozenset(
    {
        "usage",
        "asset_missing",
        "asset_identity_mismatch",
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
        "firewall_unit_unsafe",
        "firewall_asset_unsafe",
        "installer_unsafe",
        "env_template_unsafe",
        "runsc_unavailable",
        "docker_runtime_unregistered",
        "docker_version_unsafe",
        "docker_network_unsafe",
        "firewall_rule_missing",
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


def _exact_values(
    unit: dict[str, dict[str, list[str]]], key: str, expected: tuple[str, ...]
) -> bool:
    return tuple(_service_values(unit, key)) == expected


_PATH_ENVIRONMENT = (
    "PATH=/opt/conda/envs/medchat/bin:/usr/local/sbin:/usr/local/bin:"
    "/usr/sbin:/usr/bin:/sbin:/bin"
)


def _load_assets(root: Path) -> tuple[dict[str, str], dict[str, bytes]] | None:
    assets: dict[str, str] = {}
    raw_assets: dict[str, bytes] = {}
    try:
        for relative in _ASSET_PATHS:
            path = root / relative
            if not path.is_file() or path.is_symlink():
                return None
            raw = path.read_bytes()
            raw_assets[relative] = raw
            assets[relative] = raw.decode("utf-8").replace("\r\n", "\n")
    except (OSError, UnicodeError):
        return None
    return assets, raw_assets


def _asset_identity_matches(assets: dict[str, bytes]) -> bool:
    return bool(
        set(assets) == set(_PINNED_ASSET_SHA256)
        and all(
            hashlib.sha256(content).hexdigest()
            == _PINNED_ASSET_SHA256[relative]
            for relative, content in assets.items()
        )
    )


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
        docker.get("network_mode") != "medchat-opensandbox"
        or type(docker.get("pids_limit")) is not int
        or docker.get("pids_limit") != 128
        or docker.get("no_new_privileges") is not True
        or docker.get("port_range_min") != 40000
        or docker.get("port_range_max") != 60000
        or set(docker.get("drop_capabilities", ())) != _DANGEROUS_CAPABILITIES
    ):
        return "docker_policy_unsafe"
    if config.get("storage", {}).get("allowed_host_paths") != [
        "/var/lib/opensandbox/approved-empty"
    ]:
        return "host_path_allowlist_unsafe"
    if "egress" in config:
        return "docker_policy_unsafe"
    return None


def _validate_opensandbox_unit(unit: dict[str, dict[str, list[str]]]) -> bool:
    exposed = "\n".join(
        _service_values(unit, "EnvironmentFile")
        + _service_values(unit, "Environment")
    )
    return bool(
        _hardened(unit)
        and _single(unit, "User") == "medchat-opensandbox"
        and _single(unit, "Group") == "medchat-opensandbox"
        and _exact_values(
            unit, "EnvironmentFile", ("/etc/medchat/opensandbox.env",)
        )
        and _exact_values(
            unit,
            "Environment",
            (_PATH_ENVIRONMENT, "PYTHONDONTWRITEBYTECODE=1"),
        )
        and _exact_values(unit, "ExecStartPre", ("/usr/bin/runsc --version",))
        and _exact_values(
            unit,
            "ExecStart",
            (
                "/opt/conda/envs/medchat/bin/opensandbox-server --config "
                "/etc/medchat/opensandbox.toml",
            ),
        )
        and not _service_values(unit, "ExecStartPost")
        and _single(unit, "RuntimeDirectory") == "opensandbox"
        and _single(unit, "RuntimeDirectoryMode") == "0700"
        and _single(unit, "RestrictAddressFamilies") == "AF_UNIX AF_INET AF_INET6"
        and _service_words(unit, "SupplementaryGroups") == {"docker"}
        and _service_words(unit, "ReadWritePaths")
        == {"/var/lib/opensandbox", "/run/opensandbox", "/var/run/docker.sock"}
        and _service_words(unit, "ReadOnlyPaths")
        == {"/etc/medchat/opensandbox.env", "/etc/medchat/opensandbox.toml"}
        and _service_words(unit, "InaccessiblePaths")
        == {"/var/lib/opensandbox/approved-empty"}
        and _section_words(unit, "Unit", "After")
        == {
            "network-online.target",
            "docker.service",
            "medchat-opensandbox-firewall.service",
        }
        and _section_words(unit, "Unit", "Requires")
        == {"docker.service", "medchat-opensandbox-firewall.service"}
        and "OPENSANDBOX_SERVER_API_KEY" not in exposed
    )


def _validate_broker_unit(unit: dict[str, dict[str, list[str]]]) -> bool:
    expected_environment = (
        _PATH_ENVIRONMENT,
        "PYTHONDONTWRITEBYTECODE=1",
        "MEDCHAT_SANDBOX_BROKER_STATE=/var/lib/medchat-sandbox",
        "MEDCHAT_SANDBOX_BROKER_SOCKET=/run/medchat-sandbox/broker.sock",
        "OPEN_SANDBOX_DOMAIN=127.0.0.1:8080",
    )
    exposed = "\n".join(
        _service_values(unit, "EnvironmentFile")
        + _service_values(unit, "Environment")
    )
    return bool(
        _hardened(unit)
        and _single(unit, "User") == "medchat-sandbox"
        and _single(unit, "Group") == "medchat-sandbox"
        and _single(unit, "Type") == "notify"
        and _single(unit, "NotifyAccess") == "main"
        and _single(unit, "RuntimeDirectory") == "medchat-sandbox"
        and _single(unit, "RuntimeDirectoryMode") == "0750"
        and _exact_values(
            unit, "EnvironmentFile", ("/etc/medchat/sandbox-broker.env",)
        )
        and _exact_values(unit, "Environment", expected_environment)
        and _exact_values(
            unit,
            "ExecStart",
            ("/opt/conda/envs/medchat/bin/python scripts/run_sandbox_broker.py",),
        )
        and not _service_values(unit, "ExecStartPre")
        and not _service_values(unit, "ExecStartPost")
        and _single(unit, "RestrictAddressFamilies") == "AF_UNIX AF_INET AF_INET6"
        and _service_words(unit, "ReadWritePaths")
        == {"/var/lib/medchat-sandbox", "/run/medchat-sandbox"}
        and _service_words(unit, "ReadOnlyPaths")
        == {
            "/opt/medchat/molecular_chat_system",
            "/opt/conda/envs/medchat",
            "/etc/medchat/sandbox-broker.env",
        }
        and _service_words(unit, "InaccessiblePaths")
        == {
            "/var/run/docker.sock",
            "/etc/medchat/opensandbox.env",
            "/etc/medchat/opensandbox.toml",
            "/var/lib/opensandbox/approved-empty",
        }
        and "/var/run/docker.sock" not in _service_words(unit, "ReadWritePaths")
        and not _service_words(unit, "SupplementaryGroups")
        and "/etc/medchat/opensandbox.env"
        not in _service_values(unit, "EnvironmentFile")
        and "OPENSANDBOX_SERVER_API_KEY" not in exposed
    )


def _validate_worker_unit(unit: dict[str, dict[str, list[str]]]) -> bool:
    exposed_environment = "\n".join(
        _service_values(unit, "EnvironmentFile")
        + _service_values(unit, "Environment")
    )
    return bool(
        _exact_values(unit, "EnvironmentFile", ("/etc/medchat/temporal-worker.env",))
        and _exact_values(
            unit,
            "Environment",
            (_PATH_ENVIRONMENT, "PYTHONDONTWRITEBYTECODE=1"),
        )
        and _exact_values(
            unit,
            "ExecStartPre",
            (
                "/opt/conda/envs/medchat/bin/python "
                "scripts/validate_temporal_worker_production.py --check-config",
            ),
        )
        and _exact_values(
            unit,
            "ExecStart",
            (
                "/opt/conda/envs/medchat/bin/python "
                "scripts/run_temporal_docking_worker.py",
            ),
        )
        and not _service_values(unit, "ExecStartPost")
        and _service_words(unit, "SupplementaryGroups") == {"medchat-sandbox"}
        and _service_words(unit, "ReadWritePaths")
        == {
            "/opt/medchat/molecular_chat_system/scratch",
            "/opt/medchat/molecular_chat_system/temp_docking",
            "/run/medchat-sandbox",
        }
        and _service_words(unit, "ReadOnlyPaths")
        == {
            "/opt/medchat/molecular_chat_system",
            "/opt/conda/envs/medchat",
        }
        and _service_words(unit, "InaccessiblePaths")
        == {
            "/var/run/docker.sock",
            "/etc/medchat/opensandbox.env",
            "/etc/medchat/opensandbox.toml",
            "/etc/medchat/sandbox-broker.env",
            "/var/lib/opensandbox/approved-empty",
        }
        and "/var/run/docker.sock" not in _service_words(unit, "ReadWritePaths")
        and "docker" not in _service_words(unit, "SupplementaryGroups")
        and "opensandbox.env" not in exposed_environment
        and "sandbox-broker.env" not in exposed_environment
        and "OPEN_SANDBOX_API_KEY" not in exposed_environment
        and "OPENSANDBOX_SERVER_API_KEY" not in exposed_environment
        and "medchat-sandbox-broker.service"
        in _section_words(unit, "Unit", "After")
        and "medchat-sandbox-broker.service"
        in _section_words(unit, "Unit", "Requires")
        and "/etc/medchat/sandbox-broker.env"
        in _service_words(unit, "InaccessiblePaths")
    )


def _validate_firewall_unit(unit: dict[str, dict[str, list[str]]]) -> bool:
    exec_directives = {
        key for key in unit["Service"] if key.startswith("Exec")
    }
    return bool(
        _hardened(unit)
        and _single(unit, "Type") == "oneshot"
        and _single(unit, "RemainAfterExit") == "yes"
        and _single(unit, "User") == "root"
        and _single(unit, "Group") == "root"
        and _exact_values(
            unit,
            "ExecStart",
            ("/usr/local/libexec/medchat/configure-opensandbox-firewall",),
        )
        and not _service_values(unit, "ExecStartPre")
        and not _service_values(unit, "ExecStartPost")
        and exec_directives == {"ExecStart"}
        and not _service_values(unit, "Environment")
        and not _service_values(unit, "EnvironmentFile")
        and not _service_values(unit, "ReadWritePaths")
        and _single(unit, "CapabilityBoundingSet") == "CAP_NET_ADMIN"
        and _single(unit, "AmbientCapabilities") == "CAP_NET_ADMIN"
        and _single(unit, "RestrictAddressFamilies")
        == "AF_UNIX AF_INET AF_INET6 AF_NETLINK"
        and _section_words(unit, "Unit", "After") == {"docker.service"}
        and _section_words(unit, "Unit", "Requires") == {"docker.service"}
        and _section_words(unit, "Unit", "Before")
        == {"medchat-opensandbox.service"}
        and _section_words(unit, "Install", "RequiredBy")
        == {"medchat-opensandbox.service"}
    )


def _validate_firewall_script(text: str) -> bool:
    required = (
        "set +x",
        "set -eu",
        "PATH=/usr/sbin:/usr/bin:/sbin:/bin",
        "PORT_RANGE=40000:60000",
        "NETWORK_NAME=medchat-opensandbox",
        "BRIDGE_INTERFACE=br-medchat-sbox",
        "NETWORK_LABEL=com.medchat.opensandbox.network=v1",
        "SANDBOX_LABEL=opensandbox.io/id",
        "MIN_DOCKER_MAJOR=25",
        "MIN_DOCKER_MINOR=0",
        "MIN_DOCKER_PATCH=5",
        "docker version --format '{{.Server.Version}}'",
        "LEGACY_CHAIN=MEDCHAT-OPENSANDBOX",
        "MAX_LEGACY_RULES=64",
        "docker network create --driver bridge --internal --ipv6",
        "docker network inspect",
        "docker container inspect",
        "docker ps -aq --no-trunc",
        '--filter "label=$SANDBOX_LABEL"',
        "com.docker.network.bridge.name=$BRIDGE_INTERFACE",
        "com.docker.network.bridge.enable_icc=false",
        "legacy_sandboxes_present",
        "configure_family iptables 127.0.0.0/8",
        "configure_family ip6tables ::1/128",
        "-S DOCKER-USER",
        '-D "$chain"',
        "-S INPUT",
        "-i \"$BRIDGE_INTERFACE\" ! -o \"$BRIDGE_INTERFACE\"",
        "--ctstate RELATED,ESTABLISHED -j RETURN",
        "--ctstate NEW -j DROP",
        "--ctdir ORIGINAL",
        "--ctorigdstport",
        "-o \"$BRIDGE_INTERFACE\" ! -s \"$loopback_source\"",
        '"$tool" -w "$WAIT_SECONDS" -t raw',
        "-D \"$LEGACY_CHAIN\" -j DROP",
        "-X \"$LEGACY_CHAIN\"",
        "! -i lo -m addrtype --dst-type LOCAL",
        "firewall_configuration_failed",
        "firewall_migration_failed",
        ">/dev/null 2>&1",
    )
    return bool(
        all(value in text for value in required)
        and "--ctstatus" not in text
        and "--ctstate ESTABLISHED,RELATED" not in text
        and "-j ACCEPT" not in text
        and "-F DOCKER-USER" not in text
        and "--flush" not in text
        and "--delete-chain" not in text
    )


def _shell_logical_commands(text: str) -> list[str]:
    commands: list[str] = []
    pending = ""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        continued = line.endswith("\\")
        if continued:
            line = line[:-1].rstrip()
        pending = " ".join(part for part in (pending, line) if part)
        if not continued:
            if pending:
                commands.append(pending)
            pending = ""
    if pending:
        commands.append(pending)
    return commands


def _group_list_contains(value: str, group: str) -> bool:
    return group in {item.strip() for item in value.split(",") if item.strip()}


def _usermod_assigns_group(tokens: list[str], group: str) -> bool:
    if "medchat" not in tokens:
        return False
    for index, token in enumerate(tokens):
        value: str | None = None
        if token in {"-G", "--groups"} and index + 1 < len(tokens):
            value = tokens[index + 1]
        elif token.startswith("--groups="):
            value = token.partition("=")[2]
        elif token.startswith("-") and not token.startswith("--") and "G" in token:
            suffix = token.partition("G")[2]
            if suffix:
                value = suffix
            elif index + 1 < len(tokens):
                value = tokens[index + 1]
        if value is not None and _group_list_contains(value, group):
            return True
    return False


_SHELL_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*")
_MEMBERSHIP_COMMANDS = {"usermod", "adduser", "addgroup", "gpasswd"}
_COMMAND_WRAPPERS = {"env", "command", "sudo"}
_INERT_OUTPUT_COMMANDS = {"echo", "printf", "logger"}


def _shell_command_segments(line: str) -> list[list[str]]:
    lexer = shlex.shlex(line, posix=True, punctuation_chars=";&|")
    lexer.whitespace_split = True
    lexer.commenters = "#"
    segments: list[list[str]] = []
    current: list[str] = []
    for token in lexer:
        if token in {";", "&", "&&", "|", "||"}:
            if current:
                segments.append(current)
                current = []
        else:
            current.append(token)
    if current:
        segments.append(current)
    return segments


def _raw_command_hint(line: str) -> str:
    remaining = line.lstrip()
    while True:
        match = re.match(r"[A-Za-z_][A-Za-z0-9_]*=[^\s]*\s+", remaining)
        if match is None:
            break
        remaining = remaining[match.end() :]
    first = remaining.partition(" ")[0].strip("'\"")
    return Path(first).name


def _partial_command_hints(line: str) -> set[str]:
    lexer = shlex.shlex(line, posix=True, punctuation_chars=";&|")
    lexer.whitespace_split = True
    lexer.commenters = "#"
    hints: set[str] = set()
    current: list[str] = []
    try:
        for token in lexer:
            if token in {";", "&", "&&", "|", "||"}:
                resolved = _actual_command(current)
                if resolved is not None:
                    hints.add(resolved[0])
                current = []
            else:
                current.append(token)
    except ValueError:
        pass
    resolved = _actual_command(current)
    if resolved is not None:
        hints.add(resolved[0])
    if not hints:
        hints.add(_raw_command_hint(line))
    return hints


def _actual_command(tokens: list[str]) -> tuple[str, list[str]] | None:
    index = 0
    while index < len(tokens):
        while index < len(tokens) and _SHELL_ASSIGNMENT.fullmatch(tokens[index]):
            index += 1
        if index >= len(tokens):
            return None
        command = Path(tokens[index]).name
        if command in {"!", "if", "then", "do"}:
            index += 1
            continue
        if command == "env":
            index += 1
            while index < len(tokens):
                token = tokens[index]
                if token == "--":
                    index += 1
                    break
                if _SHELL_ASSIGNMENT.fullmatch(token):
                    index += 1
                    continue
                if token in {"-u", "--unset"}:
                    if index + 1 >= len(tokens):
                        return None
                    index += 2
                    continue
                if token.startswith("-"):
                    index += 1
                    continue
                break
            continue
        if command == "command":
            index += 1
            while index < len(tokens) and tokens[index].startswith("-"):
                if tokens[index] in {"-v", "-V"}:
                    return ("command", tokens[index:])
                index += 1
            continue
        if command == "sudo":
            index += 1
            options_with_values = {"-u", "--user", "-g", "--group", "-h", "--host"}
            while index < len(tokens):
                token = tokens[index]
                if token == "--":
                    index += 1
                    break
                if _SHELL_ASSIGNMENT.fullmatch(token):
                    index += 1
                    continue
                if token in options_with_values:
                    if index + 1 >= len(tokens):
                        return None
                    index += 2
                    continue
                if token.startswith("-"):
                    index += 1
                    continue
                break
            continue
        return (command, tokens[index + 1 :])
    return None


def _assigns_persistent_broker_membership(text: str) -> bool:
    for line in _shell_logical_commands(text):
        if line.startswith("#"):
            continue
        if re.search(r"\b(?:usermod|adduser|addgroup|gpasswd)\b", line) is None:
            continue
        try:
            segments = _shell_command_segments(line)
        except ValueError:
            hints = _partial_command_hints(line)
            if hints and hints.issubset(_INERT_OUTPUT_COMMANDS):
                continue
            return bool(hints.intersection(_MEMBERSHIP_COMMANDS | _COMMAND_WRAPPERS))
        for segment in segments:
            resolved = _actual_command(segment)
            if resolved is None:
                return True
            command, arguments = resolved
            if command in _MEMBERSHIP_COMMANDS:
                if command == "gpasswd" and arguments == [
                    "--delete",
                    "medchat",
                    "medchat-sandbox",
                ]:
                    continue
                return True
            if command == "timeout" and "gpasswd" in arguments:
                index = arguments.index("gpasswd")
                prefix = arguments[:index]
                mutation = [
                    token
                    for token in arguments[index:]
                    if not token.startswith((">", "1>", "2>"))
                    and token not in {"1", "2", "&"}
                ]
                if prefix == [
                    "--signal=KILL",
                    "${SYSTEMCTL_TIMEOUT_SECONDS}s",
                ] and mutation == [
                    "gpasswd",
                    "--delete",
                    "medchat",
                    "medchat-sandbox",
                ]:
                    continue
                return True
    return False


def _live_migration_order_is_safe(text: str) -> bool:
    try:
        quiesce = text.index("\n    quiesce_deployment_services\n")
        revoke = text.index("\n    remove_legacy_medchat_membership", quiesce)
        render = text.index("\n/usr/bin/env -i PATH=", revoke)
        credential = text.index(
            'rooted("/etc/medchat/sandbox-broker.env")', render
        )
        reload_units = text.index("\n    run_systemctl daemon-reload\n", credential)
        restore = text.index(
            "\n    restore_prior_medchat_services", reload_units
        )
        guarded_start = text.rfind('if [ "$DESTDIR" = / ]; then', 0, quiesce)
        guarded_end = text.index("\nfi\n", revoke)
        restore_guard = text.rfind('if [ "$DESTDIR" = / ]; then', 0, reload_units)
        restore_guard_end = text.index("\nfi\n", restore)
    except ValueError:
        return False
    return bool(
        guarded_start < quiesce < revoke < guarded_end < render < credential
        and credential < restore_guard < reload_units < restore < restore_guard_end
    )


def _failure_trap_is_safe(text: str) -> bool:
    try:
        start = text.index("restore_stopped_services_on_failure() {")
        end = text.index("\n}\n", start)
        trap = text[start:end]
        operations = [
            trap.index("force_quiesce_known_medchat_services"),
            trap.index('if [ "$BOTH_SERVICES_INACTIVE" -eq 1 ]'),
            trap.index("remove_legacy_medchat_membership"),
            trap.index("secure_broker_credentials"),
            trap.index("warn_credentials_locked_services_not_restored"),
            trap.index("warn_credential_lockdown_failed_services_not_restored"),
        ]
    except ValueError:
        return False
    return (
        operations == sorted(operations)
        and "restore_prior_medchat_services" not in trap
    )


def _revocation_check_is_fresh(text: str) -> bool:
    try:
        start = text.index("remove_legacy_medchat_membership() {")
        end = text.index("\n}\n", start)
        migration = text[start:end]
        operations = [
            migration.index("GROUP_MEMBERSHIP_REVOKED=0"),
            migration.index("gpasswd --delete medchat medchat-sandbox"),
            migration.index("id -nG medchat"),
            migration.index("GROUP_MEMBERSHIP_REVOKED=1"),
        ]
    except ValueError:
        return False
    return operations == sorted(operations)


def _atomic_commit_order_is_safe(text: str) -> bool:
    try:
        start = text.index("def stage_generation(")
        replace = text.index("os.replace(temporary, path)", start)
        committed = text.index("committed.append", replace)
        fsync = text.index("fsync_parent(path)", replace)
        verify = text.index("installed = path.lstat()", replace)
    except ValueError:
        return False
    return replace < committed < fsync < verify


def _validate_installer(text: str) -> bool:
    forbidden = (
        "mktemp",
        "install -d",
        "while [ \"$parent\" != / ]",
        "sed ",
        "eval ",
        ". /etc/medchat/opensandbox.env",
        "source /etc/medchat/opensandbox.env",
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
        "os.fsync(parent_descriptor)",
        "os.O_EXCL",
        "os.replace",
        "staged.append((path, temporary, content))",
        "for path, temporary, expected_content in staged:",
        "if read_source(path) != expected_content:",
        'if any(b"\\r" in content for content in committed_assets):',
        "def stage_generation(",
        "def rollback_generation(",
        "MEDCHAT_INSTALL_PREVIEW_FAIL_AFTER",
        "MEDCHAT_INSTALL_PREVIEW_FAIL_POINT",
        "preview_fail_after = int(sys.argv[9])",
        "preview_fail_point = sys.argv[10]",
        'if root == Path("/") and preview_fail_after != 0',
        'if root == Path("/") and preview_fail_point != "none"',
        "systemctl daemon-reload",
        "run_systemctl restart medchat-opensandbox-firewall.service",
        "GROUP_MIGRATION_INTENT=remove-medchat-from-medchat-sandbox",
        "remove_legacy_medchat_membership",
        "gpasswd --delete medchat medchat-sandbox",
        "group_migration_intent = sys.argv[8]",
        'group_migration_intent != "remove-medchat-from-medchat-sandbox"',
        "SYSTEMCTL_TIMEOUT_SECONDS=15",
        "quiesce_shared_medchat_services",
        "quiesce_deployment_services",
        "restore_stopped_services_on_failure",
        "run_systemctl daemon-reload",
        '"$SCRIPT_DIR/../medchat-temporal-worker.service"',
        'rooted("/etc/systemd/system/medchat-temporal-worker.service")',
        'rooted("/etc/systemd/system/medchat-opensandbox-firewall.service")',
        'rooted("/usr/local/libexec/medchat/configure-opensandbox-firewall")',
        "SERVICE_STATE_CAPTURED=0",
        "force_quiesce_known_medchat_services",
        (
            "run_systemctl kill --kill-whom=all --signal=SIGKILL "
            "medchat-temporal-worker.service"
        ),
        "run_systemctl kill --kill-whom=all --signal=SIGKILL medchat.service",
        "secure_broker_credentials",
        "BROKER_ENV_DIRECTORY=/etc/medchat",
        "BROKER_ENV_NAME=sandbox-broker.env",
        "credentials_locked_services_not_restored",
        "credential_lockdown_failed_services_not_restored",
        "O_NOFOLLOW",
        "follow_symlinks=False",
        "os.fchmod(descriptor, 0o600)",
        "os.fchown(descriptor, 0, 0)",
        "GROUP_MEMBERSHIP_REVOKED=0",
        're.fullmatch(r"[A-Za-z0-9_-]{32,256}", secret)',
    )
    opensandbox_owner_contract = (
        '            rooted("/etc/medchat/opensandbox.toml"),\n'
        '            rendered.encode("utf-8"),\n'
        "            0o600,\n"
        "            opensandbox_uid,\n"
        "            opensandbox_gid,\n"
    )
    return bool(
        all(value in text for value in required)
        and opensandbox_owner_contract in text
        and not any(value in text for value in forbidden)
        and "os.ftruncate" not in text
        and not _assigns_persistent_broker_membership(text)
        and _live_migration_order_is_safe(text)
        and _failure_trap_is_safe(text)
        and _revocation_check_is_fresh(text)
        and _atomic_commit_order_is_safe(text)
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
            "OPEN_SANDBOX_API_KEY": (
                "REPLACE_WITH_RANDOM_BASE64URL_SECRET_000000000000"
            ),
            "MEDCHAT_SANDBOX_IMAGE": (
                "registry.invalid/medchat-docking:approved@sha256:" + "0" * 64
            ),
        }
    )


def validate_static(root: Path) -> str | None:
    """Return a stable failure code, or ``None`` when static assets are safe."""

    try:
        root = Path(root)
        loaded = _load_assets(root)
        if loaded is None:
            return "asset_missing"
        assets, raw_assets = loaded
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
            firewall = _unit_sections(
                assets[
                    "deployment/opensandbox/medchat-opensandbox-firewall.service"
                ]
            )
        except ValueError:
            return "broker_unit_unsafe"
        if not _validate_opensandbox_unit(opensandbox):
            return "opensandbox_unit_unsafe"
        if not _validate_broker_unit(broker):
            return "broker_unit_unsafe"
        if not _validate_worker_unit(worker):
            return "worker_unit_unsafe"
        if not _validate_firewall_unit(firewall):
            return "firewall_unit_unsafe"
        if not _validate_firewall_script(
            assets["deployment/opensandbox/configure-firewall.sh"]
        ):
            return "firewall_asset_unsafe"
        if not _validate_installer(assets["deployment/opensandbox/install.sh"]):
            return "installer_unsafe"
        if not _validate_environment_example(
            assets["deployment/opensandbox/opensandbox.env.example"]
        ):
            return "env_template_unsafe"
        if not _asset_identity_matches(raw_assets):
            return "asset_identity_mismatch"
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
        if re.fullmatch(r"[A-Za-z0-9_-]{32,256}", key) is None:
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


_NETWORK_NAME = "medchat-opensandbox"
_BRIDGE_INTERFACE = "br-medchat-sbox"
_NETWORK_LABEL = "com.medchat.opensandbox.network"
_SANDBOX_LABEL = "opensandbox.io/id"


def _docker_server_version_is_safe(value: str) -> bool:
    match = re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", value)
    if match is None or len(value) > 32:
        return False
    components = tuple(int(part) for part in match.groups())
    return components >= (25, 0, 5)


def _docker_network_is_safe() -> bool:
    formats = {
        "Name": "{{.Name}}",
        "Driver": "{{.Driver}}",
        "Internal": "{{json .Internal}}",
        "EnableIPv6": "{{json .EnableIPv6}}",
        "Options": "{{json .Options}}",
        "Labels": "{{json .Labels}}",
        "Containers": "{{json .Containers}}",
    }
    network: dict[str, object] = {}
    for name, format_value in formats.items():
        result = _run_command(
            (
                "docker", "network", "inspect", "--format", format_value,
                _NETWORK_NAME,
            )
        )
        if not result.ok:
            return False
        if name in {"Name", "Driver"}:
            network[name] = result.stdout.strip()
            continue
        try:
            network[name] = json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError):
            return False
    if (
        not isinstance(network, dict)
        or network.get("Name") != _NETWORK_NAME
        or network.get("Driver") != "bridge"
        or network.get("Internal") is not True
        or network.get("EnableIPv6") is not True
        or network.get("Options")
        != {
            "com.docker.network.bridge.name": _BRIDGE_INTERFACE,
            "com.docker.network.bridge.enable_icc": "false",
        }
        or network.get("Labels") != {_NETWORK_LABEL: "v1"}
        or not isinstance(network.get("Containers"), dict)
    ):
        return False

    attached = set(network["Containers"])
    if any(re.fullmatch(r"[0-9a-f]{12,64}", item) is None for item in attached):
        return False
    for container_id in sorted(attached):
        label_result = _run_command(
            (
                "docker", "container", "inspect", "--format",
                '{{index .Config.Labels "opensandbox.io/id"}}',
                container_id,
            )
        )
        networks_result = _run_command(
            (
                "docker", "container", "inspect", "--format",
                "{{json .NetworkSettings.Networks}}", container_id,
            )
        )
        if not label_result.ok or not networks_result.ok:
            return False
        try:
            networks = json.loads(networks_result.stdout)
        except (TypeError, json.JSONDecodeError):
            return False
        sandbox_id = label_result.stdout.strip()
        if (
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", sandbox_id)
            is None
            or not isinstance(networks, dict)
            or set(networks) != {_NETWORK_NAME}
        ):
            return False

    labeled_result = _run_command(
        (
            "docker", "ps", "-aq", "--no-trunc", "--filter",
            f"label={_SANDBOX_LABEL}",
        )
    )
    if not labeled_result.ok:
        return False
    labeled = {
        line.strip() for line in labeled_result.stdout.splitlines() if line.strip()
    }
    return bool(
        all(re.fullmatch(r"[0-9a-f]{12,64}", item) for item in labeled)
        and labeled == attached
    )


def _firewall_rules_present() -> bool:
    for executable, loopback in (
        ("iptables", "127.0.0.0/8"),
        ("ip6tables", "::1/128"),
    ):
        docker_rules = (
            (
                "-i", _BRIDGE_INTERFACE, "!", "-o", _BRIDGE_INTERFACE,
                "-m", "conntrack", "--ctstate", "RELATED,ESTABLISHED",
                "-j", "RETURN",
            ),
            (
                "-o", _BRIDGE_INTERFACE, "!", "-s", loopback,
                "-p", "tcp", "-m", "conntrack", "--ctdir", "ORIGINAL",
                "--ctorigdstport", "40000:60000", "-j", "DROP",
            ),
            (
                "-i", _BRIDGE_INTERFACE, "!", "-o", _BRIDGE_INTERFACE,
                "-m", "conntrack", "--ctstate", "NEW", "-j", "DROP",
            ),
        )
        input_rule = (
            "-i", _BRIDGE_INTERFACE, "-m", "conntrack", "--ctstate", "NEW",
            "-j", "DROP",
        )
        docker_state = _run_command(
            (executable, "-w", "5", "-S", "DOCKER-USER")
        )
        input_state = _run_command((executable, "-w", "5", "-S", "INPUT"))
        if not docker_state.ok or not input_state.ok:
            return False
        for position, rule in enumerate(docker_rules, 1):
            expected = "-A DOCKER-USER " + " ".join(rule)
            present = _run_command(
                (executable, "-w", "5", "-C", "DOCKER-USER", *rule)
            )
            positioned = _run_command(
                (executable, "-w", "5", "-S", "DOCKER-USER", str(position))
            )
            if (
                not present.ok
                or not positioned.ok
                or positioned.stdout.strip() != expected
                or docker_state.stdout.splitlines().count(expected) != 1
            ):
                return False
        expected_input = "-A INPUT " + " ".join(input_rule)
        input_present = _run_command(
            (executable, "-w", "5", "-C", "INPUT", *input_rule)
        )
        input_first = _run_command(
            (executable, "-w", "5", "-S", "INPUT", "1")
        )
        if (
            not input_present.ok
            or not input_first.ok
            or input_first.stdout.strip() != expected_input
            or input_state.stdout.splitlines().count(expected_input) != 1
        ):
            return False
    return True


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

        docker_version = _run_command(
            ("docker", "version", "--format", "{{.Server.Version}}")
        )
        if (
            not docker_version.ok
            or not _docker_server_version_is_safe(docker_version.stdout.strip())
        ):
            return "docker_version_unsafe"

        if not _docker_network_is_safe():
            return "docker_network_unsafe"

        if not _firewall_rules_present():
            return "firewall_rule_missing"

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
                "inspect",
                "--format",
                "{{json .RepoDigests}}",
                image,
            )
        )
        if not image_result.ok:
            return "image_digest_missing"
        try:
            repo_digests = json.loads(image_result.stdout)
        except (json.JSONDecodeError, TypeError):
            return "image_digest_missing"
        if (
            not isinstance(repo_digests, list)
            or not all(isinstance(item, str) for item in repo_digests)
            or expected_repo_digest not in repo_digests
        ):
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
