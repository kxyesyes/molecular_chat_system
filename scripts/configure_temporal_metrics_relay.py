from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
RELAY_TEMPLATE = ROOT / "deployment" / "nginx-medchat-temporal-metrics.conf.template"
DEFAULT_FILE_SD_DIRECTORY = "/etc/medchat/generated/temporal-metrics/live/file_sd"
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
PRODUCTION_RELAY_ENV = Path("/etc/medchat/temporal.env")
PRODUCTION_WORKER_ENV = Path("/etc/medchat/medchat.env")
PRODUCTION_GENERATED_ROOT = Path("/etc/medchat/generated/temporal-metrics")
PRODUCTION_NGINX_OUTPUT = Path(
    "/etc/nginx/conf.d/medchat-temporal-worker-metrics.conf"
)
PRODUCTION_NGINX_COMMAND = Path("/usr/sbin/nginx")
GENERATION_ID = re.compile(r"[0-9a-f]{24}\Z")
SAFE_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
PAYLOAD_NAMES = (
    "compose.override.yml",
    "file_sd/worker-targets.json",
    "nginx.conf",
)
GENERATION_FILE_NAMES = frozenset((*PAYLOAD_NAMES, "manifest.json"))
MANIFEST_KEYS = frozenset(("config", "generation_id", "schema_version", "sha256"))
CONFIG_SUMMARY_KEYS = frozenset((
    "compose_project",
    "gateway",
    "namespace",
    "port",
    "prometheus_address",
    "subnet",
    "taskqueue",
))
_LOCKS_GUARD = threading.Lock()
_GENERATION_LOCKS: dict[str, threading.Lock] = {}


class RelayConfigurationError(ValueError):
    def __init__(self, code: str = "E_RELAY_CONFIGURATION") -> None:
        super().__init__(code)
        self.code = code


class AtomicWriteError(OSError):
    def __init__(self, replaced: bool) -> None:
        super().__init__("atomic relay output write failed")
        self.replaced = replaced


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


@dataclass(frozen=True)
class RenderedGeneration:
    generation_id: str
    files: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "files", MappingProxyType(dict(self.files)))


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


def validate_runtime_contract(
    compose_values: Mapping[str, str],
    worker_values: Mapping[str, str],
) -> tuple[str, str]:
    compose_namespace = compose_values.get("TEMPORAL_NAMESPACE")
    worker_namespace = worker_values.get("MEDCHAT_TEMPORAL_NAMESPACE")
    taskqueue = worker_values.get("MEDCHAT_TEMPORAL_DOCKING_QUEUE")
    if (
        compose_namespace != "default"
        or worker_namespace != "default"
        or taskqueue != "medchat-docking"
        or compose_namespace != worker_namespace
    ):
        raise RelayConfigurationError("E_RELAY_RUNTIME_MISMATCH")
    return worker_namespace, taskqueue


