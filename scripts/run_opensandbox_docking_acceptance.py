#!/usr/bin/env python3
"""Opt-in, trace-safe acceptance for real OpenSandbox docking under gVisor."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import math
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.agent.persistence.redaction import contains_credential, sanitize_sensitive_text
from src.docking.sandbox_runner import SandboxDockingRunner
from src.task_runtime.secure_io import read_file_snapshot


_GATE = "MEDCHAT_RUN_OPENSANDBOX_ACCEPTANCE"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SAFE_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_TRACE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9 .+_()-]{0,127}\Z")
_VINA_VERSION = re.compile(
    r"(?:(?:AutoDock Vina )(?:v)?|v)?"
    r"(0|[1-9][0-9]{0,8})\."
    r"(0|[1-9][0-9]{0,8})\."
    r"(0|[1-9][0-9]{0,8})\Z"
)
_IMAGE_REFERENCE = re.compile(r"[^\s@]+@sha256:([0-9a-f]{64})\Z")
_VINA_RESULT = re.compile(
    rb"^REMARK VINA RESULT:\s+([^\s]+)\s+([^\s]+)\s+([^\s]+)\s*$"
)
_TOP_LEVEL_FIELDS = {
    "schema_version",
    "status",
    "backend",
    "secure_runtime",
    "repeat",
    "pass_rate",
    "p50_latency_ms",
    "p95_latency_ms",
    "security_failures",
    "failure_type_distribution",
    "runs",
}
_RUN_FIELDS = {
    "trace_id",
    "status",
    "pose_count",
    "best_energy",
    "artifact_path",
    "artifact_sha256",
    "tool_versions",
    "image_digest",
    "secure_runtime",
    "cleanup_status",
    "latency_ms",
    "warning_codes",
    "failure_codes",
}
_MAX_POSE_BYTES = 100 * 1024 * 1024
_DOCKER_TIMEOUT_SECONDS = 12.0
_CONTAINER_WAIT_SECONDS = 45.0
_CLEANUP_WAIT_SECONDS = 30.0
_EXPECTED_NETWORK = "medchat-opensandbox"
_EXPECTED_NETWORK_LABEL = "com.medchat.opensandbox.network=v1"
_SANDBOX_PYTHON = "/opt/conda/bin/python"
_VINA_BINARY = "/opt/conda/bin/vina"


@dataclass(frozen=True)
class _CommandResult:
    ok: bool
    stdout: str


def _run_command(
    argv: Sequence[str],
    *,
    timeout: float = _DOCKER_TIMEOUT_SECONDS,
) -> _CommandResult:
    """Run one fixed-shape acceptance command without exposing diagnostics."""

    try:
        completed = subprocess.run(
            list(argv),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env={"PATH": os.environ.get("PATH", "")},
        )
        return _CommandResult(
            completed.returncode == 0,
            completed.stdout[:4096].strip(),
        )
    except (OSError, subprocess.SubprocessError):
        return _CommandResult(False, "")


def _safe_code(value: object, fallback: str) -> str:
    if type(value) is str and _SAFE_CODE.fullmatch(value):
        sanitized, changed = sanitize_sensitive_text(value, max_chars=64)
        if not changed and sanitized == value:
            return value
    return fallback


def _safe_warning_codes(values: object) -> list[str]:
    if not isinstance(values, list):
        return ["warning_redacted"] if values else []
    projected = {
        _safe_code(value, "warning_redacted")
        for value in values
    }
    return sorted(projected)


def _safe_version(value: object) -> str | None:
    if type(value) is not str:
        return None
    candidate = " ".join(value.strip().split())
    if not candidate or _VERSION.fullmatch(candidate) is None:
        return None
    sanitized, changed = sanitize_sensitive_text(candidate, max_chars=128)
    return candidate if not changed and sanitized == candidate else None


def _canonical_vina_version(value: object) -> tuple[int, int, int] | None:
    candidate = _safe_version(value)
    if candidate is None:
        return None
    match = _VINA_VERSION.fullmatch(candidate)
    if match is None:
        return None
    return tuple(int(component) for component in match.groups())


def _error_code(result: object) -> str:
    error = getattr(result, "error", None)
    value = getattr(getattr(error, "code", None), "value", None)
    return _safe_code(value, "docking_failed")


def _repo_relative_artifact(path_value: object, project_root: Path) -> tuple[Path, str] | None:
    if type(path_value) is not str:
        return None
    try:
        path = Path(path_value)
        if not path.is_absolute():
            return None
        root = project_root.resolve(strict=True)
        resolved = path.resolve(strict=True)
        relative = resolved.relative_to(root)
        if not resolved.is_file() or resolved.is_symlink():
            return None
        relative_posix = PurePosixPath(*relative.parts)
        if any(part in {"", ".", ".."} for part in relative_posix.parts):
            return None
        return resolved, str(relative_posix)
    except (OSError, RuntimeError, ValueError):
        return None


def _empty_run(trace_id: str, failure_codes: Sequence[str]) -> dict[str, Any]:
    return {
        "trace_id": trace_id,
        "status": "failed",
        "pose_count": 0,
        "best_energy": None,
        "artifact_path": None,
        "artifact_sha256": None,
        "tool_versions": {},
        "image_digest": None,
        "secure_runtime": None,
        "cleanup_status": None,
        "latency_ms": 0,
        "warning_codes": [],
        "failure_codes": sorted({_safe_code(code, "acceptance_failed") for code in failure_codes}),
    }


def _project_result(
    result: object,
    *,
    trace_id: str,
    project_root: Path,
    observer: object,
    validated_tool_versions: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    """Allowlist one worker result and independently verify its pose bytes."""

    if _TRACE_ID.fullmatch(trace_id) is None:
        raise ValueError("invalid trace id")
    if not bool(getattr(result, "success", False)):
        failed = _empty_run(trace_id, [_error_code(result)])
        latency = getattr(result, "elapsed_ms", None)
        if type(latency) is int and 0 <= latency <= 420000:
            failed["latency_ms"] = latency
        failed["warning_codes"] = _safe_warning_codes(getattr(result, "warnings", []))
        return failed

    failures: list[str] = []
    data = getattr(result, "data", None)
    quality = getattr(result, "quality", None)
    provenance = getattr(result, "provenance", None)
    artifacts = getattr(result, "artifacts", None)
    if not isinstance(data, Mapping):
        data = {}
        failures.append("result_data_invalid")
    if not isinstance(quality, Mapping):
        quality = {}
        failures.append("provenance_invalid")
    if not isinstance(artifacts, list) or len(artifacts) != 1:
        artifacts = []
        failures.append("artifact_contract_invalid")

    pose_count = data.get("total_poses")
    if type(pose_count) is not int or not 0 < pose_count <= 256:
        pose_count = 0
        failures.append("pose_count_invalid")

    best_pose = data.get("best_pose")
    best_energy = best_pose.get("binding_energy") if isinstance(best_pose, Mapping) else None
    if type(best_energy) not in (int, float) or not math.isfinite(float(best_energy)):
        best_energy = None
        failures.append("energy_invalid")
    else:
        best_energy = float(best_energy)

    artifact_path: str | None = None
    artifact_sha256: str | None = None
    observed_pose_count: int | None = None
    observed_best_energy: float | None = None
    if artifacts:
        artifact = artifacts[0]
        metadata = getattr(artifact, "metadata", None)
        expected_sha256 = metadata.get("sha256") if isinstance(metadata, Mapping) else None
        projected = _repo_relative_artifact(getattr(artifact, "path", None), project_root)
        try:
            if projected is None or _SHA256.fullmatch(str(expected_sha256)) is None:
                raise ValueError
            resolved, artifact_path = projected
            snapshot = read_file_snapshot(resolved, _MAX_POSE_BYTES)
            if (
                not snapshot.content
                or snapshot.sha256 != expected_sha256
                or len(snapshot.content) > _MAX_POSE_BYTES
            ):
                raise ValueError
            artifact_sha256 = snapshot.sha256
            lines = snapshot.content.splitlines()
            observed_pose_count = sum(line.startswith(b"MODEL ") for line in lines)
            energies: list[float] = []
            for line in lines:
                match = _VINA_RESULT.fullmatch(line.rstrip(b"\r"))
                if match is not None:
                    values = [float(value) for value in match.groups()]
                    if not all(math.isfinite(value) for value in values):
                        raise ValueError
                    energies.append(values[0])
            if not energies:
                raise ValueError
            observed_best_energy = min(energies)
        except Exception:
            artifact_path = None
            artifact_sha256 = None
            failures.append("artifact_identity_invalid")
    if observed_pose_count is not None and observed_pose_count != pose_count:
        failures.append("pose_count_mismatch")
    if (
        observed_best_energy is not None
        and best_energy is not None
        and not math.isclose(observed_best_energy, best_energy, rel_tol=0.0, abs_tol=1e-3)
    ):
        failures.append("pose_energy_mismatch")
    if artifacts:
        artifact_value = getattr(artifacts[0], "path", None)
        if (
            data.get("pose_file") != artifact_value
            or not isinstance(best_pose, Mapping)
            or best_pose.get("pose_file") != artifact_value
        ):
            failures.append("artifact_projection_mismatch")

    image_digest = quality.get("sandbox_image_digest")
    if type(image_digest) is not str or _SHA256.fullmatch(image_digest) is None:
        image_digest = None
        failures.append("image_digest_invalid")
    observed_image_digest = getattr(observer, "image_digest", None)
    if observed_image_digest != image_digest:
        failures.append("container_image_digest_mismatch")
    secure_runtime = quality.get("secure_runtime")
    if secure_runtime != "gvisor":
        secure_runtime = None
        failures.append("secure_runtime_invalid")
    cleanup_status = quality.get("cleanup_status")
    if cleanup_status != "succeeded":
        cleanup_status = None
        failures.append("cleanup_failed")
    if quality.get("real_execution") is not True or quality.get("execution_backend") != "opensandbox":
        failures.append("real_provenance_invalid")
    if (
        provenance is None
        or getattr(provenance, "tool_name", None) != "molecular_docking"
        or getattr(provenance, "demo_mode", None) is not False
        or getattr(provenance, "fallback_used", None) is not False
    ):
        failures.append("real_provenance_invalid")

    versions: dict[str, str] = {}
    vina_version = _safe_version(getattr(provenance, "tool_version", None))
    canonical_vina_version = _canonical_vina_version(vina_version)
    if vina_version is None or canonical_vina_version is None:
        failures.append("tool_version_invalid")
    else:
        versions["vina"] = vina_version
    observed_versions = (
        getattr(observer, "tool_versions", {})
        if validated_tool_versions is None
        else validated_tool_versions
    )
    if isinstance(observed_versions, Mapping):
        observed_vina = _safe_version(observed_versions.get("vina"))
        canonical_observed_vina = _canonical_vina_version(observed_vina)
        meeko_version = _safe_version(observed_versions.get("meeko"))
        if (
            observed_vina is None
            or canonical_observed_vina is None
            or canonical_observed_vina != canonical_vina_version
        ):
            failures.append("tool_version_mismatch")
        if meeko_version is not None:
            versions["meeko"] = meeko_version
    if set(versions) != {"vina", "meeko"}:
        failures.append("tool_version_invalid")

    latency = getattr(result, "elapsed_ms", None)
    if type(latency) is not int or latency < 0:
        latency = 0
        failures.append("latency_invalid")

    failures.extend(
        _safe_code(value, "security_probe_failed")
        for value in getattr(observer, "failure_codes", [])
    )
    failure_codes = sorted(set(failures))
    return {
        "trace_id": trace_id,
        "status": "passed" if not failure_codes else "failed",
        "pose_count": pose_count,
        "best_energy": best_energy,
        "artifact_path": artifact_path,
        "artifact_sha256": artifact_sha256,
        "tool_versions": versions,
        "image_digest": image_digest,
        "secure_runtime": secure_runtime,
        "cleanup_status": cleanup_status,
        "latency_ms": latency,
        "warning_codes": _safe_warning_codes(getattr(result, "warnings", [])),
        "failure_codes": failure_codes,
    }


def _percentile(values: Sequence[int], quantile: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    interpolated = ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)
    return int(round(interpolated))


def _report(
    *,
    repeat: int,
    runs: Sequence[Mapping[str, Any]],
    security_failures: Sequence[str],
    status_override: str | None = None,
) -> dict[str, Any]:
    safe_security_sequence = [
        _safe_code(code, "security_probe_failed") for code in security_failures
    ]
    safe_security = sorted(set(safe_security_sequence))
    passed = sum(run.get("status") == "passed" for run in runs)
    latencies = [
        int(run["latency_ms"])
        for run in runs
        if type(run.get("latency_ms")) is int and int(run["latency_ms"]) >= 0
    ]
    distribution: Counter[str] = Counter(safe_security_sequence)
    for run in runs:
        distribution.update(
            code for code in run.get("failure_codes", []) if code not in safe_security
        )
    status = status_override or (
        "passed" if len(runs) == repeat and passed == repeat and not safe_security else "failed"
    )
    return {
        "schema_version": 1,
        "status": status,
        "backend": "opensandbox",
        "secure_runtime": "gvisor",
        "repeat": repeat,
        "pass_rate": passed / repeat if repeat else 0.0,
        "p50_latency_ms": _percentile(latencies, 0.50),
        "p95_latency_ms": _percentile(latencies, 0.95),
        "security_failures": safe_security,
        "failure_type_distribution": dict(sorted(distribution.items())),
        "runs": list(runs),
    }


def _report_is_trace_safe(report: Mapping[str, Any]) -> bool:
    if set(report) != _TOP_LEVEL_FIELDS:
        return False
    runs = report.get("runs")
    if not isinstance(runs, list) or any(not isinstance(run, Mapping) or set(run) != _RUN_FIELDS for run in runs):
        return False
    if contains_credential(report):
        return False
    try:
        encoded = json.dumps(report, ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError):
        return False
    forbidden = (
        "OPEN_SANDBOX_API_KEY",
        "OPENSANDBOX_SERVER_API_KEY",
        "Authorization:",
        "docker inspect",
        "broker.sock",
        "127.0.0.1:8080",
        "C:\\\\",
        "/home/",
        "/root/",
        "/opt/medchat/molecular_chat_system",
        "stderr",
        "command",
    )
    return not any(value in encoded for value in forbidden)


def _atomic_write_report(path: Path, report: Mapping[str, Any]) -> None:
    if not isinstance(path, Path):
        raise ValueError("invalid report path")
    path = Path(os.path.abspath(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.parent.is_dir() or path.parent.is_symlink():
        raise ValueError("invalid report parent")
    if not _report_is_trace_safe(report):
        raise ValueError("unsafe acceptance report")
    content = (json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False) + "\n").encode("utf-8")
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
            0o600,
        )
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(temporary, path)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _deployment_failures() -> list[str]:
    """Run the pinned static and real runtime validators, returning only stable codes."""

    try:
        from scripts import validate_opensandbox_deployment as validator

        static_code = validator.validate_static(PROJECT_ROOT)
        if static_code is not None:
            return [_safe_code(static_code, "static_validation_failed")]
        runtime_code = validator.validate_runtime()
        return [] if runtime_code is None else [_safe_code(runtime_code, "runtime_validation_failed")]
    except Exception:
        return ["deployment_validation_failed"]


class DockerSandboxObserver:
    """Inspect and probe actual OpenSandbox containers without retaining their IDs."""

    def __init__(
        self,
        command_runner: Callable[..., _CommandResult] = _run_command,
    ) -> None:
        self._command = command_runner
        self._stop = threading.Event()
        self._seen = threading.Event()
        self._inspection_ready = threading.Event()
        self._probes_complete = threading.Event()
        self._thread: threading.Thread | None = None
        self._baseline: set[str] = set()
        self._observed: set[str] = set()
        self.failure_codes: list[str] = []
        self.tool_versions: dict[str, str] = {}
        self.image_digest: str | None = None
        self._network_gateway: str | None = None
        self._intrusive_probes_enabled = False

    @property
    def sandbox_count(self) -> int:
        return len(self._observed)

    def _docker(self, *argv: str, timeout: float = _DOCKER_TIMEOUT_SECONDS) -> _CommandResult:
        return self._command(("docker", *argv), timeout=timeout)

    def _container_ids(self, *, running: bool) -> set[str]:
        args = ("ps", "-q" if running else "-aq", "--no-trunc", "--filter", "label=opensandbox.io/id")
        result = self._docker(*args)
        if not result.ok:
            self.failure_codes.append("docker_observation_failed")
            return set()
        return {line for line in result.stdout.splitlines() if re.fullmatch(r"[0-9a-f]{64}", line)}

    def start(self) -> None:
        self._baseline = self._container_ids(running=False)
        self._thread = threading.Thread(target=self._observe, name="opensandbox-acceptance-observer", daemon=True)
        self._thread.start()

    def wait_for_seen(self, timeout: float = _CONTAINER_WAIT_SECONDS) -> bool:
        return self._seen.wait(timeout)

    def wait_for_inspection_ready(self, timeout: float = _CONTAINER_WAIT_SECONDS) -> bool:
        return self._inspection_ready.wait(timeout)

    def wait_for_probes_complete(self, timeout: float = _CONTAINER_WAIT_SECONDS) -> bool:
        return self._probes_complete.wait(timeout)

    def enable_intrusive_probes(self) -> None:
        """Enable workload-altering probes for a dedicated sacrificial sandbox."""

        self._intrusive_probes_enabled = True

    def disable_intrusive_probes(self) -> None:
        """Keep lifecycle inspection active without racing cancellation cleanup."""

        self._intrusive_probes_enabled = False

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=60.0)
            if self._thread.is_alive():
                self.failure_codes.append("observer_shutdown_failed")
        if not self._seen.is_set():
            self.failure_codes.append("sandbox_not_observed")
        elif not self._inspection_ready.is_set():
            self.failure_codes.append("sandbox_inspection_incomplete")
        if not self.assert_no_running():
            self.failure_codes.append("sandbox_cleanup_failed")
        self.failure_codes = sorted(set(self.failure_codes))

    def assert_no_running(self) -> bool:
        deadline = time.monotonic() + _CLEANUP_WAIT_SECONDS
        while time.monotonic() < deadline:
            if not self._container_ids(running=True):
                return True
            time.sleep(0.25)
        return False

    def _observe(self) -> None:
        inspected: set[str] = set()
        deadline = time.monotonic() + _CONTAINER_WAIT_SECONDS
        while not self._stop.is_set() and time.monotonic() < deadline:
            current = self._container_ids(running=False) - self._baseline
            for container_id in current - inspected:
                inspected.add(container_id)
                self._observed.add(container_id)
                self._seen.set()
                self._inspect_and_probe(container_id)
            time.sleep(0.05)

    def _inspect_field(self, container_id: str, template: str) -> str | None:
        result = self._docker("container", "inspect", "--format", template, container_id)
        return result.stdout if result.ok else None

    def _exec(self, container_id: str, *argv: str, timeout: float = _DOCKER_TIMEOUT_SECONDS) -> bool:
        return self._docker("exec", container_id, *argv, timeout=timeout).ok

    def _wait_for_exec_ready(self, container_id: str) -> bool:
        deadline = time.monotonic() + _CONTAINER_WAIT_SECONDS
        while not self._stop.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            result = self._docker(
                "exec",
                container_id,
                _SANDBOX_PYTHON,
                "-c",
                "raise SystemExit(0)",
                timeout=min(3.0, remaining),
            )
            if result.ok:
                return True
            if self._stop.wait(min(0.1, remaining)):
                return False
        return False

    def _inspect_and_probe(self, container_id: str) -> None:
        exec_ready = not self._intrusive_probes_enabled
        try:
            if self._inspect_field(container_id, "{{.HostConfig.Runtime}}") != "runsc":
                self.failure_codes.append("container_runtime_not_runsc")

            cpu = self._inspect_field(container_id, "{{.HostConfig.NanoCpus}}")
            memory = self._inspect_field(container_id, "{{.HostConfig.Memory}}")
            pids = self._inspect_field(container_id, "{{.HostConfig.PidsLimit}}")
            if (cpu, memory, pids) != ("2000000000", "4294967296", "128"):
                self.failure_codes.append("resource_limits_invalid")

            image = self._inspect_field(container_id, "{{.Config.Image}}")
            image_match = _IMAGE_REFERENCE.fullmatch(image or "")
            if image_match is None:
                self.failure_codes.append("container_image_not_immutable")
            else:
                self.image_digest = image_match.group(1)

            networks = self._inspect_field(container_id, "{{json .NetworkSettings.Networks}}")
            try:
                if networks is None or set(json.loads(networks)) != {_EXPECTED_NETWORK}:
                    raise ValueError
            except (TypeError, ValueError, json.JSONDecodeError):
                self.failure_codes.append("container_network_invalid")

            self._validate_network()
            if self._network_gateway is None:
                self.failure_codes.append("host_service_probe_unavailable")
            if self._intrusive_probes_enabled:
                exec_ready = self._wait_for_exec_ready(container_id)
                if not exec_ready:
                    self.failure_codes.append("sandbox_exec_not_ready")
        finally:
            self._inspection_ready.set()

        if not self._intrusive_probes_enabled:
            self._probes_complete.set()
            return
        try:
            if self._network_gateway is not None and exec_ready:
                self._run_security_probes(container_id, self._network_gateway)
        finally:
            self._probes_complete.set()

    def _validate_network(self) -> None:
        checks = {
            "{{.Name}}": _EXPECTED_NETWORK,
            "{{json .Internal}}": "true",
            '{{index .Labels "com.medchat.opensandbox.network"}}': "v1",
        }
        for template, expected in checks.items():
            result = self._docker("network", "inspect", "--format", template, _EXPECTED_NETWORK)
            if not result.ok or result.stdout != expected:
                self.failure_codes.append("owned_internal_network_invalid")
                return

        gateway = self._docker(
            "network",
            "inspect",
            "--format",
            "{{(index .IPAM.Config 0).Gateway}}",
            _EXPECTED_NETWORK,
        )
        try:
            address = ipaddress.ip_address(gateway.stdout)
            if not gateway.ok or address.is_loopback or address.is_unspecified:
                raise ValueError
            self._network_gateway = str(address)
        except ValueError:
            self.failure_codes.append("owned_internal_network_invalid")

    def _run_security_probes(self, container_id: str, network_gateway: str) -> None:
        read_probe = (
            "import os,sys;"
            "paths=['/opt/medchat/molecular_chat_system',"
            "'/opt/medchat/molecular_chat_system/.env',"
            "'/opt/medchat/molecular_chat_system/data/tasks.sqlite',"
            "'/workspace/.env','/.env','/workspace/task_runtime.sqlite',"
            "'/var/lib/medchat/tasks.sqlite','/var/run/docker.sock'];"
            "sys.exit(1 if any(os.path.exists(p) and "
            "(os.access(p,os.R_OK) or os.access(p,os.W_OK)) for p in paths) else 0)"
        )
        if not self._exec(container_id, _SANDBOX_PYTHON, "-c", read_probe):
            self.failure_codes.append("host_data_exposed")

        filesystem_probe = (
            "import os,sys;"
            "bad=[];"
            "\nfor p in ['/etc/medchat-acceptance-write','/opt/medchat/acceptance-write']:"
            "\n try: open(p,'wb').write(b'x');bad.append(p)"
            "\n except OSError: pass"
            "\nout='/workspace/output/acceptance-write';open(out,'wb').write(b'ok');os.unlink(out);"
            "sys.exit(1 if bad else 0)"
        )
        if not self._exec(container_id, _SANDBOX_PYTHON, "-c", filesystem_probe):
            self.failure_codes.append("filesystem_policy_invalid")

        network_probe = (
            "import socket,sys;ok=True;"
            "\nfor mode,target in [('dns','example.com'),('ip','1.1.1.1')]:"
            "\n try:"
            "\n  (socket.getaddrinfo(target,443) if mode=='dns' else socket.create_connection((target,443),1));ok=False"
            "\n except OSError: pass"
            "\nfor port in (8080,6001,2375):"
            f"\n try: socket.create_connection(({network_gateway!r},port),1);ok=False"
            "\n except OSError: pass"
            "\nsys.exit(0 if ok else 1)"
        )
        if not self._exec(
            container_id, _SANDBOX_PYTHON, "-c", network_probe, timeout=15.0
        ):
            self.failure_codes.append("outbound_network_accessible")

        descendant_probe = r'''
import importlib.util
import json
import os
import signal
import sys
import time
from pathlib import Path

runner_path = "/opt/medchat/run_docking.py"
pid_file = Path("/workspace/output/.setsid-acceptance.json")
try:
    pid_file.unlink()
except FileNotFoundError:
    pass
spec = importlib.util.spec_from_file_location("medchat_descendant_probe", runner_path)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
module.OUTPUT_ROOT = Path("/workspace/output")
module._enable_child_subreaper()
module._INVOCATION_BASELINE_CHILDREN = module._linux_children()
child_source = (
    "import json,os,subprocess,sys;"
    "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],"
    "preexec_fn=os.setsid);"
    "open(sys.argv[1],'w').write(json.dumps([os.getpid(),child.pid]));"
    "sys.exit(0)"
)
accepted = False
try:
    module._run_tool(
        [sys.executable, "-c", child_source, str(pid_file)],
        "docking",
        time.monotonic() + 3.0,
        time.monotonic() + 4.0,
    )
except module.DockingFailure as failure:
    accepted = failure.error_code == "tool_failed"
try:
    pids = json.loads(pid_file.read_text(encoding="utf-8"))
except Exception:
    pids = []
remaining = [pid for pid in pids if Path(f"/proc/{pid}").exists()]
for pid in remaining:
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
try:
    pid_file.unlink()
except FileNotFoundError:
    pass
raise SystemExit(0 if accepted and pids and not remaining else 1)
'''.strip()
        if not self._exec(
            container_id,
            _SANDBOX_PYTHON,
            "-c",
            descendant_probe,
            timeout=8.0,
        ):
            self.failure_codes.append("setsid_probe_failed")

        vina_cli = self._docker(
            "exec", container_id, _VINA_BINARY, "--version"
        )
        vina_package = self._docker(
            "exec",
            container_id,
            _SANDBOX_PYTHON,
            "-c",
            "import importlib.metadata as m;print(m.version('vina'))",
        )
        meeko = self._docker(
            "exec",
            container_id,
            _SANDBOX_PYTHON,
            "-c",
            "import importlib.metadata as m;print(m.version('meeko'))",
        )
        vina_banner = _safe_version(vina_cli.stdout) if vina_cli.ok else None
        vina_version = _safe_version(vina_package.stdout) if vina_package.ok else None
        meeko_version = _safe_version(meeko.stdout) if meeko.ok else None
        if (
            vina_banner is None
            or _canonical_vina_version(vina_version) is None
            or meeko_version is None
        ):
            self.failure_codes.append("tool_probe_failed")
        else:
            self.tool_versions = {"vina": vina_version, "meeko": meeko_version}

        # Resource exhaustion is intentionally last because reaching the PID
        # ceiling can make execd temporarily unable to serve later probes.
        pid_probe = (
            "import subprocess,sys;children=[];limited=False;"
            "\ntry:"
            "\n for _ in range(256): children.append(subprocess.Popen(['sleep','5']))"
            "\nexcept OSError: limited=True"
            "\nfinally:"
            "\n for p in children: p.terminate()"
            "\n for p in children:"
            "\n  try: p.wait(timeout=2)"
            "\n  except Exception: p.kill();p.wait()"
            "\nsys.exit(0 if limited else 1)"
        )
        if not self._exec(
            container_id, _SANDBOX_PYTHON, "-c", pid_probe, timeout=20.0
        ):
            self.failure_codes.append("pid_limit_not_enforced")


def _payload(receptor: Path, ligand: Path, center: Sequence[float], size: Sequence[float]) -> dict[str, Any]:
    return {
        "receptor_path": str(receptor),
        "ligand_path": str(ligand),
        "center": list(center),
        "size": list(size),
        "exhaustiveness": 8,
        "num_modes": 9,
        "energy_range": 3.0,
    }


def _validate_inputs(
    receptor: Path,
    ligand: Path,
    center: Sequence[float],
    size: Sequence[float],
    repeat: int,
    report_path: Path,
) -> None:
    if type(repeat) is not int or not 1 <= repeat <= 20:
        raise ValueError("repeat must be between 1 and 20")
    if not isinstance(report_path, Path):
        raise ValueError("report path must be a Path")
    if not receptor.is_absolute() or receptor.suffix.lower() != ".pdb" or not receptor.is_file():
        raise ValueError("receptor must be an existing absolute PDB")
    if not ligand.is_absolute() or ligand.suffix.lower() != ".sdf" or not ligand.is_file():
        raise ValueError("ligand must be an existing absolute SDF")
    for name, values in (("center", center), ("size", size)):
        if len(values) != 3 or any(type(value) not in (int, float) or not math.isfinite(float(value)) for value in values):
            raise ValueError(f"{name} must contain three finite values")
    if any(float(value) <= 0 for value in size):
        raise ValueError("size values must be positive")


def _request_cancellation_after_inspection(
    observer: object,
    cancel_event: threading.Event,
    timeout: float = _CONTAINER_WAIT_SECONDS,
) -> bool:
    """Cancel only after the observer has completed non-intrusive inspection."""

    wait = getattr(observer, "wait_for_inspection_ready", None)
    ready = bool(callable(wait) and wait(timeout))
    if ready:
        cancel_event.set()
    return ready


def _request_cancellation_after_probes(
    observer: object,
    cancel_event: threading.Event,
    timeout: float = _CONTAINER_WAIT_SECONDS,
) -> bool:
    """Cancel a sacrificial workload after inspection and intrusive probes."""

    wait_for_inspection = getattr(observer, "wait_for_inspection_ready", None)
    if not callable(wait_for_inspection) or not wait_for_inspection(timeout):
        return False
    wait_for_probes = getattr(observer, "wait_for_probes_complete", None)
    probes_complete = bool(callable(wait_for_probes) and wait_for_probes(timeout))
    cancel_event.set()
    return probes_complete


def _exercise_sacrificial_security_contract(
    runner: object,
    payload: Mapping[str, Any],
    observer_factory: Callable[[], object],
) -> tuple[list[str], dict[str, str], str | None]:
    """Run workload-altering probes in one dedicated worker/Broker sandbox."""

    failures: list[str] = []
    observer = observer_factory()
    enable_probes = getattr(observer, "enable_intrusive_probes", None)
    if not callable(enable_probes):
        return ["sacrificial_probe_control_unavailable"], {}, None
    enable_probes()
    cancel_event = threading.Event()
    observer.start()
    probe_state: list[bool] = []

    def request_cancel() -> None:
        probe_state.append(
            _request_cancellation_after_probes(observer, cancel_event)
        )

    cancel_thread = threading.Thread(target=request_cancel, daemon=True)
    cancel_thread.start()
    result = runner.execute(
        payload,
        job_id=f"acceptance-sacrificial-{secrets.token_hex(8)}",
        cancel_event=cancel_event,
    )
    cancel_thread.join(timeout=1.0)
    observer.stop()

    if probe_state != [True]:
        failures.append("sacrificial_probes_incomplete")
    if getattr(observer, "sandbox_count", 0) != 1:
        failures.append("sacrificial_sandbox_count_invalid")
    if getattr(result, "success", True) or _error_code(result) != "cancelled":
        failures.append("sacrificial_cancellation_failed")
    if not observer.assert_no_running():
        failures.append("sacrificial_cleanup_failed")
    failures.extend(getattr(observer, "failure_codes", []))
    versions: dict[str, str] = {}
    observed_versions = getattr(observer, "tool_versions", {})
    if isinstance(observed_versions, Mapping):
        vina_version = _safe_version(observed_versions.get("vina"))
        meeko_version = _safe_version(observed_versions.get("meeko"))
        if _canonical_vina_version(vina_version) is not None:
            versions["vina"] = vina_version
        if meeko_version is not None:
            versions["meeko"] = meeko_version
    if set(versions) != {"vina", "meeko"}:
        failures.append("tool_probe_failed")
    image_digest = getattr(observer, "image_digest", None)
    if type(image_digest) is not str or _SHA256.fullmatch(image_digest) is None:
        failures.append("sacrificial_image_digest_invalid")
        image_digest = None
    return (
        [_safe_code(code, "sacrificial_probe_failed") for code in failures],
        versions,
        image_digest,
    )


def _exercise_fault_contracts(
    runner: object,
    payload: Mapping[str, Any],
    observer_factory: Callable[[], object],
) -> list[str]:
    failures: list[str] = []
    nonce = secrets.token_hex(8)
    probe_payload = dict(payload)
    probe_payload["exhaustiveness"] = 8

    idempotency_observer = observer_factory()
    disable_idempotency_probes = getattr(idempotency_observer, "disable_intrusive_probes", None)
    if callable(disable_idempotency_probes):
        disable_idempotency_probes()
    idempotency_observer.start()
    key = f"acceptance-idempotency-{nonce}"
    first = runner.execute(probe_payload, job_id=key)
    second = runner.execute(probe_payload, job_id=key)
    idempotency_observer.stop()
    if not getattr(first, "success", False) or not getattr(second, "success", False):
        failures.append("idempotency_execution_failed")
    if getattr(idempotency_observer, "sandbox_count", 0) != 1:
        failures.append("idempotency_created_multiple_sandboxes")
    failures.extend(getattr(idempotency_observer, "failure_codes", []))

    cancel_observer = observer_factory()
    disable_cancel_probes = getattr(cancel_observer, "disable_intrusive_probes", None)
    if callable(disable_cancel_probes):
        disable_cancel_probes()
    cancel_event = threading.Event()
    cancel_observer.start()

    cancellation_state: list[bool] = []

    def request_cancel() -> None:
        cancellation_state.append(
            _request_cancellation_after_inspection(cancel_observer, cancel_event)
        )

    cancel_thread = threading.Thread(target=request_cancel, daemon=True)
    cancel_thread.start()
    cancelled = runner.execute(
        probe_payload,
        job_id=f"acceptance-cancel-{nonce}",
        cancel_event=cancel_event,
    )
    cancel_thread.join(timeout=1.0)
    cancel_observer.stop()
    if cancellation_state != [True]:
        failures.append("cancellation_inspection_incomplete")
    if getattr(cancelled, "success", True) or _error_code(cancelled) != "cancelled":
        failures.append("cancellation_contract_failed")
    if not cancel_observer.assert_no_running():
        failures.append("cancellation_cleanup_failed")
    failures.extend(getattr(cancel_observer, "failure_codes", []))

    timeout_observer = observer_factory()
    disable_timeout_probes = getattr(timeout_observer, "disable_intrusive_probes", None)
    if callable(disable_timeout_probes):
        disable_timeout_probes()
    timeout_observer.start()
    import src.docking.sandbox_runner as worker_runner_module

    original_deadline = worker_runner_module._TOTAL_DEADLINE_SECONDS
    try:
        worker_runner_module._TOTAL_DEADLINE_SECONDS = 5.0
        timed_out = runner.execute(
            probe_payload,
            job_id=f"acceptance-timeout-{nonce}",
        )
    finally:
        worker_runner_module._TOTAL_DEADLINE_SECONDS = original_deadline
    timeout_observer.stop()
    if getattr(timed_out, "success", True) or _error_code(timed_out) != "tool_timeout":
        failures.append("timeout_contract_failed")
    if not timeout_observer.assert_no_running():
        failures.append("timeout_cleanup_failed")
    failures.extend(getattr(timeout_observer, "failure_codes", []))
    return sorted({_safe_code(code, "fault_probe_failed") for code in failures})


def run_real_acceptance(
    *,
    receptor: Path,
    ligand: Path,
    center: Sequence[float],
    size: Sequence[float],
    repeat: int,
    report_path: Path,
    runner_factory: Callable[[Path], object] | None = None,
    deployment_validator: Callable[[], list[str]] | None = None,
    observer_factory: Callable[[], object] | None = None,
    exercise_faults: bool = True,
) -> dict[str, Any]:
    """Execute real docking through the worker UDS runner and emit a safe report."""

    receptor = receptor.resolve(strict=False)
    ligand = ligand.resolve(strict=False)
    _validate_inputs(receptor, ligand, center, size, repeat, report_path)
    injected_runner = runner_factory is not None
    injected_validator = deployment_validator is not None
    runner_factory = runner_factory or (
        lambda output_root: SandboxDockingRunner.from_env(output_root)
    )
    deployment_validator = deployment_validator or _deployment_failures
    observer_factory = observer_factory or DockerSandboxObserver

    security_failures = list(deployment_validator())
    if security_failures and not injected_validator:
        report = _report(
            repeat=repeat,
            runs=[],
            security_failures=security_failures,
        )
        _atomic_write_report(report_path, report)
        return report
    output_root = PROJECT_ROOT / "outputs/opensandbox_acceptance"
    output_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    runner = runner_factory(output_root)
    request = _payload(receptor, ligand, center, size)
    runs: list[dict[str, Any]] = []
    nonce = secrets.token_hex(8)
    projection_root = (
        report_path.parent.parent.resolve(strict=False)
        if injected_runner
        else PROJECT_ROOT
    )

    reference_tool_versions: dict[str, str] | None = None
    reference_image_digest: str | None = None
    if exercise_faults:
        sacrificial_failures, reference_tool_versions, reference_image_digest = (
            _exercise_sacrificial_security_contract(
                runner,
                request,
                observer_factory,
            )
        )
        security_failures.extend(sacrificial_failures)

    for index in range(repeat):
        trace_id = f"acceptance-{nonce}-{index + 1:02d}"
        observer = observer_factory()
        disable_measured_probes = getattr(observer, "disable_intrusive_probes", None)
        if callable(disable_measured_probes):
            disable_measured_probes()
        observer.start()
        result = runner.execute(request, job_id=trace_id)
        observer.stop()
        if reference_tool_versions is not None:
            if getattr(observer, "image_digest", None) == reference_image_digest:
                observer.tool_versions = dict(reference_tool_versions)
            else:
                security_failures.append("tool_provenance_image_mismatch")
        run = _project_result(
            result,
            trace_id=trace_id,
            project_root=projection_root,
            observer=observer,
        )
        runs.append(run)
        security_failures.extend(getattr(observer, "failure_codes", []))

    if exercise_faults:
        security_failures.extend(_exercise_fault_contracts(runner, request, observer_factory))

    report = _report(repeat=repeat, runs=runs, security_failures=security_failures)
    _atomic_write_report(report_path, report)
    return report


def _gate_report(repeat: int, code: str, status: str) -> dict[str, Any]:
    return _report(
        repeat=repeat,
        runs=[],
        security_failures=[code],
        status_override=status,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run real OpenSandbox/gVisor docking acceptance")
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("outputs/agent_evaluation/opensandbox_docking_acceptance.json"),
    )
    return parser


def main(argv: Sequence[str] | None = None, *, environ: Mapping[str, str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    environment = os.environ if environ is None else environ
    if type(arguments.repeat) is not int or not 1 <= arguments.repeat <= 20:
        return 2
    if environment.get(_GATE) != "1":
        report = _gate_report(arguments.repeat, "acceptance_gate_disabled", "skipped")
        try:
            _atomic_write_report(arguments.report, report)
        except Exception:
            return 2
        print("opensandbox_acceptance=skipped code=acceptance_gate_disabled")
        return 2
    if os.name != "posix":
        report = _gate_report(arguments.repeat, "linux_qemu_required", "skipped")
        try:
            _atomic_write_report(arguments.report, report)
        except Exception:
            return 2
        print("opensandbox_acceptance=skipped code=linux_qemu_required")
        return 2

    try:
        report = run_real_acceptance(
            receptor=PROJECT_ROOT / "data/samples/MAGL_5zun.pdb",
            ligand=PROJECT_ROOT / "data/samples/5.sdf",
            center=(5.99, 3.01, 17.345),
            size=(20.0, 20.0, 20.0),
            repeat=arguments.repeat,
            report_path=arguments.report,
        )
    except Exception:
        report = _gate_report(arguments.repeat, "acceptance_internal_error", "failed")
        try:
            _atomic_write_report(arguments.report, report)
        except Exception:
            pass
        print("opensandbox_acceptance=failed code=acceptance_internal_error")
        return 1
    print(f"opensandbox_acceptance={report['status']}")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
