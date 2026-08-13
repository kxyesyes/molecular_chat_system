"""Single verified execution boundary shared by docking runtimes."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import stat
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Callable, Mapping

from src.agent.contracts import (
    AgentErrorCode,
    ObservationStatus,
    ToolProvenance,
    ToolResult,
    WorkflowArtifact,
)
from src.agent.tools.base_tool import execute_tool_compat
from src.agent.tools.molecular_docking import MolecularDocking
from src.agent.validators.result_validator import AgentResultValidator

from .completion import (
    CompletionError,
    DockingCompletionManifest,
    DockingCompletionStore,
)
from .staging import (
    MAX_SMILES_BYTES,
    DockingInputStager,
    ManifestError,
    VerifiedDockingInputs,
)


RawExecutor = Callable[..., ToolResult]


class DockingExecution:
    """Execute or reuse docking only under a verified staging lease."""

    def __init__(
        self,
        root: str | os.PathLike[str],
        raw_executor: RawExecutor | None = None,
    ) -> None:
        self._stager = DockingInputStager(root)
        self._completions = DockingCompletionStore(root)
        self._root = self._stager._root
        self.raw_executor = raw_executor or self._execute_production_tool

    def __repr__(self) -> str:
        return f"{type(self).__name__}(root=<redacted>)"

    @staticmethod
    def _execute_production_tool(payload: Mapping[str, Any], **control: Any) -> ToolResult:
        return execute_tool_compat(MolecularDocking(), payload, **control)

    def run_verified(
        self,
        task_id: str,
        input_manifest: str | os.PathLike[str] | VerifiedDockingInputs,
        *,
        attempt: int = 1,
        progress_callback: Callable[..., Any] | None = None,
        cancel_event: Any = None,
        lease_timeout_seconds: float = 300.0,
    ) -> dict[str, Any]:
        """Return a redacted verified result, never an unvalidated tool payload."""

        if type(attempt) is not int or attempt <= 0:
            return _failure(
                AgentErrorCode.INVALID_INPUT,
                "Docking execution attempt is invalid.",
            )
        if isinstance(input_manifest, VerifiedDockingInputs):
            expected_manifest = self._root / task_id / "input_manifest.json"
            if (
                input_manifest.task_id != task_id
                or input_manifest.manifest_path != expected_manifest
            ):
                return _failure(
                    AgentErrorCode.INVALID_INPUT,
                    "Docking input identity does not match the task.",
                )
            context = nullcontext(input_manifest)
        elif isinstance(input_manifest, (str, os.PathLike)):
            context = self._stager.execution_snapshot(
                task_id,
                input_manifest,
                lease_timeout_seconds=lease_timeout_seconds,
                cancel_check=_cancel_check(cancel_event),
            )
        else:
            return _failure(
                AgentErrorCode.INVALID_INPUT,
                "A verified docking input manifest is required.",
            )

        try:
            with context as inputs:
                return self._run_under_lease(
                    inputs,
                    attempt=attempt,
                    progress_callback=progress_callback,
                    cancel_event=cancel_event,
                )
        except ManifestError:
            if _is_cancelled(cancel_event):
                return _failure(
                    AgentErrorCode.CANCELLED,
                    "Docking execution was cancelled before verification completed.",
                    status=ObservationStatus.CANCELLED,
                )
            return _failure(
                AgentErrorCode.INVALID_INPUT,
                "Docking input verification failed.",
            )
        except CompletionError:
            return _failure(
                AgentErrorCode.INVALID_OUTPUT,
                "Docking completion verification failed.",
            )
        except asyncio.CancelledError:
            return _failure(
                AgentErrorCode.CANCELLED,
                "Docking execution was cancelled.",
                status=ObservationStatus.CANCELLED,
            )
        except Exception:
            return _failure(
                AgentErrorCode.INTERNAL_ERROR,
                "Docking execution failed before a verified result was produced.",
            )

    def _run_under_lease(
        self,
        inputs: VerifiedDockingInputs,
        *,
        attempt: int,
        progress_callback: Callable[..., Any] | None,
        cancel_event: Any,
    ) -> dict[str, Any]:
        inputs.verify_integrity()
        input_hash = _input_hash(inputs)
        config_hash = inputs.config_hash

        if self._completions.has_manifest(inputs.task_id):
            completion = self._completions.load_verified(
                inputs.task_id,
                input_hash=input_hash,
                config_hash=config_hash,
            )
            inputs.verify_integrity()
            return _trusted_result(completion, reused=True)
        if self._completions.has_artifact_residue(inputs.task_id):
            raise CompletionError("completion_conflict")
        if _is_cancelled(cancel_event):
            return _failure(
                AgentErrorCode.CANCELLED,
                "Docking execution was cancelled before the scientific tool started.",
                status=ObservationStatus.CANCELLED,
            )

        payload = _build_payload(inputs)
        try:
            tool_result = self.raw_executor(
                payload,
                job_id=inputs.task_id,
                progress_callback=progress_callback,
                cancel_event=cancel_event,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            return _failure(
                AgentErrorCode.INTERNAL_ERROR,
                "Docking tool execution failed before a scientific result was produced.",
            )
        finally:
            payload.clear()

        if not isinstance(tool_result, ToolResult):
            return _failure(
                AgentErrorCode.INVALID_OUTPUT,
                "Docking tool returned an invalid result contract.",
            )
        if not tool_result.success:
            if (
                tool_result.status == ObservationStatus.CANCELLED
                or (tool_result.error and tool_result.error.code == AgentErrorCode.CANCELLED)
            ):
                return _failure(
                    AgentErrorCode.CANCELLED,
                    "Docking execution was cancelled without a scientific result.",
                    status=ObservationStatus.CANCELLED,
                )
            return _failure(
                tool_result.error.code if tool_result.error else AgentErrorCode.INTERNAL_ERROR,
                "Docking tool did not produce a verified scientific result.",
            )

        inputs.verify_integrity()
        _attach_verified_input_evidence(tool_result, inputs)
        validated = AgentResultValidator().validate_tool_result(tool_result)
        if not validated.success or validated.status != ObservationStatus.SUCCEEDED:
            return _failure(
                AgentErrorCode.INVALID_OUTPUT,
                "Docking evidence failed scientific validation.",
            )
        if _is_cancelled(cancel_event):
            return _failure(
                AgentErrorCode.CANCELLED,
                "Docking execution was cancelled before artifact commit.",
                status=ObservationStatus.CANCELLED,
            )
        inputs.verify_integrity()

        pose_source, pose_count, best_energy = _validated_scientific_values(validated)
        tool_version = (
            validated.provenance.tool_version
            if validated.provenance is not None
            else "1"
        )
        completion = self._completions.commit(
            inputs.task_id,
            input_hash=input_hash,
            config_hash=config_hash,
            tool_version=tool_version,
            pose_source=pose_source,
            pose_count=pose_count,
            best_energy=best_energy,
            validator_status="succeeded",
            attempt=attempt,
        )
        return _trusted_result(completion, reused=False)


def _build_payload(inputs: VerifiedDockingInputs) -> dict[str, Any]:
    config = inputs.config
    payload: dict[str, Any] = {
        "receptor_path": str(inputs.receptor_path),
        "center": list(config["center"]),
        "size": list(config["size"]),
        "exhaustiveness": config["exhaustiveness"],
        "num_modes": config["num_modes"],
        "energy_range": 3.0,
    }
    if inputs.ligand_mode == "smiles":
        payload["smiles"] = _read_verified_smiles(inputs)
    elif inputs.ligand_mode == "file":
        payload["ligand_path"] = str(inputs.ligand_path)
    else:
        raise ManifestError("manifest_schema_invalid")
    return payload


def _read_verified_smiles(inputs: VerifiedDockingInputs) -> str:
    path = inputs.ligand_path
    descriptor: int | None = None
    try:
        before_path = path.lstat()
        if (
            not stat.S_ISREG(before_path.st_mode)
            or before_path.st_nlink != 1
            or _is_reparse(before_path)
            or before_path.st_size != inputs.ligand_size
            or before_path.st_size > MAX_SMILES_BYTES
        ):
            raise ManifestError("manifest_integrity_failed")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(path, flags)
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size != inputs.ligand_size
            or (before.st_dev, before.st_ino) != (before_path.st_dev, before_path.st_ino)
        ):
            raise ManifestError("manifest_integrity_failed")
        content = os.read(descriptor, MAX_SMILES_BYTES + 1)
        after = os.fstat(descriptor)
        if (
            len(content) != before.st_size
            or len(content) > MAX_SMILES_BYTES
            or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            or hashlib.sha256(content).hexdigest() != inputs.ligand_sha256
        ):
            raise ManifestError("manifest_integrity_failed")
        smiles = content.decode("utf-8")
        if not smiles or "\x00" in smiles:
            raise ManifestError("manifest_integrity_failed")
        return smiles
    except ManifestError:
        raise
    except (OSError, UnicodeError, ValueError):
        raise ManifestError("manifest_integrity_failed") from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _attach_verified_input_evidence(
    result: ToolResult,
    inputs: VerifiedDockingInputs,
) -> None:
    result.quality = {
        **result.quality,
        "docking_inputs": {
            "receptor_provided": True,
            "ligand_provided": True,
            "ligand_mode": inputs.ligand_mode,
            "center": list(inputs.config["center"]),
            "size": list(inputs.config["size"]),
        },
    }


def _validated_scientific_values(result: ToolResult) -> tuple[Path, int, float]:
    if result.tool_name != "molecular_docking" or not isinstance(result.data, dict):
        raise CompletionError("completion_schema_invalid")
    data = result.data
    best_pose = data.get("best_pose")
    if not isinstance(best_pose, dict):
        raise CompletionError("completion_schema_invalid")
    pose_count = data.get("total_poses")
    energy = best_pose.get("binding_energy")
    pose_value = (
        best_pose.get("pose_file")
        or best_pose.get("output_file")
        or data.get("pose_file")
        or data.get("output_file")
    )
    if type(pose_count) is not int or pose_count <= 0:
        raise CompletionError("completion_schema_invalid")
    if type(energy) not in (int, float) or not math.isfinite(float(energy)):
        raise CompletionError("completion_schema_invalid")
    if not isinstance(pose_value, (str, os.PathLike)):
        raise CompletionError("completion_artifact_invalid")
    return Path(pose_value), pose_count, float(energy)


def _input_hash(inputs: VerifiedDockingInputs) -> str:
    identity = {
        "ligand": {
            "mode": inputs.ligand_mode,
            "sha256": inputs.ligand_sha256,
            "size": inputs.ligand_size,
        },
        "receptor": {
            "sha256": inputs.receptor_sha256,
            "size": inputs.receptor_size,
        },
    }
    encoded = json.dumps(
        identity,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _trusted_result(
    completion: DockingCompletionManifest,
    *,
    reused: bool,
) -> dict[str, Any]:
    relative_pose = completion.pose.path
    result = ToolResult.success_result(
        "molecular_docking",
        message=(
            "Verified docking completion reused."
            if reused
            else "Docking completed and passed scientific validation."
        ),
        data={
            "total_poses": completion.pose_count,
            "pose_file": relative_pose,
            "best_pose": {
                "binding_energy": completion.best_energy,
                "pose_file": relative_pose,
            },
        },
        artifacts=[
            WorkflowArtifact(
                artifact_type="docking_pose",
                path=relative_pose,
                label="Verified docking pose",
                mime_type="chemical/x-pdbqt",
                metadata={"sha256": completion.pose.sha256},
            )
        ],
        quality={
            "validated": True,
            "validator_status": completion.validator_status,
            "completion_schema": completion.schema,
            "attempt": completion.attempt,
        },
        provenance=ToolProvenance(
            tool_name="molecular_docking",
            tool_version=completion.tool_version,
            demo_mode=False,
            fallback_used=False,
            input_digest=completion.input_hash,
            output_digest=completion.pose.sha256,
        ),
    ).to_legacy_dict()
    result["completion"] = completion.to_dict()
    result["reused_completion"] = reused
    return result


def _failure(
    code: AgentErrorCode,
    message: str,
    *,
    status: ObservationStatus = ObservationStatus.FAILED,
) -> dict[str, Any]:
    result = ToolResult.error_result(
        "molecular_docking",
        code,
        message,
        status=status,
    ).to_legacy_dict()
    result["completion"] = None
    result["reused_completion"] = False
    return result


def _cancel_check(cancel_event: Any) -> Callable[[], bool] | None:
    if cancel_event is None:
        return None
    return lambda: _is_cancelled(cancel_event)


def _is_cancelled(cancel_event: Any) -> bool:
    if cancel_event is None:
        return False
    try:
        return bool(cancel_event.is_set())
    except Exception:
        return True


def _is_reparse(status: os.stat_result) -> bool:
    attributes = getattr(status, "st_file_attributes", 0) or 0
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