def _is_expected_network(network: DockerNetwork, expected_project: str) -> bool:
    return (
        network.name == f"{expected_project}_worker-metrics-scrape"
        and network.labels.get("com.docker.compose.project") == expected_project
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
    file_sd_directory: str | Path = DEFAULT_FILE_SD_DIRECTORY,
) -> str:
    source = json.dumps(str(file_sd_directory))
    return (
        "services:\n"
        "  prometheus:\n"
        "    volumes:\n"
        "      - type: bind\n"
        f"        source: {source}\n"
        "        target: /etc/prometheus/file_sd\n"
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


def render_generation(
    *,
    config: RelayConfig,
    compose_project: str,
    generated_root: str | Path,
    namespace: str,
    taskqueue: str,
    nginx_template: str,
) -> RenderedGeneration:
    if not PROJECT_NAME.fullmatch(compose_project):
        raise _configuration_error()
    if namespace != "default" or taskqueue != "medchat-docking":
        raise RelayConfigurationError("E_RELAY_RUNTIME_MISMATCH")
    root = Path(generated_root).resolve()
    rendered = {
        "compose.override.yml": render_compose_override(
            config, root / "live" / "file_sd"
        ),
        "file_sd/worker-targets.json": render_worker_targets(config),
        "nginx.conf": render_nginx_config(config, nginx_template),
    }
    hashes = {
        name: hashlib.sha256(content.encode("utf-8")).hexdigest()
        for name, content in rendered.items()
    }
    summary = {
        "compose_project": compose_project,
        "gateway": str(config.gateway),
        "namespace": namespace,
        "port": config.port,
        "prometheus_address": str(config.prometheus_address),
        "subnet": str(config.subnet),
        "taskqueue": taskqueue,
    }
    generation_id = compute_generation_id(summary, hashes)
    manifest = {
        "config": summary,
        "generation_id": generation_id,
        "schema_version": 1,
        "sha256": hashes,
    }
    return RenderedGeneration(
        generation_id=generation_id,
        files={
            **rendered,
            "manifest.json": json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        },
    )


def compute_generation_id(
    config: Mapping[str, object],
    hashes: Mapping[str, str],
) -> str:
    identity = json.dumps(
        {"config": dict(config), "sha256": dict(hashes)},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def _validate_config_summary(summary: object) -> dict[str, object]:
    if not isinstance(summary, dict) or set(summary) != CONFIG_SUMMARY_KEYS:
        raise RelayConfigurationError("E_RELAY_MANIFEST")
    clean = dict(summary)
    if (
        not isinstance(clean["compose_project"], str)
        or not PROJECT_NAME.fullmatch(clean["compose_project"])
        or clean["namespace"] != "default"
        or clean["taskqueue"] != "medchat-docking"
        or not isinstance(clean["port"], int)
        or isinstance(clean["port"], bool)
        or not 1024 <= clean["port"] <= 65535
    ):
        raise RelayConfigurationError("E_RELAY_MANIFEST")
    try:
        subnet = ipaddress.ip_network(clean["subnet"], strict=True)
        gateway = ipaddress.ip_address(clean["gateway"])
        prometheus = ipaddress.ip_address(clean["prometheus_address"])
    except (TypeError, ValueError):
        raise RelayConfigurationError("E_RELAY_MANIFEST") from None
    if (
        not isinstance(subnet, ipaddress.IPv4Network)
        or not isinstance(gateway, ipaddress.IPv4Address)
        or not isinstance(prometheus, ipaddress.IPv4Address)
        or gateway not in subnet
        or prometheus not in subnet
        or gateway == prometheus
    ):
        raise RelayConfigurationError("E_RELAY_MANIFEST")
    return clean


def _validate_generation_files(
    generation_id: str,
    files: Mapping[str, str],
) -> dict[str, object]:
    if (
        not GENERATION_ID.fullmatch(generation_id)
        or not isinstance(files, Mapping)
        or set(files) != GENERATION_FILE_NAMES
        or any(not isinstance(value, str) for value in files.values())
    ):
        raise RelayConfigurationError("E_RELAY_MANIFEST")
    try:
        manifest = json.loads(files["manifest.json"])
    except (TypeError, json.JSONDecodeError):
        raise RelayConfigurationError("E_RELAY_MANIFEST") from None
    if (
        not isinstance(manifest, dict)
        or set(manifest) != MANIFEST_KEYS
        or manifest["schema_version"] != 1
        or manifest["generation_id"] != generation_id
        or not isinstance(manifest["sha256"], dict)
        or set(manifest["sha256"]) != set(PAYLOAD_NAMES)
    ):
        raise RelayConfigurationError("E_RELAY_MANIFEST")
    summary = _validate_config_summary(manifest["config"])
    actual_hashes: dict[str, str] = {}
    for name in PAYLOAD_NAMES:
        expected = manifest["sha256"].get(name)
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise RelayConfigurationError("E_RELAY_MANIFEST")
        actual = hashlib.sha256(files[name].encode("utf-8")).hexdigest()
        if actual != expected:
            raise RelayConfigurationError("E_RELAY_MANIFEST")
        actual_hashes[name] = actual
    if compute_generation_id(summary, actual_hashes) != generation_id:
        raise RelayConfigurationError("E_RELAY_MANIFEST")
    return manifest


def validate_rendered_generation(generation: RenderedGeneration) -> None:
    _validate_generation_files(generation.generation_id, generation.files)


def _is_reparse_point(metadata: os.stat_result) -> bool:
    attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400)
    return bool(getattr(metadata, "st_file_attributes", 0) & attribute)


def _path_error() -> RelayConfigurationError:
    return RelayConfigurationError("E_RELAY_PATH")


def require_root_owned(
    metadata: os.stat_result,
    *,
    expected_type: str,
) -> None:
    """Fail closed unless a production object is root-owned and immutable to peers."""
    if getattr(metadata, "st_uid", None) != 0:
        raise _path_error()
    if stat.S_IMODE(metadata.st_mode) & 0o022:
        raise _path_error()
    if expected_type == "directory":
        valid_type = stat.S_ISDIR(metadata.st_mode)
    elif expected_type == "file":
        valid_type = stat.S_ISREG(metadata.st_mode)
    else:
        raise _path_error()
    if not valid_type or _is_reparse_point(metadata):
        raise _path_error()


def _safe_component(name: str) -> str:
    if not isinstance(name, str) or not SAFE_COMPONENT.fullmatch(name):
        raise _path_error()
    if name in {".", ".."}:
        raise _path_error()
    return name


def _open_root_directory_at(parent_fd: int, name: str) -> int:
    component = _safe_component(name)
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        descriptor = os.open(component, flags, dir_fd=parent_fd)
    except OSError:
        raise _path_error() from None
    try:
        require_root_owned(os.fstat(descriptor), expected_type="directory")
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def bootstrap_generated_root(parent_fd: int, name: str) -> int:
    """Create only the final production directory beneath an already trusted fd."""
    component = _safe_component(name)
    try:
        return _open_root_directory_at(parent_fd, component)
    except RelayConfigurationError:
        try:
            os.mkdir(component, 0o750, dir_fd=parent_fd)
            os.fsync(parent_fd)
        except FileExistsError:
            pass
        except OSError:
            raise _path_error() from None
    return _open_root_directory_at(parent_fd, component)


def _open_or_create_root_directory_at(parent_fd: int, name: str, mode: int) -> int:
    component = _safe_component(name)
    try:
        descriptor = _open_root_directory_at(parent_fd, component)
    except RelayConfigurationError:
        try:
            os.mkdir(component, mode, dir_fd=parent_fd)
            os.fsync(parent_fd)
        except FileExistsError:
            pass
        except OSError:
            raise RelayConfigurationError("E_RELAY_WRITE") from None
        descriptor = _open_root_directory_at(parent_fd, component)
    try:
        os.fchmod(descriptor, mode)
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        require_root_owned(metadata, expected_type="directory")
        if stat.S_IMODE(metadata.st_mode) != mode:
            raise RelayConfigurationError("E_RELAY_WRITE")
        return descriptor
    except RelayConfigurationError:
        os.close(descriptor)
        raise
    except OSError:
        os.close(descriptor)
        raise RelayConfigurationError("E_RELAY_WRITE") from None


def _read_regular_at(
    parent_fd: int,
    name: str,
    *,
    max_bytes: int = MAX_INSPECTION_BYTES,
) -> str:
    component = _safe_component(name)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(component, flags, dir_fd=parent_fd)
        metadata = os.fstat(descriptor)
        require_root_owned(metadata, expected_type="file")
        if metadata.st_size > max_bytes:
            raise RelayConfigurationError("E_RELAY_MANIFEST")
        data = os.read(descriptor, max_bytes + 1)
        after = os.fstat(descriptor)
        if (
            len(data) > max_bytes
            or _metadata_snapshot(after) != _metadata_snapshot(metadata)
        ):
            raise RelayConfigurationError("E_RELAY_MANIFEST")
        return data.decode("utf-8")
    except RelayConfigurationError:
        raise
    except (OSError, UnicodeError):
        raise RelayConfigurationError("E_RELAY_MANIFEST") from None
    finally:
        if "descriptor" in locals():
            os.close(descriptor)


def _atomic_write_at(parent_fd: int, name: str, content: str, mode: int) -> None:
    component = _safe_component(name)
    temporary_name = f".{component}.{secrets.token_hex(12)}.tmp"
    temporary_exists = False
    replaced = False
    try:
        try:
            existing = os.stat(component, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            require_root_owned(existing, expected_type="file")
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        descriptor = os.open(temporary_name, flags, mode, dir_fd=parent_fd)
        temporary_exists = True
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), mode)
            require_root_owned(os.fstat(stream.fileno()), expected_type="file")
        os.replace(
            temporary_name,
            component,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
        )
        temporary_exists = False
        replaced = True
        os.fsync(parent_fd)
        published = os.stat(
            component,
            dir_fd=parent_fd,
            follow_symlinks=False,
        )
        require_root_owned(published, expected_type="file")
        if stat.S_IMODE(published.st_mode) != mode:
            raise RelayConfigurationError("E_RELAY_WRITE")
    except RelayConfigurationError as error:
        if replaced:
            raise AtomicWriteError(True) from error
        raise
    except OSError as error:
        raise AtomicWriteError(replaced) from error
    finally:
        if temporary_exists:
            try:
                os.unlink(temporary_name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass


def _snapshot_regular_at(parent_fd: int, name: str) -> tuple[bool, str, int]:
    try:
        metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False, "", 0o640
    require_root_owned(metadata, expected_type="file")
    return True, _read_regular_at(parent_fd, name), stat.S_IMODE(metadata.st_mode)


def _restore_regular_at(
    parent_fd: int,
    name: str,
    previous: tuple[bool, str, int],
) -> None:
    existed, content, mode = previous
    if existed:
        _atomic_write_at(parent_fd, name, content, mode)
    else:
        try:
            os.unlink(name, dir_fd=parent_fd)
        except FileNotFoundError:
            pass
        os.fsync(parent_fd)


def _atomic_write_group_at(
    outputs: Sequence[tuple[int, str, str, int]],
) -> None:
    identities = [
        (os.fstat(parent_fd).st_dev, os.fstat(parent_fd).st_ino, name)
        for parent_fd, name, _, _ in outputs
    ]
    if len(identities) != len(set(identities)):
        raise RelayConfigurationError("E_RELAY_WRITE")
    previous = {
        identity: _snapshot_regular_at(parent_fd, name)
        for identity, (parent_fd, name, _, _) in zip(identities, outputs)
    }
    committed: list[tuple[int, str, tuple[int, int, str]]] = []
    try:
        for identity, (parent_fd, name, content, mode) in zip(identities, outputs):
            try:
                _atomic_write_at(parent_fd, name, content, mode)
            except AtomicWriteError as error:
                if error.replaced:
                    committed.append((parent_fd, name, identity))
                raise
            else:
                committed.append((parent_fd, name, identity))
    except (OSError, RelayConfigurationError) as original:
        try:
            for parent_fd, name, identity in reversed(committed):
                _restore_regular_at(parent_fd, name, previous[identity])
        except (OSError, RelayConfigurationError):
            raise RelayConfigurationError("E_RELAY_ROLLBACK") from original
        raise


def validate_trusted_path(
    path: str | Path,
    trusted_root: str | Path,
    *,
    allow_missing_leaf: bool = False,
    enforce_permissions: bool = False,
) -> Path:
    candidate = Path(os.path.abspath(path))
    root = Path(os.path.abspath(trusted_root))
    try:
        candidate.relative_to(root)
    except ValueError:
        raise _path_error() from None

    anchor = Path(candidate.anchor)
    current = anchor
    parts = candidate.parts[1:] if candidate.anchor else candidate.parts
    for index, component in enumerate(parts):
        current = current / component
        is_leaf = index == len(parts) - 1
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            if is_leaf and allow_missing_leaf:
                return candidate
            raise _path_error() from None
        except OSError:
            raise _path_error() from None
        if stat.S_ISLNK(metadata.st_mode) or _is_reparse_point(metadata):
            raise _path_error()
        if not is_leaf and not stat.S_ISDIR(metadata.st_mode):
            raise _path_error()
        if enforce_permissions and stat.S_IMODE(metadata.st_mode) & 0o022:
            raise _path_error()
    return candidate


def validate_production_paths(
    *,
    env_file: Path,
    worker_env_file: Path,
    generated_root: Path,
    nginx_output: Path,
    platform: str | None = None,
) -> None:
    if (os.name if platform is None else platform) != "posix":
        raise _path_error()
    expected = (
        (env_file, PRODUCTION_RELAY_ENV),
        (worker_env_file, PRODUCTION_WORKER_ENV),
        (generated_root, PRODUCTION_GENERATED_ROOT),
        (nginx_output, PRODUCTION_NGINX_OUTPUT),
    )
    if any(Path(actual) != required for actual, required in expected):
        raise _path_error()
    # Production traversal is intentionally deferred to open_production_context:
    # it keeps every trusted parent open and never reopens these full paths.


def parse_env_text(text: str) -> dict[str, str]:
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


def _metadata_snapshot(
    metadata: os.stat_result,
    *,
    platform: str | None = None,
) -> tuple[int, ...]:
    stable = (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
    )
    if (os.name if platform is None else platform) == "nt":
        return stable
    return (*stable, metadata.st_ctime_ns)


def _read_open_env_descriptor(descriptor: int, *, production: bool) -> dict[str, str]:
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or _is_reparse_point(opened)
            or opened.st_size > MAX_ENV_BYTES
        ):
            raise RelayConfigurationError("E_RELAY_ENV")
        if production:
            require_root_owned(opened, expected_type="file")
        data = os.read(descriptor, MAX_ENV_BYTES + 1)
        if len(data) > MAX_ENV_BYTES or b"\0" in data:
            raise RelayConfigurationError("E_RELAY_ENV")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            raise RelayConfigurationError("E_RELAY_ENV") from None
        values = parse_env_text(text)
        after = os.fstat(descriptor)
        if _metadata_snapshot(after) != _metadata_snapshot(opened):
            raise RelayConfigurationError("E_RELAY_ENV")
        if production:
            require_root_owned(after, expected_type="file")
        return values
    except RelayConfigurationError:
        raise
    except OSError:
        raise RelayConfigurationError("E_RELAY_ENV") from None


