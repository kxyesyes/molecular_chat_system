from __future__ import annotations

import importlib.util
import ipaddress
import json
import os
import subprocess
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import ModuleType

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


def run_cli(
    env_file: Path,
    output_dir: Path,
    nginx_output: Path,
    *extra: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--env-file",
            str(env_file),
            "--output-dir",
            str(output_dir),
            "--nginx-output",
            str(nginx_output),
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
        pytest.skip(f"symlink creation unavailable: {type(exc).__name__}")
    result = run_cli(link, tmp_path / "generated", tmp_path / "relay.conf")
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "ERROR E_RELAY_ENV\n"
    assert "PRIVATE_ENV_TARGET" not in result.stderr


def test_cli_writes_one_coherent_group_and_never_echoes_unrelated_secret(
    tmp_path: Path,
) -> None:
    values = {
        "UNRELATED_SECRET": "PRIVATE_RELAY_SECRET_CANARY",
        "COMPOSE_PROJECT_NAME": "medchat",
        **DEFAULT_VALUES,
    }
    env_file = write_env(tmp_path / "relay.env", values)
    output_dir = tmp_path / "generated"
    nginx_output = tmp_path / "nginx" / "relay.conf"
    result = run_cli(env_file, output_dir, nginx_output)
    assert result.returncode == 0
    assert result.stdout == "OK E_RELAY_CONFIGURED\n"
    assert result.stderr == ""
    yaml = pytest.importorskip("yaml")
    override = yaml.safe_load((output_dir / "compose.override.yml").read_text("utf-8"))
    mount = override["services"]["prometheus"]["volumes"][0]
    assert Path(mount["source"]) == (output_dir / "worker-targets.json").resolve()
    assert mount["target"] == "/etc/prometheus/file_sd/worker-targets.json"
    assert mount["read_only"] is True
    assert json.loads((output_dir / "worker-targets.json").read_text("utf-8")) == [
        {"targets": ["172.30.95.1:9466"]}
    ]
    nginx = nginx_output.read_text(encoding="utf-8")
    assert "listen 172.30.95.1:9466;" in nginx
    assert "allow 172.30.95.2;" in nginx
    combined = result.stdout + result.stderr + json.dumps(override) + nginx
    assert "PRIVATE_RELAY_SECRET_CANARY" not in combined


def test_atomic_write_failure_preserves_prior_file(tmp_path: Path, monkeypatch) -> None:
    relay = load_relay()
    target = tmp_path / "relay.conf"
    target.write_text("known-good", encoding="utf-8")

    def fail_replace(_source: object, _target: object) -> None:
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

    def fail_second(source: object, target: object) -> None:
        if Path(target) == paths[1] and Path(source).name.endswith(".tmp"):
            raise OSError("PRIVATE_GROUP_REPLACE_CANARY")
        real_replace(source, target)

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
        {**DEFAULT_VALUES, "MEDCHAT_TEMPORAL_METRICS_RELAY_PORT": "PRIVATE_BAD_PORT"},
    )
    result = run_cli(env_file, output_dir, nginx)
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "ERROR E_RELAY_CONFIGURATION\n"
    assert "PRIVATE_BAD_PORT" not in result.stderr
    assert all(path.read_text(encoding="utf-8") == "known-good" for path in (
        compose, targets, nginx
    ))
