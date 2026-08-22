from __future__ import annotations

import errno
import hashlib
import importlib.util
import ipaddress
import json
import os
import re
import stat
import subprocess
import sys
import threading
import time
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "configure_temporal_metrics_relay.py"
TEMPLATE = ROOT / "deployment" / "nginx-medchat-temporal-metrics.conf.template"
DEFAULT_VALUES = {
    "MEDCHAT_TEMPORAL_METRICS_RELAY_SUBNET": "172.30.95.0/28",
    "MEDCHAT_TEMPORAL_METRICS_RELAY_GATEWAY": "172.30.95.1",
    "MEDCHAT_TEMPORAL_PROMETHEUS_SCRAPE_ADDRESS": "172.30.95.2",
    "MEDCHAT_TEMPORAL_METRICS_RELAY_PORT": "9466",
}
POSIX_ROOT = (
    os.name == "posix"
    and hasattr(os, "geteuid")
    and os.geteuid() == 0
)


def load_relay() -> ModuleType:
    assert SCRIPT.is_file(), SCRIPT.relative_to(ROOT)
    spec = importlib.util.spec_from_file_location("configure_temporal_metrics_relay", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_env(path: Path, values: dict[str, str] | None = None) -> Path:
    selected = DEFAULT_VALUES if values is None else values
    path.write_text(
        "\n".join(f"{key}={value}" for key, value in selected.items()) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return path


def write_worker_env(
    path: Path,
    *,
    namespace: str = "default",
    taskqueue: str = "medchat-docking",
) -> Path:
    path.write_text(
        "\n".join((
            f"MEDCHAT_TEMPORAL_NAMESPACE={namespace}",
            f"MEDCHAT_TEMPORAL_DOCKING_QUEUE={taskqueue}",
        )) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return path


def render_args(root: Path, *, skip_conflicts: bool = False) -> list[str]:
    arguments = [
        "--render",
        "--env-file", str(write_env(root / "temporal.env", {
            "COMPOSE_PROJECT_NAME": "medchat",
            "TEMPORAL_NAMESPACE": "default",
            **DEFAULT_VALUES,
        })),
        "--worker-env-file", str(write_worker_env(root / "medchat.env")),
        "--output-dir", str(root / "generated"),
        "--nginx-output", str(root / "nginx" / "relay.conf"),
        "--offline-root", str(root),
    ]
    if skip_conflicts:
        arguments.append("--skip-host-conflicts")
    return arguments


def run_cli(
    env_file: Path,
    output_dir: Path,
    nginx_output: Path,
    *extra: str,
) -> subprocess.CompletedProcess[str]:
    worker_env = env_file.parent / "medchat-worker.env"
    if not worker_env.exists():
        write_worker_env(worker_env)
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--render",
            "--env-file",
            str(env_file),
            "--worker-env-file",
            str(worker_env),
            "--output-dir",
            str(output_dir),
            "--nginx-output",
            str(nginx_output),
            "--offline-root",
            str(env_file.parent),
            "--skip-host-conflicts",
            *extra,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
    )


def test_relay_config_and_docker_network_are_immutable() -> None:
    relay = load_relay()
    config = relay.parse_relay_config(DEFAULT_VALUES)
    assert config == relay.RelayConfig(
        subnet=ipaddress.ip_network("172.30.95.0/28"),
        gateway=ipaddress.ip_address("172.30.95.1"),
        prometheus_address=ipaddress.ip_address("172.30.95.2"),
        port=9466,
    )
    with pytest.raises(FrozenInstanceError):
        config.port = 9467

    network = relay.DockerNetwork(
        name="medchat_worker-metrics-scrape",
        subnet=config.subnet,
        labels={
            "com.docker.compose.project": "medchat",
            "com.docker.compose.network": "worker-metrics-scrape",
        },
    )
    with pytest.raises(FrozenInstanceError):
        network.name = "changed"
    with pytest.raises(TypeError):
        network.labels["changed"] = "true"


def test_pure_renderers_keep_compose_target_and_nginx_coherent() -> None:
    relay = load_relay()
    yaml = pytest.importorskip("yaml")
    config = relay.parse_relay_config(DEFAULT_VALUES)
    override = yaml.safe_load(relay.render_compose_override(config))
    prometheus = override["services"]["prometheus"]
    assert prometheus["networks"] == {
        "worker-metrics-scrape": {"ipv4_address": "172.30.95.2"}
    }
    assert "ports" not in prometheus
    network = override["networks"]["worker-metrics-scrape"]
    assert network["internal"] is True
    assert network["driver"] == "bridge"
    assert network["ipam"]["config"] == [{
        "subnet": "172.30.95.0/28",
        "gateway": "172.30.95.1",
    }]
    targets = json.loads(relay.render_worker_targets(config))
    assert targets == [{"targets": ["172.30.95.1:9466"]}]
    nginx = relay.render_nginx_config(config, TEMPLATE.read_text(encoding="utf-8"))
    assert "listen 172.30.95.1:9466;" in nginx
    assert "allow 172.30.95.2;" in nginx
    assert "proxy_pass http://127.0.0.1:9465/metrics;" in nginx
    assert "${" not in nginx


def test_compose_mounts_complete_file_sd_directory_read_only() -> None:
    relay = load_relay()
    yaml = pytest.importorskip("yaml")
    config = relay.parse_relay_config(DEFAULT_VALUES)
    file_sd_directory = "/etc/medchat/generated/temporal-metrics/live/file_sd"
    override = yaml.safe_load(
        relay.render_compose_override(config, file_sd_directory)
    )
    mount = override["services"]["prometheus"]["volumes"][0]
    assert mount["source"] == file_sd_directory
    assert mount["target"] == "/etc/prometheus/file_sd"
    assert mount["read_only"] is True


def test_compose_renderer_default_mounts_stable_live_file_sd_directory() -> None:
    relay = load_relay()
    yaml = pytest.importorskip("yaml")
    config = relay.parse_relay_config(DEFAULT_VALUES)
    override = yaml.safe_load(relay.render_compose_override(config))
    mount = override["services"]["prometheus"]["volumes"][0]
    assert mount["source"] == (
        "/etc/medchat/generated/temporal-metrics/live/file_sd"
    )
    assert mount["target"] == "/etc/prometheus/file_sd"


def test_cli_assigns_world_readable_mode_only_to_worker_targets(
    tmp_path: Path,
    monkeypatch,
) -> None:
    relay = load_relay()
    captured: list[tuple[Path, str, int]] = []
    monkeypatch.setattr(relay, "atomic_write_group", lambda outputs: captured.extend(outputs))
    assert relay.main(render_args(tmp_path, skip_conflicts=True)) == 0
    modes = {Path(path).name: mode for path, _content, mode in captured}
    assert modes["worker-targets.json"] == 0o644
    assert modes["compose.override.yml"] == 0o640
    assert modes["nginx.conf"] == 0o640
    assert modes["manifest.json"] == 0o640


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission contract")
def test_worker_targets_posix_mode_is_container_readable(tmp_path: Path) -> None:
    relay = load_relay()
    target = tmp_path / "worker-targets.json"
    relay.atomic_write(target, "[]\n", 0o644)
    assert stat.S_IMODE(target.stat().st_mode) == 0o644


def test_production_writer_uses_nofollow_directory_fd_operations() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "O_DIRECTORY" in source
    assert "O_NOFOLLOW" in source
    assert "dir_fd=" in source
    assert "src_dir_fd=" in source
    assert "dst_dir_fd=" in source


@pytest.mark.parametrize(
    "values",
    [
        {},
        {"MEDCHAT_TEMPORAL_METRICS_RELAY_SUBNET": "172.30.95.0/28"},
        {**DEFAULT_VALUES, "MEDCHAT_TEMPORAL_METRICS_RELAY_SUBNET": "172.30.95.0/23"},
        {**DEFAULT_VALUES, "MEDCHAT_TEMPORAL_METRICS_RELAY_SUBNET": "172.30.95.0/30"},
        {**DEFAULT_VALUES, "MEDCHAT_TEMPORAL_METRICS_RELAY_SUBNET": "8.8.8.0/28"},
        {**DEFAULT_VALUES, "MEDCHAT_TEMPORAL_METRICS_RELAY_SUBNET": "172.30.95.1/28"},
        {**DEFAULT_VALUES, "MEDCHAT_TEMPORAL_METRICS_RELAY_GATEWAY": "172.30.95.0"},
        {**DEFAULT_VALUES, "MEDCHAT_TEMPORAL_METRICS_RELAY_GATEWAY": "172.30.96.1"},
        {
            **DEFAULT_VALUES,
            "MEDCHAT_TEMPORAL_PROMETHEUS_SCRAPE_ADDRESS": "172.30.95.1",
        },
        {**DEFAULT_VALUES, "MEDCHAT_TEMPORAL_METRICS_RELAY_PORT": "1023"},
        {**DEFAULT_VALUES, "MEDCHAT_TEMPORAL_METRICS_RELAY_PORT": "65536"},
        {**DEFAULT_VALUES, "MEDCHAT_TEMPORAL_METRICS_RELAY_PORT": "9e3"},
    ],
)
def test_invalid_relay_groups_fail_closed(values: dict[str, str]) -> None:
    relay = load_relay()
    with pytest.raises(relay.RelayConfigurationError) as error:
        relay.parse_relay_config(values)
    assert error.value.code == "E_RELAY_CONFIGURATION"


@pytest.mark.parametrize(
    ("compose_namespace", "worker_namespace", "worker_queue"),
    [
        ("other", "default", "medchat-docking"),
        ("default", "other", "medchat-docking"),
        ("default", "default", "other-queue"),
    ],
)
def test_runtime_monitoring_contract_fails_closed_on_selector_mismatch(
    compose_namespace: str,
    worker_namespace: str,
    worker_queue: str,
) -> None:
    relay = load_relay()
    with pytest.raises(relay.RelayConfigurationError) as error:
        relay.validate_runtime_contract(
            {"TEMPORAL_NAMESPACE": compose_namespace},
            {
                "MEDCHAT_TEMPORAL_NAMESPACE": worker_namespace,
                "MEDCHAT_TEMPORAL_DOCKING_QUEUE": worker_queue,
            },
        )
    assert error.value.code == "E_RELAY_RUNTIME_MISMATCH"


def test_runtime_monitoring_contract_requires_explicit_matching_values() -> None:
    relay = load_relay()
    with pytest.raises(relay.RelayConfigurationError) as error:
        relay.validate_runtime_contract({}, {})
    assert error.value.code == "E_RELAY_RUNTIME_MISMATCH"
    assert relay.validate_runtime_contract(
        {"TEMPORAL_NAMESPACE": "default"},
        {
            "MEDCHAT_TEMPORAL_NAMESPACE": "default",
            "MEDCHAT_TEMPORAL_DOCKING_QUEUE": "medchat-docking",
        },
    ) == ("default", "medchat-docking")


def test_render_checks_host_conflicts_by_default(tmp_path: Path, monkeypatch) -> None:
    relay = load_relay()

    def fail_inspection() -> list[object]:
        raise relay.RelayConfigurationError("E_RELAY_INSPECTION")

    monkeypatch.setattr(relay, "_inspect_routes", fail_inspection)
    assert relay.main(render_args(tmp_path)) == 1
    assert not (tmp_path / "generated" / "generations").exists()


def test_skip_host_conflicts_is_offline_render_only(tmp_path: Path) -> None:
    relay = load_relay()
    assert relay.main(render_args(tmp_path, skip_conflicts=True)) == 0
    production_like = render_args(tmp_path, skip_conflicts=True)
    offline_index = production_like.index("--offline-root")
    del production_like[offline_index:offline_index + 2]
    assert relay.main(production_like) == 1


def test_cli_reports_path_template_and_write_errors_separately(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    relay = load_relay()
    escaped = render_args(tmp_path, skip_conflicts=True)
    nginx_index = escaped.index("--nginx-output") + 1
    escaped[nginx_index] = str(tmp_path.parent / "escaped-nginx.conf")
    assert relay.main(escaped) == 1
    assert capsys.readouterr().err == "ERROR E_RELAY_PATH\n"

    def template_failure() -> str:
        raise relay.RelayConfigurationError("E_RELAY_TEMPLATE")

    monkeypatch.setattr(relay, "_read_relay_template", template_failure)
    assert relay.main(render_args(tmp_path, skip_conflicts=True)) == 1
    assert capsys.readouterr().err == "ERROR E_RELAY_TEMPLATE\n"

    monkeypatch.setattr(
        relay,
        "_read_relay_template",
        lambda: TEMPLATE.read_text(encoding="utf-8"),
    )
    monkeypatch.setattr(
        relay,
        "stage_generation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("PRIVATE_WRITE")),
    )
    assert relay.main(render_args(tmp_path, skip_conflicts=True)) == 1
    assert capsys.readouterr().err == "ERROR E_RELAY_WRITE\n"


def test_route_and_foreign_docker_overlaps_are_rejected() -> None:
    relay = load_relay()
    config = relay.parse_relay_config(DEFAULT_VALUES)
    route_conflicts = relay.find_conflicts(
        config,
        [ipaddress.ip_network("172.30.0.0/16")],
        [],
        "medchat",
    )
    assert route_conflicts
    foreign = relay.DockerNetwork(
        name="foreign",
        subnet=ipaddress.ip_network("172.30.95.0/28"),
        labels={},
    )
    assert relay.find_conflicts(config, [], [foreign], "medchat")
    assert all("PRIVATE" not in item for item in route_conflicts)


def test_exact_compose_owned_network_and_its_route_are_allowed() -> None:
    relay = load_relay()
    config = relay.parse_relay_config(DEFAULT_VALUES)
    owned = relay.DockerNetwork(
        name="medchat_worker-metrics-scrape",
        subnet=config.subnet,
        labels={
            "com.docker.compose.project": "medchat",
            "com.docker.compose.network": "worker-metrics-scrape",
            "com.docker.compose.version": "2.29.0",
            "com.medchat.temporal.metrics-relay": "true",
        },
    )
    assert relay.find_conflicts(
        config,
        [config.subnet],
        [owned],
        "medchat",
    ) == []


def test_compose_labels_without_relay_ownership_label_are_rejected() -> None:
    relay = load_relay()
    config = relay.parse_relay_config(DEFAULT_VALUES)
    spoofed = relay.DockerNetwork(
        name="medchat_worker-metrics-scrape",
        subnet=config.subnet,
        labels={
            "com.docker.compose.project": "medchat",
            "com.docker.compose.network": "worker-metrics-scrape",
        },
    )
    assert relay.find_conflicts(config, [config.subnet], [spoofed], "medchat")


def test_wrong_name_network_with_copied_ownership_labels_is_rejected() -> None:
    relay = load_relay()
    config = relay.parse_relay_config(DEFAULT_VALUES)
    copied_labels = relay.DockerNetwork(
        name="foreign_worker-metrics-scrape",
        subnet=config.subnet,
        labels={
            "com.docker.compose.project": "medchat",
            "com.docker.compose.network": "worker-metrics-scrape",
            "com.medchat.temporal.metrics-relay": "true",
        },
    )
    assert relay.find_conflicts(
        config,
        [config.subnet],
        [copied_labels],
        "medchat",
    ) == [
        "docker:foreign_worker-metrics-scrape:172.30.95.0/28",
        "route:172.30.95.0/28",
    ]


@pytest.mark.parametrize("destination", [None, "not-a-network", "172.30.95.999/28"])
def test_route_inspection_rejects_malformed_non_default_destinations(
    monkeypatch,
    destination: object,
) -> None:
    relay = load_relay()
    monkeypatch.setattr(
        relay,
        "_run_bounded",
        lambda _argv: json.dumps([{"dst": destination}]),
    )
    with pytest.raises(relay.RelayConfigurationError) as error:
        relay._inspect_routes()
    assert error.value.code == "E_RELAY_INSPECTION"


def test_route_inspection_skips_only_explicit_default_route(monkeypatch) -> None:
    relay = load_relay()
    monkeypatch.setattr(
        relay,
        "_run_bounded",
        lambda _argv: json.dumps([
            {"dst": "default"},
            {"dst": "172.30.0.0/16"},
        ]),
    )
    assert relay._inspect_routes() == [ipaddress.ip_network("172.30.0.0/16")]


def test_docker_inspection_rejects_malformed_subnet(monkeypatch) -> None:
    relay = load_relay()
    responses = iter([
        "network-id\n",
        json.dumps([{
            "Name": "foreign",
            "Labels": {},
            "IPAM": {"Config": [{"Subnet": "172.30.95.999/28"}]},
        }]),
    ])
    monkeypatch.setattr(relay, "_run_bounded", lambda _argv: next(responses))
    with pytest.raises(relay.RelayConfigurationError) as error:
        relay._inspect_docker_networks()
    assert error.value.code == "E_RELAY_INSPECTION"


def test_inspection_output_limit_terminates_process_without_secret_echo(
    tmp_path: Path,
    monkeypatch,
) -> None:
    relay = load_relay()
    marker = tmp_path / "process-finished"
    monkeypatch.setattr(relay, "MAX_INSPECTION_BYTES", 4096)
    program = (
        "import pathlib, sys, time; "
        "sys.stdout.buffer.write(b'PRIVATE_INSPECTION_SECRET' + b'x' * 131072); "
        "sys.stdout.buffer.flush(); "
        "time.sleep(1); "
        f"pathlib.Path({str(marker)!r}).write_text('finished')"
    )
    started = time.monotonic()
    with pytest.raises(relay.RelayConfigurationError) as error:
        relay._run_bounded((sys.executable, "-c", program))
    elapsed = time.monotonic() - started
    assert error.value.code == "E_RELAY_INSPECTION"
    assert "PRIVATE_INSPECTION_SECRET" not in str(error.value)
    assert not marker.exists()
    assert elapsed < 1


def test_inspection_output_limit_applies_to_combined_stdout_and_stderr(
    monkeypatch,
) -> None:
    relay = load_relay()
    monkeypatch.setattr(relay, "MAX_INSPECTION_BYTES", 4096)
    program = (
        "import sys; "
        "sys.stdout.buffer.write(b'x' * 3072); "
        "sys.stderr.buffer.write(b'y' * 3072)"
    )
    with pytest.raises(relay.RelayConfigurationError) as error:
        relay._run_bounded((sys.executable, "-c", program))
    assert error.value.code == "E_RELAY_INSPECTION"


@pytest.mark.parametrize(
    ("content", "expected_error"),
    [
        (
            "\n".join(f"{key}={value}" for key, value in DEFAULT_VALUES.items())
            + "\nMEDCHAT_TEMPORAL_METRICS_RELAY_PORT=9467\n",
            "E_RELAY_ENV",
        ),
        ("MALFORMED_ENV_LINE\n", "E_RELAY_ENV"),
        (
            "MEDCHAT_TEMPORAL_METRICS_RELAY_SUBNET=172.30.95.0/28\n",
            "E_RELAY_CONFIGURATION",
        ),
    ],
)
def test_cli_rejects_duplicate_malformed_and_partial_env(
    tmp_path: Path,
    content: str,
    expected_error: str,
) -> None:
    env_file = tmp_path / "relay.env"
    env_file.write_text(content, encoding="utf-8", newline="\n")
    result = run_cli(env_file, tmp_path / "generated", tmp_path / "relay.conf")
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == f"ERROR {expected_error}\n"


def test_cli_rejects_symlink_env_without_disclosing_target(tmp_path: Path) -> None:
    target = write_env(tmp_path / "PRIVATE_ENV_TARGET")
    link = tmp_path / "relay.env"
    try:
        link.symlink_to(target)
    except OSError as exc:
        if os.name == "nt" and (
            exc.errno in {errno.EPERM, errno.EACCES}
            or getattr(exc, "winerror", None) == 1314
        ):
            pytest.skip("Windows symlink privilege unavailable")
        raise
    result = run_cli(link, tmp_path / "generated", tmp_path / "relay.conf")
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "ERROR E_RELAY_ENV\n"
    assert "PRIVATE_ENV_TARGET" not in result.stderr


def test_cli_rejects_symlink_env_parent_as_path_error(tmp_path: Path) -> None:
    real_parent = tmp_path / "real"
    real_parent.mkdir()
    env_file = write_env(real_parent / "relay.env")
    linked_parent = tmp_path / "linked"
    try:
        linked_parent.symlink_to(real_parent, target_is_directory=True)
    except OSError as error:
        if os.name == "nt" and (
            error.errno in {errno.EPERM, errno.EACCES}
            or getattr(error, "winerror", None) == 1314
        ):
            pytest.skip("Windows symlink privilege unavailable")
        raise
    result = run_cli(
        linked_parent / env_file.name,
        tmp_path / "generated",
        tmp_path / "relay.conf",
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "ERROR E_RELAY_PATH\n"


def test_production_paths_reject_non_posix_activation() -> None:
    relay = load_relay()
    with pytest.raises(relay.RelayConfigurationError) as error:
        relay.validate_production_paths(
            env_file=Path("/etc/medchat/temporal.env"),
            worker_env_file=Path("/etc/medchat/medchat.env"),
            generated_root=Path("/etc/medchat/generated/temporal-metrics"),
            nginx_output=Path(
                "/etc/nginx/conf.d/medchat-temporal-worker-metrics.conf"
            ),
            platform="nt",
        )
    assert error.value.code == "E_RELAY_PATH"


@pytest.mark.parametrize(
    ("mode", "expected_type"),
    [
        (stat.S_IFDIR | 0o755, "directory"),
        (stat.S_IFREG | 0o644, "file"),
    ],
)
def test_production_metadata_rejects_non_root_owner_even_with_safe_mode(
    mode: int,
    expected_type: str,
) -> None:
    relay = load_relay()
    metadata = SimpleNamespace(st_uid=1000, st_mode=mode)
    with pytest.raises(relay.RelayConfigurationError) as error:
        relay.require_root_owned(metadata, expected_type=expected_type)
    assert error.value.code == "E_RELAY_PATH"


def test_production_main_uses_descriptor_context_instead_of_path_reopen() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    production_branch = source.split("def main(", 1)[1]
    assert "open_production_context(" in production_branch
    assert "context.read_relay_env()" in production_branch
    assert "context.read_worker_env()" in production_branch
    assert "stage_generation_descriptor(" in production_branch
    assert "activate_generation_descriptor(" in production_branch


@pytest.mark.skipif(os.name != "posix", reason="Linux descriptor bootstrap contract")
def test_bootstrap_creates_only_final_generated_directory_descriptor_relatively(
    tmp_path: Path,
    monkeypatch,
) -> None:
    relay = load_relay()
    parent = tmp_path / "generated"
    parent.mkdir()
    parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    monkeypatch.setattr(relay, "require_root_owned", lambda *_args, **_kwargs: None)
    try:
        generated_fd = relay.bootstrap_generated_root(parent_fd, "temporal-metrics")
        os.close(generated_fd)
        assert (parent / "temporal-metrics").is_dir()
        with pytest.raises(relay.RelayConfigurationError) as error:
            relay.bootstrap_generated_root(parent_fd, "nested/escape")
        assert error.value.code == "E_RELAY_PATH"
    finally:
        os.close(parent_fd)


@pytest.mark.skipif(os.name != "posix", reason="Linux openat parent replacement contract")
def test_descriptor_env_read_survives_parent_path_replacement(
    tmp_path: Path,
    monkeypatch,
) -> None:
    relay = load_relay()
    original_parent = tmp_path / "medchat"
    original_parent.mkdir()
    write_env(original_parent / "temporal.env")
    parent_fd = os.open(
        original_parent,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
    )
    monkeypatch.setattr(relay, "require_root_owned", lambda *_args, **_kwargs: None)
    try:
        original_parent.rename(tmp_path / "detached-medchat")
        replacement_parent = tmp_path / "medchat"
        replacement_parent.mkdir()
        write_env(
            replacement_parent / "temporal.env",
            {**DEFAULT_VALUES, "MEDCHAT_TEMPORAL_METRICS_RELAY_PORT": "9467"},
        )
        values = relay.read_env_at(parent_fd, "temporal.env")
    finally:
        os.close(parent_fd)
    assert values["MEDCHAT_TEMPORAL_METRICS_RELAY_PORT"] == "9466"


@pytest.mark.skipif(os.name != "posix", reason="Linux root ownership contract")
def test_descriptor_directory_open_rejects_non_root_fstat(
    tmp_path: Path,
    monkeypatch,
) -> None:
    relay = load_relay()
    child = tmp_path / "child"
    child.mkdir()
    parent_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    real_fstat = relay.os.fstat

    class NonRootMetadata:
        def __init__(self, wrapped) -> None:
            self._wrapped = wrapped
            self.st_uid = 1000

        def __getattr__(self, name: str):
            return getattr(self._wrapped, name)

    monkeypatch.setattr(
        relay.os,
        "fstat",
        lambda descriptor: NonRootMetadata(real_fstat(descriptor)),
    )
    try:
        with pytest.raises(relay.RelayConfigurationError) as error:
            relay._open_root_directory_at(parent_fd, "child")
    finally:
        os.close(parent_fd)
    assert error.value.code == "E_RELAY_PATH"


@pytest.mark.skipif(not POSIX_ROOT, reason="Linux nginx executable identity contract")
def test_held_nginx_descriptor_rejects_replaced_executable_identity(
    tmp_path: Path,
) -> None:
    relay = load_relay()
    sbin = tmp_path / "sbin"
    sbin.mkdir(mode=0o755)
    nginx = sbin / "nginx"
    nginx.write_text("original", encoding="utf-8")
    os.chmod(nginx, 0o755)
    sbin_fd = os.open(sbin, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    binary_fd = os.open("nginx", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=sbin_fd)
    context = relay.ProductionContext(
        medchat_fd=-1,
        generated_fd=-1,
        nginx_parent_fd=-1,
        nginx_sbin_fd=sbin_fd,
        nginx_binary_fd=binary_fd,
        _owned_fds=(),
    )
    context.verify_nginx_executable_identity()
    replacement = sbin / "replacement"
    replacement.write_text("replacement", encoding="utf-8")
    os.chmod(replacement, 0o755)
    os.replace(replacement, nginx)
    try:
        with pytest.raises(relay.RelayConfigurationError) as error:
            context.verify_nginx_executable_identity()
    finally:
        os.close(binary_fd)
        os.close(sbin_fd)
    assert error.value.code == "E_RELAY_PATH"


def test_trusted_path_rejects_parent_symlink(tmp_path: Path) -> None:
    relay = load_relay()
    trusted_root = tmp_path / "trusted"
    trusted_root.mkdir()
    real_parent = tmp_path / "real"
    real_parent.mkdir()
    linked_parent = trusted_root / "linked"
    try:
        linked_parent.symlink_to(real_parent, target_is_directory=True)
    except OSError as error:
        if os.name == "nt" and (
            error.errno in {errno.EPERM, errno.EACCES}
            or getattr(error, "winerror", None) == 1314
        ):
            pytest.skip("Windows symlink privilege unavailable")
        raise
    with pytest.raises(relay.RelayConfigurationError) as error:
        relay.validate_trusted_path(
            linked_parent / "worker-targets.json",
            trusted_root,
            allow_missing_leaf=True,
        )
    assert error.value.code == "E_RELAY_PATH"


def test_env_read_rejects_concurrent_in_place_modification(
    tmp_path: Path,
    monkeypatch,
) -> None:
    relay = load_relay()
    env_file = write_env(tmp_path / "relay.env")
    original_parse = relay.parse_env_text

    def modify_during_parse(text: str) -> dict[str, str]:
        values = original_parse(text)
        env_file.write_text(text + "# changed concurrently\n", encoding="utf-8")
        return values

    monkeypatch.setattr(relay, "parse_env_text", modify_during_parse)
    with pytest.raises(relay.RelayConfigurationError) as error:
        relay._read_env_file(env_file)
    assert error.value.code == "E_RELAY_ENV"


def test_env_read_rejects_replacement_between_lstat_and_nofollow_open(
    tmp_path: Path,
    monkeypatch,
) -> None:
    relay = load_relay()
    env_file = write_env(tmp_path / "relay.env")
    replacement = write_env(
        tmp_path / "replacement.env",
        {**DEFAULT_VALUES, "MEDCHAT_TEMPORAL_METRICS_RELAY_PORT": "9467"},
    )
    real_open = relay.os.open
    replaced = False

    def replace_then_open(path: object, flags: int, *args, **kwargs):
        nonlocal replaced
        if not replaced and Path(path) == env_file:
            replaced = True
            os.replace(replacement, env_file)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(relay.os, "open", replace_then_open)
    with pytest.raises(relay.RelayConfigurationError) as error:
        relay._read_env_file(env_file)
    assert error.value.code == "E_RELAY_ENV"


def render_test_generation(relay: ModuleType, root: Path, port: int = 9466):
    config = relay.parse_relay_config({
        **DEFAULT_VALUES,
        "MEDCHAT_TEMPORAL_METRICS_RELAY_PORT": str(port),
    })
    return relay.render_generation(
        config=config,
        compose_project="medchat",
        generated_root=root,
        namespace="default",
        taskqueue="medchat-docking",
        nginx_template=TEMPLATE.read_text(encoding="utf-8"),
    )


def open_root_owned_descriptor_tree(tmp_path: Path) -> tuple[Path, Path, int, int]:
    generated = tmp_path / "generated"
    nginx_parent = tmp_path / "nginx"
    generated.mkdir(mode=0o750)
    nginx_parent.mkdir(mode=0o755)
    os.chmod(generated, 0o750)
    os.chmod(nginx_parent, 0o755)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    return (
        generated,
        nginx_parent,
        os.open(generated, flags),
        os.open(nginx_parent, flags),
    )


def activate_descriptor_generation(
    relay: ModuleType,
    generated_fd: int,
    nginx_fd: int,
    generation_id: str,
    *,
    nginx_reload=lambda: True,
) -> None:
    relay.activate_generation_descriptor(
        generated_root_fd=generated_fd,
        generation_id=generation_id,
        nginx_parent_fd=nginx_fd,
        recheck_host_conflicts=lambda: None,
        bridge_ready=lambda: True,
        nginx_validate=lambda _path: True,
        nginx_reload=nginx_reload,
    )


@pytest.mark.skipif(not POSIX_ROOT, reason="Linux root descriptor mode contract")
def test_descriptor_stage_repairs_file_sd_mode_under_restrictive_umask_and_is_container_readable(
    tmp_path: Path,
) -> None:
    relay = load_relay()
    generated, _nginx_parent, generated_fd, nginx_fd = (
        open_root_owned_descriptor_tree(tmp_path)
    )
    live = generated / "live"
    file_sd = live / "file_sd"
    live.mkdir(mode=0o750)
    file_sd.mkdir(mode=0o700)
    os.chmod(file_sd, 0o700)
    generation = render_test_generation(relay, generated)
    previous_umask = os.umask(0o077)
    try:
        relay.stage_generation_descriptor(generation, generated_fd)
        activate_descriptor_generation(
            relay,
            generated_fd,
            nginx_fd,
            generation.generation_id,
        )
    finally:
        os.umask(previous_umask)
        os.close(nginx_fd)
        os.close(generated_fd)
    target = file_sd / "worker-targets.json"
    assert stat.S_IMODE(file_sd.stat().st_mode) == 0o755
    assert stat.S_IMODE(target.stat().st_mode) == 0o644

    file_sd_fd = os.open(file_sd, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    child = os.fork()
    if child == 0:
        try:
            os.fchdir(file_sd_fd)
            os.setgroups([])
            os.setgid(65534)
            os.setuid(65534)
            with open("worker-targets.json", "rb") as stream:
                valid = b"172.30.95.1:9466" in stream.read()
            os._exit(0 if valid else 2)
        except BaseException:
            os._exit(3)
    os.close(file_sd_fd)
    _, status = os.waitpid(child, 0)
    assert os.waitstatus_to_exitcode(status) == 0


@pytest.mark.skipif(not POSIX_ROOT, reason="Linux root descriptor behavior")
def test_descriptor_stage_rejects_non_allowlisted_payload_without_writing(
    tmp_path: Path,
) -> None:
    relay = load_relay()
    generated, _nginx_parent, generated_fd, nginx_fd = (
        open_root_owned_descriptor_tree(tmp_path)
    )
    generation = render_test_generation(relay, generated)
    malformed_files = dict(generation.files)
    malformed_files["../escape"] = "unexpected"
    malformed = relay.RenderedGeneration(generation.generation_id, malformed_files)
    try:
        with pytest.raises(relay.RelayConfigurationError) as error:
            relay.stage_generation_descriptor(malformed, generated_fd)
    finally:
        os.close(nginx_fd)
        os.close(generated_fd)
    assert error.value.code == "E_RELAY_MANIFEST"
    assert not (tmp_path / "escape").exists()


@pytest.mark.skipif(not POSIX_ROOT, reason="Linux root descriptor behavior")
def test_descriptor_activation_rejects_same_id_payload_and_hash_tamper(
    tmp_path: Path,
) -> None:
    relay = load_relay()
    generated, _nginx_parent, generated_fd, nginx_fd = (
        open_root_owned_descriptor_tree(tmp_path)
    )
    generation = render_test_generation(relay, generated)
    relay.stage_generation_descriptor(generation, generated_fd)
    generation_root = generated / "generations" / generation.generation_id
    payload_path = generation_root / "nginx.conf"
    tampered = payload_path.read_text("utf-8") + "# same-id tamper\n"
    payload_path.write_text(tampered, encoding="utf-8", newline="\n")
    os.chmod(payload_path, 0o640)
    manifest_path = generation_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    manifest["sha256"]["nginx.conf"] = hashlib.sha256(
        tampered.encode("utf-8")
    ).hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.chmod(manifest_path, 0o640)
    try:
        with pytest.raises(relay.RelayConfigurationError) as error:
            activate_descriptor_generation(
                relay,
                generated_fd,
                nginx_fd,
                generation.generation_id,
            )
    finally:
        os.close(nginx_fd)
        os.close(generated_fd)
    assert error.value.code == "E_RELAY_MANIFEST"


@pytest.mark.skipif(not POSIX_ROOT, reason="Linux root descriptor behavior")
def test_atomic_descriptor_group_restores_prior_and_removes_new_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    relay = load_relay()
    output = tmp_path / "outputs"
    output.mkdir(mode=0o750)
    first = output / "first.yml"
    first.write_text("known-good", encoding="utf-8")
    os.chmod(first, 0o640)
    output_fd = os.open(output, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    real_write = relay._atomic_write_at
    failed = False

    def fail_second_once(parent_fd: int, name: str, content: str, mode: int) -> None:
        nonlocal failed
        if name == "second.json" and not failed:
            failed = True
            raise OSError("descriptor group fault")
        real_write(parent_fd, name, content, mode)

    monkeypatch.setattr(relay, "_atomic_write_at", fail_second_once)
    try:
        with pytest.raises(OSError):
            relay._atomic_write_group_at([
                (output_fd, "first.yml", "new-first", 0o640),
                (output_fd, "second.json", "new-second", 0o644),
            ])
    finally:
        os.close(output_fd)
    assert first.read_text("utf-8") == "known-good"
    assert not (output / "second.json").exists()


def test_render_stages_versioned_generation_without_exposing_live_target(
    tmp_path: Path,
) -> None:
    relay = load_relay()
    root = tmp_path / "generated"
    generation = render_test_generation(relay, root)
    relay.stage_generation(generation, root)
    generation_root = root / "generations" / generation.generation_id
    assert (generation_root / "compose.override.yml").is_file()
    assert (generation_root / "file_sd" / "worker-targets.json").is_file()
    assert (generation_root / "nginx.conf").is_file()
    manifest_path = generation_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["generation_id"] == generation.generation_id
    assert manifest["schema_version"] == 1
    assert set(manifest["sha256"]) == {
        "compose.override.yml", "file_sd/worker-targets.json", "nginx.conf"
    }
    assert manifest["config"] == {
        "compose_project": "medchat",
        "gateway": "172.30.95.1",
        "namespace": "default",
        "port": 9466,
        "prometheus_address": "172.30.95.2",
        "subnet": "172.30.95.0/28",
        "taskqueue": "medchat-docking",
    }
    assert not (root / "live" / "file_sd" / "worker-targets.json").exists()


@pytest.mark.parametrize(
    "unexpected_name",
    ["../escape", "/absolute", "file_sd/../escape", "extra.txt"],
)
def test_stage_rejects_non_allowlisted_generation_payloads(
    tmp_path: Path,
    unexpected_name: str,
) -> None:
    relay = load_relay()
    generation = render_test_generation(relay, tmp_path / "generated")
    files = dict(generation.files)
    files[unexpected_name] = "unexpected"
    malformed = relay.RenderedGeneration(generation.generation_id, files)
    with pytest.raises(relay.RelayConfigurationError) as error:
        relay.validate_rendered_generation(malformed)
    assert error.value.code == "E_RELAY_MANIFEST"
    assert not (tmp_path / "escape").exists()


def test_activation_rejects_payload_and_hash_tampering_under_old_generation_id(
    tmp_path: Path,
) -> None:
    relay = load_relay()
    root = tmp_path / "generated"
    generation = render_test_generation(relay, root)
    relay.stage_generation(generation, root)
    generation_root = root / "generations" / generation.generation_id
    payload = generation_root / "nginx.conf"
    tampered_content = payload.read_text("utf-8") + "# tampered\n"
    payload.write_text(
        tampered_content,
        encoding="utf-8",
        newline="\n",
    )
    manifest_path = generation_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    manifest["sha256"]["nginx.conf"] = hashlib.sha256(
        tampered_content.encode("utf-8")
    ).hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(relay.RelayConfigurationError) as error:
        relay.activate_generation(
            generated_root=root,
            generation_id=generation.generation_id,
            nginx_output=tmp_path / "relay.conf",
            recheck_host_conflicts=lambda: None,
            bridge_ready=lambda: True,
            nginx_validate=lambda _path: True,
            nginx_reload=lambda: True,
        )
    assert error.value.code == "E_RELAY_MANIFEST"


@pytest.mark.parametrize("extra_location", ["manifest", "config", "sha256"])
def test_generation_manifest_schema_rejects_all_extra_fields(
    tmp_path: Path,
    extra_location: str,
) -> None:
    relay = load_relay()
    generation = render_test_generation(relay, tmp_path / "generated")
    files = dict(generation.files)
    manifest = json.loads(files["manifest.json"])
    target = manifest if extra_location == "manifest" else manifest[extra_location]
    target["unexpected"] = "value"
    files["manifest.json"] = json.dumps(manifest, sort_keys=True)
    malformed = relay.RenderedGeneration(generation.generation_id, files)
    with pytest.raises(relay.RelayConfigurationError) as error:
        relay.validate_rendered_generation(malformed)
    assert error.value.code == "E_RELAY_MANIFEST"


def test_activation_publishes_worker_target_last_and_records_active_generation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    relay = load_relay()
    root = tmp_path / "generated"
    nginx_output = tmp_path / "nginx" / "relay.conf"
    generation = render_test_generation(relay, root)
    relay.stage_generation(generation, root)
    events: list[str] = []
    writes: list[Path] = []
    real_atomic_write = relay.atomic_write

    def observed_write(path: Path, content: str, mode: int = 0o640) -> None:
        writes.append(Path(path))
        real_atomic_write(path, content, mode)

    monkeypatch.setattr(relay, "atomic_write", observed_write)
    relay.activate_generation(
        generated_root=root,
        generation_id=generation.generation_id,
        nginx_output=nginx_output,
        recheck_host_conflicts=lambda: events.append("recheck"),
        bridge_ready=lambda: events.append("bridge") or True,
        nginx_validate=lambda _path: events.append("validate") or True,
        nginx_reload=lambda: events.append("reload") or True,
    )
    assert events == ["recheck", "bridge", "validate", "reload"]
    assert writes[-1] == root / "live" / "file_sd" / "worker-targets.json"
    assert json.loads((root / "live" / "manifest.json").read_text("utf-8"))[
        "generation_id"
    ] == generation.generation_id


def test_failed_reconfiguration_retains_old_live_generation(tmp_path: Path) -> None:
    relay = load_relay()
    root = tmp_path / "generated"
    nginx_output = tmp_path / "nginx" / "relay.conf"
    first = render_test_generation(relay, root, 9466)
    relay.stage_generation(first, root)
    relay.activate_generation(
        generated_root=root,
        generation_id=first.generation_id,
        nginx_output=nginx_output,
        recheck_host_conflicts=lambda: None,
        bridge_ready=lambda: True,
        nginx_validate=lambda _path: True,
        nginx_reload=lambda: True,
    )
    old_target = (root / "live" / "file_sd" / "worker-targets.json").read_text(
        "utf-8"
    )
    old_nginx = nginx_output.read_text("utf-8")
    second = render_test_generation(relay, root, 9467)
    relay.stage_generation(second, root)
    with pytest.raises(relay.RelayConfigurationError) as error:
        relay.activate_generation(
            generated_root=root,
            generation_id=second.generation_id,
            nginx_output=nginx_output,
            recheck_host_conflicts=lambda: None,
            bridge_ready=lambda: True,
            nginx_validate=lambda _path: True,
            nginx_reload=lambda: False,
        )
    assert error.value.code == "E_RELAY_ROLLBACK"
    assert (root / "live" / "file_sd" / "worker-targets.json").read_text(
        "utf-8"
    ) == old_target
    assert nginx_output.read_text("utf-8") == old_nginx
    assert json.loads((root / "live" / "manifest.json").read_text("utf-8"))[
        "generation_id"
    ] == first.generation_id


def test_reload_failure_after_apply_restores_and_reloads_old_nginx(
    tmp_path: Path,
) -> None:
    relay = load_relay()
    root = tmp_path / "generated"
    nginx_output = tmp_path / "nginx" / "relay.conf"
    first = render_test_generation(relay, root, 9466)
    relay.stage_generation(first, root)
    relay.activate_generation(
        generated_root=root,
        generation_id=first.generation_id,
        nginx_output=nginx_output,
        recheck_host_conflicts=lambda: None,
        bridge_ready=lambda: True,
        nginx_validate=lambda _path: True,
        nginx_reload=lambda: True,
    )
    old_nginx = nginx_output.read_text("utf-8")
    second = render_test_generation(relay, root, 9467)
    relay.stage_generation(second, root)
    reloads: list[str] = []

    def reload_after_apply_failure() -> bool:
        reloads.append("reload")
        return len(reloads) > 1

    with pytest.raises(relay.RelayConfigurationError) as error:
        relay.activate_generation(
            generated_root=root,
            generation_id=second.generation_id,
            nginx_output=nginx_output,
            recheck_host_conflicts=lambda: None,
            bridge_ready=lambda: True,
            nginx_validate=lambda _path: True,
            nginx_reload=reload_after_apply_failure,
        )
    assert error.value.code == "E_RELAY_ACTIVATION"
    assert reloads == ["reload", "reload"]
    assert nginx_output.read_text("utf-8") == old_nginx


def test_target_publish_failure_restores_live_files_and_reloads_old_nginx(
    tmp_path: Path,
    monkeypatch,
) -> None:
    relay = load_relay()
    root = tmp_path / "generated"
    nginx_output = tmp_path / "nginx" / "relay.conf"
    first = render_test_generation(relay, root, 9466)
    relay.stage_generation(first, root)
    relay.activate_generation(
        generated_root=root,
        generation_id=first.generation_id,
        nginx_output=nginx_output,
        recheck_host_conflicts=lambda: None,
        bridge_ready=lambda: True,
        nginx_validate=lambda _path: True,
        nginx_reload=lambda: True,
    )
    live_paths = (
        root / "live" / "compose.override.yml",
        root / "live" / "manifest.json",
        root / "live" / "file_sd" / "worker-targets.json",
    )
    old_live = {path: path.read_bytes() for path in live_paths}
    old_nginx = nginx_output.read_bytes()
    second = render_test_generation(relay, root, 9467)
    relay.stage_generation(second, root)
    real_write = relay.atomic_write
    failed = False

    def fail_target_once(path: Path, content: str, mode: int = 0o640) -> None:
        nonlocal failed
        if Path(path) == live_paths[-1] and not failed:
            failed = True
            raise OSError("target publish fault")
        real_write(path, content, mode)

    monkeypatch.setattr(relay, "atomic_write", fail_target_once)
    reloads: list[str] = []
    with pytest.raises(relay.RelayConfigurationError) as error:
        relay.activate_generation(
            generated_root=root,
            generation_id=second.generation_id,
            nginx_output=nginx_output,
            recheck_host_conflicts=lambda: None,
            bridge_ready=lambda: True,
            nginx_validate=lambda _path: True,
            nginx_reload=lambda: reloads.append("reload") or True,
        )
    assert error.value.code == "E_RELAY_WRITE"
    assert reloads == ["reload", "reload"]
    assert nginx_output.read_bytes() == old_nginx
    assert {path: path.read_bytes() for path in live_paths} == old_live


@pytest.mark.skipif(not POSIX_ROOT, reason="Linux root descriptor activation")
def test_descriptor_reload_false_after_apply_restores_and_reloads_old_config(
    tmp_path: Path,
) -> None:
    relay = load_relay()
    generated, nginx_parent, generated_fd, nginx_fd = (
        open_root_owned_descriptor_tree(tmp_path)
    )
    first = render_test_generation(relay, generated, 9466)
    relay.stage_generation_descriptor(first, generated_fd)
    activate_descriptor_generation(
        relay, generated_fd, nginx_fd, first.generation_id
    )
    old_nginx = (nginx_parent / relay.PRODUCTION_NGINX_OUTPUT.name).read_bytes()
    second = render_test_generation(relay, generated, 9467)
    relay.stage_generation_descriptor(second, generated_fd)
    reloads: list[str] = []

    def ambiguous_reload() -> bool:
        reloads.append("reload")
        return len(reloads) > 1

    try:
        with pytest.raises(relay.RelayConfigurationError) as error:
            activate_descriptor_generation(
                relay,
                generated_fd,
                nginx_fd,
                second.generation_id,
                nginx_reload=ambiguous_reload,
            )
    finally:
        os.close(nginx_fd)
        os.close(generated_fd)
    assert error.value.code == "E_RELAY_ACTIVATION"
    assert reloads == ["reload", "reload"]
    assert (nginx_parent / relay.PRODUCTION_NGINX_OUTPUT.name).read_bytes() == old_nginx
    active = json.loads((generated / "live" / "manifest.json").read_text("utf-8"))
    assert active["generation_id"] == first.generation_id


@pytest.mark.skipif(not POSIX_ROOT, reason="Linux root descriptor activation")
def test_descriptor_target_publish_failure_restores_every_live_file_and_nginx(
    tmp_path: Path,
    monkeypatch,
) -> None:
    relay = load_relay()
    generated, nginx_parent, generated_fd, nginx_fd = (
        open_root_owned_descriptor_tree(tmp_path)
    )
    first = render_test_generation(relay, generated, 9466)
    relay.stage_generation_descriptor(first, generated_fd)
    activate_descriptor_generation(
        relay, generated_fd, nginx_fd, first.generation_id
    )
    live_paths = (
        generated / "live" / "compose.override.yml",
        generated / "live" / "manifest.json",
        generated / "live" / "file_sd" / "worker-targets.json",
    )
    old_live = {path: path.read_bytes() for path in live_paths}
    old_nginx = (nginx_parent / relay.PRODUCTION_NGINX_OUTPUT.name).read_bytes()
    file_sd_metadata = (generated / "live" / "file_sd").stat()
    file_sd_identity = (file_sd_metadata.st_dev, file_sd_metadata.st_ino)
    second = render_test_generation(relay, generated, 9467)
    relay.stage_generation_descriptor(second, generated_fd)
    real_write = relay._atomic_write_at
    failed = False

    def fail_target_once(parent_fd: int, name: str, content: str, mode: int) -> None:
        nonlocal failed
        metadata = os.fstat(parent_fd)
        if (
            (metadata.st_dev, metadata.st_ino) == file_sd_identity
            and name == "worker-targets.json"
            and not failed
        ):
            failed = True
            raise OSError("descriptor target publish fault")
        real_write(parent_fd, name, content, mode)

    monkeypatch.setattr(relay, "_atomic_write_at", fail_target_once)
    reloads: list[str] = []
    try:
        with pytest.raises(relay.RelayConfigurationError) as error:
            activate_descriptor_generation(
                relay,
                generated_fd,
                nginx_fd,
                second.generation_id,
                nginx_reload=lambda: reloads.append("reload") or True,
            )
    finally:
        os.close(nginx_fd)
        os.close(generated_fd)
    assert error.value.code == "E_RELAY_WRITE"
    assert reloads == ["reload", "reload"]
    assert {path: path.read_bytes() for path in live_paths} == old_live
    assert (nginx_parent / relay.PRODUCTION_NGINX_OUTPUT.name).read_bytes() == old_nginx


@pytest.mark.skipif(not POSIX_ROOT, reason="Linux root descriptor activation")
def test_descriptor_rollback_reload_failure_is_stable_rollback_error(
    tmp_path: Path,
) -> None:
    relay = load_relay()
    generated, _nginx_parent, generated_fd, nginx_fd = (
        open_root_owned_descriptor_tree(tmp_path)
    )
    first = render_test_generation(relay, generated, 9466)
    relay.stage_generation_descriptor(first, generated_fd)
    activate_descriptor_generation(
        relay, generated_fd, nginx_fd, first.generation_id
    )
    second = render_test_generation(relay, generated, 9467)
    relay.stage_generation_descriptor(second, generated_fd)
    reloads: list[str] = []
    try:
        with pytest.raises(relay.RelayConfigurationError) as error:
            activate_descriptor_generation(
                relay,
                generated_fd,
                nginx_fd,
                second.generation_id,
                nginx_reload=lambda: reloads.append("reload") or False,
            )
    finally:
        os.close(nginx_fd)
        os.close(generated_fd)
    assert error.value.code == "E_RELAY_ROLLBACK"
    assert reloads == ["reload", "reload"]


@pytest.mark.skipif(not POSIX_ROOT, reason="Linux root descriptor retention")
def test_descriptor_retention_keeps_active_and_newest_four_valid_generations(
    tmp_path: Path,
) -> None:
    relay = load_relay()
    generated, _nginx_parent, generated_fd, nginx_fd = (
        open_root_owned_descriptor_tree(tmp_path)
    )
    generations = [
        render_test_generation(relay, generated, 9466 + index)
        for index in range(7)
    ]
    relay.stage_generation_descriptor(generations[0], generated_fd)
    activate_descriptor_generation(
        relay, generated_fd, nginx_fd, generations[0].generation_id
    )
    for generation in generations[1:]:
        time.sleep(0.01)
        relay.stage_generation_descriptor(generation, generated_fd)
    os.close(nginx_fd)
    os.close(generated_fd)
    retained = {
        path.name
        for path in (generated / "generations").iterdir()
        if path.is_dir()
    }
    assert retained == {
        generations[0].generation_id,
        *(generation.generation_id for generation in generations[-4:]),
    }


def test_generation_lock_serializes_concurrent_activation(tmp_path: Path) -> None:
    relay = load_relay()
    root = tmp_path / "generated"
    first = render_test_generation(relay, root, 9466)
    second = render_test_generation(relay, root, 9467)
    relay.stage_generation(first, root)
    relay.stage_generation(second, root)
    entered = threading.Event()
    release = threading.Event()
    second_entered = threading.Event()
    errors: list[BaseException] = []

    def activate(generation_id: str, first_call: bool) -> None:
        try:
            def recheck() -> None:
                if first_call:
                    entered.set()
                    assert release.wait(5)
                else:
                    second_entered.set()

            relay.activate_generation(
                generated_root=root,
                generation_id=generation_id,
                nginx_output=tmp_path / "relay.conf",
                recheck_host_conflicts=recheck,
                bridge_ready=lambda: True,
                nginx_validate=lambda _path: True,
                nginx_reload=lambda: True,
            )
        except BaseException as error:
            errors.append(error)

    first_thread = threading.Thread(target=activate, args=(first.generation_id, True))
    second_thread = threading.Thread(target=activate, args=(second.generation_id, False))
    first_thread.start()
    assert entered.wait(5)
    second_thread.start()
    assert not second_entered.wait(0.2)
    release.set()
    first_thread.join(5)
    second_thread.join(5)
    assert not errors
    assert second_entered.is_set()


def test_cli_writes_one_coherent_group_and_never_echoes_unrelated_secret(
    tmp_path: Path,
) -> None:
    values = {
        "UNRELATED_SECRET": "PRIVATE_RELAY_SECRET_CANARY",
        "COMPOSE_PROJECT_NAME": "medchat",
        "TEMPORAL_NAMESPACE": "default",
        **DEFAULT_VALUES,
    }
    env_file = write_env(tmp_path / "relay.env", values)
    output_dir = tmp_path / "generated"
    nginx_output = tmp_path / "nginx" / "relay.conf"
    result = run_cli(env_file, output_dir, nginx_output)
    assert result.returncode == 0
    assert re.fullmatch(
        r"OK E_RELAY_RENDERED generation=([0-9a-f]{24})\n",
        result.stdout,
    )
    assert result.stderr == ""
    generation_id = result.stdout.split("generation=", 1)[1].strip()
    generation_root = output_dir / "generations" / generation_id
    yaml = pytest.importorskip("yaml")
    override = yaml.safe_load(
        (generation_root / "compose.override.yml").read_text("utf-8")
    )
    mount = override["services"]["prometheus"]["volumes"][0]
    assert Path(mount["source"]) == (output_dir / "live" / "file_sd").resolve()
    assert mount["target"] == "/etc/prometheus/file_sd"
    assert mount["read_only"] is True
    assert json.loads(
        (generation_root / "file_sd" / "worker-targets.json").read_text("utf-8")
    ) == [
        {"targets": ["172.30.95.1:9466"]}
    ]
    nginx = (generation_root / "nginx.conf").read_text(encoding="utf-8")
    assert "listen 172.30.95.1:9466;" in nginx
    assert "allow 172.30.95.2;" in nginx
    combined = result.stdout + result.stderr + json.dumps(override) + nginx
    assert "PRIVATE_RELAY_SECRET_CANARY" not in combined


def test_cli_repeated_render_is_stable_on_current_platform(tmp_path: Path) -> None:
    env_file = write_env(
        tmp_path / "relay.env",
        {
            "COMPOSE_PROJECT_NAME": "medchat",
            "TEMPORAL_NAMESPACE": "default",
            **DEFAULT_VALUES,
        },
    )
    output_dir = tmp_path / "generated"
    nginx_output = tmp_path / "nginx" / "relay.conf"
    first = run_cli(env_file, output_dir, nginx_output)
    second = run_cli(env_file, output_dir, nginx_output)
    assert first.returncode == second.returncode == 0
    assert first.stderr == second.stderr == ""
    assert first.stdout == second.stdout
    generation_id = first.stdout.split("generation=", 1)[1].strip()
    manifest = output_dir / "generations" / generation_id / "manifest.json"
    assert json.loads(manifest.read_text("utf-8"))["generation_id"] == generation_id


def test_generation_retention_keeps_active_and_newest_four_valid_ids(
    tmp_path: Path,
) -> None:
    relay = load_relay()
    root = tmp_path / "generated"
    nginx_output = tmp_path / "nginx" / "relay.conf"
    generations = [render_test_generation(relay, root, 9466 + index) for index in range(7)]
    relay.stage_generation(generations[0], root)
    relay.activate_generation(
        generated_root=root,
        generation_id=generations[0].generation_id,
        nginx_output=nginx_output,
        recheck_host_conflicts=lambda: None,
        bridge_ready=lambda: True,
        nginx_validate=lambda _path: True,
        nginx_reload=lambda: True,
    )
    for generation in generations[1:]:
        time.sleep(0.01)
        relay.stage_generation(generation, root)
    retained = {
        path.name
        for path in (root / "generations").iterdir()
        if path.is_dir()
    }
    assert retained == {
        generations[0].generation_id,
        *(generation.generation_id for generation in generations[-4:]),
    }


def test_generation_retention_ignores_invalid_names_and_symlinks(
    tmp_path: Path,
) -> None:
    relay = load_relay()
    root = tmp_path / "generated"
    generations_dir = root / "generations"
    generations_dir.mkdir(parents=True)
    invalid = generations_dir / "operator-notes"
    invalid.mkdir()
    external = tmp_path / "external-generation"
    external.mkdir()
    linked = generations_dir / ("a" * 24)
    try:
        linked.symlink_to(external, target_is_directory=True)
    except OSError as error:
        if os.name == "nt" and (
            error.errno in {errno.EPERM, errno.EACCES}
            or getattr(error, "winerror", None) == 1314
        ):
            pytest.skip("Windows symlink privilege unavailable")
        raise
    generation = render_test_generation(relay, root, 9466)
    relay.stage_generation(generation, root)
    relay.prune_generations(root, pending_generation_id=generation.generation_id)
    assert invalid.is_dir()
    assert linked.is_symlink()
    assert external.is_dir()


def test_env_example_documents_root_owned_final_directory_bootstrap() -> None:
    content = (ROOT / "deployment" / "temporal" / "env.example").read_text("utf-8")
    normalized = " ".join(line.lstrip("# ") for line in content.splitlines())
    assert "/etc/medchat/generated" in content
    assert "root-owned" in content
    assert "only the final temporal-metrics directory" in normalized


def test_atomic_write_failure_preserves_prior_file(tmp_path: Path, monkeypatch) -> None:
    relay = load_relay()
    target = tmp_path / "relay.conf"
    target.write_text("known-good", encoding="utf-8")

    def fail_replace(_source: object, _target: object, **_kwargs) -> None:
        raise OSError("PRIVATE_REPLACE_CANARY")

    monkeypatch.setattr(relay.os, "replace", fail_replace)
    with pytest.raises(OSError):
        relay.atomic_write(target, "new")
    assert target.read_text(encoding="utf-8") == "known-good"
    assert not list(tmp_path.glob(".*.tmp"))


def test_atomic_group_failure_restores_every_prior_output(
    tmp_path: Path,
    monkeypatch,
) -> None:
    relay = load_relay()
    paths = [tmp_path / name for name in ("compose.yml", "targets.json", "relay.conf")]
    for path in paths:
        path.write_text(f"known-good-{path.name}", encoding="utf-8")
    real_replace = relay.os.replace

    def fail_second(source: object, target: object, **kwargs) -> None:
        target_path = (
            paths[0].parent / Path(target)
            if kwargs.get("dst_dir_fd") is not None
            else Path(target)
        )
        if target_path == paths[1] and Path(source).name.endswith(".tmp"):
            raise OSError("PRIVATE_GROUP_REPLACE_CANARY")
        real_replace(source, target, **kwargs)

    monkeypatch.setattr(relay.os, "replace", fail_second)
    with pytest.raises(OSError):
        relay.atomic_write_group([
            (paths[0], "new-compose", 0o640),
            (paths[1], "new-targets", 0o640),
            (paths[2], "new-nginx", 0o640),
        ])
    assert [path.read_text(encoding="utf-8") for path in paths] == [
        "known-good-compose.yml",
        "known-good-targets.json",
        "known-good-relay.conf",
    ]


def test_atomic_group_rolls_back_after_post_swap_directory_fsync_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    relay = load_relay()
    created = tmp_path / "new-compose.yml"
    replaced = tmp_path / "targets.json"
    untouched = tmp_path / "relay.conf"
    replaced.write_text("known-good-targets", encoding="utf-8")
    untouched.write_text("known-good-relay", encoding="utf-8")
    if os.name == "posix":
        real_fsync = relay.os.fsync
        directory_fsync_calls = 0

        def fail_second_directory_fsync(descriptor: int) -> None:
            nonlocal directory_fsync_calls
            if stat.S_ISDIR(os.fstat(descriptor).st_mode):
                directory_fsync_calls += 1
                if directory_fsync_calls == 2:
                    raise OSError("PRIVATE_POST_SWAP_FSYNC_CANARY")
            real_fsync(descriptor)

        monkeypatch.setattr(relay.os, "fsync", fail_second_directory_fsync)
    else:
        parent_fsync_calls = 0

        def fail_second_parent_fsync(_parent: Path) -> None:
            nonlocal parent_fsync_calls
            parent_fsync_calls += 1
            if parent_fsync_calls == 2:
                raise OSError("PRIVATE_POST_SWAP_FSYNC_CANARY")

        monkeypatch.setattr(
            relay,
            "_fsync_parent_directory",
            fail_second_parent_fsync,
        )
    with pytest.raises(OSError):
        relay.atomic_write_group([
            (created, "new-compose", 0o640),
            (replaced, "new-targets", 0o640),
            (untouched, "new-relay", 0o640),
        ])
    assert not created.exists()
    assert replaced.read_text(encoding="utf-8") == "known-good-targets"
    assert untouched.read_text(encoding="utf-8") == "known-good-relay"
    assert not list(tmp_path.glob(".*.tmp"))


def test_failed_cli_validation_preserves_all_prior_outputs(tmp_path: Path) -> None:
    output_dir = tmp_path / "generated"
    output_dir.mkdir()
    compose = output_dir / "compose.override.yml"
    targets = output_dir / "worker-targets.json"
    nginx = tmp_path / "relay.conf"
    for path in (compose, targets, nginx):
        path.write_text("known-good", encoding="utf-8")
    env_file = write_env(
        tmp_path / "relay.env",
        {
            "TEMPORAL_NAMESPACE": "default",
            **DEFAULT_VALUES,
            "MEDCHAT_TEMPORAL_METRICS_RELAY_PORT": "PRIVATE_BAD_PORT",
        },
    )
    result = run_cli(env_file, output_dir, nginx)
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "ERROR E_RELAY_CONFIGURATION\n"
    assert "PRIVATE_BAD_PORT" not in result.stderr
    assert all(path.read_text(encoding="utf-8") == "known-good" for path in (
        compose, targets, nginx
    ))