def read_env_at(parent_fd: int, name: str) -> dict[str, str]:
    component = _safe_component(name)
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(component, flags, dir_fd=parent_fd)
    except OSError:
        raise RelayConfigurationError("E_RELAY_ENV") from None
    try:
        return _read_open_env_descriptor(descriptor, production=True)
    finally:
        os.close(descriptor)


@dataclass
class ProductionContext:
    medchat_fd: int
    generated_fd: int
    nginx_parent_fd: int
    nginx_sbin_fd: int
    nginx_binary_fd: int
    _owned_fds: tuple[int, ...]

    def read_relay_env(self) -> dict[str, str]:
        return read_env_at(self.medchat_fd, PRODUCTION_RELAY_ENV.name)

    def read_worker_env(self) -> dict[str, str]:
        return read_env_at(self.medchat_fd, PRODUCTION_WORKER_ENV.name)

    def verify_nginx_executable_identity(self) -> None:
        try:
            held = os.fstat(self.nginx_binary_fd)
            current = os.stat(
                PRODUCTION_NGINX_COMMAND.name,
                dir_fd=self.nginx_sbin_fd,
                follow_symlinks=False,
            )
            require_root_owned(held, expected_type="file")
            require_root_owned(current, expected_type="file")
        except (OSError, RelayConfigurationError):
            raise _path_error() from None
        if (
            (held.st_dev, held.st_ino) != (current.st_dev, current.st_ino)
            or not held.st_mode & 0o111
            or not current.st_mode & 0o111
        ):
            raise _path_error()

    def close(self) -> None:
        for descriptor in reversed(self._owned_fds):
            try:
                os.close(descriptor)
            except OSError:
                pass


