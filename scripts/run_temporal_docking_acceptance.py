#!/usr/bin/env python3
"""Release-gate acceptance for the Temporal docking canary."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import re
import signal
import sys
import tempfile
import threading
import time
from pathlib import Path, PurePosixPath
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.agent.persistence.redaction import (
    contains_credential,
    sanitize_sensitive_text,
)
from src.task_runtime.models import canonical_artifact_type
from src.task_runtime.secure_io import read_file_snapshot

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SAFE_CODE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}\Z")
_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._:+-]{0,127}\Z")
_BROAD_CREDENTIAL_FRAGMENT = re.compile(
    r"(?i)(?:"
    r"sk-[A-Za-z0-9_-]{8,}|"
    r"(?:AKIA|ASIA)[A-Z0-9]{16}|"
    r"gh[pousr]_[A-Za-z0-9_]{20,}|"
    r"github_pat_[A-Za-z0-9_]{20,}|"
    r"bearer\s+[A-Za-z0-9._~+/=-]{8,}"
    r")"
)
_TERMINAL = {"succeeded", "failed", "canceled", "timed_out"}
MAX_POSE_BYTES = 64 * 1024 * 1024


def _sensitive_string(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    _, changed = sanitize_sensitive_text(
        value,
        max_chars=max(1, min(len(value), 4096)),
    )
    return changed or _BROAD_CREDENTIAL_FRAGMENT.search(value) is not None


def _contains_sensitive(value: Any) -> bool:
    if contains_credential(value):
        return True
    if isinstance(value, dict):
        return any(
            _sensitive_string(str(key)) or _contains_sensitive(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple, set)):
        return any(_contains_sensitive(item) for item in value)
    return _sensitive_string(value)


def _safe_relative_path(value: Any) -> str | None:
    if type(value) is not str or not value or "\\" in value or ":" in value:
        return None
    path = PurePosixPath(value)
    if path.is_absolute() or any(
        part in {"", ".", ".."} or _sensitive_string(part)
        for part in path.parts
    ):
        return None
    return value


def _safe_code(value: Any) -> str | None:
    return (
        value
        if type(value) is str
        and _SAFE_CODE.fullmatch(value)
        and not _sensitive_string(value)
        else None
    )


def _safe_identifier(value: Any) -> str | None:
    return (
        value
        if type(value) is str
        and _SAFE_IDENTIFIER.fullmatch(value) is not None
        and not _sensitive_string(value)
        else None
    )


def _safe_warning_codes(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    codes = []
    for item in value[:32]:
        candidate = item.get("code") if isinstance(item, dict) else item
        safe = _safe_code(candidate)
        if safe is not None and not _contains_sensitive(safe) and safe not in codes:
            codes.append(safe)
    return codes


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


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


def summarize_runs(
    runs: list[dict[str, Any]],
    *,
    expected_backend: str | None = None,
    expected_execution_backend: str | None = None,
) -> dict[str, Any]:
    """Normalize untrusted run data and apply non-negotiable science gates."""

    normalized = []
    duplicate_vina_count = 0
    terminal_violations = 0
    latencies: list[float] = []
    for index, raw in enumerate(runs, start=1):
        source = raw if isinstance(raw, dict) else {}
        failures: list[str] = []
        sensitive = _contains_sensitive(source)
        if sensitive:
            failures.append("sensitive_output_detected")

        status = source.get("status")
        if status != "succeeded":
            failures.append("task_not_succeeded")
        if expected_backend is not None and source.get("backend") != expected_backend:
            failures.append("backend_mismatch")
        execution_backend = _safe_code(source.get("execution_backend"))
        secure_runtime = _safe_code(source.get("secure_runtime"))
        sandbox_image_digest = source.get("sandbox_image_digest")
        valid_image_digest = (
            type(sandbox_image_digest) is str
            and _SHA256.fullmatch(sandbox_image_digest) is not None
        )
        cleanup_status = _safe_code(source.get("cleanup_status"))
        if (
            expected_execution_backend is not None
            and execution_backend != expected_execution_backend
        ):
            failures.append("execution_backend_mismatch")
        if expected_execution_backend == "opensandbox":
            if secure_runtime != "gvisor":
                failures.append("secure_runtime_invalid")
            if not valid_image_digest:
                failures.append("sandbox_image_digest_invalid")
            if cleanup_status != "succeeded":
                failures.append("cleanup_status_invalid")
        attempts = source.get("vina_attempts")
        if type(attempts) is not int or attempts != 1:
            failures.append("duplicate_vina_execution" if type(attempts) is int and attempts > 1 else "vina_attempt_count_invalid")
        if type(attempts) is int and attempts > 1:
            duplicate_vina_count += attempts - 1
        terminal_count = source.get("terminal_event_count")
        if type(terminal_count) is not int or terminal_count != 1:
            failures.append("terminal_event_count_invalid")
            terminal_violations += 1
        if source.get("pose_exists") is not True:
            failures.append("pose_missing")
        pose_count = source.get("pose_count")
        if type(pose_count) is not int or pose_count <= 0:
            failures.append("pose_count_invalid")
        energy = source.get("binding_energy")
        if type(energy) not in {int, float} or not math.isfinite(float(energy)):
            failures.append("binding_energy_invalid")
        artifact_path = _safe_relative_path(source.get("artifact_path"))
        if artifact_path is None:
            failures.append("artifact_path_invalid")
        digest = source.get("artifact_sha256")
        if type(digest) is not str or _SHA256.fullmatch(digest) is None:
            failures.append("artifact_hash_invalid")
        if source.get("provenance_complete") is not True:
            failures.append("provenance_incomplete")
        if source.get("artifact_integrity_valid") is not True:
            failures.append("artifact_integrity_invalid")
        if source.get("terminal_event_valid") is not True:
            failures.append("terminal_event_mismatch")
        provenance = {
            key: safe
            for key in ("tool_name", "tool_version", "model_name", "model_version")
            if (safe := _safe_identifier(source.get(key))) is not None
        }
        if (
            provenance.get("tool_name") != "molecular_docking"
            or "tool_version" not in provenance
        ):
            failures.append("provenance_incomplete")
        if source.get("demo_mode") is not False:
            failures.append("demo_science_forbidden")
        if source.get("fallback_used") is not False:
            failures.append("fallback_science_forbidden")
        latency = source.get("latency_ms")
        if type(latency) in {int, float} and math.isfinite(float(latency)) and latency >= 0:
            latencies.append(float(latency))
        else:
            failures.append("latency_invalid")

        normalized.append(
            {
                "run": index,
                "status": "passed" if not failures else "failed",
                "backend": _safe_code(source.get("backend")),
                "execution_backend": execution_backend,
                "secure_runtime": secure_runtime,
                "sandbox_image_digest": (
                    sandbox_image_digest if valid_image_digest else None
                ),
                "cleanup_status": cleanup_status,
                "workflow_id": _safe_code(source.get("workflow_id")),
                "vina_attempts": attempts if type(attempts) is int else None,
                "terminal_event_count": terminal_count if type(terminal_count) is int else None,
                "pose_exists": source.get("pose_exists") is True,
                "pose_count": pose_count if type(pose_count) is int else None,
                "binding_energy": float(energy) if type(energy) in {int, float} and math.isfinite(float(energy)) else None,
                "artifact_path": artifact_path,
                "artifact_sha256": digest if isinstance(digest, str) and _SHA256.fullmatch(digest) else None,
                "provenance_complete": source.get("provenance_complete") is True,
                "provenance": provenance,
                "demo_mode": source.get("demo_mode") if type(source.get("demo_mode")) is bool else None,
                "fallback_used": source.get("fallback_used") if type(source.get("fallback_used")) is bool else None,
                "artifact_integrity_valid": source.get("artifact_integrity_valid") is True,
                "terminal_event_valid": source.get("terminal_event_valid") is True,
                "latency_ms": float(latency) if type(latency) in {int, float} and math.isfinite(float(latency)) and latency >= 0 else None,
                "warnings": _safe_warning_codes(source.get("warnings")),
                "failures": list(dict.fromkeys(failures)),
            }
        )

    passed = sum(item["status"] == "passed" for item in normalized)
    total = len(normalized)
    failure_distribution: dict[str, int] = {}
    for item in normalized:
        for failure in item["failures"]:
            failure_distribution[failure] = failure_distribution.get(failure, 0) + 1
    return {
        "status": "passed" if total > 0 and passed == total else "failed",
        "run_count": total,
        "passed_count": passed,
        "pass_rate": passed / total if total else 0.0,
        "duplicate_vina_count": duplicate_vina_count,
        "terminal_event_violation_count": terminal_violations,
        "latency_ms": {
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
        },
        "failure_type_distribution": dict(sorted(failure_distribution.items())),
        "runs": normalized,
    }


def _contract_probe() -> tuple[dict[str, Any], dict[str, Any]]:
    """Exercise completion reuse and cancellation with a controlled fake tool."""

    from src.agent.contracts import ToolProvenance, ToolResult
    from src.task_runtime.docking_execution import DockingExecution
    from src.task_runtime.selector import TemporalDockingSelector
    from src.task_runtime.staging import DockingInputStager

    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="medchat_temporal_contract_") as tmp:
        root = Path(tmp).resolve()
        output_root = root / "output"
        output_root.mkdir()
        config = {
            "center": [1.0, 2.0, 3.0],
            "size": [20.0, 20.0, 20.0],
            "exhaustiveness": 8,
            "num_modes": 1,
        }
        stager = DockingInputStager(root)
        manifest = stager.stage(
            "contract-task",
            "receptor.pdb",
            b"ATOM\n",
            "ligand.sdf",
            b"$$$$\n",
            None,
            config,
        )
        raw_calls = []

        def executor(payload, **control):
            raw_calls.append(1)
            task_id = control.get("job_id") or "contract-task"
            job_root = output_root / f"docking_{task_id}"
            job_root.mkdir(parents=True, exist_ok=True)
            pose = job_root / "result.pdbqt"
            pose.write_text(
                "MODEL 1\n"
                "REMARK VINA RESULT: -7.000 0.000 0.000\n"
                "ROOT\n"
                "ATOM      1  C   LIG A   1       0.000   0.000   0.000  0.00  0.00    +0.000 C\n"
                "ENDROOT\nTORSDOF 0\nENDMDL\n",
                encoding="utf-8",
            )
            return ToolResult.success_result(
                "molecular_docking",
                data={
                    "total_poses": 1,
                    "pose_file": str(pose),
                    "best_pose": {
                        "binding_energy": -7.0,
                        "pose_file": str(pose),
                    },
                },
                quality={
                    "real_execution": True,
                    "docking_inputs": {
                        "receptor_provided": True,
                        "ligand_provided": True,
                        "ligand_mode": "file",
                        "center": config["center"],
                        "size": config["size"],
                    },
                },
                provenance=ToolProvenance(
                    tool_name="molecular_docking",
                    tool_version="controlled-contract-1",
                    demo_mode=False,
                    fallback_used=False,
                ),
            )

        progress = []
        execution = DockingExecution(
            root,
            raw_executor=executor,
            allowed_output_root=output_root,
        )
        first = execution.run_verified(
            "contract-task",
            manifest,
            attempt=1,
            progress_callback=lambda phase, value: progress.append((phase, value)),
        )
        second = execution.run_verified("contract-task", manifest, attempt=2)

        cancel_manifest = stager.stage(
            "contract-cancel",
            "receptor.pdb",
            b"ATOM\n",
            "ligand.sdf",
            b"$$$$\n",
            None,
            config,
        )
        cancel = threading.Event()
        cancel.set()
        cancelled = execution.run_verified(
            "contract-cancel",
            cancel_manifest,
            cancel_event=cancel,
        )
        fallback = TemporalDockingSelector(100).select(
            "docking",
            "contract-fallback",
            False,
        )
        artifact = (first.get("artifacts") or [{}])[0]
        metadata = artifact.get("metadata") if isinstance(artifact, dict) else {}
        quality = first.get("quality") if isinstance(first.get("quality"), dict) else {}
        pose_rel = first.get("data", {}).get("pose_file")
        checks = {
            "cancellation_confirmed": (
                cancelled.get("status") in {"cancelled", "canceled"}
                or cancelled.get("error", {}).get("code") == "CANCELLED"
            ),
            "completion_reused": second.get("reused_completion") is True,
            "heartbeat_observed": bool(progress),
            "raw_executor_calls": len(raw_calls),
            "startup_fallback_only": (
                fallback.backend == "local"
                and fallback.reason == "temporal_unavailable"
            ),
        }
        passed = (
            first.get("success") is True
            and checks["cancellation_confirmed"]
            and checks["completion_reused"]
            and checks["heartbeat_observed"]
            and checks["raw_executor_calls"] == 1
            and checks["startup_fallback_only"]
        )
        run = {
            "status": "succeeded" if passed else "failed",
            "backend": "contract",
            "execution_backend": quality.get("execution_backend"),
            "secure_runtime": quality.get("secure_runtime"),
            "sandbox_image_digest": quality.get("sandbox_image_digest"),
            "cleanup_status": quality.get("cleanup_status"),
            "workflow_id": "controlled-contract",
            "vina_attempts": len(raw_calls),
            "terminal_event_count": 1 if first.get("success") is True else 0,
            "pose_exists": bool(
                _safe_relative_path(pose_rel)
                and (root / "contract-task" / str(pose_rel)).is_file()
            ),
            "pose_count": first.get("data", {}).get("total_poses"),
            "binding_energy": first.get("data", {}).get("best_pose", {}).get("binding_energy"),
            "artifact_path": pose_rel,
            "artifact_sha256": metadata.get("sha256") if isinstance(metadata, dict) else None,
            "provenance_complete": (
                first.get("provenance", {}).get("tool_name") == "molecular_docking"
                and first.get("provenance", {}).get("demo_mode") is False
                and first.get("provenance", {}).get("fallback_used") is False
            ),
            "tool_name": first.get("provenance", {}).get("tool_name"),
            "tool_version": first.get("provenance", {}).get("tool_version"),
            "model_name": first.get("provenance", {}).get("model_name"),
            "model_version": first.get("provenance", {}).get("model_version"),
            "demo_mode": first.get("provenance", {}).get("demo_mode"),
            "fallback_used": first.get("provenance", {}).get("fallback_used"),
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "warnings": first.get("warnings", []),
            "artifact_integrity_valid": True,
            "terminal_event_valid": True,
        }
        return run, checks


def _project_real_record(
    record,
    events,
    *,
    staging_root: Path,
    task_id: str,
    latency_ms: int,
) -> dict[str, Any]:
    result = record.result if isinstance(record.result, dict) else {}
    pose_artifacts = [
        item
        for item in (record.artifacts or [])
        if isinstance(item, dict)
        and canonical_artifact_type(item) == "docking_pose"
    ]
    artifact = pose_artifacts[0] if len(pose_artifacts) == 1 else {}
    pose_rel = result.get("pose_file") or artifact.get("path")
    safe_pose_rel = _safe_relative_path(pose_rel)
    pose_path = (
        staging_root / task_id / safe_pose_rel
        if safe_pose_rel is not None
        else None
    )
    if artifact.get("path") != pose_rel:
        artifact = {}
    artifact_type = artifact.get("artifact_type") or artifact.get("type")
    artifact_status = artifact.get("status")
    stored_digest = artifact.get("sha256") or (
        artifact.get("metadata") or {}
    ).get("sha256")
    snapshot = None
    if pose_path is not None:
        try:
            snapshot = read_file_snapshot(
                Path(os.path.abspath(os.fspath(pose_path))), MAX_POSE_BYTES
            )
        except ValueError:
            snapshot = None
    actual_digest = snapshot.sha256 if snapshot is not None else None
    artifact_integrity_valid = (
        artifact_type == "docking_pose"
        and artifact_status in {None, "verified", "completed", "ready", "succeeded"}
        and isinstance(stored_digest, str)
        and _SHA256.fullmatch(stored_digest) is not None
        and actual_digest == stored_digest
    )
    terminal = [event for event in events if event.is_terminal]
    terminal_event_valid = (
        len(terminal) == 1 and terminal[0].event_type == "task_succeeded"
    )
    provenance = record.provenance or {}
    return {
        "status": record.status.value,
        "backend": record.backend,
        "execution_backend": (
            result.get("execution_backend")
            or provenance.get("execution_backend")
        ),
        "secure_runtime": (
            result.get("secure_runtime")
            or provenance.get("secure_runtime")
        ),
        "sandbox_image_digest": (
            result.get("sandbox_image_digest")
            or provenance.get("sandbox_image_digest")
        ),
        "cleanup_status": (
            result.get("cleanup_status")
            or provenance.get("cleanup_status")
        ),
        "workflow_id": record.external_workflow_id,
        "vina_attempts": record.attempt,
        "terminal_event_count": len(terminal),
        "terminal_event_valid": terminal_event_valid,
        "pose_exists": snapshot is not None,
        "pose_count": result.get("pose_count"),
        "binding_energy": result.get("best_energy"),
        "artifact_path": safe_pose_rel,
        "artifact_sha256": actual_digest if artifact_integrity_valid else None,
        "artifact_integrity_valid": artifact_integrity_valid,
        "provenance_complete": (
            provenance.get("tool_name") == "molecular_docking"
            and isinstance(provenance.get("tool_version"), str)
            and provenance.get("demo_mode") is False
            and provenance.get("fallback_used") is False
        ),
        "tool_name": provenance.get("tool_name"),
        "tool_version": provenance.get("tool_version"),
        "model_name": provenance.get("model_name"),
        "model_version": provenance.get("model_version"),
        "demo_mode": provenance.get("demo_mode"),
        "fallback_used": provenance.get("fallback_used"),
        "latency_ms": latency_ms,
        "warnings": record.warnings or [],
    }


async def _cancel_and_confirm_terminal(
    runtime: object,
    task_id: str,
    *,
    terminal_statuses: set[object],
    poll_interval_seconds: float,
    reason: str,
) -> bool:
    try:
        await asyncio.wait_for(
            runtime.cancel(task_id, reason=reason), timeout=10.0
        )
    except Exception:
        return False
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        try:
            record = await asyncio.wait_for(runtime.get(task_id), timeout=10.0)
        except Exception:
            return False
        if record.status in terminal_statuses:
            return True
        await asyncio.sleep(poll_interval_seconds)
    return False


async def _real_runs(
    repeat: int,
    timeout_seconds: float,
    *,
    config: object | None = None,
    runtime_factory: object | None = None,
    poll_interval_seconds: float = 1.0,
) -> list[dict[str, Any]]:
    from src.task_runtime.config import TaskRuntimeConfig
    from src.task_runtime.models import TaskStatus
    from src.task_runtime.runtime import TaskRuntime

    config = config or TaskRuntimeConfig.from_env()
    if config.backend != "temporal_canary" or config.canary_percent != 100:
        return [{"status": "failed"} for _ in range(repeat)]
    receptor = PROJECT_ROOT / "data" / "samples" / "MAGL_5zun.pdb"
    ligand = PROJECT_ROOT / "data" / "samples" / "5.sdf"
    if not receptor.is_file() or not ligand.is_file():
        return [{"status": "failed"} for _ in range(repeat)]

    factory = runtime_factory or TaskRuntime
    runtime = factory(config=config)
    results: list[dict[str, Any]] = []
    try:
        for _ in range(repeat):
            started = time.perf_counter()
            receipt = None
            try:
                deadline = time.monotonic() + timeout_seconds
                receipt = await asyncio.wait_for(
                    runtime.submit_docking(
                        receptor_name=receptor.name,
                        receptor_bytes=receptor.read_bytes(),
                        ligand_name=ligand.name,
                        ligand_bytes=ligand.read_bytes(),
                        smiles=None,
                        config={
                            "center": [5.99, 3.01, 17.345],
                            "size": [20.0, 20.0, 20.0],
                            "exhaustiveness": 8,
                            "num_modes": 10,
                        },
                    ),
                    timeout=max(0.001, deadline - time.monotonic()),
                )
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("acceptance timeout")
                    try:
                        record = await asyncio.wait_for(
                            runtime.get(receipt.task_id), timeout=remaining
                        )
                    except asyncio.TimeoutError:
                        raise TimeoutError("acceptance timeout") from None
                    if record.status in {
                        TaskStatus.SUCCEEDED,
                        TaskStatus.FAILED,
                        TaskStatus.CANCELED,
                        TaskStatus.TIMED_OUT,
                    }:
                        break
                    await asyncio.sleep(poll_interval_seconds)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("acceptance timeout")
                try:
                    events = await asyncio.wait_for(
                        runtime.events(receipt.task_id), timeout=remaining
                    )
                except asyncio.TimeoutError:
                    raise TimeoutError("acceptance timeout") from None
                results.append(
                    _project_real_record(
                        record,
                        events,
                        staging_root=config.staging_root,
                        task_id=receipt.task_id,
                        latency_ms=int((time.perf_counter() - started) * 1000),
                    )
                )
            except asyncio.CancelledError:
                if receipt is not None:
                    await asyncio.shield(
                        _cancel_and_confirm_terminal(
                            runtime,
                            receipt.task_id,
                            terminal_statuses={
                                TaskStatus.SUCCEEDED,
                                TaskStatus.FAILED,
                                TaskStatus.CANCELED,
                                TaskStatus.TIMED_OUT,
                            },
                            poll_interval_seconds=poll_interval_seconds,
                            reason="acceptance_cancelled",
                        )
                    )
                raise
            except Exception as error:
                timed_out = isinstance(error, TimeoutError)
                cleanup_confirmed = True
                if receipt is not None:
                    cleanup_confirmed = await _cancel_and_confirm_terminal(
                        runtime,
                        receipt.task_id,
                        terminal_statuses={
                            TaskStatus.SUCCEEDED,
                            TaskStatus.FAILED,
                            TaskStatus.CANCELED,
                            TaskStatus.TIMED_OUT,
                        },
                        poll_interval_seconds=poll_interval_seconds,
                        reason="acceptance_timeout" if timed_out else "acceptance_error",
                    )
                warnings = ["acceptance_timeout" if timed_out else "acceptance_run_failed"]
                if not cleanup_confirmed:
                    warnings.append("cleanup_failed")
                results.append(
                    {
                        "status": "failed",
                        "latency_ms": int((time.perf_counter() - started) * 1000),
                        "warnings": warnings,
                    }
                )
                if not cleanup_confirmed:
                    break
    finally:
        try:
            await asyncio.wait_for(runtime.close(), timeout=10.0)
        except Exception:
            if results:
                warnings = results[-1].setdefault("warnings", [])
                if isinstance(warnings, list) and "cleanup_failed" not in warnings:
                    warnings.append("cleanup_failed")
    return results


async def _real_runs_with_signal_cancellation(
    repeat: int,
    timeout_seconds: float,
) -> list[dict[str, Any]]:
    """Translate process termination into cancellation of the active receipt."""

    loop = asyncio.get_running_loop()
    task = asyncio.create_task(_real_runs(repeat, timeout_seconds))
    previous: list[tuple[int, object]] = []

    def request_cancellation(_selected: int, _frame: object) -> None:
        if not task.done():
            loop.call_soon_threadsafe(task.cancel)

    for name in ("SIGTERM", "SIGINT", "SIGBREAK"):
        selected = getattr(signal, name, None)
        if selected is None:
            continue
        try:
            prior = signal.getsignal(selected)
            signal.signal(selected, request_cancellation)
            previous.append((selected, prior))
        except (OSError, RuntimeError, ValueError):
            continue
    try:
        return await task
    except asyncio.CancelledError:
        return [
            {
                "status": "failed",
                "latency_ms": 0,
                "warnings": ["acceptance_cancelled"],
            }
            for _ in range(repeat)
        ]
    finally:
        for selected, handler in reversed(previous):
            try:
                signal.signal(selected, handler)
            except (OSError, RuntimeError, ValueError):
                pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate Temporal docking release gates")
    parser.add_argument("--mode", choices=("contract", "real"), default="contract")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--timeout-seconds", type=float, default=2400.0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if not 1 <= args.repeat <= 20 or not 1 <= args.timeout_seconds <= 7200:
        parser.error("repeat or timeout is outside the allowed range")

    contract_checks = None
    if args.mode == "contract":
        probes = [_contract_probe() for _ in range(args.repeat)]
        runs = [run for run, _checks in probes]
        contract_checks = {
            "cancellation_confirmed": all(
                checks["cancellation_confirmed"] for _run, checks in probes
            ),
            "completion_reused": all(
                checks["completion_reused"] for _run, checks in probes
            ),
            "heartbeat_observed": all(
                checks["heartbeat_observed"] for _run, checks in probes
            ),
            "probe_count": len(probes),
            "raw_executor_calls": sum(
                checks["raw_executor_calls"] for _run, checks in probes
            ),
            "startup_fallback_only": all(
                checks["startup_fallback_only"] for _run, checks in probes
            ),
        }
    else:
        runs = asyncio.run(
            _real_runs_with_signal_cancellation(args.repeat, args.timeout_seconds)
        )
    report = summarize_runs(
        runs,
        expected_backend="contract" if args.mode == "contract" else "temporal",
        expected_execution_backend=(
            None if args.mode == "contract" else "opensandbox"
        ),
    )
    report["mode"] = args.mode
    report["scientific_execution"] = args.mode == "real"
    report["provenance_gate"] = {
        "status": report["status"],
        "evidence_type": (
            "real_temporal_vina" if args.mode == "real" else "contract_replay"
        ),
    }
    if contract_checks is not None:
        report["contract_checks"] = contract_checks
    if _contains_sensitive(report):
        report = {
            "status": "failed",
            "mode": args.mode,
            "run_count": 0,
            "scientific_execution": args.mode == "real",
            "error": "report_projection_failed",
            "provenance_gate": {
                "status": "failed",
                "evidence_type": (
                    "real_temporal_vina"
                    if args.mode == "real"
                    else "contract_replay"
                ),
            },
        }
    report["sha256"] = _canonical_sha256(report)
    try:
        from scripts.validate_temporal_deployment import write_report_atomic

        write_report_atomic(args.output, report)
    except Exception:
        print("temporal_docking_acceptance=failed code=report_write_failed", file=sys.stderr)
        return 1
    print(json.dumps({"status": report["status"], "mode": args.mode, "run_count": report["run_count"]}))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
