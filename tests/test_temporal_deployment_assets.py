from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
COMPOSE_PATH = ROOT / "deployment" / "temporal" / "docker-compose.yml"
ENV_EXAMPLE_PATH = ROOT / "deployment" / "temporal" / "env.example"
EXPORTER_INIT_PATH = (
    ROOT / "deployment" / "temporal" / "postgres-init" / "010-exporter.sh"
)

EXPECTED_IMAGES = {
    "postgres": "postgres:16.14-alpine3.23",
    "postgres-exporter": (
        "ghcr.io/prometheus-community/postgres-exporter:v0.20.1"
    ),
    "temporal": "temporalio/auto-setup:1.29.7",
    "temporal-ui": "temporalio/ui:2.53.1",
    "prometheus": "prom/prometheus:v3.13.2",
    "grafana": "grafana/grafana:12.4.8-ubuntu",
}

EXPECTED_PORTS = {
    "postgres": [
        "127.0.0.1:${TEMPORAL_POSTGRES_HOST_PORT:-5433}:5432"
    ],
    "postgres-exporter": [],
    "temporal": ["127.0.0.1:${TEMPORAL_GRPC_HOST_PORT:-7233}:7233"],
    "temporal-ui": ["127.0.0.1:${TEMPORAL_UI_HOST_PORT:-8233}:8080"],
    "prometheus": ["127.0.0.1:${PROMETHEUS_HOST_PORT:-9090}:9090"],
    "grafana": ["127.0.0.1:${GRAFANA_HOST_PORT:-3000}:3000"],
}

EXPECTED_VOLUMES = {
    "postgres": [
        "temporal-postgres-data:/var/lib/postgresql/data",
        (
            "./postgres-init/010-exporter.sh:"
            "/docker-entrypoint-initdb.d/010-exporter.sh:ro"
        ),
    ],
    "postgres-exporter": [],
    "temporal": [],
    "temporal-ui": [],
    "prometheus": [
        "prometheus-data:/prometheus",
        "./prometheus:/etc/prometheus:ro",
    ],
    "grafana": [
        "grafana-data:/var/lib/grafana",
        "./grafana/provisioning:/etc/grafana/provisioning:ro",
        "./grafana/dashboards:/var/lib/grafana/dashboards:ro",
    ],
}


def load_yaml(path: Path) -> dict[str, Any]:
    yaml = pytest.importorskip("yaml", reason="PyYAML is required for YAML checks")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def environment_map(service: dict[str, Any]) -> dict[str, str]:
    environment = service.get("environment", {})
    if isinstance(environment, dict):
        return {str(key): str(value) for key, value in environment.items()}

    result: dict[str, str] = {}
    for item in environment:
        key, separator, value = str(item).partition("=")
        assert separator, f"environment entry must use KEY=value: {item!r}"
        result[key] = value
    return result


def dependency_map(service: dict[str, Any]) -> dict[str, dict[str, str]]:
    dependencies = service.get("depends_on", {})
    assert isinstance(dependencies, dict)
    return dependencies


def test_compose_uses_exact_services_images_ports_and_named_volumes() -> None:
    compose = load_yaml(COMPOSE_PATH)
    services = compose["services"]

    assert set(services) == set(EXPECTED_IMAGES)
    assert {name: service["image"] for name, service in services.items()} == (
        EXPECTED_IMAGES
    )
    assert all(not image.endswith(":latest") for image in EXPECTED_IMAGES.values())
    assert set(compose.get("volumes", {})) == {
        "temporal-postgres-data",
        "prometheus-data",
        "grafana-data",
    }

    for name, service in services.items():
        assert service.get("ports", []) == EXPECTED_PORTS[name]
        assert service.get("volumes", []) == EXPECTED_VOLUMES[name]
        assert all(str(port).startswith("127.0.0.1:") for port in service.get("ports", []))


def test_every_service_has_restart_init_security_and_real_healthcheck() -> None:
    services = load_yaml(COMPOSE_PATH)["services"]

    for name, service in services.items():
        assert service.get("restart") == "unless-stopped", name
        assert service.get("init") is True, name
        assert service.get("security_opt") == ["no-new-privileges:true"], name
        assert service.get("stop_grace_period"), name

        healthcheck = service.get("healthcheck")
        assert isinstance(healthcheck, dict), name
        assert healthcheck.get("test") and healthcheck["test"][0] in {
            "CMD",
            "CMD-SHELL",
        }, name
        assert healthcheck.get("interval"), name
        assert healthcheck.get("timeout"), name
        assert isinstance(healthcheck.get("retries"), int), name
        assert healthcheck["retries"] > 0, name
        assert healthcheck.get("start_period"), name

    commands = {
        name: " ".join(map(str, service["healthcheck"]["test"]))
        for name, service in services.items()
    }
    assert "pg_isready" in commands["postgres"]
    assert "127.0.0.1:9187/metrics" in commands["postgres-exporter"]
    assert "temporal operator cluster health" in commands["temporal"]
    assert "temporal:7233" in commands["temporal"]
    assert "127.0.0.1:8080" in commands["temporal-ui"]
    assert "127.0.0.1:9090/-/healthy" in commands["prometheus"]
    assert "127.0.0.1:3000/api/health" in commands["grafana"]