@contextmanager
def open_production_context(arguments: argparse.Namespace):
    validate_production_paths(
        env_file=arguments.env_file,
        worker_env_file=arguments.worker_env_file,
        generated_root=arguments.output_dir,
        nginx_output=arguments.nginx_output,
    )
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise _path_error()
    if arguments.nginx_command != PRODUCTION_NGINX_COMMAND:
        raise _path_error()
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    opened: list[int] = []
    context: ProductionContext | None = None
    try:
        root_fd = os.open("/", flags)
        opened.append(root_fd)
        require_root_owned(os.fstat(root_fd), expected_type="directory")

        etc_fd = _open_root_directory_at(root_fd, "etc")
        opened.append(etc_fd)
        medchat_fd = _open_root_directory_at(etc_fd, "medchat")
        opened.append(medchat_fd)
        generated_parent_fd = _open_root_directory_at(medchat_fd, "generated")
        opened.append(generated_parent_fd)
        generated_fd = bootstrap_generated_root(
            generated_parent_fd, PRODUCTION_GENERATED_ROOT.name
        )
        opened.append(generated_fd)

        nginx_fd = _open_root_directory_at(etc_fd, "nginx")
        opened.append(nginx_fd)
        nginx_parent_fd = _open_root_directory_at(nginx_fd, "conf.d")
        opened.append(nginx_parent_fd)

        usr_fd = _open_root_directory_at(root_fd, "usr")
        opened.append(usr_fd)
        sbin_fd = _open_root_directory_at(usr_fd, "sbin")
        opened.append(sbin_fd)
        binary_flags = (
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        nginx_binary_fd = os.open("nginx", binary_flags, dir_fd=sbin_fd)
        opened.append(nginx_binary_fd)
        binary_metadata = os.fstat(nginx_binary_fd)
        require_root_owned(binary_metadata, expected_type="file")
        if not binary_metadata.st_mode & 0o111:
            raise _path_error()

        context = ProductionContext(
            medchat_fd=medchat_fd,
            generated_fd=generated_fd,
            nginx_parent_fd=nginx_parent_fd,
            nginx_sbin_fd=sbin_fd,
            nginx_binary_fd=nginx_binary_fd,
            _owned_fds=tuple(opened),
        )
        yield context
    except RelayConfigurationError:
        raise
    except OSError:
        raise _path_error() from None
    finally:
        if context is not None:
            context.close()
        else:
            for descriptor in reversed(opened):
                try:
                    os.close(descriptor)
                except OSError:
                    pass


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
            or _metadata_snapshot(opened) != _metadata_snapshot(lexical)
            or _metadata_snapshot(opened) != _metadata_snapshot(current)
        ):
            raise RelayConfigurationError("E_RELAY_ENV")
        data = os.read(descriptor, MAX_ENV_BYTES + 1)
        if len(data) > MAX_ENV_BYTES or b"\0" in data:
            raise RelayConfigurationError("E_RELAY_ENV")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            raise RelayConfigurationError("E_RELAY_ENV") from None
        values = parse_env_text(text)
        after = os.fstat(descriptor)
        current_after = path.lstat()
        if (
            _metadata_snapshot(after) != _metadata_snapshot(opened)
            or _metadata_snapshot(current_after) != _metadata_snapshot(opened)
            or _is_reparse_point(current_after)
        ):
            raise RelayConfigurationError("E_RELAY_ENV")
    finally:
        os.close(descriptor)
    return values


def _run_bounded(argv: Sequence[str]) -> str:
    try:
        process = subprocess.Popen(
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
    except OSError:
        raise RelayConfigurationError("E_RELAY_INSPECTION") from None

    assert process.stdout is not None
    assert process.stderr is not None
    stdout = bytearray()
    stderr = bytearray()
    overflow = threading.Event()
    reader_failed = threading.Event()
    output_lock = threading.Lock()
    output_bytes = 0

    def drain(stream: object, destination: bytearray) -> None:
        nonlocal output_bytes
        try:
            while True:
                chunk = stream.read(64 * 1024)
                if not chunk:
                    return
                with output_lock:
                    remaining = MAX_INSPECTION_BYTES - output_bytes
                    if remaining > 0:
                        accepted = chunk[:remaining]
                        destination.extend(accepted)
                        output_bytes += len(accepted)
                    if len(chunk) > remaining:
                        overflow.set()
                        return
        except (OSError, ValueError):
            reader_failed.set()

    readers = [
        threading.Thread(target=drain, args=(process.stdout, stdout), daemon=True),
        threading.Thread(target=drain, args=(process.stderr, stderr), daemon=True),
    ]
    for reader in readers:
        reader.start()

    deadline = time.monotonic() + INSPECTION_TIMEOUT_SECONDS
    timed_out = False
    while process.poll() is None:
        if overflow.is_set() or reader_failed.is_set():
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            timed_out = True
            break
        overflow.wait(min(0.05, remaining))

    if process.poll() is None:
        try:
            process.terminate()
            process.wait(timeout=1)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
                process.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                pass

    for reader in readers:
        reader.join(timeout=1)
    if any(reader.is_alive() for reader in readers):
        try:
            process.stdout.close()
            process.stderr.close()
        except OSError:
            pass
        for reader in readers:
            reader.join(timeout=1)
    else:
        process.stdout.close()
        process.stderr.close()

    if (
        timed_out
        or overflow.is_set()
        or reader_failed.is_set()
        or any(reader.is_alive() for reader in readers)
        or process.returncode != 0
    ):
        raise RelayConfigurationError("E_RELAY_INSPECTION")
    try:
        return stdout.decode("utf-8")
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
        if destination == "default":
            continue
        if not isinstance(destination, str):
            raise RelayConfigurationError("E_RELAY_INSPECTION")
        try:
            network = ipaddress.ip_network(destination, strict=False)
        except ValueError:
            raise RelayConfigurationError("E_RELAY_INSPECTION") from None
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
                raise RelayConfigurationError("E_RELAY_INSPECTION")
            try:
                subnet = ipaddress.ip_network(subnet_value, strict=True)
            except ValueError:
                raise RelayConfigurationError("E_RELAY_INSPECTION") from None
            if isinstance(subnet, ipaddress.IPv4Network):
                networks.append(DockerNetwork(name, subnet, clean_labels))
    return networks


def _expected_project(values: Mapping[str, str]) -> str:
    project = values.get("COMPOSE_PROJECT_NAME", "temporal")
    if not isinstance(project, str) or not PROJECT_NAME.fullmatch(project):
        raise RelayConfigurationError("E_RELAY_CONFIGURATION")
    return project


def _fsync_parent_directory(parent: Path) -> None:
    if os.name != "posix":
        return
    directory = os.open(
        parent,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _open_directory_path_nofollow(path: str | Path, *, create: bool) -> int:
    directory = Path(os.path.abspath(path))
    if not directory.is_absolute():
        raise OSError("directory path must be absolute")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    descriptor = os.open(directory.anchor, flags)
    try:
        for component in directory.parts[1:]:
            if create:
                try:
                    os.mkdir(component, mode=0o750, dir_fd=descriptor)
                except FileExistsError:
                    pass
            child = os.open(component, flags, dir_fd=descriptor)
            metadata = os.fstat(child)
            if not stat.S_ISDIR(metadata.st_mode):
                os.close(child)
                raise OSError("unsafe directory component")
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _atomic_write_posix(target: Path, content: str, mode: int) -> None:
    parent_descriptor = _open_directory_path_nofollow(target.parent, create=True)
    temporary_name = f".{target.name}.{secrets.token_hex(12)}.tmp"
    replaced = False
    temporary_exists = False
    try:
        try:
            existing = os.stat(
                target.name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            pass
        else:
            if not stat.S_ISREG(existing.st_mode):
                raise OSError("unsafe output target")
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        descriptor = os.open(
            temporary_name,
            flags,
            mode,
            dir_fd=parent_descriptor,
        )
        temporary_exists = True
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), mode)
        os.replace(
            temporary_name,
            target.name,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
        )
        temporary_exists = False
        replaced = True
        os.fsync(parent_descriptor)
    except OSError as error:
        raise AtomicWriteError(replaced) from error
    finally:
        if temporary_exists:
            try:
                os.unlink(temporary_name, dir_fd=parent_descriptor)
            except FileNotFoundError:
                pass
        os.close(parent_descriptor)


def atomic_write(path: str | Path, content: str, mode: int = 0o640) -> None:
    target = Path(os.path.abspath(path))
    if os.name == "posix":
        _atomic_write_posix(target, content, mode)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink():
        raise OSError("unsafe output target")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    temporary = Path(temporary_name)
    replaced = False
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, target)
        replaced = True
        _fsync_parent_directory(target.parent)
    except OSError as error:
        raise AtomicWriteError(replaced) from error
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
            try:
                atomic_write(path, content, mode)
            except AtomicWriteError as error:
                if error.replaced:
                    committed.append(path)
                raise
            else:
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
                    _fsync_parent_directory(path.parent)
            except OSError:
                rollback_failed = True
        if rollback_failed:
            raise OSError("relay output rollback failed") from original
        raise


