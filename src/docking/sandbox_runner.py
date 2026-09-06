"""Fail-closed UDS client for the standalone sandbox docking broker."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import secrets
import socket
import stat
import time
from pathlib import Path
from typing import Any, Callable, Literal, Mapping

import httpx
import httpcore
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from src.agent.contracts import (
    AgentErrorCode,
    ObservationStatus,
    ToolProvenance,
    ToolResult,
    WorkflowArtifact,
)
from src.agent.persistence.redaction import sanitize_sensitive_text
from src.sandbox_broker.models import (
    BrokerErrorCode,
    BrokerJobStatus,
    TERMINAL_STATUSES,
)
from src.task_runtime.config import _stat_identity, _stat_version
from src.task_runtime.secure_io import read_file_snapshot


_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_UUID_HEX_PATTERN = re.compile(r"[0-9a-f]{32}\Z")
_TRACE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_JOB_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_VERSION_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9 .+_()-]{0,127}\Z")
_REMOTE_RECEPTOR_SUFFIXES = frozenset({".pdb"})
_REMOTE_LIGAND_SUFFIXES = frozenset({".sdf", ".mol"})
_RECEPTOR_MAX_BYTES = 50 * 1024 * 1024
_LIGAND_MAX_BYTES = 10 * 1024 * 1024
_ARTIFACT_MAX_BYTES = 100 * 1024 * 1024
_JSON_MAX_BYTES = 1024 * 1024
_POLL_SECONDS = 0.5
_TOTAL_DEADLINE_SECONDS = 420.0
_CANCEL_DEADLINE_SECONDS = 2.0
_RENAME_NOREPLACE = 1
_SUBMIT_TIMEOUT = httpx.Timeout(connect=2.0, read=30.0, write=30.0, pool=2.0)
_POLL_TIMEOUT = httpx.Timeout(connect=2.0, read=5.0, write=5.0, pool=2.0)
_DOWNLOAD_TIMEOUT = httpx.Timeout(connect=2.0, read=15.0, write=5.0, pool=2.0)
_BASE_URL = "http://medchat-sandbox-broker"
_HTTP_TRANSPORT_TYPE = httpx.HTTPTransport


class _Cancelled(RuntimeError):
    pass


class _BrokerFailure(RuntimeError):
    def __init__(
        self,
        code: BrokerErrorCode,
        warnings: tuple[str, ...] = (),
    ) -> None:
        self.code = code
        self.warnings = warnings


class _BrokerHttpFailure(RuntimeError):
    def __init__(
        self,
        status_code: int,
        stage: Literal["submit", "poll", "manifest", "artifact", "cancel"],
    ) -> None:
        self.status_code = status_code
        self.stage = stage


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


def _safe_metadata(value: object, *, maximum: int = 256) -> str:
    if type(value) is not str or not value or len(value) > maximum:
        raise ValueError("invalid broker response")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("invalid broker response")
    sanitized, changed = sanitize_sensitive_text(value, max_chars=maximum)
    if changed or sanitized != value:
        raise ValueError("invalid broker response")
    return value


class _BrokerProvenance(_StrictModel):
    sandbox_id: str
    image_uri: str
    image_digest: str
    secure_runtime: Literal["gvisor"]
    vina_version: str
    meeko_version: str
    receptor_sha256: str
    ligand_sha256: str
    cleanup_status: Literal["succeeded"]
    demo_mode: StrictBool
    fallback_used: StrictBool

    @field_validator("image_digest", "receptor_sha256", "ligand_sha256")
    @classmethod
    def validate_digest(cls, value: str) -> str:
        if type(value) is not str or _SHA256_PATTERN.fullmatch(value) is None:
            raise ValueError("invalid broker response")
        return value

    @field_validator("sandbox_id", "image_uri")
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        return _safe_metadata(value)

    @field_validator("vina_version", "meeko_version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        if type(value) is not str or _VERSION_PATTERN.fullmatch(value) is None:
            raise ValueError("invalid broker response")
        return value

    @field_validator("demo_mode", "fallback_used")
    @classmethod
    def validate_real_execution(cls, value: bool) -> bool:
        if value is not False:
            raise ValueError("invalid broker response")
        return value


class _Artifact(_StrictModel):
    artifact_id: str
    media_type: Literal["chemical/x-pdbqt"]
    sha256: str
    size_bytes: int = Field(gt=0, le=_ARTIFACT_MAX_BYTES)

    @field_validator("artifact_id")
    @classmethod
    def validate_artifact_id(cls, value: str) -> str:
        if type(value) is not str or _UUID_HEX_PATTERN.fullmatch(value) is None:
            raise ValueError("invalid broker response")
        return value

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        if type(value) is not str or _SHA256_PATTERN.fullmatch(value) is None:
            raise ValueError("invalid broker response")
        return value


class _Manifest(_StrictModel):
    schema_version: Literal[1]
    job_id: str
    trace_id: str
    pose_count: int = Field(gt=0, le=256)
    best_energy: float
    artifacts: list[_Artifact]
    warnings: list[str]
    provenance: _BrokerProvenance

    @field_validator("job_id")
    @classmethod
    def validate_job_id(cls, value: str) -> str:
        if type(value) is not str or _UUID_HEX_PATTERN.fullmatch(value) is None:
            raise ValueError("invalid broker response")
        return value

    @field_validator("trace_id")
    @classmethod
    def validate_trace_id(cls, value: str) -> str:
        if type(value) is not str or _TRACE_PATTERN.fullmatch(value) is None:
            raise ValueError("invalid broker response")
        return value

    @field_validator("best_energy", mode="before")
    @classmethod
    def validate_best_energy(cls, value: object) -> float:
        if type(value) not in (int, float) or not math.isfinite(float(value)):
            raise ValueError("invalid broker response")
        return float(value)

    @field_validator("artifacts")
    @classmethod
    def validate_single_pose(cls, value: list[_Artifact]) -> list[_Artifact]:
        if len(value) != 1:
            raise ValueError("invalid broker response")
        return value

    @field_validator("warnings")
    @classmethod
    def validate_warnings(cls, value: list[str]) -> list[str]:
        if len(value) > 64:
            raise ValueError("invalid broker response")
        return [_safe_metadata(item, maximum=1024) for item in value]


class _PublicJob(_StrictModel):
    job_id: str
    trace_id: str
    status: BrokerJobStatus
    phase: str
    error_code: BrokerErrorCode | None
    warnings: list[str]
    provenance: _BrokerProvenance | None
    cancel_requested: StrictBool
    cleanup_status: str
    created_at: float
    updated_at: float

    @field_validator("status", mode="before")
    @classmethod
    def validate_status(cls, value: object) -> BrokerJobStatus:
        if type(value) is not str:
            raise ValueError("invalid broker response")
        return BrokerJobStatus(value)

    @field_validator("job_id")
    @classmethod
    def validate_job_id(cls, value: str) -> str:
        if type(value) is not str or _UUID_HEX_PATTERN.fullmatch(value) is None:
            raise ValueError("invalid broker response")
        return value

    @field_validator("trace_id")
    @classmethod
    def validate_trace_id(cls, value: str) -> str:
        if type(value) is not str or _TRACE_PATTERN.fullmatch(value) is None:
            raise ValueError("invalid broker response")
        return value

    @field_validator("phase", "cleanup_status")
    @classmethod
    def validate_metadata(cls, value: str) -> str:
        return _safe_metadata(value)

    @field_validator("error_code", mode="before")
    @classmethod
    def validate_error_code(cls, value: object) -> BrokerErrorCode | None:
        if value is None:
            return None
        if type(value) is not str:
            raise ValueError("invalid broker response")
        try:
            return BrokerErrorCode(value)
        except ValueError:
            raise ValueError("invalid broker response") from None

    @field_validator("warnings")
    @classmethod
    def validate_warnings(cls, value: list[str]) -> list[str]:
        if len(value) > 64:
            raise ValueError("invalid broker response")
        return [_safe_metadata(item, maximum=1024) for item in value]

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def validate_timestamp(cls, value: object) -> float:
        if type(value) not in (int, float) or not math.isfinite(float(value)):
            raise ValueError("invalid broker response")
        return float(value)


def _reject_json_constant(_value: str) -> None:
    raise ValueError("invalid broker response")


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("invalid broker response")
        result[key] = value
    return result


def _check_absolute_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise TimeoutError("sandbox docking deadline exceeded")


def _deadline_timeout(template: httpx.Timeout, deadline: float) -> httpx.Timeout:
    _check_absolute_deadline(deadline)
    remaining = deadline - time.monotonic()

    def clipped(value: float | None) -> float:
        return remaining if value is None else min(value, remaining)

    return httpx.Timeout(
        connect=clipped(template.connect),
        read=clipped(template.read),
        write=clipped(template.write),
        pool=clipped(template.pool),
    )


def _remaining_timeout(
    deadline: float,
    requested: float | None,
    timeout_error: type[Exception],
) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise timeout_error
    return remaining if requested is None else min(requested, remaining)


class _AbsoluteDeadlineNetworkStream:
    """Recompute the absolute deadline before every blocking socket operation."""

    def __init__(self, stream: Any, *, deadline: float) -> None:
        self._stream = stream
        self._deadline = deadline
        candidate = stream.get_extra_info("socket")
        self._socket = candidate if isinstance(candidate, socket.socket) else None

    def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        bounded = _remaining_timeout(self._deadline, timeout, httpcore.ReadTimeout)
        if self._socket is None:
            content = self._stream.read(max_bytes, timeout=bounded)
        else:
            try:
                self._socket.settimeout(bounded)
                content = self._socket.recv(max_bytes)
            except socket.timeout:
                raise httpcore.ReadTimeout from None
            except OSError:
                raise httpcore.ReadError from None
        if time.monotonic() >= self._deadline:
            self.close()
            raise httpcore.ReadTimeout
        return content

    def write(self, buffer: bytes, timeout: float | None = None) -> None:
        if self._socket is None:
            bounded = _remaining_timeout(
                self._deadline,
                timeout,
                httpcore.WriteTimeout,
            )
            self._stream.write(buffer, timeout=bounded)
            if time.monotonic() >= self._deadline:
                self.close()
                raise httpcore.WriteTimeout
            return
        view = memoryview(buffer)
        while view:
            bounded = _remaining_timeout(
                self._deadline,
                timeout,
                httpcore.WriteTimeout,
            )
            try:
                self._socket.settimeout(bounded)
                written = self._socket.send(view)
            except socket.timeout:
                raise httpcore.WriteTimeout from None
            except OSError:
                raise httpcore.WriteError from None
            if written <= 0:
                raise httpcore.WriteError
            view = view[written:]

    def close(self) -> None:
        self._stream.close()

    def start_tls(
        self,
        ssl_context: Any,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> "_AbsoluteDeadlineNetworkStream":
        bounded = _remaining_timeout(
            self._deadline,
            timeout,
            httpcore.ConnectTimeout,
        )
        stream = self._stream.start_tls(
            ssl_context,
            server_hostname=server_hostname,
            timeout=bounded,
        )
        return _AbsoluteDeadlineNetworkStream(stream, deadline=self._deadline)

    def get_extra_info(self, info: str) -> Any:
        return self._stream.get_extra_info(info)


class _AbsoluteDeadlineNetworkBackend:
    """UDS-only httpcore backend carrying one immutable absolute deadline."""

    def __init__(self, backend: Any, *, deadline: float, socket_path: str) -> None:
        self._backend = backend
        self._deadline = deadline
        self._socket_path = socket_path

    def connect_tcp(self, *_args: Any, **_kwargs: Any) -> Any:
        raise httpcore.ConnectError

    def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Any = None,
    ) -> _AbsoluteDeadlineNetworkStream:
        if path != self._socket_path:
            raise httpcore.ConnectError
        bounded = _remaining_timeout(
            self._deadline,
            timeout,
            httpcore.ConnectTimeout,
        )
        stream = self._backend.connect_unix_socket(
            path,
            timeout=bounded,
            socket_options=socket_options,
        )
        if time.monotonic() >= self._deadline:
            stream.close()
            raise httpcore.ConnectTimeout
        return _AbsoluteDeadlineNetworkStream(stream, deadline=self._deadline)


def _rename_noreplace(
    source: Path | str,
    destination: Path | str,
    parent_descriptor: int | None,
) -> None:
    """Atomically publish without replacement on Linux and Windows."""

    if parent_descriptor is None:
        if os.name == "posix":
            raise ValueError("unsafe artifact output")
        os.rename(source, destination)
        return
    if os.name != "posix":
        raise ValueError("unsafe artifact output")
    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise ValueError("unsafe artifact output")
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    result = renameat2(
        parent_descriptor,
        os.fsencode(source),
        parent_descriptor,
        os.fsencode(destination),
        _RENAME_NOREPLACE,
    )
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number))


def _json_payload(response: httpx.Response, deadline: float) -> object:
    media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    lengths = response.headers.get_list("content-length")
    if (
        media_type != "application/json"
        or "content-encoding" in response.headers
        or len(lengths) > 1
        or (
            lengths
            and (
                not lengths[0].isascii()
                or not lengths[0].isdigit()
                or int(lengths[0]) > _JSON_MAX_BYTES
            )
        )
    ):
        raise ValueError("invalid broker response")
    content = bytearray()
    if response.is_stream_consumed:
        _check_absolute_deadline(deadline)
        content.extend(response.content)
        if len(content) > _JSON_MAX_BYTES:
            raise ValueError("invalid broker response")
    else:
        iterator = response.iter_raw()
        while True:
            _check_absolute_deadline(deadline)
            try:
                chunk = next(iterator)
            except StopIteration:
                break
            _check_absolute_deadline(deadline)
            if type(chunk) is not bytes or not chunk:
                raise ValueError("invalid broker response")
            content.extend(chunk)
            if len(content) > _JSON_MAX_BYTES:
                raise ValueError("invalid broker response")
    _check_absolute_deadline(deadline)
    if not content or (lengths and int(lengths[0]) != len(content)):
        raise ValueError("invalid broker response")
    try:
        return json.loads(
            bytes(content).decode("utf-8", errors="strict"),
            parse_constant=_reject_json_constant,
            object_pairs_hook=_unique_json_object,
        )
    except (UnicodeError, ValueError):
        raise ValueError("invalid broker response") from None


def _response_model(
    response: httpx.Response,
    *,
    status_code: int,
    model: type[_StrictModel],
    deadline: float,
    stage: Literal["submit", "poll", "manifest", "cancel"],
) -> _StrictModel:
    if response.status_code != status_code or response.is_redirect:
        raise _BrokerHttpFailure(response.status_code, stage)
    try:
        validated = model.model_validate(_json_payload(response, deadline))
        _check_absolute_deadline(deadline)
        return validated
    except (TimeoutError, httpx.TimeoutException):
        raise
    except Exception:
        raise ValueError("invalid broker response") from None


def _file_snapshot(path_value: object, suffixes: frozenset[str], limit: int) -> tuple[bytes, str]:
    if type(path_value) is not str:
        raise ValueError("unsafe docking input")
    path = Path(path_value)
    if (
        not path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts[1:])
        or path.suffix.lower() not in suffixes
    ):
        raise ValueError("unsafe docking input")
    try:
        before = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_ISLNK(before.st_mode)
            or before.st_nlink != 1
            or before.st_size <= 0
            or before.st_size > limit
            or bool(getattr(before, "st_file_attributes", 0) & 0x400)
        ):
            raise ValueError
        snapshot = read_file_snapshot(path, limit)
        after = path.lstat()
        identity = (_stat_identity(before), _stat_version(before))
        if (
            identity != snapshot.identity
            or identity != (_stat_identity(after), _stat_version(after))
            or after.st_nlink != 1
            or len(snapshot.content) != before.st_size
        ):
            raise ValueError
        return snapshot.content, snapshot.sha256
    except Exception:
        raise ValueError("unsafe docking input") from None


def _cancelled(cancel_event: Any) -> bool:
    if cancel_event is None:
        return False
    try:
        return bool(cancel_event.is_set())
    except Exception:
        return True


def _emit_progress(callback: Callable[..., Any] | None, status: BrokerJobStatus) -> None:
    if callback is None:
        return
    phases = {
        BrokerJobStatus.QUEUED: ("sandbox_queued", 10.0),
        BrokerJobStatus.PROVISIONING: ("sandbox_provisioning", 20.0),
        BrokerJobStatus.UPLOADING: ("sandbox_uploading", 35.0),
        BrokerJobStatus.RUNNING: ("vina_running", 60.0),
        BrokerJobStatus.VALIDATING: ("sandbox_validating", 70.0),
        BrokerJobStatus.SUCCEEDED: ("sandbox_succeeded", 72.0),
    }
    phase = phases.get(status)
    if phase is None:
        return
    try:
        callback(*phase)
    except Exception:
        pass


def _broker_error_result(
    code: BrokerErrorCode,
    warnings: tuple[str, ...] = (),
) -> ToolResult:
    quality = {"real_execution": False, "execution_backend": "opensandbox"}
    if code is BrokerErrorCode.CANCELLED:
        return ToolResult.error_result(
            "molecular_docking",
            AgentErrorCode.CANCELLED,
            "Docking execution was cancelled in OpenSandbox.",
            status=ObservationStatus.CANCELLED,
            quality=quality,
        )
    if code in {BrokerErrorCode.EXECUTION_TIMEOUT, BrokerErrorCode.EXPIRED}:
        return ToolResult.error_result(
            "molecular_docking",
            AgentErrorCode.TOOL_TIMEOUT,
            "OpenSandbox docking exceeded its execution deadline.",
            details={"reason": "sandbox_timeout"},
            quality=quality,
        )
    if code in {
        BrokerErrorCode.OPENSANDBOX_UNAVAILABLE,
        BrokerErrorCode.PROVISIONING_FAILED,
        BrokerErrorCode.UPLOAD_FAILED,
        BrokerErrorCode.CLEANUP_FAILED,
    }:
        return ToolResult.error_result(
            "molecular_docking",
            AgentErrorCode.EXTERNAL_TOOL_UNAVAILABLE,
            "The OpenSandbox docking environment is unavailable.",
            details={"reason": "opensandbox_unavailable"},
            quality=quality,
        )
    if code is BrokerErrorCode.QUEUE_SATURATED:
        return ToolResult.error_result(
            "molecular_docking",
            AgentErrorCode.EXTERNAL_TOOL_UNAVAILABLE,
            "The OpenSandbox docking queue is unavailable.",
            details={"reason": "sandbox_queue_saturated"},
            quality=quality,
        )
    if code is BrokerErrorCode.UNAUTHORIZED:
        return ToolResult.error_result(
            "molecular_docking",
            AgentErrorCode.UNAUTHORIZED_TOOL,
            "The OpenSandbox docking request was not authorized.",
            details={"reason": "opensandbox_unauthorized"},
            quality=quality,
        )
    if code in {
        BrokerErrorCode.INVALID_INPUT,
        BrokerErrorCode.IDEMPOTENCY_CONFLICT,
    }:
        return ToolResult.error_result(
            "molecular_docking",
            AgentErrorCode.INVALID_INPUT,
            "The OpenSandbox docking request was rejected.",
            details={
                "reason": (
                    "idempotency_conflict"
                    if code is BrokerErrorCode.IDEMPOTENCY_CONFLICT
                    else "input_invalid"
                )
            },
            quality=quality,
        )
    if code is BrokerErrorCode.COMMAND_FAILED and "sandbox_tool_failed" in warnings:
        return ToolResult.error_result(
            "molecular_docking",
            AgentErrorCode.PROVIDER_ERROR,
            "The sandboxed scientific tool failed before producing a verified result.",
            details={"reason": "sandbox_tool_failed"},
            quality=quality,
        )
    return ToolResult.error_result(
        "molecular_docking",
        AgentErrorCode.INVALID_OUTPUT,
        "OpenSandbox docking did not produce a verified scientific artifact.",
        details={"reason": "artifact_invalid"},
        quality=quality,
    )


def _http_failure_code(
    status_code: int,
    stage: Literal["submit", "poll", "manifest", "artifact", "cancel"],
) -> BrokerErrorCode:
    if status_code in {401, 403}:
        return BrokerErrorCode.UNAUTHORIZED
    if status_code == 429:
        return BrokerErrorCode.QUEUE_SATURATED
    if status_code in {404, 405}:
        return BrokerErrorCode.OPENSANDBOX_UNAVAILABLE
    if stage == "submit" and status_code == 409:
        return BrokerErrorCode.IDEMPOTENCY_CONFLICT
    if stage == "submit" and status_code in {400, 415, 422}:
        return BrokerErrorCode.INVALID_INPUT
    return BrokerErrorCode.OPENSANDBOX_UNAVAILABLE


def _with_failure_elapsed(result: ToolResult, started: float) -> ToolResult:
    if not result.success:
        try:
            delta = time.monotonic() - started
            elapsed_ms = max(0, int(delta * 1000)) if math.isfinite(delta) else 0
        except Exception:
            elapsed_ms = 0
        result.elapsed_ms = elapsed_ms
    return result


def _initial_clock_sample() -> float | None:
    try:
        value = time.monotonic()
        if type(value) not in (int, float) or not math.isfinite(value):
            return None
        return float(value)
    except Exception:
        return None


class SandboxDockingRunner:
    """Submit verified docking inputs to a broker reachable only over one UDS."""

    def __init__(
        self,
        socket_path: str | os.PathLike[str],
        allowed_output_root: str | os.PathLike[str],
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        socket = Path(socket_path)
        output = Path(os.path.abspath(allowed_output_root))
        if (
            not socket.is_absolute()
            or any(part in {"", ".", ".."} for part in socket.parts[1:])
            or not output.is_absolute()
            or any(part in {"", ".", ".."} for part in output.parts[1:])
        ):
            raise ValueError("invalid sandbox runner configuration")
        self.socket_path = socket
        self._allowed_output_root = output
        self._transport_injected = transport is not None
        self._transport = transport

    @classmethod
    def from_env(
        cls,
        allowed_output_root: str | os.PathLike[str],
    ) -> "SandboxDockingRunner":
        value = os.environ.get("MEDCHAT_SANDBOX_BROKER_SOCKET")
        if value is None or not value.strip():
            raise ValueError("sandbox broker socket is not configured")
        return cls(Path(value), allowed_output_root)

    def execute(
        self,
        payload: Mapping[str, Any],
        *,
        job_id: str,
        progress_callback: Callable[..., Any] | None = None,
        cancel_event: Any = None,
    ) -> ToolResult:
        started = _initial_clock_sample()
        if started is None:
            return ToolResult.error_result(
                "molecular_docking",
                AgentErrorCode.INTERNAL_ERROR,
                "OpenSandbox docking could not start because the process clock was unavailable.",
                details={"reason": "process_failed"},
                elapsed_ms=0,
                quality={"real_execution": False, "execution_backend": "opensandbox"},
            )
        result = self._execute(
            payload,
            job_id=job_id,
            progress_callback=progress_callback,
            cancel_event=cancel_event,
            started=started,
        )
        return _with_failure_elapsed(result, started)

    def _execute(
        self,
        payload: Mapping[str, Any],
        *,
        job_id: str,
        progress_callback: Callable[..., Any] | None,
        cancel_event: Any,
        started: float,
    ) -> ToolResult:
        broker_job_id: str | None = None
        completed = False
        pose_published = False
        deadline = started + _TOTAL_DEADLINE_SECONDS
        try:
            if type(job_id) is not str or _JOB_ID_PATTERN.fullmatch(job_id) is None:
                raise ValueError("invalid docking job")
            self._preflight_output_job_path(job_id)
            if not isinstance(payload, Mapping):
                raise ValueError("invalid docking payload")
            if "smiles" in payload:
                return ToolResult.error_result(
                    "molecular_docking",
                    AgentErrorCode.INVALID_INPUT,
                    "SMILES docking is not supported by the OpenSandbox runner.",
                    details={"reason": "smiles_not_supported_by_opensandbox"},
                    quality={
                        "real_execution": False,
                        "execution_backend": "opensandbox",
                    },
                )
            try:
                if type(payload.get("ligand_path")) is not str:
                    raise ValueError
                receptor, receptor_sha256 = _file_snapshot(
                    payload.get("receptor_path"),
                    _REMOTE_RECEPTOR_SUFFIXES,
                    _RECEPTOR_MAX_BYTES,
                )
                ligand, ligand_sha256 = _file_snapshot(
                    payload.get("ligand_path"),
                    _REMOTE_LIGAND_SUFFIXES,
                    _LIGAND_MAX_BYTES,
                )
                parameters = self._parameters(payload)
            except ValueError:
                return _broker_error_result(BrokerErrorCode.INVALID_INPUT)
            if _cancelled(cancel_event):
                raise _Cancelled

            with httpx.Client(
                transport=self._new_transport(deadline),
                base_url=_BASE_URL,
                timeout=_POLL_TIMEOUT,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                with client.stream(
                    "POST",
                    "/v1/docking/jobs",
                    headers={"Idempotency-Key": job_id},
                    data={
                        "request_json": json.dumps(
                            parameters,
                            separators=(",", ":"),
                            allow_nan=False,
                        )
                    },
                    files={
                        "receptor": (
                            f"receptor{Path(str(payload['receptor_path'])).suffix.lower()}",
                            receptor,
                            "application/octet-stream",
                        ),
                        "ligand": (
                            f"ligand{Path(str(payload['ligand_path'])).suffix.lower()}",
                            ligand,
                            "application/octet-stream",
                        ),
                    },
                    timeout=_deadline_timeout(_SUBMIT_TIMEOUT, deadline),
                ) as response:
                    submitted = _response_model(
                        response,
                        status_code=202,
                        model=_PublicJob,
                        deadline=deadline,
                        stage="submit",
                    )
                assert isinstance(submitted, _PublicJob)
                broker_job_id = submitted.job_id
                trace_id = submitted.trace_id
                current = submitted
                _emit_progress(progress_callback, current.status)
                while current.status not in TERMINAL_STATUSES:
                    if _cancelled(cancel_event):
                        raise _Cancelled
                    self._check_deadline(deadline)
                    poll_remaining = deadline - time.monotonic()
                    time.sleep(min(_POLL_SECONDS, poll_remaining / 2))
                    self._check_deadline(deadline)
                    with client.stream(
                        "GET",
                        f"/v1/docking/jobs/{broker_job_id}",
                        timeout=_deadline_timeout(_POLL_TIMEOUT, deadline),
                    ) as response:
                        current = _response_model(
                            response,
                            status_code=200,
                            model=_PublicJob,
                            deadline=deadline,
                            stage="poll",
                        )
                    assert isinstance(current, _PublicJob)
                    if current.job_id != broker_job_id or current.trace_id != trace_id:
                        raise ValueError("invalid broker response")
                    _emit_progress(progress_callback, current.status)

                if current.status is BrokerJobStatus.CANCELLED:
                    raise _Cancelled
                if current.status is not BrokerJobStatus.SUCCEEDED:
                    if current.error_code is not None:
                        raise _BrokerFailure(
                            current.error_code,
                            tuple(current.warnings),
                        )
                    raise ValueError("sandbox docking did not succeed")
                if (
                    current.error_code is not None
                    or current.cancel_requested
                    or current.cleanup_status != "succeeded"
                    or current.provenance is None
                ):
                    raise ValueError("sandbox docking did not succeed")
                if _cancelled(cancel_event):
                    raise _Cancelled

                with client.stream(
                    "GET",
                    f"/v1/docking/jobs/{broker_job_id}/manifest",
                    timeout=_deadline_timeout(_POLL_TIMEOUT, deadline),
                ) as response:
                    manifest = _response_model(
                        response,
                        status_code=200,
                        model=_Manifest,
                        deadline=deadline,
                        stage="manifest",
                    )
                assert isinstance(manifest, _Manifest)
                if (
                    manifest.job_id != broker_job_id
                    or manifest.trace_id != trace_id
                    or manifest.provenance != current.provenance
                    or manifest.warnings != current.warnings
                    or manifest.provenance.receptor_sha256 != receptor_sha256
                    or manifest.provenance.ligand_sha256 != ligand_sha256
                ):
                    raise ValueError("invalid broker manifest identity")
                if _cancelled(cancel_event):
                    raise _Cancelled
                artifact = manifest.artifacts[0]
                pose_path, pose_published = self._download_and_publish(
                    client,
                    broker_job_id,
                    artifact,
                    manifest,
                    job_id,
                    deadline,
                    cancel_event,
                )

            if _cancelled(cancel_event):
                if pose_published:
                    self._discard_uncommitted_pose(pose_path, artifact)
                raise _Cancelled

            elapsed_ms = max(0, int((time.monotonic() - started) * 1000))
            provenance = manifest.provenance
            data = {
                "job_id": job_id,
                "total_poses": manifest.pose_count,
                "best_pose": {
                    "binding_energy": manifest.best_energy,
                    "pose_file": str(pose_path),
                },
                "pose_file": str(pose_path),
                "warnings": list(manifest.warnings),
            }
            result = ToolResult.success_result(
                "molecular_docking",
                message="Real AutoDock Vina docking completed in OpenSandbox.",
                data=data,
                elapsed_ms=elapsed_ms,
                warnings=list(manifest.warnings),
                artifacts=[
                    WorkflowArtifact(
                        artifact_type="docking_pose",
                        path=str(pose_path),
                        label="Docking pose",
                        mime_type="chemical/x-pdbqt",
                        metadata={"sha256": artifact.sha256},
                    )
                ],
                quality={
                    "real_execution": True,
                    "execution_backend": "opensandbox",
                    "secure_runtime": "gvisor",
                    "sandbox_image_digest": provenance.image_digest,
                    "cleanup_status": provenance.cleanup_status,
                    "docking_inputs": {
                        "receptor_provided": True,
                        "ligand_provided": True,
                    },
                },
                provenance=ToolProvenance(
                    tool_name="molecular_docking",
                    tool_version=provenance.vina_version,
                    model_name="AutoDock Vina",
                    model_version=provenance.vina_version,
                    demo_mode=False,
                    fallback_used=False,
                ),
            )
            if _cancelled(cancel_event):
                if pose_published:
                    self._discard_uncommitted_pose(pose_path, artifact)
                raise _Cancelled
            completed = True
            return result
        except _Cancelled:
            return ToolResult.error_result(
                "molecular_docking",
                AgentErrorCode.CANCELLED,
                "Docking execution was cancelled in OpenSandbox.",
                status=ObservationStatus.CANCELLED,
                quality={"real_execution": False, "execution_backend": "opensandbox"},
            )
        except _BrokerFailure as exc:
            return _broker_error_result(exc.code, exc.warnings)
        except _BrokerHttpFailure as exc:
            return _broker_error_result(
                _http_failure_code(exc.status_code, exc.stage)
            )
        except httpx.ConnectTimeout:
            return _broker_error_result(BrokerErrorCode.OPENSANDBOX_UNAVAILABLE)
        except (TimeoutError, httpx.TimeoutException):
            return ToolResult.error_result(
                "molecular_docking",
                AgentErrorCode.TOOL_TIMEOUT,
                "OpenSandbox docking exceeded its execution deadline.",
                details={"reason": "sandbox_timeout"},
                quality={"real_execution": False, "execution_backend": "opensandbox"},
            )
        except httpx.ConnectError:
            return _broker_error_result(BrokerErrorCode.OPENSANDBOX_UNAVAILABLE)
        except Exception:
            return ToolResult.error_result(
                "molecular_docking",
                AgentErrorCode.INVALID_OUTPUT,
                "OpenSandbox docking failed before a verified result was produced.",
                details={"reason": "sandbox_execution_failed"},
                quality={"real_execution": False, "execution_backend": "opensandbox"},
            )
        finally:
            if broker_job_id is not None and not completed:
                self._cancel_best_effort(broker_job_id)

    @staticmethod
    def _parameters(payload: Mapping[str, Any]) -> dict[str, Any]:
        expected = {
            "receptor_path",
            "ligand_path",
            "center",
            "size",
            "exhaustiveness",
            "num_modes",
            "energy_range",
        }
        if set(payload) != expected:
            raise ValueError("invalid docking payload")
        from src.sandbox_broker.models import DockingParameters

        parameters = DockingParameters.model_validate(
            {key: payload[key] for key in expected - {"receptor_path", "ligand_path"}}
        )
        return parameters.model_dump(mode="json")

    @staticmethod
    def _check_deadline(deadline: float) -> None:
        _check_absolute_deadline(deadline)

    def _cancel_best_effort(self, broker_job_id: str) -> None:
        try:
            deadline = time.monotonic() + _CANCEL_DEADLINE_SECONDS
            with httpx.Client(
                transport=self._new_transport(deadline),
                base_url=_BASE_URL,
                timeout=_POLL_TIMEOUT,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                with client.stream(
                    "POST",
                    f"/v1/docking/jobs/{broker_job_id}/cancel",
                    timeout=_deadline_timeout(_POLL_TIMEOUT, deadline),
                ) as response:
                    _response_model(
                        response,
                        status_code=202,
                        model=_PublicJob,
                        deadline=deadline,
                        stage="cancel",
                    )
        except Exception:
            pass

    def _new_transport(self, deadline: float) -> httpx.BaseTransport:
        if self._transport_injected:
            assert self._transport is not None
            return self._transport
        transport = httpx.HTTPTransport(uds=str(self.socket_path))
        if not isinstance(transport, _HTTP_TRANSPORT_TYPE):
            try:
                close = getattr(transport, "close", None)
                if callable(close):
                    close()
            finally:
                raise ValueError("invalid sandbox runner transport")
        try:
            pool = transport._pool
            backend = pool._network_backend
            pool._network_backend = _AbsoluteDeadlineNetworkBackend(
                backend,
                deadline=deadline,
                socket_path=str(self.socket_path),
            )
        except Exception:
            transport.close()
            raise ValueError("invalid sandbox runner transport") from None
        return transport

    def _download_and_publish(
        self,
        client: httpx.Client,
        broker_job_id: str,
        artifact: _Artifact,
        manifest: _Manifest,
        original_job_id: str,
        deadline: float,
        cancel_event: Any,
    ) -> tuple[Path, bool]:
        (
            output_root,
            job_root,
            root_descriptor,
            parent_descriptor,
            root_identity,
            parent_identity,
        ) = self._private_job_directory(original_job_id)
        temporary_name = f".result.{secrets.token_hex(12)}.part"
        temporary = job_root / temporary_name
        final_name = "result.pdbqt"
        final = job_root / "result.pdbqt"
        descriptor: int | None = None
        published = False
        temporary_exists = False
        final_linked = False
        final_identity: tuple[int, ...] | None = None
        try:
            self._verify_output_parent(
                output_root,
                job_root,
                root_descriptor,
                parent_descriptor,
                root_identity,
                parent_identity,
            )
            existing = self._reuse_existing_pose(
                output_root,
                job_root,
                final,
                final_name,
                root_descriptor,
                parent_descriptor,
                root_identity,
                parent_identity,
                artifact,
                manifest,
                deadline,
                cancel_event,
            )
            if existing is not None:
                published = True
                return existing, False
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            flags |= getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
            if parent_descriptor is not None:
                descriptor = os.open(
                    temporary_name,
                    flags,
                    0o600,
                    dir_fd=parent_descriptor,
                )
            else:
                descriptor = os.open(temporary, flags, 0o600)
            temporary_exists = True
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
                raise ValueError("unsafe artifact output")
            digest = hashlib.sha256()
            total = 0
            from src.task_runtime.docking_execution import _VinaPoseStreamValidator

            validator = _VinaPoseStreamValidator(
                expected_pose_count=manifest.pose_count,
                expected_best_energy=manifest.best_energy,
            )
            with client.stream(
                "GET",
                f"/v1/docking/jobs/{broker_job_id}/artifacts/{artifact.artifact_id}",
                timeout=_deadline_timeout(_DOWNLOAD_TIMEOUT, deadline),
            ) as response:
                self._check_deadline(deadline)
                if response.status_code != 200 or response.is_redirect:
                    raise _BrokerHttpFailure(
                        response.status_code,
                        "artifact",
                    )
                lengths = response.headers.get_list("content-length")
                if (
                    len(lengths) != 1
                    or not lengths[0]
                    or not lengths[0].isascii()
                    or not lengths[0].isdigit()
                    or int(lengths[0]) != artifact.size_bytes
                    or int(lengths[0]) > _ARTIFACT_MAX_BYTES
                    or response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    != artifact.media_type
                    or "content-encoding" in response.headers
                ):
                    raise ValueError("invalid artifact response")
                iterator = response.iter_raw()
                while True:
                    self._check_deadline(deadline)
                    try:
                        chunk = next(iterator)
                    except StopIteration:
                        break
                    self._check_deadline(deadline)
                    if type(chunk) is not bytes or not chunk:
                        raise ValueError("invalid artifact response")
                    if _cancelled(cancel_event):
                        raise _Cancelled
                    self._check_deadline(deadline)
                    total += len(chunk)
                    if total > artifact.size_bytes or total > _ARTIFACT_MAX_BYTES:
                        raise ValueError("invalid artifact response")
                    view = memoryview(chunk)
                    while view:
                        written = os.write(descriptor, view)
                        if type(written) is not int or written <= 0:
                            raise OSError("artifact write failed")
                        view = view[written:]
                    digest.update(chunk)
                    validator.feed(chunk)
                self._check_deadline(deadline)
            self._check_deadline(deadline)
            if _cancelled(cancel_event):
                raise _Cancelled
            if total != artifact.size_bytes or digest.hexdigest() != artifact.sha256:
                raise ValueError("invalid artifact identity")
            validator.finish()
            self._check_deadline(deadline)
            if _cancelled(cancel_event):
                raise _Cancelled
            os.fsync(descriptor)
            if hasattr(os, "fchmod"):
                os.fchmod(descriptor, 0o600)
            else:
                os.chmod(temporary, 0o600)
            os.fsync(descriptor)
            after = os.fstat(descriptor)
            named = (
                os.stat(
                    temporary_name,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
                if parent_descriptor is not None
                else temporary.lstat()
            )
            if (
                after.st_nlink != 1
                or (_stat_identity(after), _stat_version(after))
                != (_stat_identity(named), _stat_version(named))
                or (os.name == "posix" and stat.S_IMODE(after.st_mode) != 0o600)
            ):
                raise ValueError("unsafe artifact output")
            self._verify_output_parent(
                output_root,
                job_root,
                root_descriptor,
                parent_descriptor,
                root_identity,
                parent_identity,
            )
            if _cancelled(cancel_event):
                raise _Cancelled
            final_identity = _stat_identity(after)
            if parent_descriptor is None:
                os.close(descriptor)
                descriptor = None
            _rename_noreplace(
                temporary_name if parent_descriptor is not None else temporary,
                final_name if parent_descriptor is not None else final,
                parent_descriptor,
            )
            final_linked = True
            temporary_exists = False
            self._check_deadline(deadline)
            if _cancelled(cancel_event):
                raise _Cancelled
            linked_final = (
                os.stat(
                    final_name,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
                if parent_descriptor is not None
                else final.lstat()
            )
            if (
                not stat.S_ISREG(linked_final.st_mode)
                or stat.S_ISLNK(linked_final.st_mode)
                or bool(getattr(linked_final, "st_file_attributes", 0) & 0x400)
                or linked_final.st_nlink != 1
                or _stat_identity(linked_final) != final_identity
                or linked_final.st_size != artifact.size_bytes
            ):
                raise ValueError("unsafe artifact output")
            final_metadata = (
                os.stat(
                    final_name,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
                if parent_descriptor is not None
                else final.lstat()
            )
            if (
                not stat.S_ISREG(final_metadata.st_mode)
                or final_metadata.st_nlink != 1
                or _stat_identity(final_metadata) != final_identity
                or (
                    os.name == "posix"
                    and stat.S_IMODE(final_metadata.st_mode) != 0o600
                )
            ):
                raise ValueError("unsafe artifact output")
            self._verify_output_parent(
                output_root,
                job_root,
                root_descriptor,
                parent_descriptor,
                root_identity,
                parent_identity,
            )
            if parent_descriptor is not None:
                os.fsync(parent_descriptor)
            self._check_deadline(deadline)
            if _cancelled(cancel_event):
                raise _Cancelled
            published = True
            return final, True
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            if not published and final_linked and final_identity is not None:
                try:
                    current = (
                        os.stat(
                            final_name,
                            dir_fd=parent_descriptor,
                            follow_symlinks=False,
                        )
                        if parent_descriptor is not None
                        else final.lstat()
                    )
                    if _stat_identity(current) == final_identity:
                        if parent_descriptor is not None:
                            os.unlink(final_name, dir_fd=parent_descriptor)
                        else:
                            final.unlink()
                except (FileNotFoundError, OSError):
                    pass
            if not published and temporary_exists:
                try:
                    if parent_descriptor is not None:
                        os.unlink(temporary_name, dir_fd=parent_descriptor)
                    else:
                        temporary.unlink()
                except (FileNotFoundError, OSError):
                    pass
            if parent_descriptor is not None:
                try:
                    os.close(parent_descriptor)
                except OSError:
                    pass
            if root_descriptor is not None:
                try:
                    os.close(root_descriptor)
                except OSError:
                    pass

    def _reuse_existing_pose(
        self,
        output_root: Path,
        job_root: Path,
        final: Path,
        final_name: str,
        root_descriptor: int | None,
        parent_descriptor: int | None,
        root_identity: tuple[int, ...],
        parent_identity: tuple[int, ...],
        artifact: _Artifact,
        manifest: _Manifest,
        deadline: float,
        cancel_event: Any,
    ) -> Path | None:
        from src.task_runtime.docking_execution import _VinaPoseStreamValidator

        self._verify_output_parent(
            output_root,
            job_root,
            root_descriptor,
            parent_descriptor,
            root_identity,
            parent_identity,
        )
        validator = _VinaPoseStreamValidator(
            expected_pose_count=manifest.pose_count,
            expected_best_energy=manifest.best_energy,
        )
        if parent_descriptor is not None:
            flags = (
                os.O_RDONLY
                | getattr(os, "O_BINARY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0)
            )
            descriptor: int | None = None
            try:
                try:
                    descriptor = os.open(
                        final_name,
                        flags,
                        dir_fd=parent_descriptor,
                    )
                except FileNotFoundError:
                    return None
                except OSError:
                    raise ValueError("unsafe artifact output") from None
                before = os.fstat(descriptor)
                named_before = os.stat(
                    final_name,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
                if (
                    not stat.S_ISREG(before.st_mode)
                    or not stat.S_ISREG(named_before.st_mode)
                    or stat.S_ISLNK(named_before.st_mode)
                    or before.st_nlink != 1
                    or named_before.st_nlink != 1
                    or before.st_uid != os.geteuid()
                    or stat.S_IMODE(before.st_mode) != 0o600
                    or before.st_size != artifact.size_bytes
                    or (_stat_identity(before), _stat_version(before))
                    != (_stat_identity(named_before), _stat_version(named_before))
                ):
                    raise ValueError("unsafe artifact output")
                digest = hashlib.sha256()
                total = 0
                while True:
                    self._check_deadline(deadline)
                    if _cancelled(cancel_event):
                        raise _Cancelled
                    chunk = os.read(
                        descriptor,
                        min(64 * 1024, artifact.size_bytes - total + 1),
                    )
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > artifact.size_bytes:
                        raise ValueError("invalid artifact identity")
                    digest.update(chunk)
                    validator.feed(chunk)
                self._check_deadline(deadline)
                if _cancelled(cancel_event):
                    raise _Cancelled
                validator.finish()
                after = os.fstat(descriptor)
                named_after = os.stat(
                    final_name,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
                if (
                    total != artifact.size_bytes
                    or digest.hexdigest() != artifact.sha256
                    or (_stat_identity(after), _stat_version(after))
                    != (_stat_identity(before), _stat_version(before))
                    or (_stat_identity(named_after), _stat_version(named_after))
                    != (_stat_identity(before), _stat_version(before))
                    or named_after.st_nlink != 1
                ):
                    raise ValueError("invalid artifact identity")
            finally:
                if descriptor is not None:
                    os.close(descriptor)
        else:
            try:
                before = final.lstat()
            except FileNotFoundError:
                return None
            if (
                not stat.S_ISREG(before.st_mode)
                or stat.S_ISLNK(before.st_mode)
                or before.st_nlink != 1
                or before.st_size != artifact.size_bytes
                or bool(getattr(before, "st_file_attributes", 0) & 0x400)
            ):
                raise ValueError("unsafe artifact output")
            self._check_deadline(deadline)
            if _cancelled(cancel_event):
                raise _Cancelled
            try:
                snapshot = read_file_snapshot(final, artifact.size_bytes)
                after = final.lstat()
            except Exception:
                raise ValueError("unsafe artifact output") from None
            if (
                snapshot.sha256 != artifact.sha256
                or len(snapshot.content) != artifact.size_bytes
                or snapshot.identity
                != (_stat_identity(before), _stat_version(before))
                or snapshot.identity
                != (_stat_identity(after), _stat_version(after))
                or after.st_nlink != 1
                or bool(getattr(after, "st_file_attributes", 0) & 0x400)
            ):
                raise ValueError("invalid artifact identity")
            validator.feed(snapshot.content)
            validator.finish()
        self._check_deadline(deadline)
        if _cancelled(cancel_event):
            raise _Cancelled
        self._verify_output_parent(
            output_root,
            job_root,
            root_descriptor,
            parent_descriptor,
            root_identity,
            parent_identity,
        )
        return final

    def _private_job_directory(
        self,
        job_id: str,
    ) -> tuple[Path, Path, int | None, int | None, tuple[int, ...], tuple[int, ...]]:
        root = self._allowed_output_root
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        root_metadata = root.lstat()
        if (
            not stat.S_ISDIR(root_metadata.st_mode)
            or stat.S_ISLNK(root_metadata.st_mode)
            or bool(getattr(root_metadata, "st_file_attributes", 0) & 0x400)
            or root.resolve(strict=True) != root
        ):
            raise ValueError("unsafe artifact output")
        job_root = root / f"docking_{job_id}"
        if job_root.parent != root:
            raise ValueError("unsafe artifact output")
        if os.name == "posix":
            if not getattr(os, "O_DIRECTORY", 0) or not getattr(os, "O_NOFOLLOW", 0):
                raise ValueError("unsafe artifact output")
            flags = (
                os.O_RDONLY
                | os.O_DIRECTORY
                | os.O_NOFOLLOW
                | getattr(os, "O_CLOEXEC", 0)
            )
            root_descriptor: int | None = None
            parent_descriptor: int | None = None
            try:
                root_descriptor = os.open(root, flags)
                opened_root = os.fstat(root_descriptor)
                if (
                    not stat.S_ISDIR(opened_root.st_mode)
                    or _stat_identity(opened_root) != _stat_identity(root_metadata)
                ):
                    raise ValueError("unsafe artifact output")
                try:
                    os.mkdir(job_root.name, 0o700, dir_fd=root_descriptor)
                except FileExistsError:
                    pass
                parent_descriptor = os.open(
                    job_root.name,
                    flags,
                    dir_fd=root_descriptor,
                )
                parent_metadata = os.fstat(parent_descriptor)
                named_parent = os.stat(
                    job_root.name,
                    dir_fd=root_descriptor,
                    follow_symlinks=False,
                )
                if (
                    not stat.S_ISDIR(parent_metadata.st_mode)
                    or stat.S_ISLNK(named_parent.st_mode)
                    or _stat_identity(parent_metadata) != _stat_identity(named_parent)
                ):
                    raise ValueError("unsafe artifact output")
                os.fchmod(parent_descriptor, 0o700)
                root_identity = _stat_identity(opened_root)
                parent_identity = _stat_identity(parent_metadata)
                self._verify_output_parent(
                    root,
                    job_root,
                    root_descriptor,
                    parent_descriptor,
                    root_identity,
                    parent_identity,
                )
                return (
                    root,
                    job_root,
                    root_descriptor,
                    parent_descriptor,
                    root_identity,
                    parent_identity,
                )
            except Exception:
                if parent_descriptor is not None:
                    os.close(parent_descriptor)
                if root_descriptor is not None:
                    os.close(root_descriptor)
                raise

        root_identity = _stat_identity(root_metadata)
        job_root.mkdir(mode=0o700, exist_ok=True)
        metadata = job_root.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or bool(getattr(metadata, "st_file_attributes", 0) & 0x400)
            or job_root.resolve(strict=True).parent != root
            or _stat_identity(root.lstat()) != root_identity
        ):
            raise ValueError("unsafe artifact output")
        os.chmod(job_root, 0o700)
        after = job_root.lstat()
        if _stat_identity(after) != _stat_identity(metadata):
            raise ValueError("unsafe artifact output")
        return root, job_root, None, None, root_identity, _stat_identity(after)

    def _preflight_output_job_path(self, job_id: str) -> None:
        """Reject an already-unsafe output path before contacting the broker."""

        root = self._allowed_output_root
        try:
            root_metadata = root.lstat()
        except FileNotFoundError:
            return
        if (
            not stat.S_ISDIR(root_metadata.st_mode)
            or stat.S_ISLNK(root_metadata.st_mode)
            or bool(getattr(root_metadata, "st_file_attributes", 0) & 0x400)
            or root.resolve(strict=True) != root
        ):
            raise ValueError("unsafe artifact output")

        job_root = root / f"docking_{job_id}"
        if job_root.parent != root:
            raise ValueError("unsafe artifact output")
        try:
            metadata = job_root.lstat()
        except FileNotFoundError:
            return
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or bool(getattr(metadata, "st_file_attributes", 0) & 0x400)
            or job_root.resolve(strict=True).parent != root
        ):
            raise ValueError("unsafe artifact output")

    @staticmethod
    def _verify_output_parent(
        root: Path,
        job_root: Path,
        root_descriptor: int | None,
        parent_descriptor: int | None,
        root_identity: tuple[int, ...],
        parent_identity: tuple[int, ...],
    ) -> None:
        root_named = root.lstat()
        parent_named = job_root.lstat()
        if (
            not stat.S_ISDIR(root_named.st_mode)
            or not stat.S_ISDIR(parent_named.st_mode)
            or stat.S_ISLNK(root_named.st_mode)
            or stat.S_ISLNK(parent_named.st_mode)
            or bool(getattr(root_named, "st_file_attributes", 0) & 0x400)
            or bool(getattr(parent_named, "st_file_attributes", 0) & 0x400)
            or _stat_identity(root_named) != root_identity
            or _stat_identity(parent_named) != parent_identity
        ):
            raise ValueError("unsafe artifact output")
        if root_descriptor is not None and parent_descriptor is not None:
            root_open = os.fstat(root_descriptor)
            parent_open = os.fstat(parent_descriptor)
            named_from_root = os.stat(
                job_root.name,
                dir_fd=root_descriptor,
                follow_symlinks=False,
            )
            if (
                _stat_identity(root_open) != root_identity
                or _stat_identity(parent_open) != parent_identity
                or _stat_identity(named_from_root) != parent_identity
            ):
                raise ValueError("unsafe artifact output")

    def _discard_uncommitted_pose(self, path: Path, artifact: _Artifact) -> None:
        try:
            if path.parent.parent != self._allowed_output_root:
                raise ValueError
            snapshot = read_file_snapshot(path, _ARTIFACT_MAX_BYTES)
            metadata = path.lstat()
            if (
                snapshot.sha256 != artifact.sha256
                or len(snapshot.content) != artifact.size_bytes
                or not stat.S_ISREG(metadata.st_mode)
                or stat.S_ISLNK(metadata.st_mode)
                or metadata.st_nlink != 1
                or bool(getattr(metadata, "st_file_attributes", 0) & 0x400)
            ):
                raise ValueError
            identity = _stat_identity(metadata)
            if os.name == "posix":
                flags = (
                    os.O_RDONLY
                    | os.O_DIRECTORY
                    | os.O_NOFOLLOW
                    | getattr(os, "O_CLOEXEC", 0)
                )
                descriptor = os.open(path.parent, flags)
                try:
                    named = os.stat(
                        path.name,
                        dir_fd=descriptor,
                        follow_symlinks=False,
                    )
                    if _stat_identity(named) != identity:
                        raise ValueError
                    os.unlink(path.name, dir_fd=descriptor)
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            else:
                if _stat_identity(path.lstat()) != identity:
                    raise ValueError
                path.unlink()
        except Exception:
            pass
