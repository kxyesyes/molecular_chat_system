"""Fail-closed UDS client for the standalone sandbox docking broker."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import secrets
import stat
import time
from pathlib import Path
from typing import Any, Callable, Literal, Mapping

import httpx
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from src.agent.contracts import (
    AgentErrorCode,
    ObservationStatus,
    ToolProvenance,
    ToolResult,
    WorkflowArtifact,
)
from src.agent.persistence.redaction import sanitize_sensitive_text
from src.sandbox_broker.models import BrokerJobStatus, TERMINAL_STATUSES
from src.task_runtime.config import _stat_identity, _stat_version
from src.task_runtime.secure_io import read_file_snapshot


_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_UUID_HEX_PATTERN = re.compile(r"[0-9a-f]{32}\Z")
_TRACE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_JOB_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_VERSION_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9 .+_()-]{0,127}\Z")
_RECEPTOR_SUFFIXES = frozenset({".pdb", ".pdbqt"})
_LIGAND_SUFFIXES = frozenset({".sdf", ".mol", ".pdb", ".pdbqt"})
_RECEPTOR_MAX_BYTES = 50 * 1024 * 1024
_LIGAND_MAX_BYTES = 10 * 1024 * 1024
_ARTIFACT_MAX_BYTES = 100 * 1024 * 1024
_JSON_MAX_BYTES = 1024 * 1024
_DOWNLOAD_CHUNK_BYTES = 64 * 1024
_POLL_SECONDS = 0.5
_TOTAL_DEADLINE_SECONDS = 330.0
_SUBMIT_TIMEOUT = httpx.Timeout(connect=2.0, read=30.0, write=30.0, pool=2.0)
_POLL_TIMEOUT = httpx.Timeout(connect=2.0, read=5.0, write=5.0, pool=2.0)
_DOWNLOAD_TIMEOUT = httpx.Timeout(connect=2.0, read=15.0, write=5.0, pool=2.0)
_BASE_URL = "http://medchat-sandbox-broker"


class _Cancelled(RuntimeError):
    pass


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
    error_code: str | None
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

    @field_validator("error_code")
    @classmethod
    def validate_error_code(cls, value: str | None) -> str | None:
        return None if value is None else _safe_metadata(value)

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


def _json_payload(response: httpx.Response) -> object:
    content = response.content
    if not content or len(content) > _JSON_MAX_BYTES:
        raise ValueError("invalid broker response")
    media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != "application/json":
        raise ValueError("invalid broker response")
    try:
        return json.loads(
            content.decode("utf-8", errors="strict"),
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
) -> _StrictModel:
    if response.status_code != status_code or response.is_redirect:
        raise ValueError("invalid broker response")
    try:
        return model.model_validate(_json_payload(response))
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
        broker_job_id: str | None = None
        started = time.monotonic()
        deadline = started + _TOTAL_DEADLINE_SECONDS
        try:
            if type(job_id) is not str or _JOB_ID_PATTERN.fullmatch(job_id) is None:
                raise ValueError("invalid docking job")
            if not isinstance(payload, Mapping):
                raise ValueError("invalid docking payload")
            if "smiles" in payload or type(payload.get("ligand_path")) is not str:
                raise ValueError("sandbox docking requires a verified ligand file")
            receptor, receptor_sha256 = _file_snapshot(
                payload.get("receptor_path"), _RECEPTOR_SUFFIXES, _RECEPTOR_MAX_BYTES
            )
            ligand, ligand_sha256 = _file_snapshot(
                payload.get("ligand_path"), _LIGAND_SUFFIXES, _LIGAND_MAX_BYTES
            )
            parameters = self._parameters(payload)
            if _cancelled(cancel_event):
                raise _Cancelled

            with httpx.Client(
                transport=self._new_transport(),
                base_url=_BASE_URL,
                timeout=_POLL_TIMEOUT,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                submitted = _response_model(
                    client.post(
                        "/v1/docking/jobs",
                        headers={"Idempotency-Key": job_id},
                        data={"request_json": json.dumps(parameters, separators=(",", ":"), allow_nan=False)},
                        files={
                            "receptor": (f"receptor{Path(str(payload['receptor_path'])).suffix.lower()}", receptor, "application/octet-stream"),
                            "ligand": (f"ligand{Path(str(payload['ligand_path'])).suffix.lower()}", ligand, "application/octet-stream"),
                        },
                        timeout=_SUBMIT_TIMEOUT,
                    ),
                    status_code=202,
                    model=_PublicJob,
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
                    time.sleep(_POLL_SECONDS)
                    self._check_deadline(deadline)
                    current = _response_model(
                        client.get(
                            f"/v1/docking/jobs/{broker_job_id}",
                            timeout=_POLL_TIMEOUT,
                        ),
                        status_code=200,
                        model=_PublicJob,
                    )
                    assert isinstance(current, _PublicJob)
                    if current.job_id != broker_job_id or current.trace_id != trace_id:
                        raise ValueError("invalid broker response")
                    _emit_progress(progress_callback, current.status)

                if current.status is BrokerJobStatus.CANCELLED:
                    raise _Cancelled
                if (
                    current.status is not BrokerJobStatus.SUCCEEDED
                    or current.error_code is not None
                    or current.cancel_requested
                    or current.cleanup_status != "succeeded"
                    or current.provenance is None
                ):
                    raise ValueError("sandbox docking did not succeed")
                if _cancelled(cancel_event):
                    raise _Cancelled

                manifest = _response_model(
                    client.get(
                        f"/v1/docking/jobs/{broker_job_id}/manifest",
                        timeout=_POLL_TIMEOUT,
                    ),
                    status_code=200,
                    model=_Manifest,
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
                pose_path = self._download_and_publish(
                    client,
                    broker_job_id,
                    artifact,
                    manifest,
                    job_id,
                    deadline,
                    cancel_event,
                )

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
            return ToolResult.success_result(
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
        except _Cancelled:
            if broker_job_id is not None:
                self._cancel_best_effort(broker_job_id)
            return ToolResult.error_result(
                "molecular_docking",
                AgentErrorCode.CANCELLED,
                "Docking execution was cancelled in OpenSandbox.",
                status=ObservationStatus.CANCELLED,
                quality={"real_execution": False, "execution_backend": "opensandbox"},
            )
        except Exception:
            return ToolResult.error_result(
                "molecular_docking",
                AgentErrorCode.INVALID_OUTPUT,
                "OpenSandbox docking failed before a verified result was produced.",
                details={"reason": "sandbox_execution_failed"},
                quality={"real_execution": False, "execution_backend": "opensandbox"},
            )

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
        if time.monotonic() >= deadline:
            raise TimeoutError("sandbox docking deadline exceeded")

    def _cancel_best_effort(self, broker_job_id: str) -> None:
        try:
            with httpx.Client(
                transport=self._new_transport(),
                base_url=_BASE_URL,
                timeout=_POLL_TIMEOUT,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                response = client.post(
                    f"/v1/docking/jobs/{broker_job_id}/cancel",
                    timeout=_POLL_TIMEOUT,
                )
                if response.status_code != 202 or response.is_redirect:
                    return
                _PublicJob.model_validate(_json_payload(response))
        except Exception:
            pass

    def _new_transport(self) -> httpx.BaseTransport:
        if self._transport_injected:
            assert self._transport is not None
            return self._transport
        return httpx.HTTPTransport(uds=str(self.socket_path))

    def _download_and_publish(
        self,
        client: httpx.Client,
        broker_job_id: str,
        artifact: _Artifact,
        manifest: _Manifest,
        original_job_id: str,
        deadline: float,
        cancel_event: Any,
    ) -> Path:
        output_root, job_root = self._private_job_directory(original_job_id)
        parent_identity = _stat_identity(job_root.lstat())
        temporary = job_root / f".result.{secrets.token_hex(12)}.part"
        final = job_root / "result.pdbqt"
        descriptor: int | None = None
        published = False
        replacement_identity: tuple[int, ...] | None = None
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            flags |= getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
            descriptor = os.open(temporary, flags, 0o600)
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
                timeout=_DOWNLOAD_TIMEOUT,
            ) as response:
                if response.status_code != 200 or response.is_redirect:
                    raise ValueError("invalid artifact response")
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
                for chunk in response.iter_raw(chunk_size=_DOWNLOAD_CHUNK_BYTES):
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
            if total != artifact.size_bytes or digest.hexdigest() != artifact.sha256:
                raise ValueError("invalid artifact identity")
            validator.finish()
            os.fsync(descriptor)
            after = os.fstat(descriptor)
            named = temporary.lstat()
            if (
                after.st_nlink != 1
                or (_stat_identity(after), _stat_version(after))
                != (_stat_identity(named), _stat_version(named))
                or _stat_identity(job_root.lstat()) != parent_identity
            ):
                raise ValueError("unsafe artifact output")
            os.close(descriptor)
            descriptor = None
            try:
                existing = final.lstat()
            except FileNotFoundError:
                existing = None
            if existing is not None and (
                not stat.S_ISREG(existing.st_mode)
                or stat.S_ISLNK(existing.st_mode)
                or existing.st_nlink != 1
                or bool(getattr(existing, "st_file_attributes", 0) & 0x400)
            ):
                raise ValueError("unsafe artifact output")
            replacement_identity = _stat_identity(after)
            os.replace(temporary, final)
            os.chmod(final, 0o600)
            final_metadata = final.lstat()
            if (
                not stat.S_ISREG(final_metadata.st_mode)
                or final_metadata.st_nlink != 1
                or final_metadata.st_size != artifact.size_bytes
                or _stat_identity(job_root.lstat()) != parent_identity
                or job_root.parent != output_root
            ):
                raise ValueError("unsafe artifact output")
            if os.name == "posix":
                directory = os.open(
                    job_root,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                )
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            published = True
            return final
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            if not published:
                try:
                    temporary.unlink()
                except (FileNotFoundError, OSError):
                    pass
                if replacement_identity is not None:
                    try:
                        final_metadata = final.lstat()
                        if (
                            stat.S_ISREG(final_metadata.st_mode)
                            and not stat.S_ISLNK(final_metadata.st_mode)
                            and final_metadata.st_nlink == 1
                            and not bool(
                                getattr(final_metadata, "st_file_attributes", 0)
                                & 0x400
                            )
                            and _stat_identity(final_metadata)
                            == replacement_identity
                        ):
                            final.unlink()
                    except (FileNotFoundError, OSError, RuntimeError):
                        pass

    def _private_job_directory(self, job_id: str) -> tuple[Path, Path]:
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
        job_root.mkdir(mode=0o700, exist_ok=True)
        os.chmod(job_root, 0o700)
        metadata = job_root.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or bool(getattr(metadata, "st_file_attributes", 0) & 0x400)
            or job_root.resolve(strict=True).parent != root
        ):
            raise ValueError("unsafe artifact output")
        return root, job_root