def _thread_lock_for(root: Path) -> threading.Lock:
    key = os.path.normcase(str(root.resolve(strict=False)))
    with _LOCKS_GUARD:
        return _GENERATION_LOCKS.setdefault(key, threading.Lock())


def _ensure_directory(path: Path, mode: int) -> None:
    if os.name == "posix":
        descriptor = _open_directory_path_nofollow(path, create=True)
        try:
            os.fchmod(descriptor, mode)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    else:
        path.mkdir(parents=True, exist_ok=True, mode=mode)


@contextmanager
def generation_lock(generated_root: str | Path):
    root = Path(generated_root)
    _ensure_directory(root, 0o750)
    if root.is_symlink():
        raise _path_error()
    thread_lock = _thread_lock_for(root)
    with thread_lock:
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
        no_follow = getattr(os, "O_NOFOLLOW", 0)
        flags |= no_follow
        root_descriptor: int | None = None
        try:
            if os.name == "posix":
                root_descriptor = os.open(
                    root,
                    os.O_RDONLY
                    | getattr(os, "O_DIRECTORY", 0)
                    | no_follow,
                )
                descriptor = os.open(
                    ".relay.lock",
                    flags,
                    0o600,
                    dir_fd=root_descriptor,
                )
            else:
                descriptor = os.open(root / ".relay.lock", flags, 0o600)
        except OSError:
            if root_descriptor is not None:
                os.close(root_descriptor)
            raise _path_error() from None
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise _path_error()
            if os.name == "posix":
                os.fchmod(descriptor, 0o600)
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX)
            else:
                os.chmod(root / ".relay.lock", 0o600)
            yield
        finally:
            if os.name == "posix":
                try:
                    import fcntl

                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                except OSError:
                    pass
            os.close(descriptor)
            if root_descriptor is not None:
                os.close(root_descriptor)


@contextmanager
def generation_lock_descriptor(root_fd: int):
    metadata = os.fstat(root_fd)
    require_root_owned(metadata, expected_type="directory")
    key = f"fd:{metadata.st_dev}:{metadata.st_ino}"
    with _LOCKS_GUARD:
        thread_lock = _GENERATION_LOCKS.setdefault(key, threading.Lock())
    with thread_lock:
        flags = (
            os.O_RDWR
            | os.O_CREAT
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        try:
            descriptor = os.open("relay.lock", flags, 0o600, dir_fd=root_fd)
            os.fchmod(descriptor, 0o600)
            require_root_owned(os.fstat(descriptor), expected_type="file")
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX)
        except (OSError, RelayConfigurationError):
            if "descriptor" in locals():
                os.close(descriptor)
            raise _path_error() from None
        try:
            yield
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


def _read_verified_generation_at(root_fd: int, generation_id: str) -> dict[str, str]:
    if not GENERATION_ID.fullmatch(generation_id):
        raise RelayConfigurationError("E_RELAY_MANIFEST")
    descriptors: list[int] = []
    try:
        generations_fd = _open_root_directory_at(root_fd, "generations")
        descriptors.append(generations_fd)
        generation_fd = _open_root_directory_at(generations_fd, generation_id)
        descriptors.append(generation_fd)
        file_sd_fd = _open_root_directory_at(generation_fd, "file_sd")
        descriptors.append(file_sd_fd)
        if set(os.listdir(generation_fd)) != {
            "compose.override.yml", "file_sd", "manifest.json", "nginx.conf"
        } or set(os.listdir(file_sd_fd)) != {"worker-targets.json"}:
            raise RelayConfigurationError("E_RELAY_MANIFEST")
        files = {
            "compose.override.yml": _read_regular_at(generation_fd, "compose.override.yml"),
            "file_sd/worker-targets.json": _read_regular_at(file_sd_fd, "worker-targets.json"),
            "nginx.conf": _read_regular_at(generation_fd, "nginx.conf"),
            "manifest.json": _read_regular_at(generation_fd, "manifest.json"),
        }
        _validate_generation_files(generation_id, files)
        return files
    except RelayConfigurationError as error:
        if error.code == "E_RELAY_PATH":
            raise RelayConfigurationError("E_RELAY_MANIFEST") from None
        raise
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _active_generation_id_at(root_fd: int) -> str | None:
    descriptors: list[int] = []
    try:
        live_fd = _open_root_directory_at(root_fd, "live")
        descriptors.append(live_fd)
        manifest = json.loads(_read_regular_at(live_fd, "manifest.json"))
        generation_id = manifest.get("generation_id")
    except (RelayConfigurationError, json.JSONDecodeError, AttributeError):
        return None
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
    return generation_id if isinstance(generation_id, str) and GENERATION_ID.fullmatch(generation_id) else None


def _remove_valid_generation_at(generations_fd: int, generation_id: str) -> None:
    descriptors: list[int] = []
    try:
        generation_fd = _open_root_directory_at(generations_fd, generation_id)
        descriptors.append(generation_fd)
        file_sd_fd = _open_root_directory_at(generation_fd, "file_sd")
        descriptors.append(file_sd_fd)
        os.unlink("worker-targets.json", dir_fd=file_sd_fd)
        os.close(file_sd_fd)
        descriptors.pop()
        os.rmdir("file_sd", dir_fd=generation_fd)
        for name in ("compose.override.yml", "nginx.conf", "manifest.json"):
            os.unlink(name, dir_fd=generation_fd)
        os.close(generation_fd)
        descriptors.pop()
        os.rmdir(generation_id, dir_fd=generations_fd)
        os.fsync(generations_fd)
    except (OSError, RelayConfigurationError):
        # Exact-shape deletion only; unexpected files or races are retained.
        return
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


