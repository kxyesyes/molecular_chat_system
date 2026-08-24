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
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

MAX_ASSET_BYTES = 4 * 1024 * 1024
COMMAND_TIMEOUT_SECONDS = 60.0
_SECRET_FILE_KEYS = (
    "TEMPORAL_POSTGRES_PASSWORD_FILE",
    "TEMPORAL_POSTGRES_EXPORTER_PASSWORD_FILE",
    "GRAFANA_ADMIN_PASSWORD_FILE",
)


class DeploymentCommandRunner:
    def available(self, tool: str) -> bool:
        return shutil.which(tool) is not None

    def run(
        self,
        argv: list[str],
        *,
        cwd: Path,
        environment: dict[str, str],
        timeout: float,
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            argv,
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            timeout=timeout,
            check=False,
        )


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
    destination = Path(os.path.abspath(os.fspath(path)))
    parent = destination.parent
    parent_before = parent.lstat()
    if not stat.S_ISDIR(parent_before.st_mode) or _is_reparse_point(parent_before):
        raise ValueError("unsafe report output")
    try:
        before = destination.lstat()
    except FileNotFoundError:
        before = None
    if before is not None and (
        not stat.S_ISREG(before.st_mode) or _is_reparse_point(before)
    ):
        raise ValueError("unsafe report output")
    try:
        content = json.dumps(
            report,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8") + b"\n"
    except (TypeError, ValueError):
        raise ValueError("invalid report output") from None
    descriptor = -1
    temporary: Path | None = None
    try:
        descriptor, raw_temporary = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=parent,
        )
        temporary = Path(raw_temporary)
        os.chmod(temporary, 0o600)
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short write")
            view = view[written:]
        os.fsync(descriptor)
        temporary_identity = _file_identity(os.fstat(descriptor))
        if os.name != "posix":
            os.close(descriptor)
            descriptor = -1
        if _directory_identity(parent.lstat()) != _directory_identity(parent_before):
            raise ValueError("unsafe report output")
        try:
            current = destination.lstat()
        except FileNotFoundError:
            current = None
        if (before is None) != (current is None) or (
            before is not None
            and current is not None
            and _file_identity(before) != _file_identity(current)
        ):
            raise ValueError("unsafe report output")
        os.replace(temporary, destination)
        temporary = None
        final = destination.lstat()
        if descriptor >= 0:
            temporary_identity = _file_identity(os.fstat(descriptor))
        if (
            not stat.S_ISREG(final.st_mode)
            or _is_reparse_point(final)
            or _file_identity(final) != temporary_identity
        ):
            raise ValueError("unsafe report output")
        if os.name == "posix":
            directory_fd = os.open(
                parent,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
            )
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    except (OSError, RuntimeError, ValueError):
        raise ValueError("atomic report output failed") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def _generated_at(value: datetime | None) -> str:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("invalid deployment validation clock")
    return current.astimezone(timezone.utc).replace(microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _read_regular_asset(path: Path) -> str:
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_ASSET_BYTES:
        raise ValueError("deployment asset invalid")
    content = path.read_bytes()
    if len(content) != metadata.st_size or b"\x00" in content:
        raise ValueError("deployment asset invalid")
    return content.decode("utf-8", errors="strict")


def _python_static(repo_root: Path) -> dict[str, str]:
    required = (
        "deployment/temporal/docker-compose.yml",
        "deployment/temporal/env.example",
        "deployment/temporal/prometheus/prometheus.yml",
        "deployment/temporal/prometheus/rules/medchat-temporal.yml",
        "deployment/temporal/grafana/dashboards/medchat-temporal-docking.json",
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
        env_text = contents["deployment/temporal/env.example"]
        for key in _SECRET_FILE_KEYS:
            if f"{key}=" not in env_text:
                raise ValueError("deployment asset invalid")
        compose = contents["deployment/temporal/docker-compose.yml"]
        if "latest" in compose or "127.0.0.1:" not in compose:
            raise ValueError("deployment asset invalid")
        dashboard = json.loads(
            contents[
                "deployment/temporal/grafana/dashboards/medchat-temporal-docking.json"
            ]
        )
        if type(dashboard) is not dict:
            raise ValueError("deployment asset invalid")
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return {"status": "failed", "code": "python_static_invalid"}
    return {"status": "passed", "code": "python_static_valid"}


def _public_compose_environment(env_text: str) -> dict[str, str]:
    environment = {
        key: value
        for key in ("PATH", "SYSTEMROOT", "WINDIR", "HOME", "USERPROFILE")
        if (value := os.environ.get(key)) is not None
    }
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
    root = Path(repo_root)
    runner = command_runner or DeploymentCommandRunner()
    host = platform_name or platform.system()
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
                    "deployment/medchat-temporal-worker-prepare.service",
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
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        write_report_atomic(arguments.output, report)
    except Exception:
        print("temporal_deployment_validation=failed code=validation_failed", file=sys.stderr)
        return 1
    print(f"status={report['status']} sha256={report['sha256']}")
    return 0 if report["status"] == "passed" else 2 if report["status"] == "partial" else 1


if __name__ == "__main__":
    raise SystemExit(main())
