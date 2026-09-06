#!/usr/bin/env python3
"""Aggregate truthful production preflight evidence for Temporal docking."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
from pathlib import PurePosixPath
import re
import signal
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
from src.task_runtime.secure_io import read_file_snapshot
from src.task_runtime.trusted_process import open_trusted_executable


MAX_ACCEPTANCE_REPORT_BYTES = 4 * 1024 * 1024
_ACCEPTANCE_MODES = {"contract", "real"}
ACCEPTANCE_REPEAT = 3
REAL_RUN_TIMEOUT_SECONDS = 2400.0
RUN_CANCELLATION_BUDGET_SECONDS = 40.0
ACCEPTANCE_CLOSE_BUDGET_SECONDS = 10.0
ACCEPTANCE_MARGIN_SECONDS = 30.0
_ACCEPTANCE_PROJECTION_KEYS = {
    "status", "mode", "run_count", "scientific_execution", "provenance_gate",
    "source_report_sha256", "source_schema_valid", "sha256",
}
_SOURCE_COMMON_KEYS = {
    "status", "run_count", "passed_count", "pass_rate",
    "duplicate_vina_count", "terminal_event_violation_count", "latency_ms",
    "failure_type_distribution", "runs", "mode", "scientific_execution",
    "provenance_gate", "sha256",
}
_RUN_KEYS = {
    "run", "status", "backend", "workflow_id", "vina_attempts",
    "terminal_event_count", "pose_exists", "pose_count", "binding_energy",
    "artifact_path", "artifact_sha256", "provenance_complete", "provenance",
    "demo_mode", "fallback_used", "artifact_integrity_valid",
    "terminal_event_valid", "latency_ms", "warnings", "failures",
}
_CHILD_ENVIRONMENT_KEYS = frozenset(
    {
        "MEDCHAT_TEMPORAL_ADDRESS", "MEDCHAT_TEMPORAL_NAMESPACE",
        "MEDCHAT_TEMPORAL_DOCKING_QUEUE", "MEDCHAT_TEMPORAL_DOCKING_CONCURRENCY",
        "MEDCHAT_TASK_STAGING_ROOT", "MEDCHAT_TASK_DB_PATH",
        "MEDCHAT_TEMPORAL_WORKER_METRICS_ADDRESS",
        "MEDCHAT_TEMPORAL_WORKER_METRICS_PORT",
        "MEDCHAT_TEMPORAL_BASELINE_P95_SECONDS", "MEDCHAT_TEMPORAL_BACKUP_STATE",
        "MOLECULAR_DOCKING_ROOT", "MOLECULAR_DOCKING_VINA",
        "MOLECULAR_DOCKING_ADFR_BIN", "MOLECULAR_DOCKING_PREPARE_RECEPTOR",
        "MOLECULAR_DOCKING_PREPARE_LIGAND",
        "MOLECULAR_DOCKING_VINA_TIMEOUT_SECONDS", "SYSTEMROOT", "WINDIR",
        "TMP", "TEMP", "TMPDIR",
    }
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
        set(value) == _ACCEPTANCE_PROJECTION_KEYS
        and status == "passed"
        and hash_valid
        and value.get("mode") == expected_mode
        and value.get("run_count") == 3
        and value.get("scientific_execution") is real
        and type(provenance) is dict
        and provenance.get("status") == "passed"
        and provenance.get("evidence_type") == expected_type
        and value.get("source_schema_valid") is True
        and type(value.get("source_report_sha256")) is str
        and re.fullmatch(r"[0-9a-f]{64}", value["source_report_sha256"]) is not None
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
    pass_fds: tuple[int, ...] = (),
    cleanup_grace: float = 0.0,
) -> subprocess.CompletedProcess[bytes]:
    soft_timeout = timeout - cleanup_grace
    if soft_timeout <= 0 or cleanup_grace < 0:
        raise ValueError("invalid acceptance timeout")
    popen_kwargs: dict[str, object] = {}
    if os.name == "posix":
        popen_kwargs["pass_fds"] = pass_fds
        popen_kwargs["start_new_session"] = True
    elif hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    process = subprocess.Popen(
        argv,
        cwd=cwd,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        shell=False,
        **popen_kwargs,
    )
    try:
        process.communicate(timeout=soft_timeout)
    except subprocess.TimeoutExpired:
        try:
            if os.name == "nt" and hasattr(signal, "CTRL_BREAK_EVENT"):
                process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                process.send_signal(signal.SIGTERM)
        except (OSError, ValueError):
            process.terminate()
        try:
            process.communicate(timeout=cleanup_grace)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=ACCEPTANCE_CLOSE_BUDGET_SECONDS)
    return subprocess.CompletedProcess(argv, process.returncode)


def _acceptance_timeout_budget(*, real: bool) -> tuple[float, float]:
    if not real:
        return 300.0, ACCEPTANCE_CLOSE_BUDGET_SECONDS
    cleanup = (
        ACCEPTANCE_REPEAT * RUN_CANCELLATION_BUDGET_SECONDS
        + ACCEPTANCE_CLOSE_BUDGET_SECONDS
    )
    total = (
        ACCEPTANCE_REPEAT
        * (REAL_RUN_TIMEOUT_SECONDS + RUN_CANCELLATION_BUDGET_SECONDS)
        + ACCEPTANCE_CLOSE_BUDGET_SECONDS
        + ACCEPTANCE_MARGIN_SECONDS
    )
    return total, cleanup


def _read_acceptance(path: Path) -> dict[str, Any] | None:
    try:
        snapshot = read_file_snapshot(
            Path(os.path.abspath(os.fspath(path))), MAX_ACCEPTANCE_REPORT_BYTES
        )
        if b"\x00" in snapshot.content:
            return None
        payload = json.loads(snapshot.content.decode("utf-8", errors="strict"))
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


def _finite_number(value: object) -> bool:
    return type(value) in {int, float} and math.isfinite(float(value))


def _safe_artifact_path(value: object) -> bool:
    if type(value) is not str or not value or "\\" in value or ":" in value:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and all(
        part not in {"", ".", ".."} for part in path.parts
    )


def _validate_source_acceptance(
    payload: object,
    mode: str,
    *,
    artifact_root: Path | None,
) -> bool:
    if type(payload) is not dict:
        return False
    expected_keys = set(_SOURCE_COMMON_KEYS)
    if mode == "contract":
        expected_keys.add("contract_checks")
    if set(payload) != expected_keys or payload.get("mode") != mode:
        return False
    if (
        payload.get("status") != "passed"
        or type(payload.get("run_count")) is not int
        or payload.get("run_count") != ACCEPTANCE_REPEAT
    ):
        return False
    if payload.get("scientific_execution") is not (mode == "real"):
        return False
    expected_type = "real_temporal_vina" if mode == "real" else "contract_replay"
    if payload.get("provenance_gate") != {
        "status": "passed", "evidence_type": expected_type
    }:
        return False
    runs = payload.get("runs")
    if type(runs) is not list or len(runs) != 3:
        return False
    latencies: list[float] = []
    expected_backend = "temporal" if mode == "real" else "contract"
    for index, run in enumerate(runs, start=1):
        if type(run) is not dict or set(run) != _RUN_KEYS:
            return False
        provenance = run.get("provenance")
        if (
            type(run.get("run")) is not int
            or run.get("run") != index
            or run.get("status") != "passed"
            or run.get("backend") != expected_backend
            or type(run.get("workflow_id")) is not str
            or type(run.get("vina_attempts")) is not int
            or run.get("vina_attempts") != 1
            or type(run.get("terminal_event_count")) is not int
            or run.get("terminal_event_count") != 1
            or run.get("pose_exists") is not True
            or type(run.get("pose_count")) is not int
            or run["pose_count"] < 1
            or type(run.get("binding_energy")) is not float
            or not _finite_number(run.get("binding_energy"))
            or not _safe_artifact_path(run.get("artifact_path"))
            or type(run.get("artifact_sha256")) is not str
            or re.fullmatch(r"[0-9a-f]{64}", run["artifact_sha256"]) is None
            or run.get("provenance_complete") is not True
            or type(provenance) is not dict
            or not {"tool_name", "tool_version"}.issubset(provenance)
            or not set(provenance).issubset(
                {"tool_name", "tool_version", "model_name", "model_version"}
            )
            or provenance.get("tool_name") != "molecular_docking"
            or not all(type(value) is str and value for value in provenance.values())
            or run.get("demo_mode") is not False
            or run.get("fallback_used") is not False
            or run.get("artifact_integrity_valid") is not True
            or run.get("terminal_event_valid") is not True
            or type(run.get("latency_ms")) is not float
            or not _finite_number(run.get("latency_ms"))
            or float(run["latency_ms"]) < 0
            or run.get("warnings") != []
            or run.get("failures") != []
        ):
            return False
        latencies.append(float(run["latency_ms"]))
        if mode == "real":
            workflow_id = run.get("workflow_id")
            if (
                type(workflow_id) is not str
                or not workflow_id.startswith("medchat-docking-")
                or artifact_root is None
            ):
                return False
            task_id = workflow_id.removeprefix("medchat-docking-")
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,254}", task_id) is None:
                return False
            artifact = Path(os.path.abspath(os.fspath(artifact_root))) / task_id
            artifact = artifact.joinpath(*PurePosixPath(run["artifact_path"]).parts)
            try:
                snapshot = read_file_snapshot(artifact, 64 * 1024 * 1024)
            except ValueError:
                return False
            if snapshot.sha256 != run["artifact_sha256"]:
                return False
    ordered = sorted(latencies)
    latency_summary = payload.get("latency_ms")
    if (
        type(latency_summary) is not dict
        or set(latency_summary) != {"p50", "p95"}
        or type(latency_summary.get("p50")) is not float
        or type(latency_summary.get("p95")) is not float
    ):
        return False
    aggregate = {
        "status": "passed",
        "run_count": 3,
        "passed_count": 3,
        "pass_rate": 1.0,
        "duplicate_vina_count": 0,
        "terminal_event_violation_count": 0,
        "latency_ms": {"p50": ordered[1], "p95": ordered[2]},
        "failure_type_distribution": {},
    }
    if (
        type(payload.get("passed_count")) is not int
        or type(payload.get("pass_rate")) is not float
        or type(payload.get("duplicate_vina_count")) is not int
        or type(payload.get("terminal_event_violation_count")) is not int
    ):
        return False
    if any(payload.get(key) != value for key, value in aggregate.items()):
        return False
    if mode == "contract":
        checks = payload.get("contract_checks")
        if type(checks) is not dict or set(checks) != {
            "cancellation_confirmed", "completion_reused", "heartbeat_observed",
            "probe_count", "raw_executor_calls", "startup_fallback_only",
        }:
            return False
        if (
            any(
                type(checks.get(key)) is not bool
                for key in (
                    "cancellation_confirmed", "completion_reused",
                    "heartbeat_observed", "startup_fallback_only",
                )
            )
            or type(checks.get("probe_count")) is not int
            or type(checks.get("raw_executor_calls")) is not int
        ):
            return False
        if checks != {
            "cancellation_confirmed": True,
            "completion_reused": True,
            "heartbeat_observed": True,
            "probe_count": 3,
            "raw_executor_calls": 3,
            "startup_fallback_only": True,
        }:
            return False
    return True


def _project_acceptance(payload: object, mode: str, *, valid: bool) -> dict[str, Any]:
    source = payload if type(payload) is dict else {}
    provenance = source.get("provenance_gate")
    projected_provenance = {
        "status": provenance.get("status"),
        "evidence_type": provenance.get("evidence_type"),
    } if type(provenance) is dict else {"status": "failed", "evidence_type": "invalid"}
    projected = {
        "status": "passed" if valid else "failed",
        "mode": source.get("mode") if source.get("mode") in _ACCEPTANCE_MODES else mode,
        "run_count": source.get("run_count") if type(source.get("run_count")) is int else 0,
        "scientific_execution": source.get("scientific_execution") is True,
        "provenance_gate": projected_provenance,
        "source_report_sha256": source.get("sha256") if valid else None,
        "source_schema_valid": valid,
    }
    if not valid:
        projected["provenance_gate"]["status"] = "failed"
    projected["sha256"] = _canonical_sha256(projected)
    return projected


def run_acceptance_evidence(
    *,
    mode: str,
    repo_root: Path,
    output_path: Path,
    command_runner: Callable[..., object] = _subprocess_runner,
    base_environment: dict[str, str] | None = None,
    artifact_root: Path | None = None,
) -> dict[str, Any]:
    if mode not in _ACCEPTANCE_MODES:
        raise ValueError("invalid acceptance mode")
    source_environment = base_environment if base_environment is not None else os.environ
    environment = {
        key: value
        for key, value in source_environment.items()
        if key in _CHILD_ENVIRONMENT_KEYS and type(value) is str
    }
    if mode == "real":
        environment["MEDCHAT_TASK_BACKEND"] = "temporal_canary"
        environment["MEDCHAT_TEMPORAL_CANARY_PERCENT"] = "100"
    argv = [
        os.path.abspath(sys.executable),
        "-I",
        os.fspath(Path(repo_root) / "scripts/run_temporal_docking_acceptance.py"),
        "--mode",
        mode,
        "--repeat",
        str(ACCEPTANCE_REPEAT),
        "--output",
        os.fspath(output_path),
    ]
    trusted = None
    timeout, cleanup_grace = _acceptance_timeout_budget(real=mode == "real")
    try:
        if command_runner is _subprocess_runner:
            trusted = open_trusted_executable(Path(os.path.realpath(argv[0])))
            argv[0] = trusted.executable
            trusted.revalidate()
            result = command_runner(
                argv, cwd=Path(repo_root), environment=environment,
                timeout=timeout,
                pass_fds=trusted.pass_fds,
                cleanup_grace=cleanup_grace,
            )
            trusted.revalidate()
        else:
            result = command_runner(
                argv, cwd=Path(repo_root), environment=environment,
                timeout=timeout,
            )
        payload = _read_acceptance(output_path)
    except Exception:
        payload = None
        result = None
    finally:
        if trusted is not None:
            trusted.close()
    valid = (
        getattr(result, "returncode", 1) == 0
        and _validate_source_acceptance(payload, mode, artifact_root=artifact_root)
    )
    projected = _project_acceptance(payload, mode, valid=valid)
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
                artifact_root=TaskRuntimeConfig.from_env().staging_root,
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
        write_report_atomic(arguments.output, report)
    except Exception:
        print("temporal_production_preflight=failed code=preflight_failed", file=sys.stderr)
        return 1
    print(f"status={report['status']} sha256={report['sha256']}")
    return 0 if report["status"] == "passed" else 2 if report["status"] == "partial" else 1


if __name__ == "__main__":
    raise SystemExit(main())