def _prune_generations_descriptor_locked(root_fd: int, pending_generation_id: str) -> None:
    if not GENERATION_ID.fullmatch(pending_generation_id):
        raise RelayConfigurationError("E_RELAY_MANIFEST")
    try:
        generations_fd = _open_root_directory_at(root_fd, "generations")
    except RelayConfigurationError:
        return
    try:
        valid: list[tuple[int, str]] = []
        for name in os.listdir(generations_fd):
            if not GENERATION_ID.fullmatch(name):
                continue
            try:
                generation_fd = _open_root_directory_at(generations_fd, name)
                try:
                    modified = os.fstat(generation_fd).st_mtime_ns
                finally:
                    os.close(generation_fd)
                _read_verified_generation_at(root_fd, name)
            except RelayConfigurationError:
                continue
            valid.append((modified, name))
        active = _active_generation_id_at(root_fd)
        protected = {active} if active is not None else set()
        candidates = sorted(
            valid,
            key=lambda item: (item[1] == pending_generation_id, item[0], item[1]),
            reverse=True,
        )
        for _, generation_id in candidates:
            if generation_id not in protected and len(protected) < (5 if active else 4):
                protected.add(generation_id)
        protected.add(pending_generation_id)
        for _, generation_id in valid:
            if generation_id not in protected:
                _remove_valid_generation_at(generations_fd, generation_id)
    finally:
        os.close(generations_fd)


def stage_generation_descriptor(
    generation: RenderedGeneration,
    generated_root_fd: int,
) -> None:
    validate_rendered_generation(generation)
    with generation_lock_descriptor(generated_root_fd):
        descriptors: list[int] = []
        try:
            generations_fd = _open_or_create_root_directory_at(
                generated_root_fd, "generations", 0o750
            )
            descriptors.append(generations_fd)
            generation_fd = _open_or_create_root_directory_at(
                generations_fd, generation.generation_id, 0o750
            )
            descriptors.append(generation_fd)
            generation_file_sd_fd = _open_or_create_root_directory_at(
                generation_fd, "file_sd", 0o750
            )
            descriptors.append(generation_file_sd_fd)
            live_fd = _open_or_create_root_directory_at(generated_root_fd, "live", 0o750)
            descriptors.append(live_fd)
            live_file_sd_fd = _open_or_create_root_directory_at(live_fd, "file_sd", 0o755)
            descriptors.append(live_file_sd_fd)
            _atomic_write_group_at([
                (generation_fd, "compose.override.yml", generation.files["compose.override.yml"], 0o640),
                (generation_file_sd_fd, "worker-targets.json", generation.files["file_sd/worker-targets.json"], 0o644),
                (generation_fd, "nginx.conf", generation.files["nginx.conf"], 0o640),
                (generation_fd, "manifest.json", generation.files["manifest.json"], 0o640),
            ])
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)
        _prune_generations_descriptor_locked(generated_root_fd, generation.generation_id)


def stage_generation(
    generation: RenderedGeneration,
    generated_root: str | Path,
) -> Path:
    validate_rendered_generation(generation)
    root = Path(generated_root)
    with generation_lock(root):
        generation_root = root / "generations" / generation.generation_id
        _ensure_directory(generation_root / "file_sd", 0o750)
        _ensure_directory(root / "live" / "file_sd", 0o755)
        outputs = [
            (
                generation_root / name,
                content,
                0o644 if name == "file_sd/worker-targets.json" else 0o640,
            )
            for name, content in generation.files.items()
        ]
        atomic_write_group(outputs)
        _prune_generations_locked(root, generation.generation_id)
    return generation_root


def _read_verified_generation(root: Path, generation_id: str) -> dict[str, str]:
    if not GENERATION_ID.fullmatch(generation_id):
        raise RelayConfigurationError("E_RELAY_MANIFEST")
    generation_root = root / "generations" / generation_id
    try:
        generation_metadata = generation_root.lstat()
        file_sd_metadata = (generation_root / "file_sd").lstat()
        root_entries = {entry.name for entry in os.scandir(generation_root)}
        file_sd_entries = {
            entry.name for entry in os.scandir(generation_root / "file_sd")
        }
    except OSError:
        raise RelayConfigurationError("E_RELAY_MANIFEST") from None
    if (
        not stat.S_ISDIR(generation_metadata.st_mode)
        or stat.S_ISLNK(generation_metadata.st_mode)
        or _is_reparse_point(generation_metadata)
        or not stat.S_ISDIR(file_sd_metadata.st_mode)
        or stat.S_ISLNK(file_sd_metadata.st_mode)
        or _is_reparse_point(file_sd_metadata)
        or root_entries != {"compose.override.yml", "file_sd", "manifest.json", "nginx.conf"}
        or file_sd_entries != {"worker-targets.json"}
    ):
        raise RelayConfigurationError("E_RELAY_MANIFEST")

    def read_regular(path: Path) -> str:
        try:
            metadata = path.lstat()
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_ISLNK(metadata.st_mode)
                or _is_reparse_point(metadata)
            ):
                raise RelayConfigurationError("E_RELAY_MANIFEST")
            return path.read_text("utf-8")
        except RelayConfigurationError:
            raise
        except (OSError, UnicodeError):
            raise RelayConfigurationError("E_RELAY_MANIFEST") from None

    try:
        manifest_text = read_regular(generation_root / "manifest.json")
        manifest = json.loads(manifest_text)
    except json.JSONDecodeError:
        raise RelayConfigurationError("E_RELAY_MANIFEST") from None
    files: dict[str, str] = {"manifest.json": manifest_text}
    for name in PAYLOAD_NAMES:
        files[name] = read_regular(generation_root / name)
    _validate_generation_files(generation_id, files)
    return files


def _active_generation_id(root: Path) -> str | None:
    try:
        manifest_path = root / "live" / "manifest.json"
        metadata = manifest_path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or _is_reparse_point(metadata)
        ):
            return None
        manifest = json.loads(manifest_path.read_text("utf-8"))
        generation_id = manifest.get("generation_id")
    except (OSError, UnicodeError, json.JSONDecodeError, AttributeError):
        return None
    return generation_id if isinstance(generation_id, str) and GENERATION_ID.fullmatch(generation_id) else None


def _remove_valid_generation(root: Path, generation_id: str) -> None:
    if not GENERATION_ID.fullmatch(generation_id):
        return
    generation_root = root / "generations" / generation_id
    try:
        for name in ("compose.override.yml", "nginx.conf", "manifest.json"):
            (generation_root / name).unlink()
        (generation_root / "file_sd" / "worker-targets.json").unlink()
        (generation_root / "file_sd").rmdir()
        generation_root.rmdir()
        _fsync_parent_directory(root / "generations")
    except OSError:
        # Retention is conservative: unexpected content or races leave the
        # generation in place instead of broadening deletion behavior.
        return


