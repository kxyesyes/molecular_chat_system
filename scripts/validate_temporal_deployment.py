#!/usr/bin/env python3
"""Validate committed Temporal deployment assets without overstating host checks."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.task_runtime.deployment_contract import (  # noqa: E402
    TEMPORAL_RELEASE_BLOCKER_DURATIONS,
)
from src.task_runtime.secure_io import (  # noqa: E402
    read_file_snapshot,
    write_json_atomic,
)
from src.task_runtime.trusted_process import (  # noqa: E402
    TrustedExecutable,
    open_trusted_executable,
)

MAX_ASSET_BYTES = 4 * 1024 * 1024
COMMAND_TIMEOUT_SECONDS = 60.0
_SECRET_FILE_KEYS = (
    "TEMPORAL_POSTGRES_PASSWORD_FILE",
    "TEMPORAL_POSTGRES_EXPORTER_PASSWORD_FILE",
    "GRAFANA_ADMIN_PASSWORD_FILE",
)
_LONG_RUNNING = {
    "postgres",
    "postgres-exporter",
    "temporal",
    "temporal-ui",
    "prometheus",
    "grafana",
}
_JOBS = {"temporal-schema", "temporal-namespace", "temporal-exporter-role-sync"}
_IMAGES = {
    "postgres": "postgres:16.14-alpine3.23",
    "temporal-schema": "temporalio/admin-tools:1.29.7-tctl-1.18.4-cli-1.7.2",
    "temporal": "temporalio/server:1.29.7",
    "temporal-namespace": "temporalio/admin-tools:1.29.7-tctl-1.18.4-cli-1.7.2",
    "temporal-exporter-role-sync": "postgres:16.14-alpine3.23",
    "postgres-exporter": "ghcr.io/prometheus-community/postgres-exporter:v0.20.1",
    "temporal-ui": "temporalio/ui:2.53.1",
    "prometheus": "prom/prometheus:v3.13.2",
    "grafana": "grafana/grafana:12.4.8-ubuntu",
}
_PORTS = {
    "postgres": ["127.0.0.1:${TEMPORAL_POSTGRES_HOST_PORT:-5433}:5432"],
    "temporal-schema": [],
    "temporal": ["127.0.0.1:${TEMPORAL_GRPC_HOST_PORT:-7233}:7233"],
    "temporal-namespace": [],
    "temporal-exporter-role-sync": [],
    "postgres-exporter": [],
    "temporal-ui": ["127.0.0.1:${TEMPORAL_UI_HOST_PORT:-8233}:8080"],
    "prometheus": ["127.0.0.1:${PROMETHEUS_HOST_PORT:-9090}:9090"],
    "grafana": ["127.0.0.1:${GRAFANA_HOST_PORT:-3000}:3000"],
}
_NETWORKS = {
    "postgres": {"database"},
    "temporal-schema": {"database"},
    "temporal": {"database", "temporal", "temporal-metrics"},
    "temporal-namespace": {"temporal"},
    "temporal-exporter-role-sync": {"database"},
    "postgres-exporter": {"database", "postgres-metrics"},
    "temporal-ui": {"temporal"},
    "prometheus": {"temporal-metrics", "postgres-metrics", "prometheus-grafana"},
    "grafana": {"prometheus-grafana"},
}
_MOUNTS = {
    "postgres": ["temporal-postgres-data:/var/lib/postgresql/data"],
    "temporal-schema": [
        "./scripts/temporal-schema-setup.sh:/opt/medchat/temporal-schema-setup.sh:ro"
    ],
    "temporal": [
        "./scripts/temporal-server-entrypoint.sh:/opt/medchat/temporal-server-entrypoint.sh:ro"
    ],
    "temporal-namespace": [
        "./scripts/temporal-namespace-setup.sh:/opt/medchat/temporal-namespace-setup.sh:ro"
    ],
    "temporal-exporter-role-sync": [
        "./postgres-init/010-exporter.sh:/opt/medchat/010-exporter.sh:ro"
    ],
    "postgres-exporter": [],
    "temporal-ui": [],
    "prometheus": ["prometheus-data:/prometheus", "./prometheus:/etc/prometheus:ro"],
    "grafana": [
        "grafana-data:/var/lib/grafana",
        "./grafana/provisioning:/etc/grafana/provisioning:ro",
        "./grafana/dashboards:/var/lib/grafana/dashboards:ro",
    ],
}
_SERVICE_ENVIRONMENTS = {
    "postgres": {
        "POSTGRES_DB": "${TEMPORAL_POSTGRES_DB:-temporal}",
        "POSTGRES_PASSWORD_FILE": "/run/secrets/temporal_postgres_password",
        "POSTGRES_USER": "${TEMPORAL_POSTGRES_USER:-temporal}",
    },
    "temporal-schema": {
        "DBNAME": "${TEMPORAL_POSTGRES_DB:-temporal}",
        "DB_PORT": "5432",
        "POSTGRES_SEEDS": "postgres",
        "POSTGRES_USER": "${TEMPORAL_POSTGRES_USER:-temporal}",
        "VISIBILITY_DBNAME": "${TEMPORAL_POSTGRES_VISIBILITY_DB:-temporal_visibility}",
    },
    "temporal": {
        "BIND_ON_IP": "0.0.0.0",
        "DB": "postgres12",
        "DBNAME": "${TEMPORAL_POSTGRES_DB:-temporal}",
        "DB_PORT": "5432",
        "POSTGRES_SEEDS": "postgres",
        "POSTGRES_USER": "${TEMPORAL_POSTGRES_USER:-temporal}",
        "PROMETHEUS_ENDPOINT": "0.0.0.0:8000",
        "TEMPORAL_ADDRESS": "temporal:7233",
        "VISIBILITY_DBNAME": "${TEMPORAL_POSTGRES_VISIBILITY_DB:-temporal_visibility}",
    },
    "temporal-namespace": {
        "DEFAULT_NAMESPACE": "${TEMPORAL_NAMESPACE:-default}",
        "DEFAULT_NAMESPACE_RETENTION": "${TEMPORAL_NAMESPACE_RETENTION:-72h}",
        "MAX_ATTEMPTS": "30",
        "SLEEP_SECONDS": "5",
        "TEMPORAL_ADDRESS": "temporal:7233",
    },
    "temporal-exporter-role-sync": {
        "PGDATABASE": "${TEMPORAL_POSTGRES_DB:-temporal}",
        "PGHOST": "postgres",
        "PGPORT": "5432",
        "PGUSER": "${TEMPORAL_POSTGRES_USER:-temporal}",
        "VISIBILITY_DBNAME": "${TEMPORAL_POSTGRES_VISIBILITY_DB:-temporal_visibility}",
    },
    "postgres-exporter": {
        "DATA_SOURCE_PASS_FILE": "/run/secrets/temporal_postgres_exporter_password",
        "DATA_SOURCE_URI": "postgres:5432/${TEMPORAL_POSTGRES_DB:-temporal}?sslmode=disable",
        "DATA_SOURCE_USER": "temporal_exporter",
    },
    "temporal-ui": {"TEMPORAL_ADDRESS": "temporal:7233"},
    "prometheus": {},
    "grafana": {
        "GF_SECURITY_ADMIN_PASSWORD__FILE": "/run/secrets/grafana_admin_password",
        "GF_SECURITY_ADMIN_USER": "${GRAFANA_ADMIN_USER:-admin}",
    },
}
_SERVICE_SECRETS = {
    "postgres": {"temporal_postgres_password"},
    "temporal-schema": {"temporal_postgres_password"},
    "temporal": {"temporal_postgres_password"},
    "temporal-namespace": set(),
    "temporal-exporter-role-sync": {
        "temporal_postgres_password",
        "temporal_postgres_exporter_password",
    },
    "postgres-exporter": {"temporal_postgres_exporter_password"},
    "temporal-ui": set(),
    "prometheus": set(),
    "grafana": {"grafana_admin_password"},
}
_SERVICE_ENTRYPOINTS = {
    "temporal-schema": ["/opt/medchat/temporal-schema-setup.sh"],
    "temporal": ["/opt/medchat/temporal-server-entrypoint.sh"],
    "temporal-namespace": ["/opt/medchat/temporal-namespace-setup.sh"],
    "temporal-exporter-role-sync": ["/opt/medchat/010-exporter.sh"],
}
_ALERT_DURATIONS = TEMPORAL_RELEASE_BLOCKER_DURATIONS


class DeploymentCommandRunner:
    _PATHS = {
        "docker": (Path("/usr/bin/docker"), Path("/usr/local/bin/docker")),
        "promtool": (Path("/usr/bin/promtool"), Path("/usr/local/bin/promtool")),
        "systemd-analyze": (Path("/usr/bin/systemd-analyze"),),
    }

    def __init__(self) -> None:
        self._tools: dict[str, TrustedExecutable] = {}

    def available(self, tool: str) -> bool:
        if tool in self._tools:
            return True
        for path in self._PATHS.get(tool, ()):
            try:
                self._tools[tool] = open_trusted_executable(path)
                return True
            except ValueError:
                continue
        return False

    def close(self) -> None:
        tools = tuple(self._tools.values())
        self._tools.clear()
        failed = False
        for tool in tools:
            try:
                tool.close()
            except Exception:
                failed = True
        if failed:
            raise ValueError("deployment tool close failed")

    def __enter__(self) -> "DeploymentCommandRunner":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def run(
        self,
        argv: list[str],
        *,
        cwd: Path,
        environment: dict[str, str],
        timeout: float,
    ) -> subprocess.CompletedProcess[bytes]:
        tool = self._tools.get(argv[0])
        if tool is None:
            raise ValueError("deployment tool unavailable")
        tool.revalidate()
        result = subprocess.run(
            [tool.executable, *argv[1:]],
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            timeout=timeout,
            check=False,
            pass_fds=tool.pass_fds,
        )
        tool.revalidate()
        return result


def _canonical_sha256(payload: dict[str, Any]) -> str:
    projected = dict(payload)
    projected.pop("sha256", None)
    canonical = json.dumps(
        projected,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _file_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _directory_identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


def _is_reparse_point(metadata: os.stat_result) -> bool:
    return bool(getattr(metadata, "st_file_attributes", 0) & 0x400)


def write_report_atomic(path: Path, report: dict[str, Any]) -> None:
    write_json_atomic(path, report, maximum_bytes=MAX_ASSET_BYTES)


def _generated_at(value: datetime | None) -> str:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("invalid deployment validation clock")
    return current.astimezone(timezone.utc).replace(microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _read_regular_asset(path: Path) -> str:
    snapshot = read_file_snapshot(
        Path(os.path.abspath(os.fspath(path))), MAX_ASSET_BYTES
    )
    if b"\x00" in snapshot.content:
        raise ValueError("deployment asset invalid")
    return snapshot.content.decode("utf-8", errors="strict")


def _require(condition: object) -> None:
    if condition is not True:
        raise ValueError("deployment asset invalid")


def _load_yaml(text: str) -> dict[str, Any]:
    import yaml

    try:
        value = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise ValueError("deployment asset invalid") from error
    if type(value) is not dict:
        raise ValueError("deployment asset invalid")
    return value


def _environment_map(service: dict[str, Any]) -> dict[str, str]:
    environment = service.get("environment", {})
    if type(environment) is dict:
        return {str(key): str(value) for key, value in environment.items()}
    if type(environment) is not list:
        raise ValueError("deployment asset invalid")
    result: dict[str, str] = {}
    for item in environment:
        key, separator, value = str(item).partition("=")
        _require(bool(key) and separator == "=" and key not in result)
        result[key] = value
    return result


def _parse_systemd(text: str) -> dict[str, dict[str, list[str]]]:
    parsed: dict[str, dict[str, list[str]]] = {}
    section: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            parsed.setdefault(section, {})
            continue
        _require(section is not None and "=" in line)
        name, value = line.split("=", 1)
        parsed[section].setdefault(name, []).append(value)
    return parsed


def _validate_compose(compose: dict[str, Any], text: str) -> None:
    services = compose.get("services")
    _require(type(services) is dict and set(services) == _LONG_RUNNING | _JOBS)
    _require(all(type(service) is dict for service in services.values()))
    _require(
        {name: service.get("image") for name, service in services.items()}
        == _IMAGES
    )
    _require({
        name: service["entrypoint"]
        for name, service in services.items()
        if "entrypoint" in service
    } == _SERVICE_ENTRYPOINTS)
    _require(all("command" not in service for service in services.values()))
    _require(set(compose.get("volumes", {})) == {
        "temporal-postgres-data",
        "prometheus-data",
        "grafana-data",
    })
    _require(compose.get("networks") == {
        "database": {"internal": True},
        "temporal": None,
        "temporal-metrics": None,
        "postgres-metrics": None,
        "prometheus-grafana": None,
    })
    _require(compose.get("secrets") == {
        "temporal_postgres_password": {
            "file": "${TEMPORAL_POSTGRES_PASSWORD_FILE:?required}"
        },
        "temporal_postgres_exporter_password": {
            "file": "${TEMPORAL_POSTGRES_EXPORTER_PASSWORD_FILE:?required}"
        },
        "grafana_admin_password": {
            "file": "${GRAFANA_ADMIN_PASSWORD_FILE:?required}"
        },
    })
    expected_logging = {
        "driver": "json-file",
        "options": {"max-size": "10m", "max-file": "5"},
    }
    forbidden_service_keys = {
        "cap_add",
        "devices",
        "network_mode",
        "pid",
        "privileged",
        "userns_mode",
    }
    for name, service in services.items():
        _require(type(service) is dict)
        _require(forbidden_service_keys.isdisjoint(service))
        _require(service.get("ports", []) == _PORTS[name])
        _require(service.get("volumes", []) == _MOUNTS[name])
        _require(set(service.get("networks", [])) == _NETWORKS[name])
        _require(_environment_map(service) == _SERVICE_ENVIRONMENTS[name])
        _require(service.get("init") is True)
        _require(service.get("security_opt") == ["no-new-privileges:true"])
        _require(service.get("logging") == expected_logging)
        _require(type(service.get("stop_grace_period")) is str)
        mounted = {
            item.get("source") if type(item) is dict else str(item)
            for item in service.get("secrets", [])
        }
        _require(mounted == _SERVICE_SECRETS[name])
        _require(all(
            type(item) is not dict or {"uid", "gid", "mode"}.isdisjoint(item)
            for item in service.get("secrets", [])
        ))
        if name in _LONG_RUNNING:
            _require(service.get("restart") == "unless-stopped")
            health = service.get("healthcheck")
            _require(type(health) is dict and all(
                health.get(key)
                for key in ("test", "interval", "timeout", "retries", "start_period")
            ))
        else:
            _require(
                type(service.get("restart")) is str
                and re.fullmatch(r"on-failure:[1-9][0-9]*", service["restart"])
                is not None
            )
            _require("healthcheck" not in service and service.get("ports", []) == [])
    health_commands = {
        name: " ".join(map(str, services[name]["healthcheck"]["test"]))
        for name in _LONG_RUNNING
    }
    _require("pg_isready" in health_commands["postgres"])
    _require("127.0.0.1:9187/metrics" in health_commands["postgres-exporter"])
    _require("wget -qO- -T 4" in health_commands["postgres-exporter"])
    _require("grep -Eq" in health_commands["postgres-exporter"])
    _require("pg_up 1" in health_commands["postgres-exporter"])
    _require("7233" in health_commands["temporal"])
    _require(
        "temporal operator cluster health" in health_commands["temporal"]
        or "nc -z" in health_commands["temporal"]
    )
    _require("127.0.0.1:8080" in health_commands["temporal-ui"])
    _require("127.0.0.1:9090/-/healthy" in health_commands["prometheus"])
    _require("127.0.0.1:3000/api/health" in health_commands["grafana"])
    dependencies = {
        name: service.get("depends_on", {}) for name, service in services.items()
    }
    _require(dependencies == {
        "postgres": {},
        "temporal-schema": {"postgres": {"condition": "service_healthy"}},
        "temporal": {
            "temporal-schema": {"condition": "service_completed_successfully"}
        },
        "temporal-namespace": {"temporal": {"condition": "service_healthy"}},
        "temporal-exporter-role-sync": {
            "postgres": {"condition": "service_healthy"}
        },
        "postgres-exporter": {
            "temporal-exporter-role-sync": {
                "condition": "service_completed_successfully",
                "restart": True,
            }
        },
        "temporal-ui": {
            "temporal-namespace": {"condition": "service_completed_successfully"}
        },
        "prometheus": {
            "postgres-exporter": {"condition": "service_healthy"},
            "temporal": {"condition": "service_healthy"},
        },
        "grafana": {"prometheus": {"condition": "service_healthy"}},
    })
    role_sync = services["temporal-exporter-role-sync"]
    _require("profiles" not in role_sync)
    _require(role_sync.get("entrypoint") == ["/opt/medchat/010-exporter.sh"])
    forbidden = (
        ":latest",
        "auto-setup",
        "/docker-entrypoint-initdb.d",
        "/var/run/docker.sock",
        "../..:/",
        "POSTGRES_PASSWORD=${",
        "POSTGRES_PWD=${",
        "GF_SECURITY_ADMIN_PASSWORD=${",
    )
    _require(all(item not in text for item in forbidden))
    _require(re.search(r"(?m)^version:", text) is None)
    _require("$${POSTGRES_USER}" in text and "$${POSTGRES_DB}" in text)
    _require(re.search(r"(?<!\$)\$\{POSTGRES_(?:USER|DB)\}", text) is None)
    _require(all(
        not str(port).startswith("0.0.0.0:")
        for service in services.values()
        for port in service.get("ports", [])
    ))


def _validate_shell_header(text: str) -> None:
    _require(text.startswith("#!/bin/sh\n"))
    _require("set -eu" in text and "set -x" not in text)


def _validate_bounded_secret_handling(text: str) -> None:
    _require(all(token in text for token in (
        "maximum_secret_bytes=1024",
        "wc -c",
        "wc -l",
        "printf '\\r'",
        "E_SECRET_FILE",
        "E_SECRET_READ",
        "E_SECRET_FORMAT",
    )))
    _require(
        re.search(r"(?m)^\s*(?:echo|printf).*password", text) is None
    )


def _validate_identifier_contract(text: str) -> None:
    _require(all(token in text for token in (
        "LC_ALL=C",
        "validate_identifier()",
        '"${#value}" -le 63',
        "[A-Za-z_]",
        "*[!A-Za-z0-9_]*",
        "E_IDENTIFIER",
    )))


def _validate_schema_script(text: str) -> None:
    _validate_shell_header(text)
    _validate_bounded_secret_handling(text)
    _validate_identifier_contract(text)
    _require(all(token in text for token in (
        "/run/secrets/temporal_postgres_password",
        'export PGPASSWORD="$postgres_password"',
        'export SQL_PASSWORD="$postgres_password"',
        "if ! psql \\",
        "--set=ON_ERROR_STOP=1",
        "WHERE NOT EXISTS",
        "format('CREATE DATABASE %I'",
        "\\gexec",
        "E_SCHEMA_DATABASES",
        "E_SCHEMA_MIGRATION",
        "/etc/temporal/schema/postgresql/v12/temporal/versioned",
        "/etc/temporal/schema/postgresql/v12/visibility/versioned",
    )))
    _require(text.count("setup-schema -v 0.0") == 2)
    _require(text.count("update-schema -d") == 2)
    _require(re.search(r"temporal-sql-tool[^\n]*\bcreate\b", text) is None)
    _require("--password" not in text and "--pw" not in text)


def _validate_namespace_script(text: str) -> None:
    _validate_shell_header(text)
    _require(all(token in text for token in (
        "TEMPORAL_ADDRESS",
        "DEFAULT_NAMESPACE",
        "DEFAULT_NAMESPACE_RETENTION",
        "MAX_ATTEMPTS",
        "SLEEP_SECONDS",
        "*[!0-9]*",
        '"$MAX_ATTEMPTS" -gt 0',
        "temporal operator cluster health",
        "temporal operator namespace describe",
        "temporal operator namespace create",
        "temporal operator namespace update",
        '--namespace "$DEFAULT_NAMESPACE"',
        '--retention "$DEFAULT_NAMESPACE_RETENTION"',
        '[ "$attempt" -ge "$MAX_ATTEMPTS" ]',
        "E_CONFIGURATION",
        "E_TEMPORAL_UNAVAILABLE",
        "E_NAMESPACE_UNAVAILABLE",
        "E_NAMESPACE_RETENTION",
    )))
    _require("MAX_ATTdMPTS" not in text)


def _validate_server_wrapper(text: str) -> None:
    _validate_shell_header(text)
    _validate_bounded_secret_handling(text)
    _require(all(token in text for token in (
        "/run/secrets/temporal_postgres_password",
        'export POSTGRES_PWD="$postgres_password"',
        "unset postgres_password",
        'exec /etc/temporal/entrypoint.sh "$@"',
    )))


def _validate_role_sync_script(text: str) -> None:
    _validate_shell_header(text)
    _validate_bounded_secret_handling(text)
    _validate_identifier_contract(text)
    _require(all(token in text for token in (
        "/run/secrets/temporal_postgres_password",
        "/run/secrets/temporal_postgres_exporter_password",
        "if ! psql \\",
        "--no-psqlrc",
        "--set=ON_ERROR_STOP=1",
        "\\getenv exporter_password TEMPORAL_EXPORTER_PASSWORD",
        "BEGIN;",
        "DROP ROLE IF EXISTS temporal_exporter",
        "CREATE ROLE temporal_exporter WITH LOGIN PASSWORD",
        "NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT",
        "NOREPLICATION NOBYPASSRLS",
        "GRANT pg_monitor TO temporal_exporter WITH INHERIT TRUE",
        "GRANT pg_monitor TO temporal_exporter WITH SET FALSE",
        "GRANT pg_monitor TO temporal_exporter WITH ADMIN FALSE",
        "COMMIT;",
        "E_EXPORTER_RESET",
    )))
    _require("GRANT CONNECT ON DATABASE" not in text)
    _require("REASSIGN OWNED" not in text and "DROP OWNED" not in text)
    _require(all(catalog not in text for catalog in (
        "pg_shdepend",
        "pg_auth_members",
        "pg_database",
        "pg_namespace",
        "pg_class",
        "pg_proc",
        "pg_type",
        "pg_default_acl",
        "aclexplode",
    )))
    _require("--set=exporter_password" not in text)
    psql_argv = text.split("psql", 1)[1].split("<<'SQL'", 1)[0]
    _require("exporter_password" not in psql_argv)
    _require("TEMPORAL_EXPORTER_PASSWORD" not in psql_argv)


def _validate_rotate_exporter_script(text: str) -> None:
    _validate_shell_header(text)
    _validate_bounded_secret_handling(text)
    _require(all(token in text for token in (
        "LC_ALL=C",
        'TEMPORAL_POSTGRES_EXPORTER_PASSWORD_FILE:-',
        '[ ! -L "$secret_file" ]',
        "docker compose version --short",
        "E_COMPOSE_UNAVAILABLE",
        "E_COMPOSE_VERSION",
        "compose up --help",
        "--wait-timeout 120",
        "E_COMPOSE_WAIT_UNSUPPORTED",
        'script_directory=$(CDPATH= cd -- "$script_directory" 2>/dev/null && pwd -P)',
        "compose_file=$script_directory/../docker-compose.yml",
        "run --rm temporal-exporter-role-sync",
        "up -d --wait --wait-timeout 120 --no-deps --force-recreate",
        "postgres-exporter",
        "E_ROLE_SYNC",
        "E_EXPORTER_HEALTH",
        "OK E_EXPORTER_ROTATED",
    )))
    _require(
        '[ "$major" -lt 2 ]' in text
        and '[ "$major" -eq 2 ] && [ "$minor" -lt 17 ]' in text
    )


def _validate_operator_scripts(contents: dict[str, str]) -> None:
    _validate_schema_script(contents[
        "deployment/temporal/scripts/temporal-schema-setup.sh"
    ])
    _validate_namespace_script(contents[
        "deployment/temporal/scripts/temporal-namespace-setup.sh"
    ])
    _validate_server_wrapper(contents[
        "deployment/temporal/scripts/temporal-server-entrypoint.sh"
    ])
    _validate_role_sync_script(contents[
        "deployment/temporal/postgres-init/010-exporter.sh"
    ])
    _validate_rotate_exporter_script(contents[
        "deployment/temporal/scripts/rotate-exporter-password.sh"
    ])


def _validate_environment_example(text: str) -> None:
    pairs = [
        line.split("=", 1)
        for line in text.splitlines()
        if line and not line.lstrip().startswith("#")
    ]
    _require(all(len(pair) == 2 for pair in pairs))
    values = {key: value for key, value in pairs}
    _require(len(values) == len(pairs))
    _require(all(values.get(key) == "" for key in _SECRET_FILE_KEYS))
    _require("TEMPORAL_POSTGRES_PASSWORD" not in values)
    _require("GRAFANA_ADMIN_PASSWORD" not in values)
    _require(values.get("TEMPORAL_POSTGRES_DB") == "temporal")
    _require(values.get("TEMPORAL_POSTGRES_VISIBILITY_DB") == "temporal_visibility")
    _require(values.get("TEMPORAL_POSTGRES_USER") == "temporal")
    _require(values.get("TEMPORAL_NAMESPACE") == "default")
    _require(values.get("TEMPORAL_NAMESPACE_RETENTION") == "72h")
    _require(values.get("GRAFANA_ADMIN_USER") == "admin")
    _require(values.get("MEDCHAT_TEMPORAL_METRICS_RELAY_SUBNET") == "172.30.95.0/28")
    _require(values.get("MEDCHAT_TEMPORAL_METRICS_RELAY_GATEWAY") == "172.30.95.1")
    _require(values.get("MEDCHAT_TEMPORAL_PROMETHEUS_SCRAPE_ADDRESS") == "172.30.95.2")
    _require(values.get("MEDCHAT_TEMPORAL_METRICS_RELAY_PORT") == "9466")
    _require("MEDCHAT_TEMPORAL_NAMESPACE=default" in text)
    _require("MEDCHAT_TEMPORAL_DOCKING_QUEUE=medchat-docking" in text)
    guidance = text.lower()
    _require("e_relay_runtime_mismatch" in guidance)
    _require("never commit real" in guidance)
    _require(all(contract in guidance for contract in (
        "regular file",
        "symlink",
        "trailing newline",
        "1024",
        "0700",
        "0444",
        "deployment owner",
        "bind mount",
        "uid",
        "gid",
        "mode",
        "printf",
        "rotate-exporter-password.sh",
        ">= 2.17",
    )))
    _require("0600" not in guidance)


def _validate_systemd_assets(contents: dict[str, str]) -> None:
    web = _parse_systemd(contents["deployment/medchat.service"])
    worker = _parse_systemd(contents["deployment/medchat-temporal-worker.service"])
    prepare = _parse_systemd(
        contents["deployment/medchat-temporal-worker-prepare.service"]
    )
    _require(web == {
        "Unit": {
            "Description": ["MedChat Web Service"],
            "After": ["network.target"],
        },
        "Service": {
            "Type": ["simple"],
            "User": ["medchat"],
            "Group": ["medchat"],
            "WorkingDirectory": ["/opt/medchat/molecular_chat_system"],
            "EnvironmentFile": ["/etc/medchat/medchat.env"],
            "ExecStart": [
                "/opt/conda/envs/medchat/bin/python main.py --no-reload"
            ],
            "Restart": ["always"],
            "RestartSec": ["3"],
            "TimeoutStartSec": ["120"],
            "TimeoutStopSec": ["30"],
        },
        "Install": {"WantedBy": ["multi-user.target"]},
    })
    _require(worker == {
        "Unit": {
            "Description": ["MedChat Temporal Docking Worker"],
            "After": [
                "network-online.target docker.service "
                "medchat-temporal-worker-prepare.service "
                "medchat-sandbox-broker.service"
            ],
            "Wants": ["network-online.target"],
            "Requires": [
                "medchat-temporal-worker-prepare.service "
                "medchat-sandbox-broker.service"
            ],
        },
        "Service": {
            "Type": ["simple"],
            "User": ["medchat"],
            "Group": ["medchat"],
            "SupplementaryGroups": ["medchat-sandbox"],
            "WorkingDirectory": ["/opt/medchat/molecular_chat_system"],
            "EnvironmentFile": ["/etc/medchat/temporal-worker.env"],
            "Environment": [
                "PATH=/opt/conda/envs/medchat/bin:/usr/local/sbin:"
                "/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                "PYTHONDONTWRITEBYTECODE=1",
            ],
            "ExecStartPre": [
                "/opt/conda/envs/medchat/bin/python "
                "scripts/validate_temporal_worker_production.py --check-config"
            ],
            "ExecStart": [
                "/opt/conda/envs/medchat/bin/python "
                "scripts/run_temporal_docking_worker.py"
            ],
            "Restart": ["on-failure"],
            "RestartSec": ["5"],
            "TimeoutStartSec": ["60"],
            "TimeoutStopSec": ["90"],
            "KillMode": ["mixed"],
            "KillSignal": ["SIGTERM"],
            "SendSIGKILL": ["yes"],
            "UMask": ["0077"],
            "PrivateTmp": ["true"],
            "NoNewPrivileges": ["true"],
            "ProtectSystem": ["strict"],
            "ReadWritePaths": [
                "/opt/medchat/molecular_chat_system/scratch "
                "/opt/medchat/molecular_chat_system/temp_docking "
                "/run/medchat-sandbox"
            ],
            "ReadOnlyPaths": [
                "/opt/medchat/molecular_chat_system "
                "/opt/conda/envs/medchat"
            ],
            "InaccessiblePaths": [
                "/var/run/docker.sock /etc/medchat/opensandbox.env "
                "/etc/medchat/opensandbox.toml "
                "/etc/medchat/sandbox-broker.env "
                "/var/lib/opensandbox/approved-empty"
            ],
        },
        "Install": {"WantedBy": ["multi-user.target"]},
    })
    _require(prepare == {
        "Unit": {
            "Description": [
                "Prepare MedChat Temporal Docking Worker Trust Boundary"
            ],
            "Before": ["medchat-temporal-worker.service"],
            "PartOf": ["medchat-temporal-worker.service"],
        },
        "Service": {
            "Type": ["oneshot"],
            "ExecStart": [
                "/usr/lib/medchat/temporal-worker/current/libexec/"
                "validate-temporal-worker-env",
                "/usr/lib/medchat/temporal-worker/current/libexec/"
                "prepare-temporal-worker-directories",
            ],
            "RemainAfterExit": ["yes"],
        },
    })
    unit_text = "\n".join(
        contents[name]
        for name in (
            "deployment/medchat.service",
            "deployment/medchat-temporal-worker.service",
            "deployment/medchat-temporal-worker-prepare.service",
        )
    )
    _require(re.search(r"(?i)(?:password|secret|token)\s*=\s*\S+", unit_text) is None)
    _require("$(" not in unit_text and "${" not in unit_text and "`" not in unit_text)


def _validate_prometheus(config: dict[str, Any], rules: dict[str, Any], rule_text: str) -> None:
    _require(config.get("global") == {
        "scrape_interval": "15s",
        "evaluation_interval": "15s",
    })
    _require(config.get("rule_files") == ["/etc/prometheus/rules/*.yml"])
    scrape_configs = config.get("scrape_configs")
    _require(type(scrape_configs) is list)
    jobs = {job.get("job_name"): job for job in scrape_configs if type(job) is dict}
    _require(set(jobs) == {
        "prometheus",
        "temporal-server",
        "temporal-postgres",
        "medchat-temporal-worker",
    })
    _require(jobs["prometheus"].get("static_configs") == [{"targets": ["prometheus:9090"]}])
    _require(jobs["temporal-server"].get("static_configs") == [{"targets": ["temporal:8000"]}])
    _require(jobs["temporal-postgres"].get("static_configs") == [{"targets": ["postgres-exporter:9187"]}])
    worker = jobs["medchat-temporal-worker"]
    _require("static_configs" not in worker)
    _require(worker.get("file_sd_configs") == [{
        "files": ["/etc/prometheus/file_sd/worker-targets.json"],
        "refresh_interval": "30s",
    }])
    _require("host.docker.internal" not in json.dumps(config))
    groups = rules.get("groups")
    _require(type(groups) is list and bool(groups))
    flattened = [
        rule
        for group in groups
        if type(group) is dict and type(group.get("rules")) is list
        for rule in group["rules"]
    ]
    alerts = {
        rule.get("alert"): rule for rule in flattened if type(rule) is dict
    }
    _require(len(flattened) == 11 and set(alerts) == set(_ALERT_DURATIONS))
    for name, expected_duration in _ALERT_DURATIONS.items():
        rule = alerts[name]
        _require(rule.get("labels") == {
            "severity": "critical",
            "release_blocker": "true",
        })
        _require(set(rule.get("annotations", {})) == {"summary", "description"})
        _require(all(str(value).strip() for value in rule["annotations"].values()))
        serialized_rule = json.dumps(rule, ensure_ascii=True).lower()
        _require(all(value not in serialized_rule for value in (
            "task_id",
            "trace_id",
            "workflow_id",
            "prompt",
            "smiles",
            "secret",
            "password",
            "file_path",
            "c:\\",
            "/home/",
            "/users/",
        )))
        if expected_duration is None:
            _require("for" not in rule)
        else:
            _require(rule.get("for") == expected_duration)
    compact = {
        name: re.sub(r"\s+", "", str(rule.get("expr")))
        for name, rule in alerts.items()
    }
    heartbeat_metric = "medchat_temporal_worker_last_heartbeat_timestamp_seconds"
    _require(f"time()-{heartbeat_metric}>60" in compact["TemporalWorkerHeartbeatStale"])
    _require(f"absent({heartbeat_metric})" in compact["TemporalWorkerHeartbeatStale"])
    baseline = compact["TemporalDockingP95BaselineMissing"]
    _require("absent(medchat_temporal_baseline_p95_seconds)" in baseline)
    _require("max(medchat_temporal_baseline_p95_seconds)<=0" in baseline)
    compact_backlog = compact["TemporalQueueBacklogGrowing"]
    _require(
        'deriv(approximate_backlog_count{namespace="default",taskqueue="medchat-docking"}[10m])>0'
        in compact_backlog
    )
    _require("increase(approximate_backlog_count" not in compact_backlog)
    starts = compact["TemporalWorkflowStartUnexpectedErrors"]
    _require(
        'increase(service_errors{service_name="frontend",namespace="default",operation="StartWorkflowExecution"}[15m])>0'
        in starts
    )
    for name, metric in {
        "TemporalDuplicateVinaExecution": "medchat_temporal_duplicate_vina_execution_total",
        "TemporalMultipleTerminalEvents": "medchat_temporal_terminal_invariant_violation_total",
        "TemporalArtifactValidationFailure": "medchat_temporal_artifact_validation_failure_total",
    }.items():
        _require(f"increase({metric}[15m])>0" in compact[name])
    p95 = compact["TemporalDockingP95TooHigh"]
    quantile = (
        "histogram_quantile(0.95,sumby(le)(rate("
        "medchat_temporal_task_duration_seconds_bucket[15m])))"
    )
    _require(
        f"{quantile}>scalar(max(medchat_temporal_baseline_p95_seconds))*1.5"
        in p95
    )
    _require(f"{quantile}>60" in p95 and "or" in p95)
    failure_rate = compact["TemporalRuntimeFailureRateHigh"]
    _require(
        'medchat_temporal_task_terminal_total{status!="succeeded"}[30m]'
        in failure_rate
    )
    _require("medchat_temporal_task_terminal_total[30m]" in failure_rate)
    _require("clamp_min(" in failure_rate and ",1)" in failure_rate)
    _require(">0.05" in failure_rate)
    unavailable = compact["TemporalPostgresUnavailable"]
    for job in ("temporal-server", "temporal-postgres"):
        _require(f'absent(up{{job="{job}"}})' in unavailable)
        _require(f'up{{job="{job}"}}!=1' in unavailable)
    _require('absent(pg_up{job="temporal-postgres"})' in unavailable)
    _require('pg_up{job="temporal-postgres"}!=1' in unavailable)
    backup_metric = "medchat_temporal_backup_verified_timestamp_seconds"
    backup = compact["TemporalBackupVerificationStale"]
    _require(f"absent({backup_metric})" in backup)
    _require(f"{backup_metric}==0" in backup)
    _require(f"time()-{backup_metric}>86400" in backup)
    _require("task_queue" not in rule_text)


def _validate_grafana(
    datasource: dict[str, Any],
    provider: dict[str, Any],
    dashboard: dict[str, Any],
) -> None:
    _require(datasource == {
        "apiVersion": 1,
        "datasources": [{
            "name": "Prometheus",
            "type": "prometheus",
            "uid": "medchat-prometheus",
            "access": "proxy",
            "url": "http://prometheus:9090",
            "isDefault": True,
            "editable": False,
        }],
    })
    _require(provider == {
        "apiVersion": 1,
        "providers": [{
            "name": "medchat-temporal",
            "orgId": 1,
            "folder": "MedChat",
            "type": "file",
            "disableDeletion": True,
            "editable": False,
            "updateIntervalSeconds": 30,
            "options": {
                "path": "/var/lib/grafana/dashboards",
                "foldersFromFilesStructure": False,
            },
        }],
    })
    _require(dashboard.get("uid") == "medchat-temporal-docking")
    _require(dashboard.get("refresh") == "15s")
    _require(type(dashboard.get("schemaVersion")) is int and dashboard["schemaVersion"] >= 39)
    _require(dashboard.get("editable") is False)
    panels = dashboard.get("panels")
    _require(
        type(panels) is list
        and all(type(panel) is dict for panel in panels)
    )
    expected_titles = {
        "Canary level",
        "Worker readiness",
        "Worker heartbeat",
        "Queue backlog",
        "Workflow start requests",
        "Terminal distribution",
        "p50 / p95 latency",
        "Vina attempts",
        "Duplicate prevention",
        "Terminal invariants",
        "Artifact validation",
        "Provenance gate",
        "Scientific gates",
        "Firing release blockers",
    }
    _require({panel.get("title") for panel in panels if type(panel) is dict} == expected_titles)
    queries_by_title = {
        panel["title"]: [target.get("expr") for target in panel.get("targets", [])]
        for panel in panels
    }
    _require(queries_by_title.get("Queue backlog") == [
        'sum(approximate_backlog_count{namespace="default",taskqueue="medchat-docking"})'
    ])
    _require(queries_by_title.get("Workflow start requests") == [
        'sum(increase(service_requests{service_name="frontend",namespace="default",operation="StartWorkflowExecution"}[5m]))'
    ])
    _require(queries_by_title.get("Firing release blockers") == [
        'count(ALERTS{alertstate="firing",release_blocker="true"}) or vector(0)'
    ])
    _require(all(
        target.get("datasource", {}).get("uid") == "medchat-prometheus"
        for panel in panels
        for target in panel.get("targets", [])
    ))
    serialized = json.dumps(dashboard, ensure_ascii=True).lower()
    query_text = "\n".join(
        str(target.get("expr"))
        for panel in panels
        for target in panel.get("targets", [])
    )
    allowed_metrics = {
        "approximate_backlog_count",
        "service_requests",
        "ALERTS",
        "medchat_temporal_worker_ready",
        "medchat_temporal_worker_last_heartbeat_timestamp_seconds",
        "medchat_temporal_canary_percent",
        "medchat_temporal_task_terminal_total",
        "medchat_temporal_task_duration_seconds_bucket",
        "medchat_temporal_docking_process_attempts_total",
        "medchat_temporal_duplicate_vina_execution_total",
        "medchat_temporal_terminal_invariant_violation_total",
        "medchat_temporal_artifact_validation_failure_total",
        "medchat_temporal_provenance_validation_failure_total",
    }
    metric_like = set(re.findall(
        r"\b[A-Za-z][A-Za-z0-9_:]*_[A-Za-z0-9_:]+\b",
        query_text,
    ))
    _require(
        metric_like
        - {"alertstate", "histogram_quantile", "release_blocker", "service_name", "task_type"}
        <= allowed_metrics
    )
    _require(allowed_metrics <= set(re.findall(
        r"\b(?:ALERTS|[A-Za-z][A-Za-z0-9_:]*_[A-Za-z0-9_:]+)\b",
        query_text,
    )))
    _require("task_queue" not in query_text)
    _require(all(item not in serialized for item in (
        "smiles",
        "prompt",
        "user_id",
        "task_id",
        "trace_id",
        "workflow_id",
        "file_path",
        "secret",
        "password",
        "c:\\",
        "/home/",
        "/users/",
    )))


def _python_static(repo_root: Path) -> dict[str, str]:
    required = (
        "deployment/temporal/docker-compose.yml",
        "deployment/temporal/env.example",
        "deployment/temporal/prometheus/prometheus.yml",
        "deployment/temporal/prometheus/rules/medchat-temporal.yml",
        "deployment/temporal/prometheus/tests/medchat-temporal.test.yml",
        "deployment/temporal/grafana/provisioning/datasources/prometheus.yml",
        "deployment/temporal/grafana/provisioning/dashboards/dashboard.yml",
        "deployment/temporal/grafana/dashboards/medchat-temporal-docking.json",
        "deployment/temporal/scripts/temporal-schema-setup.sh",
        "deployment/temporal/scripts/temporal-namespace-setup.sh",
        "deployment/temporal/scripts/temporal-server-entrypoint.sh",
        "deployment/temporal/postgres-init/010-exporter.sh",
        "deployment/temporal/scripts/rotate-exporter-password.sh",
        "deployment/medchat.service",
        "deployment/medchat-temporal-worker.service",
        "deployment/medchat-temporal-worker-prepare.service",
        "scripts/run_temporal_docking_acceptance.py",
        "scripts/backup_temporal_postgres.py",
        "scripts/restore_temporal_postgres.py",
    )
    try:
        contents = {
            relative: _read_regular_asset(repo_root / relative)
            for relative in required
        }
        compose_text = contents["deployment/temporal/docker-compose.yml"]
        env_text = contents["deployment/temporal/env.example"]
        prometheus_text = contents[
            "deployment/temporal/prometheus/prometheus.yml"
        ]
        rules_text = contents[
            "deployment/temporal/prometheus/rules/medchat-temporal.yml"
        ]
        datasource_text = contents[
            "deployment/temporal/grafana/provisioning/datasources/prometheus.yml"
        ]
        provider_text = contents[
            "deployment/temporal/grafana/provisioning/dashboards/dashboard.yml"
        ]
        dashboard = json.loads(contents[
            "deployment/temporal/grafana/dashboards/medchat-temporal-docking.json"
        ])
        _require(type(dashboard) is dict)
        _validate_compose(_load_yaml(compose_text), compose_text)
        _validate_operator_scripts(contents)
        _validate_environment_example(env_text)
        _validate_systemd_assets(contents)
        _validate_prometheus(
            _load_yaml(prometheus_text),
            _load_yaml(rules_text),
            rules_text,
        )
        _validate_grafana(
            _load_yaml(datasource_text),
            _load_yaml(provider_text),
            dashboard,
        )
        rule_fixture = _load_yaml(contents[
            "deployment/temporal/prometheus/tests/medchat-temporal.test.yml"
        ])
        _require(rule_fixture.get("rule_files") == ["../rules/medchat-temporal.yml"])
        _require({
            case.get("name")
            for case in rule_fixture.get("tests", [])
            if type(case) is dict
        } == {
            "labeled baseline p95 fires",
            "missing baseline blocks release",
            "scoped docking queue grows",
            "unrelated queue does not fire",
            "low frequency start error persists",
            "low frequency runtime failure persists",
            "one scrape gap is tolerated",
            "sustained unavailability fires",
        })
        fixture_text = contents[
            "deployment/temporal/prometheus/tests/medchat-temporal.test.yml"
        ]
        _require('taskqueue="medchat-docking"' in fixture_text)
        _require('taskqueue="unrelated"' in fixture_text)
        _require("task_queue=" not in fixture_text)
        _require("medchat_temporal_baseline_p95_seconds" in fixture_text)
        _require("service_errors" in fixture_text)
        _require("medchat_temporal_task_terminal_total" in fixture_text)
        _require("alertname: TemporalPostgresUnavailable" in fixture_text)
        monitoring_text = "\n".join((
            prometheus_text,
            rules_text,
            datasource_text,
            provider_text,
            contents[
                "deployment/temporal/grafana/dashboards/medchat-temporal-docking.json"
            ],
        )).lower()
        _require(all(value not in monitoring_text for value in (
            "password:",
            "api_key",
            "apikey",
            "bearer_token",
            "client_secret",
            "c:\\users\\",
            "/home/",
            "/users/",
        )))
    except (
        AttributeError,
        ImportError,
        KeyError,
        OSError,
        TypeError,
        UnicodeError,
        ValueError,
        json.JSONDecodeError,
    ):
        return {"status": "failed", "code": "python_static_invalid"}
    return {"status": "passed", "code": "python_static_valid"}


def _public_compose_environment(env_text: str) -> dict[str, str]:
    environment = {
        key: value
        for key in ("SYSTEMROOT", "WINDIR", "HOME", "USERPROFILE")
        if (value := os.environ.get(key)) is not None
    }
    environment["PATH"] = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    for raw_line in env_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key not in _SECRET_FILE_KEYS:
            environment[key] = value
    return environment


def _returncode(result: object) -> int:
    value = getattr(result, "returncode", None)
    return value if type(value) is int else 1


def _run_check(
    runner: object,
    argv: list[str],
    *,
    repo_root: Path,
    environment: dict[str, str],
) -> bool:
    try:
        result = runner.run(
            argv,
            cwd=repo_root,
            environment=environment,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except Exception:
        return False
    return _returncode(result) == 0


def validate_deployment(
    *,
    repo_root: Path = PROJECT_ROOT,
    command_runner: object | None = None,
    platform_name: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    owns_runner = command_runner is None
    runner = command_runner or DeploymentCommandRunner()
    try:
        return _validate_deployment_with_runner(
            root=Path(repo_root),
            runner=runner,
            host=platform_name or platform.system(),
            now=now,
        )
    finally:
        if owns_runner:
            runner.close()


def _validate_deployment_with_runner(
    *,
    root: Path,
    runner: object,
    host: str,
    now: datetime | None,
) -> dict[str, Any]:
    checks: dict[str, dict[str, str]] = {
        "python_static": _python_static(root),
    }
    try:
        docker_available = bool(runner.available("docker"))
    except Exception:
        docker_available = False
    if not docker_available:
        checks["docker_compose"] = {
            "status": "skipped",
            "code": "docker_unavailable",
        }
    else:
        try:
            env_text = _read_regular_asset(root / "deployment/temporal/env.example")
            with tempfile.TemporaryDirectory(prefix="medchat-compose-validation-") as raw:
                secret_root = Path(raw)
                environment = _public_compose_environment(env_text)
                for key in _SECRET_FILE_KEYS:
                    secret = secret_root / key.lower()
                    secret.write_bytes(b"validation-only")
                    if os.name == "posix":
                        os.chmod(secret, 0o600)
                    environment[key] = os.fspath(secret)
                passed = _run_check(
                    runner,
                    [
                        "docker",
                        "compose",
                        "--env-file",
                        "deployment/temporal/env.example",
                        "-f",
                        "deployment/temporal/docker-compose.yml",
                        "config",
                        "--quiet",
                    ],
                    repo_root=root,
                    environment=environment,
                )
        except Exception:
            passed = False
        checks["docker_compose"] = {
            "status": "passed" if passed else "failed",
            "code": "docker_compose_valid" if passed else "docker_compose_invalid",
        }

    try:
        promtool_available = bool(runner.available("promtool"))
    except Exception:
        promtool_available = False
    if not promtool_available:
        checks["promtool"] = {
            "status": "skipped",
            "code": "promtool_unavailable",
        }
    else:
        environment = _public_compose_environment("")
        commands = (
            [
                "promtool",
                "check",
                "config",
                "deployment/temporal/prometheus/prometheus.yml",
            ],
            [
                "promtool",
                "check",
                "rules",
                "deployment/temporal/prometheus/rules/medchat-temporal.yml",
            ],
            [
                "promtool",
                "test",
                "rules",
                "deployment/temporal/prometheus/tests/medchat-temporal.test.yml",
            ],
        )
        passed = all(
            _run_check(runner, command, repo_root=root, environment=environment)
            for command in commands
        )
        checks["promtool"] = {
            "status": "passed" if passed else "failed",
            "code": "promtool_valid" if passed else "promtool_invalid",
        }

    if host != "Linux":
        checks["systemd_analyze"] = {
            "status": "skipped",
            "code": "linux_systemd_required",
        }
    else:
        try:
            systemd_available = bool(runner.available("systemd-analyze"))
        except Exception:
            systemd_available = False
        if not systemd_available:
            checks["systemd_analyze"] = {
                "status": "skipped",
                "code": "systemd_analyze_unavailable",
            }
        else:
            passed = _run_check(
                runner,
                [
                    "systemd-analyze",
                    "verify",
                    "deployment/medchat.service",
                    "deployment/medchat-temporal-worker.service",
                ],
                repo_root=root,
                environment=_public_compose_environment(""),
            )
            checks["systemd_analyze"] = {
                "status": "passed" if passed else "failed",
                "code": "systemd_units_valid" if passed else "systemd_units_invalid",
            }

    statuses = {check["status"] for check in checks.values()}
    status = "failed" if "failed" in statuses else "partial" if "skipped" in statuses else "passed"
    report: dict[str, Any] = {
        "schema_version": 1,
        "stage": "deployment_validation",
        "status": status,
        "generated_at": _generated_at(now),
        "checks": checks,
    }
    report["sha256"] = _canonical_sha256(report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate Temporal deployment assets")
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        report = validate_deployment()
        write_report_atomic(arguments.output, report)
    except Exception:
        print("temporal_deployment_validation=failed code=validation_failed", file=sys.stderr)
        return 1
    print(f"status={report['status']} sha256={report['sha256']}")
    return 0 if report["status"] == "passed" else 2 if report["status"] == "partial" else 1


if __name__ == "__main__":
    raise SystemExit(main())