def test_dependencies_use_health_conditions_without_cycles() -> None:
    services = load_yaml(COMPOSE_PATH)["services"]
    expected = {
        "postgres": {},
        "postgres-exporter": {"postgres": {"condition": "service_healthy"}},
        "temporal": {"postgres": {"condition": "service_healthy"}},
        "temporal-ui": {"temporal": {"condition": "service_healthy"}},
        "prometheus": {
            "postgres-exporter": {"condition": "service_healthy"},
            "temporal": {"condition": "service_healthy"},
        },
        "grafana": {"prometheus": {"condition": "service_healthy"}},
    }
    assert {name: dependency_map(service) for name, service in services.items()} == expected

    def visit(name: str, ancestors: frozenset[str]) -> None:
        assert name not in ancestors, f"circular dependency involving {name}"
        for dependency in dependency_map(services[name]):
            assert dependency in services
            visit(dependency, ancestors | {name})

    for service_name in services:
        visit(service_name, frozenset())


def test_temporal_persistence_ui_and_exporter_are_isolated() -> None:
    services = load_yaml(COMPOSE_PATH)["services"]
    postgres_env = environment_map(services["postgres"])
    temporal_env = environment_map(services["temporal"])
    ui_env = environment_map(services["temporal-ui"])
    exporter_env = environment_map(services["postgres-exporter"])

    assert postgres_env == {
        "POSTGRES_DB": "${TEMPORAL_POSTGRES_DB:-temporal}",
        "POSTGRES_USER": "${TEMPORAL_POSTGRES_USER:-temporal}",
        "POSTGRES_PASSWORD": "${TEMPORAL_POSTGRES_PASSWORD:?required}",
    }
    assert temporal_env == {
        "DB": "postgres12",
        "DB_PORT": "5432",
        "POSTGRES_SEEDS": "postgres",
        "POSTGRES_USER": "${TEMPORAL_POSTGRES_USER:-temporal}",
        "POSTGRES_PWD": "${TEMPORAL_POSTGRES_PASSWORD:?required}",
        "DBNAME": "${TEMPORAL_POSTGRES_DB:-temporal}",
        "VISIBILITY_DBNAME": (
            "${TEMPORAL_POSTGRES_VISIBILITY_DB:-temporal_visibility}"
        ),
        "TEMPORAL_ADDRESS": "temporal:7233",
    }
    assert "sqlite" not in repr(temporal_env).lower()
    assert ui_env == {"TEMPORAL_ADDRESS": "temporal:7233"}
    assert set(dependency_map(services["temporal-ui"])) == {"temporal"}
    assert exporter_env == {
        "DATA_SOURCE_URI": "postgres:5432/temporal?sslmode=disable",
        "DATA_SOURCE_USER": "temporal_exporter",
        "DATA_SOURCE_PASS_FILE": (
            "/run/secrets/temporal_postgres_exporter_password"
        ),
    }
    assert services["postgres-exporter"].get("ports", []) == []


def test_compose_secrets_are_file_or_required_runtime_interpolation() -> None:
    compose = load_yaml(COMPOSE_PATH)
    services = compose["services"]
    secret = "temporal_postgres_exporter_password"

    assert compose.get("secrets") == {
        secret: {"file": "${TEMPORAL_POSTGRES_EXPORTER_PASSWORD_FILE:?required}"}
    }
    expected_grants = [{"source": secret, "target": secret}]
    assert services["postgres"]["secrets"] == expected_grants
    assert services["postgres-exporter"]["secrets"] == expected_grants
    assert environment_map(services["grafana"])["GF_SECURITY_ADMIN_PASSWORD"] == (
        "${GRAFANA_ADMIN_PASSWORD:?required}"
    )

    text = COMPOSE_PATH.read_text(encoding="utf-8")
    assert "POSTGRES_PASSWORD=${TEMPORAL_POSTGRES_PASSWORD:?required}" in text
    assert "POSTGRES_PWD=${TEMPORAL_POSTGRES_PASSWORD:?required}" in text
    assert "GF_SECURITY_ADMIN_PASSWORD=${GRAFANA_ADMIN_PASSWORD:?required}" in text
    assert "DATA_SOURCE_PASS_FILE=/run/secrets/temporal_postgres_exporter_password" in text
    assert not re.search(r"\$\{[^}\n]*(?:PASSWORD|SECRET)[^}\n]*:-", text)


