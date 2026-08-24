#!/usr/bin/env python3
"""Aggregate truthful production preflight evidence for Temporal docking."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from typing import Any, Callable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import observe_temporal_canary as observer
from scripts.validate_temporal_deployment import (
    validate_deployment,
    write_report_atomic,
)
from src.task_runtime.config import TaskRuntimeConfig


MAX_ACCEPTANCE_REPORT_BYTES = 4 * 1024 * 1024
_ACCEPTANCE_MODES = {"contract", "real"}


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


def _generated_at(value: datetime | None) -> str:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("invalid preflight clock")
    return current.astimezone(timezone.utc).replace(microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _state(status: str, code: str) -> dict[str, str]:
    return {"status": status, "code": code}


def _status(value: object) -> object:
    return value.get("status") if type(value) is dict else None


def _acceptance_gate(value: object, *, real: bool) -> dict[str, str]:
    if type(value) is not dict:
        return _state("skipped", "real_science_missing" if real else "contract_missing")
    status = value.get("status")
    if status in {"skipped", "partial", None}:
        return _state("skipped", "real_science_missing" if real else "contract_missing")
    expected_mode = "real" if real else "contract"
    expected_type = "real_temporal_vina" if real else "contract_replay"
    provenance = value.get("provenance_gate")
    expected_hash = value.get("sha256")
    hash_valid = (
        type(expected_hash) is str
        and len(expected_hash) == 64
        and _canonical_sha256(value) == expected_hash
    )
    valid = (
        status == "passed"
        and hash_valid
        and value.get("mode") == expected_mode
        and value.get("run_count") == 3
        and value.get("scientific_execution") is real
        and type(provenance) is dict
        and provenance.get("status") == "passed"
        and provenance.get("evidence_type") == expected_type
    )
    if valid:
        return _state(
            "passed",
            "real_science_valid" if real else "contract_valid",
        )
    return _state(
        "failed",
        "real_science_invalid" if real else "contract_invalid",
    )


def build_preflight_report(
    *,
    deployment: object,
    contract: object,
    real: object,
    infrastructure: object,
    blocking_alerts: object,
    backup: object,
    current_level: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    deployment_status = _status(deployment)
    if deployment_status == "passed":
        deployment_gate = _state("passed", "deployment_valid")
    elif deployment_status == "failed":
        deployment_gate = _state("failed", "deployment_invalid")
    else:
        deployment_gate = _state("skipped", "deployment_incomplete")

    if type(infrastructure) is dict and all(
        infrastructure.get(key) is True
        for key in ("temporal", "namespace", "queue", "worker")
    ):
        infrastructure_gate = _state("passed", "infrastructure_ready")
    elif type(infrastructure) is dict and any(
        infrastructure.get(key) is False
        for key in ("temporal", "namespace", "queue", "worker")
    ):
        infrastructure_gate = _state("failed", "infrastructure_unready")
    else:
        infrastructure_gate = _state("skipped", "infrastructure_unknown")

    if blocking_alerts == []:
        alerts_gate = _state("passed", "blocking_alerts_clear")
    elif type(blocking_alerts) is list:
        alerts_gate = _state("failed", "blocking_alerts_firing")
    else:
        alerts_gate = _state("skipped", "blocking_alerts_unknown")

    if (
        type(backup) is dict
        and backup.get("status") == "passed"
        and backup.get("restore_verified") is True
    ):
        backup_gate = _state("passed", "backup_restore_verified")
    elif type(backup) is dict and backup.get("status") == "failed":
        backup_gate = _state("failed", "backup_restore_invalid")
    else:
        backup_gate = _state("skipped", "backup_restore_missing")

    checks = {
        "deployment": deployment_gate,
        "contract": _acceptance_gate(contract, real=False),
        "real_science": _acceptance_gate(real, real=True),
        "infrastructure": infrastructure_gate,
        "blocking_alerts": alerts_gate,
        "backup_restore": backup_gate,
    }
    statuses = {gate["status"] for gate in checks.values()}
    status = "failed" if "failed" in statuses else "partial" if "skipped" in statuses else "passed"
    report: dict[str, Any] = {
        "schema_version": 1,
        "stage": "preflight",
        "status": status,
        "current_level": current_level if current_level == 0 else 0,
        "generated_at": _generated_at(now),
        "checks": checks,
        "all_required_gates": status == "passed",
    }
    report["sha256"] = _canonical_sha256(report)
    return report


def _subprocess_runner(
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


def _read_acceptance(path: Path) -> dict[str, Any] | None:
    try:
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_ACCEPTANCE_REPORT_BYTES:
            return None
        content = path.read_bytes()
        if len(content) != metadata.st_size:
            return None
        payload = json.loads(content)
        if type(payload) is not dict:
            return None
        digest = payload.get("sha256")
        if (
            type(digest) is not str
            or len(digest) != 64
            or _canonical_sha256(payload) != digest
        ):
            return None
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return None
    return payload


def _project_acceptance(payload: object, mode: str) -> dict[str, Any]:
    source = payload if type(payload) is dict else {}
    provenance = source.get("provenance_gate")
    projected_provenance = {
        "status": provenance.get("status"),
        "evidence_type": provenance.get("evidence_type"),
    } if type(provenance) is dict else {"status": "failed", "evidence_type": "invalid"}
    projected = {
        "status": source.get("status") if source.get("status") in {"passed", "failed"} else "failed",
        "mode": source.get("mode") if source.get("mode") in _ACCEPTANCE_MODES else mode,
        "run_count": source.get("run_count") if type(source.get("run_count")) is int else 0,
        "scientific_execution": source.get("scientific_execution") is True,
        "provenance_gate": projected_provenance,
    }
    projected["sha256"] = _canonical_sha256(projected)
    return projected


def run_acceptance_evidence(
    *,
    mode: str,
    repo_root: Path,
    output_path: Path,
    command_runner: Callable[..., object] = _subprocess_runner,
    base_environment: dict[str, str] | None = None,
) -> dict[str, Any]:
    if mode not in _ACCEPTANCE_MODES:
        raise ValueError("invalid acceptance mode")
    environment = dict(base_environment if base_environment is not None else os.environ)
    if mode == "real":
        environment["MEDCHAT_TASK_BACKEND"] = "temporal_canary"
        environment["MEDCHAT_TEMPORAL_CANARY_PERCENT"] = "100"
    argv = [
        sys.executable,
        os.fspath(Path(repo_root) / "scripts/run_temporal_docking_acceptance.py"),
        "--mode",
        mode,
        "--repeat",
        "3",
        "--output",
        os.fspath(output_path),
    ]
    try:
        result = command_runner(
            argv,
            cwd=Path(repo_root),
            environment=environment,
            timeout=7260.0 if mode == "real" else 300.0,
        )
        payload = _read_acceptance(output_path)
    except Exception:
        payload = None
        result = None
    projected = _project_acceptance(payload, mode)
    if getattr(result, "returncode", 1) != 0:
        projected["status"] = "failed"
        projected["provenance_gate"]["status"] = "failed"
    return projected


def _runtime_evidence(
    *,
    prometheus_url: str,
    now: datetime,
) -> tuple[dict[str, bool | None], list[str] | None, dict[str, object]]:
    try:
        config = TaskRuntimeConfig.from_env()
        fetcher = lambda endpoint, query=None: observer._request_prometheus_json(
            prometheus_url,
            endpoint,
            query,
        )
        worker, infrastructure, alerts = observer._prometheus_state(fetcher)
        projected_infrastructure = dict(infrastructure)
        projected_infrastructure["worker"] = worker.get("available")
        backup_verified = observer.read_backup_verification(
            config.backup_state_path,
            now=now,
        )
    except Exception:
        return (
            {"temporal": None, "namespace": None, "queue": None, "worker": None},
            None,
            {"status": "skipped", "restore_verified": False},
        )
    backup = {
        "status": "passed" if backup_verified is True else "failed" if backup_verified is False else "skipped",
        "restore_verified": backup_verified is True,
    }
    return projected_infrastructure, alerts, backup


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Temporal production preflight")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prometheus-url", default="http://127.0.0.1:9090")
    arguments = parser.parse_args(argv)
    now = datetime.now(timezone.utc)
    try:
        deployment = validate_deployment(repo_root=PROJECT_ROOT, now=now)
        with tempfile.TemporaryDirectory(prefix="medchat-temporal-preflight-") as raw:
            temporary = Path(raw)
            contract = run_acceptance_evidence(
                mode="contract",
                repo_root=PROJECT_ROOT,
                output_path=temporary / "contract.json",
            )
            real = run_acceptance_evidence(
                mode="real",
                repo_root=PROJECT_ROOT,
                output_path=temporary / "real.json",
            )
        infrastructure, alerts, backup = _runtime_evidence(
            prometheus_url=arguments.prometheus_url,
            now=now,
        )
        report = build_preflight_report(
            deployment=deployment,
            contract=contract,
            real=real,
            infrastructure=infrastructure,
            blocking_alerts=alerts,
            backup=backup,
            current_level=0,
            now=now,
        )
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        write_report_atomic(arguments.output, report)
    except Exception:
        print("temporal_production_preflight=failed code=preflight_failed", file=sys.stderr)
        return 1
    print(f"status={report['status']} sha256={report['sha256']}")
    return 0 if report["status"] == "passed" else 2 if report["status"] == "partial" else 1


if __name__ == "__main__":
    raise SystemExit(main())
