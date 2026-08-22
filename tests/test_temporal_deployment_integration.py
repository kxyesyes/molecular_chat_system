from __future__ import annotations

import json
import os
import secrets
import shutil
import socket
import stat
import subprocess
import time
import uuid
from pathlib import Path
from typing import Callable

import pytest


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deployment" / "temporal"
COMPOSE = DEPLOY / "docker-compose.yml"
ROTATE_SCRIPT = DEPLOY / "scripts" / "rotate-exporter-password.sh"
OPT_IN = "MEDCHAT_RUN_TEMPORAL_COMPOSE_INTEGRATION"


def require_compose_integration() -> str:
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("docker command unavailable")
    if os.environ.get(OPT_IN) != "1":
        pytest.skip(f"set {OPT_IN}=1 to run isolated Compose integration")
    result = subprocess.run(
        [docker, "compose", "version"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        pytest.skip("docker compose unavailable")
    return docker


def unused_loopback_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def run(
    argv: list[str],
    *,
    environment: dict[str, str],
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=DEPLOY,
        env=environment,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def replace_secret_file(
    path: Path,
    value: str,
    *,
    platform_name: str | None = None,
) -> None:
    host_platform = os.name if platform_name is None else platform_name
    replacement = path.parent / f".{path.name}.new"
    if host_platform == "nt":
        for candidate in (path, replacement):
            if candidate.exists():
                candidate.chmod(stat.S_IREAD | stat.S_IWRITE)
    replacement.write_text(value, encoding="utf-8", newline="")
    if host_platform == "posix":
        replacement.chmod(0o444)
    os.replace(replacement, path)
    if host_platform == "posix":
        path.chmod(0o444)


def test_replace_secret_file_windows_keeps_target_replaceable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "secret"
    target.write_text("old", encoding="utf-8", newline="")
    simulated_modes = {target: 0o444}
    chmod_calls: list[tuple[Path, int]] = []
    real_replace = os.replace

    def record_chmod(path: Path, mode: int) -> None:
        chmod_calls.append((path, mode))
        simulated_modes[path] = mode

    def windows_replace(source: str | Path, destination: str | Path) -> None:
        source_path = Path(source)
        destination_path = Path(destination)
        if simulated_modes.get(destination_path) == 0o444:
            raise PermissionError("simulated Windows read-only target")
        real_replace(source_path, destination_path)
        simulated_modes[destination_path] = simulated_modes.pop(source_path, 0o666)

    monkeypatch.setattr(Path, "chmod", record_chmod)
    monkeypatch.setattr(os, "replace", windows_replace)

    replace_secret_file(target, "first", platform_name="nt")
    replace_secret_file(target, "second", platform_name="nt")

    assert target.read_text(encoding="utf-8") == "second"
    assert all(mode != 0o444 for _, mode in chmod_calls)
    assert simulated_modes[target] != 0o444


def test_replace_secret_file_posix_applies_read_only_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "secret"
    chmod_calls: list[tuple[Path, int]] = []

    def record_chmod(path: Path, mode: int) -> None:
        chmod_calls.append((path, mode))

    monkeypatch.setattr(Path, "chmod", record_chmod)
    replace_secret_file(target, "value", platform_name="posix")

    replacement = tmp_path / ".secret.new"
    assert (replacement, 0o444) in chmod_calls
    assert chmod_calls[-1] == (target, 0o444)


def wait_for_health(
    base: list[str],
    service: str,
    expected: str,
    *,
    environment: dict[str, str],
    timeout: int,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        container = run(
            [*base, "ps", "-q", service], environment=environment, timeout=15
        ).stdout.strip()
        if container:
            result = run(
                [base[0], "inspect", "--format", "{{.State.Health.Status}}", container],
                environment=environment,
                timeout=15,
            )
            if result.returncode == 0 and result.stdout.strip() == expected:
                return
        time.sleep(2)
    pytest.fail(f"{service} did not reach expected health state {expected}")


def health_status(
    base: list[str],
    service: str,
    *,
    environment: dict[str, str],
) -> str:
    container = run(
        [*base, "ps", "-q", service], environment=environment, timeout=15
    ).stdout.strip()
    assert container
    result = run(
        [base[0], "inspect", "--format", "{{.State.Health.Status}}", container],
        environment=environment,
        timeout=15,
    )
    assert result.returncode == 0
    return result.stdout.strip()


@pytest.fixture
def register_compose_cleanup(
    request: pytest.FixtureRequest,
) -> Callable[[list[str], dict[str, str]], None]:
    def register(base: list[str], environment: dict[str, str]) -> None:
        def cleanup() -> None:
            result = run(
                [*base, "down", "--volumes", "--remove-orphans"],
                environment=environment,
                timeout=180,
            )
            if result.returncode != 0:
                pytest.fail("isolated Compose cleanup failed", pytrace=False)

        request.addfinalizer(cleanup)

    return register


def test_exporter_role_audit_health_and_supported_rotation(
    tmp_path: Path,
    register_compose_cleanup: Callable[[list[str], dict[str, str]], None],
) -> None:
    docker = require_compose_integration()
    project = f"medchat-temporal-it-{uuid.uuid4().hex[:10]}"
    postgres_password = secrets.token_urlsafe(24)
    exporter_password = secrets.token_urlsafe(24)
    secret_directory = tmp_path / "secrets"
    secret_directory.mkdir(mode=0o700)
    secret_directory.chmod(0o700)
    postgres_secret = secret_directory / "postgres-password"
    exporter_secret = secret_directory / "exporter-password"
    grafana_secret = secret_directory / "grafana-password"

    replace_secret_file(postgres_secret, postgres_password)
    replace_secret_file(exporter_secret, exporter_password)
    replace_secret_file(grafana_secret, secrets.token_urlsafe(24))
    if os.name == "posix":
        assert stat.S_IMODE(secret_directory.stat().st_mode) == 0o700
        assert secret_directory.stat().st_uid == os.geteuid()
        for secret_file in (postgres_secret, exporter_secret, grafana_secret):
            assert stat.S_IMODE(secret_file.stat().st_mode) == 0o444
            assert secret_file.stat().st_uid == os.geteuid()

    grafana_provisioning = tmp_path / "grafana-provisioning"
    grafana_dashboards = tmp_path / "grafana-dashboards"
    grafana_provisioning.mkdir()
    grafana_dashboards.mkdir()
    compose_override = tmp_path / "compose.integration.yml"
    compose_override.write_text(
        "\n".join([
            "services:",
            "  grafana:",
            "    volumes:",
            "      - grafana-data:/var/lib/grafana",
            "      - type: bind",
            f"        source: {json.dumps(str(grafana_provisioning))}",
            "        target: /etc/grafana/provisioning",
            "        read_only: true",
            "      - type: bind",
            f"        source: {json.dumps(str(grafana_dashboards))}",
            "        target: /var/lib/grafana/dashboards",
            "        read_only: true",
            "",
        ]),
        encoding="utf-8",
        newline="\n",
    )

    environment = os.environ.copy()
    environment.update({
        "COMPOSE_PROJECT_NAME": project,
        "TEMPORAL_POSTGRES_PASSWORD_FILE": str(postgres_secret),
        "TEMPORAL_POSTGRES_EXPORTER_PASSWORD_FILE": str(exporter_secret),
        "GRAFANA_ADMIN_PASSWORD_FILE": str(grafana_secret),
        "TEMPORAL_POSTGRES_DB": "temporal_it",
        "TEMPORAL_POSTGRES_VISIBILITY_DB": "temporal_visibility_it",
        "TEMPORAL_POSTGRES_USER": "temporal_it",
        "TEMPORAL_POSTGRES_HOST_PORT": str(unused_loopback_port()),
    })
    base = [
        docker, "compose", "-p", project,
        "-f", str(COMPOSE), "-f", str(compose_override),
    ]
    register_compose_cleanup(base, environment)

    def compose(*arguments: str, timeout: int = 180) -> subprocess.CompletedProcess[str]:
        return run([*base, *arguments], environment=environment, timeout=timeout)

    def psql(database: str, sql: str) -> subprocess.CompletedProcess[str]:
        return compose(
            "exec", "-T", "postgres", "psql", "--no-psqlrc", "--quiet",
            "--set=ON_ERROR_STOP=1", "--username", "temporal_it",
            "--dbname", database, "--command", sql,
        )

    def scalar(database: str, sql: str) -> str:
        result = compose(
            "exec", "-T", "postgres", "psql", "--no-psqlrc", "--quiet",
            "--tuples-only", "--no-align", "--set=ON_ERROR_STOP=1",
            "--username", "temporal_it", "--dbname", database,
            "--command", sql,
        )
        assert result.returncode == 0
        return result.stdout.strip()

    def role_sync() -> subprocess.CompletedProcess[str]:
        return compose("run", "--rm", "temporal-exporter-role-sync")

    def exercise() -> None:
        assert compose("up", "-d", "postgres", timeout=300).returncode == 0
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            ready = compose(
                "exec", "-T", "postgres", "pg_isready", "--username", "temporal_it",
                "--dbname", "temporal_it", timeout=15,
            )
            if ready.returncode == 0:
                break
            time.sleep(2)
        else:
            pytest.fail("isolated PostgreSQL did not become ready")

        assert compose(
            "run", "--rm", "temporal-schema", timeout=300
        ).returncode == 0
        assert compose("up", "-d", "temporal", timeout=300).returncode == 0
        wait_for_health(
            base, "temporal", "healthy", environment=environment, timeout=180
        )
        assert role_sync().returncode == 0

        assert compose(
            "up", "-d", "--no-deps", "grafana", timeout=300
        ).returncode == 0
        wait_for_health(
            base, "grafana", "healthy", environment=environment, timeout=120
        )

        first_oid = scalar(
            "postgres", "SELECT oid FROM pg_roles WHERE rolname = 'temporal_exporter'"
        )
        replace_secret_file(exporter_secret, secrets.token_urlsafe(24))
        assert role_sync().returncode == 0
        second_oid = scalar(
            "postgres", "SELECT oid FROM pg_roles WHERE rolname = 'temporal_exporter'"
        )
        assert first_oid != second_oid

        assert psql("postgres", "CREATE ROLE forbidden_membership").returncode == 0
        assert psql(
            "postgres", "GRANT pg_monitor TO temporal_exporter WITH ADMIN OPTION; "
            "GRANT forbidden_membership TO temporal_exporter WITH ADMIN OPTION"
        ).returncode == 0
        assert role_sync().returncode == 0
        assert scalar(
            "postgres",
            "SELECT count(*) FROM pg_auth_members membership "
            "JOIN pg_roles granted_role ON granted_role.oid = membership.roleid "
            "WHERE membership.member = ("
            "SELECT oid FROM pg_roles WHERE rolname = 'temporal_exporter') "
            "AND granted_role.rolname <> 'pg_monitor'",
        ) == "0"
        assert scalar(
            "postgres",
            "SELECT concat_ws(',', admin_option, inherit_option, set_option) "
            "FROM pg_auth_members membership "
            "JOIN pg_roles granted_role ON granted_role.oid = membership.roleid "
            "WHERE membership.member = ("
            "SELECT oid FROM pg_roles WHERE rolname = 'temporal_exporter') "
            "AND granted_role.rolname = 'pg_monitor'",
        ) == "f,t,f"
        assert psql(
            "postgres", "DROP ROLE forbidden_membership"
        ).returncode == 0

        assert psql(
            "temporal_it",
            "CREATE TABLE exporter_column_acl(id integer, payload integer); "
            "GRANT UPDATE (payload) ON exporter_column_acl TO temporal_exporter",
        ).returncode == 0
        column_acl_sync = role_sync()
        assert column_acl_sync.returncode != 0
        assert "ERROR E_EXPORTER_RESET" in column_acl_sync.stderr
        assert psql(
            "temporal_it",
            "REVOKE UPDATE (payload) ON exporter_column_acl FROM temporal_exporter; "
            "DROP TABLE exporter_column_acl",
        ).returncode == 0

        assert psql(
            "temporal_visibility_it",
            "CREATE COLLATION exporter_owned_collation (provider = libc, locale = 'C'); "
            "ALTER COLLATION exporter_owned_collation OWNER TO temporal_exporter",
        ).returncode == 0
        omitted_object_sync = role_sync()
        assert omitted_object_sync.returncode != 0
        assert "ERROR E_EXPORTER_RESET" in omitted_object_sync.stderr
        assert psql(
            "temporal_visibility_it",
            "ALTER COLLATION exporter_owned_collation OWNER TO temporal_it; "
            "DROP COLLATION exporter_owned_collation",
        ).returncode == 0
        assert role_sync().returncode == 0

        assert compose("up", "-d", "postgres-exporter", timeout=300).returncode == 0
        wait_for_health(
            base, "postgres-exporter", "healthy", environment=environment, timeout=120
        )

        replace_secret_file(exporter_secret, secrets.token_urlsafe(24))
        assert compose(
            "up", "-d", "--no-deps", "--force-recreate", "postgres-exporter",
            timeout=180,
        ).returncode == 0
        wait_for_health(
            base, "postgres-exporter", "unhealthy", environment=environment, timeout=120
        )

        mismatch_secret = secret_directory / "exporter-password-mismatch"
        replace_secret_file(mismatch_secret, secrets.token_urlsafe(24))
        replace_secret_file(exporter_secret, secrets.token_urlsafe(24))
        wrapper_directory = tmp_path / "docker-wrapper"
        wrapper_directory.mkdir()
        docker_wrapper = wrapper_directory / "docker"
        docker_wrapper.write_text(
            """#!/bin/sh
set -eu
if printf '%s\\n' "$*" | grep -q ' run --rm temporal-exporter-role-sync$'; then
    "$REAL_DOCKER" "$@"
    rm -f "$ROTATION_SECRET_FILE"
    cp "$ROTATION_MISMATCH_FILE" "$ROTATION_SECRET_FILE"
    chmod 0444 "$ROTATION_SECRET_FILE"
    exit 0
fi
if printf '%s\\n' "$*" | grep -q \
    ' up -d --wait --wait-timeout 120 --no-deps --force-recreate postgres-exporter$'; then
    exec "$REAL_DOCKER" compose -f "$ROTATION_COMPOSE_FILE" up -d --wait \
        --wait-timeout 1 --no-deps --force-recreate postgres-exporter
fi
exec "$REAL_DOCKER" "$@"
""",
            encoding="utf-8",
            newline="\n",
        )
        docker_wrapper.chmod(0o755)
        timeout_environment = environment.copy()
        timeout_environment.update({
            "PATH": f"{wrapper_directory}{os.pathsep}{environment.get('PATH', '')}",
            "REAL_DOCKER": docker,
            "ROTATION_COMPOSE_FILE": str(COMPOSE),
            "ROTATION_SECRET_FILE": str(exporter_secret),
            "ROTATION_MISMATCH_FILE": str(mismatch_secret),
        })
        timed_out_rotation = run(
            [shutil.which("sh") or "/bin/sh", str(ROTATE_SCRIPT)],
            environment=timeout_environment,
            timeout=60,
        )
        assert timed_out_rotation.returncode != 0
        assert timed_out_rotation.stdout == ""
        assert timed_out_rotation.stderr == "ERROR E_EXPORTER_HEALTH\n"

        replace_secret_file(exporter_secret, secrets.token_urlsafe(24))
        rotation = run(
            [shutil.which("sh") or "/bin/sh", str(ROTATE_SCRIPT)],
            environment=environment,
            timeout=300,
        )
        assert rotation.returncode == 0
        assert rotation.stdout == "OK E_EXPORTER_ROTATED\n"
        assert health_status(
            base, "postgres-exporter", environment=environment
        ) == "healthy"

    exercise()