def _prune_generations_locked(root: Path, pending_generation_id: str) -> None:
    if not GENERATION_ID.fullmatch(pending_generation_id):
        raise RelayConfigurationError("E_RELAY_MANIFEST")
    generations_root = root / "generations"
    valid: list[tuple[int, str]] = []
    try:
        entries = list(os.scandir(generations_root))
    except FileNotFoundError:
        return
    except OSError:
        raise RelayConfigurationError("E_RELAY_WRITE") from None
    for entry in entries:
        if not GENERATION_ID.fullmatch(entry.name) or not entry.is_dir(follow_symlinks=False):
            continue
        try:
            _read_verified_generation(root, entry.name)
            modified = entry.stat(follow_symlinks=False).st_mtime_ns
        except (OSError, RelayConfigurationError):
            continue
        valid.append((modified, entry.name))
    active = _active_generation_id(root)
    protected = {active} if active is not None else set()
    candidates = sorted(
        valid,
        key=lambda item: (item[1] == pending_generation_id, item[0], item[1]),
        reverse=True,
    )
    for _, generation_id in candidates:
        if generation_id not in protected and len(protected) < (5 if active else 4):
            protected.add(generation_id)
    protected.add(pending_generation_id)
    for _, generation_id in valid:
        if generation_id not in protected:
            _remove_valid_generation(root, generation_id)


def prune_generations(
    generated_root: str | Path,
    *,
    pending_generation_id: str,
) -> None:
    root = Path(generated_root)
    with generation_lock(root):
        _prune_generations_locked(root, pending_generation_id)


def _restore_file(path: Path, previous: tuple[bool, str, int]) -> None:
    existed, content, mode = previous
    if existed:
        atomic_write(path, content, mode)
    else:
        path.unlink(missing_ok=True)
        _fsync_parent_directory(path.parent)


def activate_generation(
    *,
    generated_root: str | Path,
    generation_id: str,
    nginx_output: str | Path,
    recheck_host_conflicts,
    bridge_ready,
    nginx_validate,
    nginx_reload,
    expected_config: Mapping[str, object] | None = None,
) -> None:
    root = Path(generated_root)
    nginx_path = Path(nginx_output)
    with generation_lock(root):
        files = _read_verified_generation(root, generation_id)
        if expected_config is not None:
            try:
                manifest_config = json.loads(files["manifest.json"])["config"]
            except (json.JSONDecodeError, KeyError, TypeError):
                raise RelayConfigurationError("E_RELAY_MANIFEST") from None
            if manifest_config != dict(expected_config):
                raise RelayConfigurationError("E_RELAY_RUNTIME_MISMATCH")
        try:
            recheck_host_conflicts()
        except RelayConfigurationError:
            raise
        except Exception:
            raise RelayConfigurationError("E_RELAY_INSPECTION") from None
        try:
            if not bridge_ready():
                raise RelayConfigurationError("E_RELAY_ACTIVATION")
        except RelayConfigurationError:
            raise
        except Exception:
            raise RelayConfigurationError("E_RELAY_ACTIVATION") from None

        if nginx_path.exists():
            metadata = nginx_path.stat()
            old_nginx = (
                True,
                nginx_path.read_text("utf-8"),
                stat.S_IMODE(metadata.st_mode),
            )
        else:
            old_nginx = (False, "", 0o640)
        reload_attempted = False
        try:
            atomic_write(nginx_path, files["nginx.conf"], 0o640)
            if not nginx_validate(nginx_path):
                raise RelayConfigurationError("E_RELAY_ACTIVATION")
            reload_attempted = True
            if not nginx_reload():
                raise RelayConfigurationError("E_RELAY_ACTIVATION")
            atomic_write_group([
                (root / "live" / "compose.override.yml", files["compose.override.yml"], 0o640),
                (root / "live" / "manifest.json", files["manifest.json"], 0o640),
                (
                    root / "live" / "file_sd" / "worker-targets.json",
                    files["file_sd/worker-targets.json"],
                    0o644,
                ),
            ])
        except Exception as original:
            try:
                _restore_file(nginx_path, old_nginx)
                if reload_attempted:
                    if not nginx_validate(nginx_path) or not nginx_reload():
                        raise OSError("nginx rollback failed")
            except Exception:
                raise RelayConfigurationError("E_RELAY_ROLLBACK") from original
            if isinstance(original, RelayConfigurationError):
                raise original
            raise RelayConfigurationError("E_RELAY_WRITE") from None


def activate_generation_descriptor(
    *,
    generated_root_fd: int,
    generation_id: str,
    nginx_parent_fd: int,
    recheck_host_conflicts,
    bridge_ready,
    nginx_validate,
    nginx_reload,
    expected_config: Mapping[str, object] | None = None,
) -> None:
    nginx_name = PRODUCTION_NGINX_OUTPUT.name
    with generation_lock_descriptor(generated_root_fd):
        files = _read_verified_generation_at(generated_root_fd, generation_id)
        if expected_config is not None:
            try:
                manifest_config = json.loads(files["manifest.json"])["config"]
            except (json.JSONDecodeError, KeyError, TypeError):
                raise RelayConfigurationError("E_RELAY_MANIFEST") from None
            if manifest_config != dict(expected_config):
                raise RelayConfigurationError("E_RELAY_RUNTIME_MISMATCH")
        try:
            recheck_host_conflicts()
        except RelayConfigurationError:
            raise
        except Exception:
            raise RelayConfigurationError("E_RELAY_INSPECTION") from None
        try:
            if not bridge_ready():
                raise RelayConfigurationError("E_RELAY_ACTIVATION")
        except RelayConfigurationError:
            raise
        except Exception:
            raise RelayConfigurationError("E_RELAY_ACTIVATION") from None

        old_nginx = _snapshot_regular_at(nginx_parent_fd, nginx_name)
        reload_attempted = False
        live_descriptors: list[int] = []
        try:
            _atomic_write_at(nginx_parent_fd, nginx_name, files["nginx.conf"], 0o640)
            if not nginx_validate(PRODUCTION_NGINX_OUTPUT):
                raise RelayConfigurationError("E_RELAY_ACTIVATION")
            reload_attempted = True
            if not nginx_reload():
                raise RelayConfigurationError("E_RELAY_ACTIVATION")
            live_fd = _open_or_create_root_directory_at(generated_root_fd, "live", 0o750)
            live_descriptors.append(live_fd)
            file_sd_fd = _open_or_create_root_directory_at(live_fd, "file_sd", 0o755)
            live_descriptors.append(file_sd_fd)
            _atomic_write_group_at([
                (live_fd, "compose.override.yml", files["compose.override.yml"], 0o640),
                (live_fd, "manifest.json", files["manifest.json"], 0o640),
                (file_sd_fd, "worker-targets.json", files["file_sd/worker-targets.json"], 0o644),
            ])
        except Exception as original:
            try:
                _restore_regular_at(nginx_parent_fd, nginx_name, old_nginx)
                if reload_attempted:
                    if not nginx_validate(PRODUCTION_NGINX_OUTPUT) or not nginx_reload():
                        raise OSError("nginx rollback failed")
            except Exception:
                raise RelayConfigurationError("E_RELAY_ROLLBACK") from original
            if isinstance(original, RelayConfigurationError):
                raise original
            raise RelayConfigurationError("E_RELAY_WRITE") from None
        finally:
            for descriptor in reversed(live_descriptors):
                os.close(descriptor)


