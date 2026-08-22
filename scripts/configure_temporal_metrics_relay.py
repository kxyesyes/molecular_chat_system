from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
RELAY_TEMPLATE = ROOT / "deployment" / "nginx-medchat-temporal-metrics.conf.template"
DEFAULT_WORKER_TARGETS = Path(
    "/etc/medchat/generated/temporal-metrics/worker-targets.json"
)
RELAY_KEYS = (
    "MEDCHAT_TEMPORAL_METRICS_RELAY_SUBNET",
    "MEDCHAT_TEMPORAL_METRICS_RELAY_GATEWAY",
    "MEDCHAT_TEMPORAL_PROMETHEUS_SCRAPE_ADDRESS",
    "MEDCHAT_TEMPORAL_METRICS_RELAY_PORT",
)
PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(value) for value in (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
    )
)
ENV_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
PROJECT_NAME = re.compile(r"[a-z0-9][a-z0-9_-]*\Z")
MAX_ENV_BYTES = 64 * 1024
MAX_INSPECTION_BYTES = 1024 * 1024
MAX_DOCKER_NETWORKS = 256
INSPECTION_TIMEOUT_SECONDS = 5


class RelayConfigurationError(ValueError):
    def __init__(self, code: str = "E_RELAY_CONFIGURATION") -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class RelayConfig:
    subnet: ipaddress.IPv4Network
    gateway: ipaddress.IPv4Address
    prometheus_address: ipaddress.IPv4Address
    port: int


@dataclass(frozen=True)
class DockerNetwork:
    name: str
    subnet: ipaddress.IPv4Network
    labels: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "labels", MappingProxyType(dict(self.labels)))


def _configuration_error() -> RelayConfigurationError:
    return RelayConfigurationError("E_RELAY_CONFIGURATION")


def parse_relay_config(values: Mapping[str, str]) -> RelayConfig:
    present = {key for key in RELAY_KEYS if key in values}
    if present != set(RELAY_KEYS):
        raise _configuration_error()
    try:
        subnet_value = values[RELAY_KEYS[0]]
        gateway_value = values[RELAY_KEYS[1]]
        prometheus_value = values[RELAY_KEYS[2]]
        port_value = values[RELAY_KEYS[3]]
        if not all(isinstance(value, str) and value for value in (
            subnet_value,
            gateway_value,
            prometheus_value,
            port_value,
        )):
            raise ValueError
        subnet = ipaddress.ip_network(subnet_value, strict=True)
        gateway = ipaddress.ip_address(gateway_value)
        prometheus_address = ipaddress.ip_address(prometheus_value)
        if not re.fullmatch(r"[0-9]+", port_value):
            raise ValueError
        port = int(port_value, 10)
    except (TypeError, ValueError):
        raise _configuration_error() from None
    if not isinstance(subnet, ipaddress.IPv4Network):
        raise _configuration_error()
    if not isinstance(gateway, ipaddress.IPv4Address):
        raise _configuration_error()
    if not isinstance(prometheus_address, ipaddress.IPv4Address):
        raise _configuration_error()
    if not 24 <= subnet.prefixlen <= 29:
        raise _configuration_error()
    if not any(subnet.subnet_of(private) for private in PRIVATE_NETWORKS):
        raise _configuration_error()
    unusable = {subnet.network_address, subnet.broadcast_address}
    if gateway not in subnet or gateway in unusable:
        raise _configuration_error()
    if prometheus_address not in subnet or prometheus_address in unusable:
        raise _configuration_error()
    if gateway == prometheus_address:
        raise _configuration_error()
    if not 1024 <= port <= 65535:
        raise _configuration_error()
    return RelayConfig(
        subnet=subnet,
        gateway=gateway,
        prometheus_address=prometheus_address,
        port=port,
    )


def _is_expected_network(network: DockerNetwork, expected_project: str) -> bool:
    return (
        network.labels.get("com.docker.compose.project") == expected_project
        and network.labels.get("com.docker.compose.network")
        == "worker-metrics-scrape"
        and network.labels.get("com.medchat.temporal.metrics-relay") == "true"
    )


def find_conflicts(
    config: RelayConfig,
    routes: Iterable[ipaddress.IPv4Network],
    docker_networks: Iterable[DockerNetwork],
    expected_project: str,
) -> list[str]:
    networks = tuple(docker_networks)
    expected = tuple(
        network
        for network in networks
        if network.subnet == config.subnet
        and _is_expected_network(network, expected_project)
    )
    conflicts: list[str] = []
    for route in routes:
        if not isinstance(route, ipaddress.IPv4Network):
            continue
        if not route.overlaps(config.subnet):
            continue
        if route == config.subnet and expected:
            continue
        conflicts.append(f"route:{route}")
    for network in networks:
        if not network.subnet.overlaps(config.subnet):
            continue
        if network.subnet == config.subnet and _is_expected_network(
            network, expected_project
        ):
            continue
        conflicts.append(f"docker:{network.name}:{network.subnet}")
    return sorted(set(conflicts))


