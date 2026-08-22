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
    identity = json.dumps(
        {"config": summary, "sha256": hashes},
        sort_keys=True,
        separators=(",", ":"),
    )
    generation_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
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


def _is_reparse_point(metadata: os.stat_result) -> bool:
    attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400)
    return bool(getattr(metadata, "st_file_attributes", 0) & attribute)


def _path_error() -> RelayConfigurationError:
    return RelayConfigurationError("E_RELAY_PATH")


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
    validate_trusted_path(
        env_file,
        Path("/etc/medchat"),
        enforce_permissions=True,
    )
    validate_trusted_path(
        worker_env_file,
        Path("/etc/medchat"),
        enforce_permissions=True,
    )
    validate_trusted_path(
        generated_root,
        Path("/etc/medchat"),
        enforce_permissions=True,
    )
    validate_trusted_path(
        nginx_output,
        Path("/etc/nginx/conf.d"),
        allow_missing_leaf=True,
        enforce_permissions=True,
    )


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


def _metadata_snapshot(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


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


def stage_generation(
    generation: RenderedGeneration,
    generated_root: str | Path,
) -> Path:
    if not GENERATION_ID.fullmatch(generation.generation_id):
        raise RelayConfigurationError("E_RELAY_MANIFEST")
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
    return generation_root


def _read_verified_generation(root: Path, generation_id: str) -> dict[str, str]:
    if not GENERATION_ID.fullmatch(generation_id):
        raise RelayConfigurationError("E_RELAY_MANIFEST")
    generation_root = root / "generations" / generation_id
    try:
        manifest_text = (generation_root / "manifest.json").read_text("utf-8")
        manifest = json.loads(manifest_text)
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise RelayConfigurationError("E_RELAY_MANIFEST") from None
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != 1
        or manifest.get("generation_id") != generation_id
        or not isinstance(manifest.get("sha256"), dict)
    ):
        raise RelayConfigurationError("E_RELAY_MANIFEST")
    files: dict[str, str] = {"manifest.json": manifest_text}
    for name in ("compose.override.yml", "file_sd/worker-targets.json", "nginx.conf"):
        expected = manifest["sha256"].get(name)
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise RelayConfigurationError("E_RELAY_MANIFEST")
        try:
            content = (generation_root / name).read_text("utf-8")
        except (OSError, UnicodeError):
            raise RelayConfigurationError("E_RELAY_MANIFEST") from None
        if hashlib.sha256(content.encode("utf-8")).hexdigest() != expected:
            raise RelayConfigurationError("E_RELAY_MANIFEST")
        files[name] = content
    return files


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
        reloaded = False
        try:
            atomic_write(nginx_path, files["nginx.conf"], 0o640)
            if not nginx_validate(nginx_path):
                raise RelayConfigurationError("E_RELAY_ACTIVATION")
            if not nginx_reload():
                raise RelayConfigurationError("E_RELAY_ACTIVATION")
            reloaded = True
            atomic_write_group([
                (root / "live" / "compose.override.yml", files["compose.override.yml"], 0o640),
                (root / "live" / "manifest.json", files["manifest.json"], 0o640),
                (
                    root / "live" / "file_sd" / "worker-targets.json",
                    files["file_sd/worker-targets.json"],
                    0o644,
                ),
            ])
        except (OSError, RelayConfigurationError) as original:
            try:
                _restore_file(nginx_path, old_nginx)
                if reloaded:
                    if not nginx_validate(nginx_path) or not nginx_reload():
                        raise OSError("nginx rollback failed")
            except (OSError, UnicodeError):
                raise RelayConfigurationError("E_RELAY_ROLLBACK") from original
            if isinstance(original, RelayConfigurationError):
                raise original
            raise RelayConfigurationError("E_RELAY_WRITE") from None


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


def _validate_nginx_command(path: Path) -> Path:
    if path != PRODUCTION_NGINX_COMMAND:
        raise _path_error()
    trusted = validate_trusted_path(path, Path("/usr/sbin"), enforce_permissions=True)
    try:
        metadata = trusted.stat()
    except OSError:
        raise _path_error() from None
    if not stat.S_ISREG(metadata.st_mode) or not metadata.st_mode & 0o111:
        raise _path_error()
    return trusted


def _validate_offline_paths(arguments: argparse.Namespace) -> None:
    root = Path(os.path.abspath(arguments.offline_root))
    validate_trusted_path(root, root)
    validate_trusted_path(arguments.env_file, root)
    validate_trusted_path(arguments.worker_env_file, root)
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
    parser = argparse.ArgumentParser(description="Configure Temporal worker metrics relay")
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
        else:
            validate_production_paths(
                env_file=arguments.env_file,
                worker_env_file=arguments.worker_env_file,
                generated_root=arguments.output_dir,
                nginx_output=arguments.nginx_output,
            )
        if arguments.skip_host_conflicts and arguments.offline_root is None:
            raise _path_error()
        values = _read_env_file(arguments.env_file)
        worker_values = _read_env_file(arguments.worker_env_file)
        config = parse_relay_config(values)
        namespace, taskqueue = validate_runtime_contract(values, worker_values)
        compose_project = _expected_project(values)
        if arguments.render and not arguments.skip_host_conflicts:
            conflicts = find_conflicts(
                config,
                _inspect_routes(),
                _inspect_docker_networks(),
                compose_project,
            )
            if conflicts:
                raise RelayConfigurationError("E_RELAY_CONFLICT")
        if arguments.render:
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
        if arguments.nginx_command is None:
            raise _path_error()
        nginx_command = _validate_nginx_command(arguments.nginx_command)
        inspected_networks: list[DockerNetwork] = []

        def recheck() -> None:
            fresh_values = _read_env_file(arguments.env_file)
            fresh_worker_values = _read_env_file(arguments.worker_env_file)
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

        activate_generation(
            generated_root=arguments.output_dir,
            generation_id=arguments.activate,
            nginx_output=arguments.nginx_output,
            recheck_host_conflicts=recheck,
            bridge_ready=bridge_ready,
            nginx_validate=lambda _path: _command_succeeds(
                (str(nginx_command), "-t")
            ),
            nginx_reload=lambda: _command_succeeds(
                (str(nginx_command), "-s", "reload")
            ),
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
