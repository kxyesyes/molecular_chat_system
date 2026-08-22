from __future__ import annotations

import os
import secrets
import shutil
import socket
import subprocess
import time
import uuid
from pathlib import Path

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


def test_exporter_role_audit_health_and_supported_rotation(tmp_path: Path) -> None:
    docker = require_compose_integration()
    project = f"medchat-temporal-it-{uuid.uuid4().hex[:10]}"
    postgres_password = secrets.token_urlsafe(24)
    exporter_password = secrets.token_urlsafe(24)
    postgres_secret = tmp_path / "postgres-password"
    exporter_secret = tmp_path / "exporter-password"
    grafana_secret = tmp_path / "grafana-password"
    postgres_secret.write_text(postgres_password, encoding="utf-8", newline="")
    exporter_secret.write_text(exporter_password, encoding="utf-8", newline="")
    grafana_secret.write_text(secrets.token_urlsafe(24), encoding="utf-8", newline="")
    for secret_file in (postgres_secret, exporter_secret, grafana_secret):
        secret_file.chmod(0o600)

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
    base = [docker, "compose", "-p", project, "-f", str(COMPOSE)]

    def compose(*arguments: str, timeout: int = 180) -> subprocess.CompletedProcess[str]:
        return run([*base, *arguments], environment=environment, timeout=timeout)

    def psql(database: str, sql: str) -> subprocess.CompletedProcess[str]:
        return compose(
            "exec", "-T", "postgres", "psql", "--no-psqlrc", "--quiet",
            "--set=ON_ERROR_STOP=1", "--username", "temporal_it",
            "--dbname", database, "--command", sql,
        )

    def role_sync() -> subprocess.CompletedProcess[str]:
        return compose("run", "--rm", "temporal-exporter-role-sync")

    try:
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

        assert psql(
            "postgres", "CREATE DATABASE temporal_visibility_it"
        ).returncode == 0
        assert role_sync().returncode == 0

        assert psql("postgres", "CREATE ROLE forbidden_membership").returncode == 0
        assert psql(
            "postgres", "GRANT forbidden_membership TO temporal_exporter"
        ).returncode == 0
        assert role_sync().returncode != 0
        assert psql(
            "postgres", "REVOKE forbidden_membership FROM temporal_exporter; "
            "DROP ROLE forbidden_membership"
        ).returncode == 0

        assert psql(
            "temporal_visibility_it",
            "CREATE TABLE exporter_owned(id integer); "
            "ALTER TABLE exporter_owned OWNER TO temporal_exporter",
        ).returncode == 0
        assert role_sync().returncode != 0
        assert psql(
            "temporal_visibility_it",
            "ALTER TABLE exporter_owned OWNER TO temporal_it; DROP TABLE exporter_owned",
        ).returncode == 0

        assert psql(
            "temporal_it",
            "CREATE TABLE exporter_write_grant(id integer); "
            "GRANT INSERT ON exporter_write_grant TO temporal_exporter",
        ).returncode == 0
        assert role_sync().returncode != 0
        assert psql(
            "temporal_it",
            "REVOKE INSERT ON exporter_write_grant FROM temporal_exporter; "
            "DROP TABLE exporter_write_grant",
        ).returncode == 0
        assert role_sync().returncode == 0

        assert compose("up", "-d", "postgres-exporter", timeout=300).returncode == 0
        wait_for_health(
            base, "postgres-exporter", "healthy", environment=environment, timeout=120
        )

        exporter_secret.write_text(
            secrets.token_urlsafe(24), encoding="utf-8", newline=""
        )
        assert compose(
            "up", "-d", "--no-deps", "--force-recreate", "postgres-exporter",
            timeout=180,
        ).returncode == 0
        wait_for_health(
            base, "postgres-exporter", "unhealthy", environment=environment, timeout=120
        )

        exporter_secret.write_text(
            secrets.token_urlsafe(24), encoding="utf-8", newline=""
        )
        rotation = run(
            [shutil.which("sh") or "/bin/sh", str(ROTATE_SCRIPT)],
            environment=environment,
            timeout=300,
        )
        assert rotation.returncode == 0
        wait_for_health(
            base, "postgres-exporter", "healthy", environment=environment, timeout=120
        )
    finally:
        run(
            [*base, "down", "--volumes", "--remove-orphans"],
            environment=environment,
            timeout=180,
        )