def render_compose_override(
    config: RelayConfig,
    worker_targets_path: str | Path = DEFAULT_WORKER_TARGETS,
) -> str:
    source = json.dumps(str(Path(worker_targets_path).resolve()))
    return (
        "services:\n"
        "  prometheus:\n"
        "    volumes:\n"
        "      - type: bind\n"
        f"        source: {source}\n"
        "        target: /etc/prometheus/file_sd/worker-targets.json\n"
        "        read_only: true\n"
        "    networks:\n"
        "      worker-metrics-scrape:\n"
        f"        ipv4_address: {config.prometheus_address}\n"
        "networks:\n"
        "  worker-metrics-scrape:\n"
        "    driver: bridge\n"
        "    internal: true\n"
        "    labels:\n"
        "      com.medchat.temporal.metrics-relay: \"true\"\n"
        "    ipam:\n"
        "      config:\n"
        f"        - subnet: {config.subnet}\n"
        f"          gateway: {config.gateway}\n"
    )


def render_worker_targets(config: RelayConfig) -> str:
    return json.dumps(
        [{"targets": [f"{config.gateway}:{config.port}"]}],
        indent=2,
        sort_keys=True,
    ) + "\n"


def render_nginx_config(config: RelayConfig, template: str) -> str:
    replacements = {
        "${RELAY_GATEWAY}": str(config.gateway),
        "${RELAY_PORT}": str(config.port),
        "${PROMETHEUS_ADDRESS}": str(config.prometheus_address),
    }
    if any(template.count(token) != 1 for token in replacements):
        raise RelayConfigurationError("E_RELAY_TEMPLATE")
    rendered = template
    for token, value in replacements.items():
        rendered = rendered.replace(token, value)
    if "${" in rendered:
        raise RelayConfigurationError("E_RELAY_TEMPLATE")
    return rendered


def _is_reparse_point(metadata: os.stat_result) -> bool:
    attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400)
    return bool(getattr(metadata, "st_file_attributes", 0) & attribute)


def _read_env_file(path: Path) -> dict[str, str]:
    try:
        lexical = path.lstat()
    except OSError:
        raise RelayConfigurationError("E_RELAY_ENV") from None
    if (
        not stat.S_ISREG(lexical.st_mode)
        or stat.S_ISLNK(lexical.st_mode)
        or _is_reparse_point(lexical)
        or lexical.st_size > MAX_ENV_BYTES
    ):
        raise RelayConfigurationError("E_RELAY_ENV")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is not None:
        flags |= no_follow
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise RelayConfigurationError("E_RELAY_ENV") from None
    try:
        opened = os.fstat(descriptor)
        current = path.lstat()
        if (
            not stat.S_ISREG(opened.st_mode)
            or _is_reparse_point(opened)
            or _is_reparse_point(current)
            or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
        ):
            raise RelayConfigurationError("E_RELAY_ENV")
        data = os.read(descriptor, MAX_ENV_BYTES + 1)
    finally:
        os.close(descriptor)
    if len(data) > MAX_ENV_BYTES or b"\0" in data:
        raise RelayConfigurationError("E_RELAY_ENV")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise RelayConfigurationError("E_RELAY_ENV") from None
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        if not separator or not ENV_KEY.fullmatch(key) or key in values:
            raise RelayConfigurationError("E_RELAY_ENV")
        values[key] = value
    return values