def test_compose_has_no_dangerous_privilege_network_or_mount() -> None:
    compose = load_yaml(COMPOSE_PATH)
    text = COMPOSE_PATH.read_text(encoding="utf-8")
    forbidden_keys = {
        "cap_add",
        "devices",
        "network_mode",
        "pid",
        "privileged",
        "userns_mode",
    }

    assert "version:" not in text
    assert "/var/run/docker.sock" not in text
    assert "../..:/" not in text
    assert not re.search(r"(?im)^\s*(?:password|secret)\s*:", text)
    for name, service in compose["services"].items():
        assert forbidden_keys.isdisjoint(service), name
        for mount in service.get("volumes", []):
            if str(mount).startswith("./"):
                assert str(mount).endswith(":ro"), (name, mount)


def test_compose_escapes_container_shell_variables() -> None:
    text = COMPOSE_PATH.read_text(encoding="utf-8")
    assert "$${POSTGRES_USER}" in text
    assert "$${POSTGRES_DB}" in text
    assert not re.search(r'(?<!\$)\$\{POSTGRES_(?:USER|DB)\}', text)


def test_exporter_init_rejects_unsafe_secrets_and_quotes_psql_variable() -> None:
    script = EXPORTER_INIT_PATH.read_text(encoding="utf-8")

    assert script.startswith("#!/bin/sh\n")
    assert "set -eu" in script
    assert "set -x" not in script
    assert "secret_size=$(wc -c" in script
    assert '"$secret_size" -lt 1' in script
    assert "maximum_secret_bytes=1024" in script
    assert '"$secret_size" -gt "$maximum_secret_bytes"' in script
    assert "wc -l" in script
    assert "printf '\\r'" in script
    assert '[ -n "$exporter_password" ]' in script
    assert "<<'SQL'" in script
    assert '--set=exporter_password="$exporter_password"' in script
    assert ":'exporter_password'" in script
    assert "GRANT pg_monitor TO temporal_exporter" in script

    assert not re.search(r"(?m)^\s*(?:echo|printf).*exporter_password", script)
    assert not re.search(r"PASSWORD\s+['\"]?\$exporter_password", script)
    assert not re.search(r"(?<!NO)\bSUPERUSER\b", script)
    assert not re.search(r"(?<!NO)\bCREATEDB\b", script)
    assert not re.search(r"(?<!NO)\bCREATEROLE\b", script)
    assert not re.search(r"(?<!NO)\bREPLICATION\b", script)
    assert not re.search(r"\b(?:INSERT|UPDATE|DELETE|TRUNCATE|CREATE DATABASE)\b", script)
    assert not re.search(r"\b(?:OWNER|OWNERSHIP|GRANT ALL)\b", script)


def test_exporter_init_creates_fixed_role_only_if_absent_then_hardens_it() -> None:
    script = EXPORTER_INIT_PATH.read_text(encoding="utf-8")

    assert "/run/secrets/temporal_postgres_exporter_password" in script
    assert "WHERE NOT EXISTS" in script
    assert "rolname = 'temporal_exporter'" in script
    assert "CREATE ROLE temporal_exporter LOGIN" in script
    assert "\\gexec" in script
    assert re.search(
        r"ALTER ROLE temporal_exporter WITH LOGIN PASSWORD\s+:'exporter_password'\s+"
        r"NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;",
        script,
    )


def test_env_example_has_unique_keys_empty_secrets_and_nonsecret_defaults() -> None:
    lines = ENV_EXAMPLE_PATH.read_text(encoding="utf-8").splitlines()
    assignments = [line for line in lines if line and not line.lstrip().startswith("#")]
    pairs = [line.split("=", 1) for line in assignments]
    keys = [key for key, _ in pairs]
    values = dict(pairs)

    assert len(keys) == len(set(keys))
    assert values["TEMPORAL_POSTGRES_PASSWORD"] == ""
    assert values["TEMPORAL_POSTGRES_EXPORTER_PASSWORD_FILE"] == ""
    assert values["GRAFANA_ADMIN_PASSWORD"] == ""
    assert values["TEMPORAL_POSTGRES_DB"] == "temporal"
    assert values["TEMPORAL_POSTGRES_VISIBILITY_DB"] == "temporal_visibility"
    assert values["TEMPORAL_POSTGRES_USER"] == "temporal"
    assert values["TEMPORAL_POSTGRES_HOST_PORT"] == "5433"
    assert values["TEMPORAL_GRPC_HOST_PORT"] == "7233"
    assert values["TEMPORAL_UI_HOST_PORT"] == "8233"
    assert values["PROMETHEUS_HOST_PORT"] == "9090"
    assert values["GRAFANA_HOST_PORT"] == "3000"
    assert values["GRAFANA_ADMIN_USER"] == "admin"

    text = ENV_EXAMPLE_PATH.read_text(encoding="utf-8")
    assert "never commit real" in text.lower()
    assert not re.search(r"(?im)(?:password|secret)[ \t]*=[ \t]*[^\s#]+", text)