def _config_summary(
    config: RelayConfig,
    compose_project: str,
    namespace: str,
    taskqueue: str,
) -> dict[str, object]:
    return {
        "compose_project": compose_project,
        "gateway": str(config.gateway),
        "namespace": namespace,
        "port": config.port,
        "prometheus_address": str(config.prometheus_address),
        "subnet": str(config.subnet),
        "taskqueue": taskqueue,
    }


def _command_succeeds(argv: Sequence[str]) -> bool:
    try:
        _run_bounded(argv)
    except RelayConfigurationError:
        return False
    return True


def _validate_offline_paths(arguments: argparse.Namespace) -> None:
    root = Path(os.path.abspath(arguments.offline_root))
    validate_trusted_path(root, root)
    for env_path in (arguments.env_file, arguments.worker_env_file):
        candidate = Path(os.path.abspath(env_path))
        try:
            candidate.relative_to(root)
        except ValueError:
            raise _path_error() from None
        # Parent trust failures are path errors. The final leaf is opened later
        # with O_NOFOLLOW so a final env symlink has stable E_RELAY_ENV taxonomy.
        validate_trusted_path(candidate.parent, root)
    validate_trusted_path(arguments.output_dir, root, allow_missing_leaf=True)
    nginx_parent = Path(arguments.nginx_output).parent
    validate_trusted_path(nginx_parent, root, allow_missing_leaf=True)
    candidate = Path(os.path.abspath(arguments.nginx_output))
    try:
        candidate.relative_to(root)
    except ValueError:
        raise _path_error() from None


def _read_relay_template() -> str:
    try:
        return RELAY_TEMPLATE.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise RelayConfigurationError("E_RELAY_TEMPLATE") from None


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Configure Temporal worker metrics relay",
        epilog=(
            "Production requires root, existing root-owned trusted parents, and "
            "creates only /etc/medchat/generated/temporal-metrics as the final directory."
        ),
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--render", action="store_true")
    mode.add_argument("--activate", metavar="GENERATION_ID")
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--worker-env-file", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--nginx-output", required=True, type=Path)
    parser.add_argument("--offline-root", type=Path)
    parser.add_argument("--skip-host-conflicts", action="store_true")
    parser.add_argument("--nginx-command", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        arguments = _parse_args(argv)
        if arguments.offline_root is not None:
            if not arguments.render:
                raise _path_error()
            _validate_offline_paths(arguments)
            values = _read_env_file(arguments.env_file)
            worker_values = _read_env_file(arguments.worker_env_file)
            config = parse_relay_config(values)
            namespace, taskqueue = validate_runtime_contract(values, worker_values)
            compose_project = _expected_project(values)
            if not arguments.skip_host_conflicts:
                conflicts = find_conflicts(
                    config,
                    _inspect_routes(),
                    _inspect_docker_networks(),
                    compose_project,
                )
                if conflicts:
                    raise RelayConfigurationError("E_RELAY_CONFLICT")
            generation = render_generation(
                config=config,
                compose_project=compose_project,
                generated_root=arguments.output_dir,
                namespace=namespace,
                taskqueue=taskqueue,
                nginx_template=_read_relay_template(),
            )
            stage_generation(generation, arguments.output_dir)
            print(f"OK E_RELAY_RENDERED generation={generation.generation_id}")
            return 0
        if arguments.skip_host_conflicts:
            raise _path_error()
        with open_production_context(arguments) as context:
            values = context.read_relay_env()
            worker_values = context.read_worker_env()
            config = parse_relay_config(values)
            namespace, taskqueue = validate_runtime_contract(values, worker_values)
            compose_project = _expected_project(values)
            if arguments.render:
                conflicts = find_conflicts(
                    config,
                    _inspect_routes(),
                    _inspect_docker_networks(),
                    compose_project,
                )
                if conflicts:
                    raise RelayConfigurationError("E_RELAY_CONFLICT")
                generation = render_generation(
                    config=config,
                    compose_project=compose_project,
                    generated_root=arguments.output_dir,
                    namespace=namespace,
                    taskqueue=taskqueue,
                    nginx_template=_read_relay_template(),
                )
                stage_generation_descriptor(generation, context.generated_fd)
                print(f"OK E_RELAY_RENDERED generation={generation.generation_id}")
                return 0

            inspected_networks: list[DockerNetwork] = []

            def recheck() -> None:
                fresh_values = context.read_relay_env()
                fresh_worker_values = context.read_worker_env()
                fresh_namespace, fresh_taskqueue = validate_runtime_contract(
                    fresh_values, fresh_worker_values
                )
                fresh_config = parse_relay_config(fresh_values)
                if (
                    fresh_config != config
                    or fresh_namespace != namespace
                    or fresh_taskqueue != taskqueue
                    or _expected_project(fresh_values) != compose_project
                ):
                    raise RelayConfigurationError("E_RELAY_RUNTIME_MISMATCH")
                networks = _inspect_docker_networks()
                conflicts = find_conflicts(
                    config,
                    _inspect_routes(),
                    networks,
                    compose_project,
                )
                if conflicts:
                    raise RelayConfigurationError("E_RELAY_CONFLICT")
                inspected_networks[:] = networks

            def bridge_ready() -> bool:
                return any(
                    network.subnet == config.subnet
                    and _is_expected_network(network, compose_project)
                    for network in inspected_networks
                )

            def checked_nginx_command(*command: str) -> bool:
                context.verify_nginx_executable_identity()
                return _command_succeeds(
                    (str(PRODUCTION_NGINX_COMMAND), *command)
                )

            activate_generation_descriptor(
                generated_root_fd=context.generated_fd,
                generation_id=arguments.activate,
                nginx_parent_fd=context.nginx_parent_fd,
                recheck_host_conflicts=recheck,
                bridge_ready=bridge_ready,
                nginx_validate=lambda _path: checked_nginx_command("-t"),
                nginx_reload=lambda: checked_nginx_command("-s", "reload"),
                expected_config=_config_summary(
                    config, compose_project, namespace, taskqueue
                ),
            )
            print(f"OK E_RELAY_ACTIVATED generation={arguments.activate}")
            return 0
    except RelayConfigurationError as error:
        print(f"ERROR {error.code}", file=sys.stderr)
        return 1
    except (OSError, UnicodeError):
        print("ERROR E_RELAY_WRITE", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