def _run_bounded(argv: Sequence[str]) -> str:
    try:
        result = subprocess.run(
            list(argv),
            capture_output=True,
            timeout=INSPECTION_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise RelayConfigurationError("E_RELAY_INSPECTION") from None
    if (
        result.returncode != 0
        or len(result.stdout) > MAX_INSPECTION_BYTES
        or len(result.stderr) > MAX_INSPECTION_BYTES
    ):
        raise RelayConfigurationError("E_RELAY_INSPECTION")
    try:
        return result.stdout.decode("utf-8")
    except UnicodeDecodeError:
        raise RelayConfigurationError("E_RELAY_INSPECTION") from None


def _inspect_routes() -> list[ipaddress.IPv4Network]:
    try:
        payload = json.loads(_run_bounded(("ip", "-json", "route", "show")))
    except (json.JSONDecodeError, TypeError):
        raise RelayConfigurationError("E_RELAY_INSPECTION") from None
    if not isinstance(payload, list) or len(payload) > 4096:
        raise RelayConfigurationError("E_RELAY_INSPECTION")
    routes: list[ipaddress.IPv4Network] = []
    for item in payload:
        if not isinstance(item, dict):
            raise RelayConfigurationError("E_RELAY_INSPECTION")
        destination = item.get("dst")
        if not isinstance(destination, str) or destination == "default":
            continue
        try:
            network = ipaddress.ip_network(destination, strict=False)
        except ValueError:
            continue
        if isinstance(network, ipaddress.IPv4Network):
            routes.append(network)
    return routes


def _inspect_docker_networks() -> list[DockerNetwork]:
    identifiers = [
        value.strip()
        for value in _run_bounded(("docker", "network", "ls", "--quiet")).splitlines()
        if value.strip()
    ]
    if len(identifiers) > MAX_DOCKER_NETWORKS:
        raise RelayConfigurationError("E_RELAY_INSPECTION")
    if not identifiers:
        return []
    try:
        payload = json.loads(
            _run_bounded(("docker", "network", "inspect", *identifiers))
        )
    except (json.JSONDecodeError, TypeError):
        raise RelayConfigurationError("E_RELAY_INSPECTION") from None
    if not isinstance(payload, list) or len(payload) != len(identifiers):
        raise RelayConfigurationError("E_RELAY_INSPECTION")
    networks: list[DockerNetwork] = []
    for item in payload:
        if not isinstance(item, dict):
            raise RelayConfigurationError("E_RELAY_INSPECTION")
        name = item.get("Name")
        labels = item.get("Labels") or {}
        configurations = (item.get("IPAM") or {}).get("Config") or []
        if not isinstance(name, str) or not isinstance(labels, dict):
            raise RelayConfigurationError("E_RELAY_INSPECTION")
        if not isinstance(configurations, list):
            raise RelayConfigurationError("E_RELAY_INSPECTION")
        clean_labels = {
            str(key): str(value) for key, value in labels.items()
            if isinstance(key, str) and isinstance(value, str)
        }
        for configuration in configurations:
            if not isinstance(configuration, dict):
                raise RelayConfigurationError("E_RELAY_INSPECTION")
            subnet_value = configuration.get("Subnet")
            if not isinstance(subnet_value, str):
                continue
            try:
                subnet = ipaddress.ip_network(subnet_value, strict=True)
            except ValueError:
                continue
            if isinstance(subnet, ipaddress.IPv4Network):
                networks.append(DockerNetwork(name, subnet, clean_labels))
    return networks


def _expected_project(values: Mapping[str, str]) -> str:
    project = values.get("COMPOSE_PROJECT_NAME", "temporal")
    if not isinstance(project, str) or not PROJECT_NAME.fullmatch(project):
        raise RelayConfigurationError("E_RELAY_CONFIGURATION")
    return project


def atomic_write(path: str | Path, content: str, mode: int = 0o640) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink():
        raise OSError("unsafe output target")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, target)
        if os.name == "posix":
            directory = os.open(target.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def atomic_write_group(
    outputs: Sequence[tuple[str | Path, str, int]],
) -> None:
    normalized = [(Path(path), content, mode) for path, content, mode in outputs]
    paths = [path.resolve(strict=False) for path, _, _ in normalized]
    if len(paths) != len(set(paths)):
        raise OSError("duplicate output target")
    previous: dict[Path, tuple[bool, str, int]] = {}
    for path, _, _ in normalized:
        if path.is_symlink():
            raise OSError("unsafe output target")
        if path.exists():
            metadata = path.stat()
            if not stat.S_ISREG(metadata.st_mode):
                raise OSError("unsafe output target")
            previous[path] = (
                True,
                path.read_text(encoding="utf-8"),
                stat.S_IMODE(metadata.st_mode),
            )
        else:
            previous[path] = (False, "", 0o640)
    committed: list[Path] = []
    try:
        for path, content, mode in normalized:
            atomic_write(path, content, mode)
            committed.append(path)
    except OSError as original:
        rollback_failed = False
        for path in reversed(committed):
            existed, content, mode = previous[path]
            try:
                if existed:
                    atomic_write(path, content, mode)
                else:
                    path.unlink(missing_ok=True)
            except OSError:
                rollback_failed = True
        if rollback_failed:
            raise OSError("relay output rollback failed") from original
        raise


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Configure Temporal worker metrics relay")
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--nginx-output", required=True, type=Path)
    parser.add_argument("--check-host-conflicts", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        arguments = _parse_args(argv)
        values = _read_env_file(arguments.env_file)
        config = parse_relay_config(values)
        if arguments.check_host_conflicts:
            conflicts = find_conflicts(
                config,
                _inspect_routes(),
                _inspect_docker_networks(),
                _expected_project(values),
            )
            if conflicts:
                raise RelayConfigurationError("E_RELAY_CONFLICT")
        targets_path = (arguments.output_dir / "worker-targets.json").resolve()
        rendered = (
            (
                arguments.output_dir / "compose.override.yml",
                render_compose_override(config, targets_path),
            ),
            (targets_path, render_worker_targets(config)),
            (
                arguments.nginx_output,
                render_nginx_config(config, RELAY_TEMPLATE.read_text(encoding="utf-8")),
            ),
        )
        atomic_write_group([
            (path, content, 0o640) for path, content in rendered
        ])
    except RelayConfigurationError as error:
        print(f"ERROR {error.code}", file=sys.stderr)
        return 1
    except (OSError, UnicodeError):
        print("ERROR E_RELAY_WRITE", file=sys.stderr)
        return 1
    print("OK E_RELAY_CONFIGURED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
