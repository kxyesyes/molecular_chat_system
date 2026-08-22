from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deployment" / "temporal"
COMPOSE = DEPLOY / "docker-compose.yml"
ENV_EXAMPLE = DEPLOY / "env.example"
ROLE_SCRIPT = DEPLOY / "postgres-init" / "010-exporter.sh"
SCHEMA_SCRIPT = DEPLOY / "scripts" / "temporal-schema-setup.sh"
SERVER_SCRIPT = DEPLOY / "scripts" / "temporal-server-entrypoint.sh"
NAMESPACE_SCRIPT = DEPLOY / "scripts" / "temporal-namespace-setup.sh"
ROTATE_SCRIPT = DEPLOY / "scripts" / "rotate-exporter-password.sh"
PLAN = ROOT / "docs/superpowers/plans/2026-08-21-temporal-production-canary-readiness.md"
DESIGN = ROOT / "docs/superpowers/specs/2026-08-21-temporal-production-canary-readiness-design.md"

LONG_RUNNING = {
    "postgres", "postgres-exporter", "temporal", "temporal-ui", "prometheus", "grafana"
}
JOBS = {"temporal-schema", "temporal-namespace", "temporal-exporter-role-sync"}
IMAGES = {
    "postgres": "postgres:16.14-alpine3.23",
    "temporal-schema": "temporalio/admin-tools:1.29.7",
    "temporal": "temporalio/server:1.29.7",
    "temporal-namespace": "temporalio/admin-tools:1.29.7",
    "temporal-exporter-role-sync": "postgres:16.14-alpine3.23",
    "postgres-exporter": "ghcr.io/prometheus-community/postgres-exporter:v0.20.1",
    "temporal-ui": "temporalio/ui:2.53.1",
    "prometheus": "prom/prometheus:v3.13.2",
    "grafana": "grafana/grafana:12.4.8-ubuntu",
}
PORTS = {
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
NETWORKS = {
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
MOUNTS = {
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


def shell_executable() -> Path:
    candidates = [
        shutil.which("sh"),
        r"C:\Program Files\Git\bin\sh.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    pytest.skip("POSIX shell is unavailable")


def shell_path(shell: Path, path: Path) -> str:
    if os.name != "nt":
        return str(path)
    resolved = path.resolve().as_posix()
    assert re.fullmatch(r"[A-Za-z]:/.*", resolved)
    return f"/{resolved[0].lower()}{resolved[2:]}"


def run_shell_script(
    script: Path,
    tmp_path: Path,
    *,
    environment: dict[str, str],
    stubs: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    shell = shell_executable()
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    for name, content in stubs.items():
        stub = stub_dir / name
        stub.write_text(content, encoding="utf-8", newline="\n")
        stub.chmod(0o755)
    process_environment = os.environ.copy()
    process_environment.update(environment)
    process_environment["PATH"] = (
        f"{shell_path(shell, stub_dir)}:{process_environment.get('PATH', '')}"
    )
    return subprocess.run(
        [str(shell), shell_path(shell, script)],
        cwd=DEPLOY,
        env=process_environment,
        capture_output=True,
        text=True,
        timeout=15,
    )


def identifier_test_environment(tmp_path: Path, *, role_sync: bool) -> dict[str, str]:
    shell = shell_executable()
    postgres_secret = tmp_path / "postgres-password"
    postgres_secret.write_bytes(b"test-postgres-password")
    environment = {
        "POSTGRES_SEEDS": "postgres",
        "POSTGRES_USER": "temporal",
        "DB_PORT": "5432",
        "DBNAME": "temporal",
        "VISIBILITY_DBNAME": "temporal_visibility",
        "PGHOST": "postgres",
        "PGPORT": "5432",
        "PGDATABASE": "temporal",
        "PGUSER": "temporal",
        "TEMPORAL_POSTGRES_PASSWORD_FILE": shell_path(shell, postgres_secret),
    }
    if role_sync:
        exporter_secret = tmp_path / "exporter-password"
        exporter_secret.write_bytes(b"test-exporter-password")
        environment["TEMPORAL_POSTGRES_EXPORTER_PASSWORD_FILE"] = shell_path(
            shell, exporter_secret
        )
    return environment


def command_recording_stubs() -> dict[str, str]:
    return {
        "wc": """#!/bin/sh
printf 'wc %s\\n' "$*" >> "$STUB_LOG"
exec /usr/bin/wc "$@"
""",
        "cat": """#!/bin/sh
printf 'cat %s\\n' "$*" >> "$STUB_LOG"
exec /usr/bin/cat "$@"
""",
        "psql": """#!/bin/sh
printf 'psql %s\\n' "$*" >> "$STUB_LOG"
exit 0
""",
        "temporal-sql-tool": """#!/bin/sh
printf 'temporal-sql-tool %s\\n' "$*" >> "$STUB_LOG"
exit 0
""",
    }


def load_yaml(path: Path) -> dict[str, Any]:
    yaml = pytest.importorskip("yaml", reason="PyYAML is required for YAML checks")
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def env_map(service: dict[str, Any]) -> dict[str, str]:
    environment = service.get("environment", {})
    if isinstance(environment, dict):
        return {str(key): str(value) for key, value in environment.items()}
    result = {}
    for item in environment:
        key, separator, value = str(item).partition("=")
        assert separator
        result[key] = value
    return result


def deps(service: dict[str, Any]) -> dict[str, dict[str, Any]]:
    value = service.get("depends_on", {})
    assert isinstance(value, dict)
    return value


def test_images_services_ports_and_mount_boundaries() -> None:
    compose = load_yaml(COMPOSE)
    services = compose["services"]
    assert set(services) == LONG_RUNNING | JOBS
    assert {name: service["image"] for name, service in services.items()} == IMAGES
    assert "auto-setup" not in COMPOSE.read_text(encoding="utf-8")
    assert all(not image.endswith(":latest") and "@sha256:" not in image for image in IMAGES.values())
    assert set(compose["volumes"]) == {
        "temporal-postgres-data", "prometheus-data", "grafana-data"
    }
    for name, service in services.items():
        assert service.get("ports", []) == PORTS[name]
        assert service.get("volumes", []) == MOUNTS[name]
        assert all(str(port).startswith("127.0.0.1:") for port in service.get("ports", []))
        assert all(not str(mount).startswith("./") or str(mount).endswith(":ro")
                   for mount in service.get("volumes", []))
        assert all(not str(mount).startswith("/") for mount in service.get("volumes", []))
    text = COMPOSE.read_text(encoding="utf-8")
    assert "/docker-entrypoint-initdb.d" not in text
    assert "./postgres-init/010-exporter.sh:/opt/medchat/010-exporter.sh:ro" in text
    assert "/var/run/docker.sock" not in text
    assert "../..:/" not in text


def test_lifecycle_healthchecks_and_bounded_logs() -> None:
    services = load_yaml(COMPOSE)["services"]
    expected_logging = {
        "driver": "json-file",
        "options": {"max-size": "10m", "max-file": "5"},
    }
    for name in LONG_RUNNING:
        service = services[name]
        assert service["restart"] == "unless-stopped", name
        health = service.get("healthcheck")
        assert health and all(health.get(key) for key in (
            "test", "interval", "timeout", "retries", "start_period"
        )), name
    for name in JOBS:
        service = services[name]
        assert re.fullmatch(r"on-failure:[1-9][0-9]*", service["restart"]), name
        assert "healthcheck" not in service
        assert service.get("ports", []) == []
    for name, service in services.items():
        assert service.get("init") is True, name
        assert service.get("security_opt") == ["no-new-privileges:true"], name
        assert service.get("stop_grace_period"), name
        assert service.get("logging") == expected_logging, name

    commands = {
        name: " ".join(map(str, services[name]["healthcheck"]["test"]))
        for name in LONG_RUNNING
    }
    assert "pg_isready" in commands["postgres"]
    assert "127.0.0.1:9187/metrics" in commands["postgres-exporter"]
    assert "wget -qO- -T 4" in commands["postgres-exporter"]
    assert "grep -Eq" in commands["postgres-exporter"]
    assert "pg_up 1" in commands["postgres-exporter"]
    assert "7233" in commands["temporal"]
    assert (
        "temporal operator cluster health" in commands["temporal"]
        or "nc -z" in commands["temporal"]
    )
    assert "127.0.0.1:8080" in commands["temporal-ui"]
    assert "127.0.0.1:9090/-/healthy" in commands["prometheus"]
    assert "127.0.0.1:3000/api/health" in commands["grafana"]


def test_job_dependencies_are_completed_successfully_and_acyclic() -> None:
    services = load_yaml(COMPOSE)["services"]
    expected = {
        "postgres": {},
        "temporal-schema": {"postgres": {"condition": "service_healthy"}},
        "temporal": {"temporal-schema": {"condition": "service_completed_successfully"}},
        "temporal-namespace": {"temporal": {"condition": "service_healthy"}},
        "temporal-exporter-role-sync": {"postgres": {"condition": "service_healthy"}},
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
    }
    actual = {name: deps(service) for name, service in services.items()}
    assert actual == expected
    role_sync = services["temporal-exporter-role-sync"]
    assert "profiles" not in role_sync
    assert role_sync["entrypoint"] == ["/opt/medchat/010-exporter.sh"]

    def visit(name: str, ancestors: frozenset[str]) -> None:
        assert name not in ancestors
        for dependency in actual[name]:
            visit(dependency, ancestors | {name})

    for name in services:
        visit(name, frozenset())


def test_named_networks_segment_database_temporal_and_monitoring() -> None:
    compose = load_yaml(COMPOSE)
    assert compose["networks"] == {
        "database": {"internal": True},
        "temporal": None,
        "temporal-metrics": None,
        "postgres-metrics": None,
        "prometheus-grafana": None,
    }
    assert all("name" not in (configuration or {})
               for configuration in compose["networks"].values())
    for name, service in compose["services"].items():
        assert set(service.get("networks", [])) == NETWORKS[name]
    assert NETWORKS["grafana"].isdisjoint(
        NETWORKS["temporal"] | NETWORKS["postgres-exporter"]
    )
    assert "temporal-metrics" not in NETWORKS["temporal-ui"]


def test_secrets_are_file_backed_not_compose_values() -> None:
    compose = load_yaml(COMPOSE)
    services = compose["services"]
    assert compose["secrets"] == {
        "temporal_postgres_password": {
            "file": "${TEMPORAL_POSTGRES_PASSWORD_FILE:?required}"
        },
        "temporal_postgres_exporter_password": {
            "file": "${TEMPORAL_POSTGRES_EXPORTER_PASSWORD_FILE:?required}"
        },
        "grafana_admin_password": {
            "file": "${GRAFANA_ADMIN_PASSWORD_FILE:?required}"
        },
    }
    assert env_map(services["postgres"])["POSTGRES_PASSWORD_FILE"] == (
        "/run/secrets/temporal_postgres_password"
    )
    assert env_map(services["grafana"])["GF_SECURITY_ADMIN_PASSWORD__FILE"] == (
        "/run/secrets/grafana_admin_password"
    )
    for name in {"postgres", "grafana", "temporal", "temporal-schema",
                 "temporal-exporter-role-sync"}:
        environment = env_map(services[name])
        assert not {"POSTGRES_PASSWORD", "POSTGRES_PWD", "SQL_PASSWORD", "PGPASSWORD"} & set(environment)
        assert "GF_SECURITY_ADMIN_PASSWORD" not in environment
    text = COMPOSE.read_text(encoding="utf-8")
    assert "POSTGRES_PASSWORD=${" not in text
    assert "POSTGRES_PWD=${" not in text
    assert "GF_SECURITY_ADMIN_PASSWORD=${" not in text
    assert "${TEMPORAL_POSTGRES_PASSWORD:" not in text
    assert "${GRAFANA_ADMIN_PASSWORD:" not in text


def test_exporter_database_is_dynamic_and_role_sync_precedes_exporter() -> None:
    services = load_yaml(COMPOSE)["services"]
    exporter = services["postgres-exporter"]
    assert env_map(exporter) == {
        "DATA_SOURCE_URI": "postgres:5432/${TEMPORAL_POSTGRES_DB:-temporal}?sslmode=disable",
        "DATA_SOURCE_USER": "temporal_exporter",
        "DATA_SOURCE_PASS_FILE": "/run/secrets/temporal_postgres_exporter_password",
    }
    assert deps(exporter) == {
        "temporal-exporter-role-sync": {
            "condition": "service_completed_successfully",
            "restart": True,
        }
    }
    role_environment = env_map(services["temporal-exporter-role-sync"])
    assert role_environment["PGDATABASE"] == "${TEMPORAL_POSTGRES_DB:-temporal}"
    assert role_environment["VISIBILITY_DBNAME"] == (
        "${TEMPORAL_POSTGRES_VISIBILITY_DB:-temporal_visibility}"
    )


@pytest.mark.parametrize(
    ("scenario", "expected_returncode", "expected_error", "expected_calls"),
    [
        ("success", 0, "", ["version", "role-sync", "recreate"]),
        ("role-sync-fails", 1, "ERROR E_ROLE_SYNC\n", ["version", "role-sync"]),
        ("recreate-fails", 1, "ERROR E_EXPORTER_RECREATE\n", ["version", "role-sync", "recreate"]),
        ("old-compose", 1, "ERROR E_COMPOSE_VERSION\n", ["version"]),
    ],
)
def test_exporter_password_rotation_runs_supported_sequence_and_stops_on_failure(
    tmp_path: Path,
    scenario: str,
    expected_returncode: int,
    expected_error: str,
    expected_calls: list[str],
) -> None:
    secret = tmp_path / "exporter-password"
    secret.write_bytes(b"rotation-test-password")
    log = tmp_path / "docker.log"
    docker_stub = """#!/bin/sh
set -eu
case "$*" in
  "compose version --short")
    printf '%s\\n' version >> "$STUB_LOG"
    if [ "$STUB_SCENARIO" = "old-compose" ]; then
      printf '%s\\n' '2.16.9'
    else
      printf '%s\\n' '2.17.0'
    fi
    ;;
  *" run --rm temporal-exporter-role-sync")
    printf '%s\\n' role-sync >> "$STUB_LOG"
    [ "$STUB_SCENARIO" != "role-sync-fails" ] || exit 7
    ;;
  *" up -d --no-deps --force-recreate postgres-exporter")
    printf '%s\\n' recreate >> "$STUB_LOG"
    [ "$STUB_SCENARIO" != "recreate-fails" ] || exit 8
    ;;
  *) exit 93 ;;
esac
"""
    result = run_shell_script(
        ROTATE_SCRIPT,
        tmp_path,
        environment={
            "TEMPORAL_POSTGRES_EXPORTER_PASSWORD_FILE": shell_path(
                shell_executable(), secret
            ),
            "STUB_LOG": shell_path(shell_executable(), log),
            "STUB_SCENARIO": scenario,
        },
        stubs={"docker": docker_stub},
    )
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    assert result.returncode == expected_returncode
    assert result.stderr == expected_error
    assert calls == expected_calls
    assert "rotation-test-password" not in result.stdout + result.stderr


def test_exporter_rotation_rejects_unsupported_compose_and_unsafe_secret_file(
    tmp_path: Path,
) -> None:
    script = ROTATE_SCRIPT.read_text(encoding="utf-8")
    assert "2.17" in script
    assert '-L "$secret_file"' in script
    assert "maximum_secret_bytes=1024" in script
    assert "wc -l" in script
    assert "set -x" not in script


def test_exporter_rotation_rejects_host_secret_symlink_before_docker(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.write_bytes(b"rotation-test-password")
    link = tmp_path / "secret-link"
    try:
        link.symlink_to(target)
    except OSError as error:
        pytest.skip(f"host symlink creation unavailable: {error.__class__.__name__}")
    log = tmp_path / "docker.log"
    result = run_shell_script(
        ROTATE_SCRIPT,
        tmp_path,
        environment={
            "TEMPORAL_POSTGRES_EXPORTER_PASSWORD_FILE": shell_path(
                shell_executable(), link
            ),
            "STUB_LOG": shell_path(shell_executable(), log),
        },
        stubs={
            "docker": "#!/bin/sh\nprintf '%s\\n' called >> \"$STUB_LOG\"\nexit 0\n"
        },
    )
    assert result.returncode == 1
    assert result.stderr == "ERROR E_SECRET_FILE\n"
    assert not log.exists()


def test_schema_job_is_idempotent_and_uses_official_schema_tools() -> None:
    script = SCHEMA_SCRIPT.read_text(encoding="utf-8")
    assert script.startswith("#!/bin/sh\n") and "set -eu" in script
    assert "/run/secrets/temporal_postgres_password" in script
    assert "export SQL_PASSWORD=" in script and "export PGPASSWORD=" in script
    assert "WHERE NOT EXISTS" in script and "format('CREATE DATABASE %I'" in script
    assert "\\gexec" in script
    assert script.count("setup-schema -v 0.0") == 2
    assert script.count("update-schema -d") == 2
    assert "/etc/temporal/schema/postgresql/v12/temporal/versioned" in script
    assert "/etc/temporal/schema/postgresql/v12/visibility/versioned" in script
    assert not re.search(r"temporal-sql-tool[^\n]*\bcreate\b", script)
    assert "--password" not in script and "--pw" not in script


@pytest.mark.parametrize("role_sync", [False, True], ids=["schema", "role-sync"])
def test_postgres_identifiers_reject_invalid_values_before_external_commands(
    tmp_path: Path,
    role_sync: bool,
) -> None:
    script = ROLE_SCRIPT if role_sync else SCHEMA_SCRIPT
    database_key = "PGDATABASE" if role_sync else "DBNAME"
    user_key = "PGUSER" if role_sync else "POSTGRES_USER"
    cases = {
        "empty-database": {database_key: ""},
        "unicode": {database_key: "témporal"},
        "slash": {database_key: "bad/name"},
        "question": {database_key: "bad?name"},
        "hash": {database_key: "bad#name"},
        "control": {database_key: "bad\nname"},
        "leading-digit": {database_key: "1temporal"},
        "too-long": {database_key: "a" * 64},
        "invalid-user": {user_key: "bad-user"},
        "same-databases": {
            database_key: "temporal",
            "VISIBILITY_DBNAME": "temporal",
        },
    }
    for name, changes in cases.items():
        case_dir = tmp_path / name
        case_dir.mkdir()
        log = case_dir / "commands.log"
        environment = identifier_test_environment(case_dir, role_sync=role_sync)
        environment.update(changes)
        environment["STUB_LOG"] = shell_path(shell_executable(), log)
        result = run_shell_script(
            script,
            case_dir,
            environment=environment,
            stubs=command_recording_stubs(),
        )
        calls = log.read_text(encoding="utf-8") if log.exists() else ""
        assert result.returncode == 1, (name, result.stderr)
        assert result.stderr == "ERROR E_IDENTIFIER\n", name
        assert calls == "", name


@pytest.mark.parametrize("role_sync", [False, True], ids=["schema", "role-sync"])
def test_postgres_identifiers_accept_ascii_63_byte_boundary(
    tmp_path: Path,
    role_sync: bool,
) -> None:
    script = ROLE_SCRIPT if role_sync else SCHEMA_SCRIPT
    database_key = "PGDATABASE" if role_sync else "DBNAME"
    log = tmp_path / "commands.log"
    environment = identifier_test_environment(tmp_path, role_sync=role_sync)
    environment.update({
        database_key: "a" * 63,
        "VISIBILITY_DBNAME": "_",
        "PGUSER" if role_sync else "POSTGRES_USER": "Z" + "0" * 62,
        "STUB_LOG": shell_path(shell_executable(), log),
    })
    result = run_shell_script(
        script,
        tmp_path,
        environment=environment,
        stubs=command_recording_stubs(),
    )
    calls = log.read_text(encoding="utf-8") if log.exists() else ""
    assert result.returncode == 0, result.stderr
    assert "psql " in calls
    if not role_sync:
        assert calls.count("temporal-sql-tool ") == 4


@pytest.mark.parametrize(
    ("failing_command", "expected_error"),
    [
        ("psql", "ERROR E_SCHEMA_DATABASES\n"),
        ("temporal-sql-tool", "ERROR E_SCHEMA_MIGRATION\n"),
    ],
)
def test_schema_external_failures_are_stable_and_hide_tool_output(
    tmp_path: Path,
    failing_command: str,
    expected_error: str,
) -> None:
    log = tmp_path / "commands.log"
    environment = identifier_test_environment(tmp_path, role_sync=False)
    environment["STUB_LOG"] = shell_path(shell_executable(), log)
    stubs = command_recording_stubs()
    stubs[failing_command] = f"""#!/bin/sh
printf '{failing_command} %s\\n' "$*" >> "$STUB_LOG"
printf '%s\\n' 'tool-payload /absolute/internal/path' >&2
exit 7
"""
    result = run_shell_script(
        SCHEMA_SCRIPT,
        tmp_path,
        environment=environment,
        stubs=stubs,
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == expected_error
    assert "tool-payload" not in result.stdout + result.stderr
    assert "/absolute/internal/path" not in result.stdout + result.stderr


def test_namespace_job_is_bounded_idempotent_and_typo_free() -> None:
    script = NAMESPACE_SCRIPT.read_text(encoding="utf-8")
    assert script.startswith("#!/bin/sh\n") and "set -eu" in script
    assert "MAX_ATTEMPTS" in script and "MAX_ATTdMPTS" not in script
    assert "temporal operator cluster health" in script
    assert script.count("temporal operator namespace describe") >= 1
    assert "temporal operator namespace create" in script
    assert "temporal operator namespace update" in script
    assert '--retention "$DEFAULT_NAMESPACE_RETENTION"' in script
    assert '[ "$attempt" -ge "$MAX_ATTEMPTS" ]' in script


def test_namespace_missing_configuration_fails_with_stable_error_before_cli(
    tmp_path: Path,
) -> None:
    log = tmp_path / "temporal.log"
    result = run_shell_script(
        NAMESPACE_SCRIPT,
        tmp_path,
        environment={
            "TEMPORAL_ADDRESS": "",
            "DEFAULT_NAMESPACE": "default",
            "DEFAULT_NAMESPACE_RETENTION": "72h",
            "MAX_ATTEMPTS": "3",
            "SLEEP_SECONDS": "0",
            "STUB_LOG": shell_path(shell_executable(), log),
        },
        stubs={
            "temporal": "#!/bin/sh\nprintf '%s\\n' called >> \"$STUB_LOG\"\nexit 0\n"
        },
    )
    assert result.returncode == 1
    assert result.stderr == "ERROR E_CONFIGURATION\n"
    assert not log.exists()


@pytest.mark.parametrize(
    ("scenario", "expected_returncode", "expected_create_count"),
    [
        ("existing", 0, 0),
        ("create", 0, 1),
        ("race", 0, 1),
        ("update-fails", 1, 0),
    ],
)
def test_namespace_retention_converges_for_all_creation_paths(
    tmp_path: Path,
    scenario: str,
    expected_returncode: int,
    expected_create_count: int,
) -> None:
    log = tmp_path / "temporal.log"
    state = tmp_path / "state"
    state.mkdir()
    temporal_stub = """#!/bin/sh
set -eu
printf '%s\\n' "$*" >> "$STUB_LOG"
case "$*" in
  "operator cluster health "*) exit 0 ;;
  "operator namespace describe "*)
    case "$STUB_SCENARIO" in
      existing|update-fails) exit 0 ;;
      create) [ -f "$STUB_STATE/created" ] && exit 0 || exit 1 ;;
      race) [ -f "$STUB_STATE/raced" ] && exit 0 || exit 1 ;;
    esac
    ;;
  "operator namespace create "*)
    case "$STUB_SCENARIO" in
      create) : > "$STUB_STATE/created"; exit 0 ;;
      race) : > "$STUB_STATE/raced"; exit 1 ;;
      *) exit 91 ;;
    esac
    ;;
  "operator namespace update "*)
    [ "$STUB_SCENARIO" != "update-fails" ] || exit 9
    exit 0
    ;;
esac
exit 92
"""
    result = run_shell_script(
        NAMESPACE_SCRIPT,
        tmp_path,
        environment={
            "TEMPORAL_ADDRESS": "temporal:7233",
            "DEFAULT_NAMESPACE": "default",
            "DEFAULT_NAMESPACE_RETENTION": "168h",
            "MAX_ATTEMPTS": "3",
            "SLEEP_SECONDS": "0",
            "STUB_LOG": shell_path(shell_executable(), log),
            "STUB_STATE": shell_path(shell_executable(), state),
            "STUB_SCENARIO": scenario,
        },
        stubs={"temporal": temporal_stub},
    )
    calls = log.read_text(encoding="utf-8").splitlines()
    assert result.returncode == expected_returncode, result.stderr
    assert sum("operator namespace create" in call for call in calls) == expected_create_count
    update_calls = [call for call in calls if "operator namespace update" in call]
    assert len(update_calls) == 1
    assert "--namespace default" in update_calls[0]
    assert "--retention 168h" in update_calls[0]
    if scenario == "update-fails":
        assert result.stderr == "ERROR E_NAMESPACE_RETENTION\n"


def test_role_sync_rotates_existing_role_without_exporter_password_argv() -> None:
    script = ROLE_SCRIPT.read_text(encoding="utf-8")
    assert script.startswith("#!/bin/sh\n") and "set -eu" in script
    assert "/run/secrets/temporal_postgres_password" in script
    assert "/run/secrets/temporal_postgres_exporter_password" in script
    assert "\\getenv exporter_password TEMPORAL_EXPORTER_PASSWORD" in script
    assert "WHERE NOT EXISTS" in script and "CREATE ROLE temporal_exporter NOLOGIN" in script
    assert "ALTER ROLE temporal_exporter WITH LOGIN PASSWORD" in script
    assert "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS" in script
    assert "GRANT pg_monitor TO temporal_exporter" in script
    assert "GRANT CONNECT ON DATABASE" in script
    for catalog in (
        "pg_auth_members", "pg_database", "pg_namespace", "pg_class",
        "pg_proc", "pg_type", "pg_default_acl",
    ):
        assert catalog in script
    assert "aclexplode" in script
    for privilege in (
        "CREATE", "TEMPORARY", "INSERT", "UPDATE", "DELETE", "TRUNCATE",
        "REFERENCES", "TRIGGER", "USAGE", "EXECUTE",
    ):
        assert privilege in script
    assert "fail E_EXPORTER_AUDIT" in script
    assert "--set=exporter_password" not in script
    psql_argv = script.split("psql", 1)[1].split("<<'SQL'", 1)[0]
    assert "exporter_password" not in psql_argv
    assert "TEMPORAL_EXPORTER_PASSWORD" not in psql_argv


@pytest.mark.parametrize("path", [ROLE_SCRIPT, SCHEMA_SCRIPT, SERVER_SCRIPT])
def test_secret_scripts_bound_input_and_do_not_log_password(path: Path) -> None:
    script = path.read_text(encoding="utf-8")
    assert "maximum_secret_bytes=1024" in script
    assert "wc -c" in script and "wc -l" in script and "printf '\\r'" in script
    assert "set -x" not in script
    assert not re.search(r"(?m)^\s*(?:echo|printf).*password", script)


@pytest.mark.parametrize(
    ("path", "role_sync"),
    [(SCHEMA_SCRIPT, False), (ROLE_SCRIPT, True), (SERVER_SCRIPT, False)],
)
def test_secret_script_errors_are_stable_and_do_not_disclose_paths(
    tmp_path: Path,
    path: Path,
    role_sync: bool,
) -> None:
    environment = identifier_test_environment(tmp_path, role_sync=role_sync)
    missing = tmp_path / "missing-secret"
    environment["TEMPORAL_POSTGRES_PASSWORD_FILE"] = shell_path(
        shell_executable(), missing
    )
    result = run_shell_script(path, tmp_path, environment=environment, stubs={})
    assert result.returncode == 1
    assert result.stderr == "ERROR E_SECRET_FILE\n"
    assert str(missing) not in result.stderr


def test_server_wrapper_exports_secret_then_execs_official_entrypoint() -> None:
    script = SERVER_SCRIPT.read_text(encoding="utf-8")
    temporal = load_yaml(COMPOSE)["services"]["temporal"]
    assert "/run/secrets/temporal_postgres_password" in script
    assert 'export POSTGRES_PWD="$postgres_password"' in script
    assert 'exec /etc/temporal/entrypoint.sh "$@"' in script
    assert temporal["entrypoint"] == ["/opt/medchat/temporal-server-entrypoint.sh"]


def test_env_example_has_only_empty_file_secret_keys() -> None:
    lines = ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
    pairs = [line.split("=", 1) for line in lines
             if line and not line.lstrip().startswith("#")]
    keys = [key for key, _ in pairs]
    values = dict(pairs)
    assert len(keys) == len(set(keys))
    for key in ("TEMPORAL_POSTGRES_PASSWORD_FILE",
                "TEMPORAL_POSTGRES_EXPORTER_PASSWORD_FILE",
                "GRAFANA_ADMIN_PASSWORD_FILE"):
        assert values[key] == ""
    assert "TEMPORAL_POSTGRES_PASSWORD" not in values
    assert "GRAFANA_ADMIN_PASSWORD" not in values
    assert values["TEMPORAL_POSTGRES_DB"] == "temporal"
    assert values["TEMPORAL_POSTGRES_VISIBILITY_DB"] == "temporal_visibility"
    assert values["TEMPORAL_POSTGRES_USER"] == "temporal"
    assert values["TEMPORAL_NAMESPACE"] == "default"
    assert values["TEMPORAL_NAMESPACE_RETENTION"] == "72h"
    assert values["GRAFANA_ADMIN_USER"] == "admin"
    guidance = ENV_EXAMPLE.read_text(encoding="utf-8").lower()
    assert "never commit real" in guidance
    for contract in (
        "regular file", "symlink", "trailing newline", "1024", "0600", "acl",
        "printf", "rotate-exporter-password.sh", ">= 2.17",
    ):
        assert contract in guidance


def test_no_dangerous_privileges_version_key_or_unescaped_container_vars() -> None:
    compose = load_yaml(COMPOSE)
    text = COMPOSE.read_text(encoding="utf-8")
    forbidden = {"cap_add", "devices", "network_mode", "pid", "privileged", "userns_mode"}
    assert not re.search(r"(?m)^version:", text)
    assert "$${POSTGRES_USER}" in text and "$${POSTGRES_DB}" in text
    assert not re.search(r'(?<!\$)\$\{POSTGRES_(?:USER|DB)\}', text)
    for name, service in compose["services"].items():
        assert forbidden.isdisjoint(service), name


def test_task6_documentation_matches_runtime_contracts() -> None:
    plan = PLAN.read_text(encoding="utf-8")
    task6 = plan.split("## Task 6:", 1)[1].split("## Task 7:", 1)[0]
    design = DESIGN.read_text(encoding="utf-8")
    boundary = design.split("### 5.2 Docker Compose 边界", 1)[1].split("### 5.3", 1)[0]
    assert "Temporal Server auto-setup" not in plan
    assert "temporalio/auto-setup" not in task6
    assert "temporalio/server:1.29.7" in task6
    assert "temporalio/admin-tools:1.29.7" in task6
    assert all(word in task6 for word in ("schema", "namespace", "role-sync"))
    assert "temporalio/server" in boundary and "temporalio/admin-tools" in boundary
    assert "*_FILE" in boundary
    assert "database" in boundary and "temporal-metrics" in boundary
    for contract in (
        "rotate-exporter-password.sh", ">= 2.17", "pg_up",
        "namespace update", "NOBYPASSRLS", "project",
    ):
        assert contract in task6
