"""Single verified execution boundary shared by docking runtimes."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from src.agent.contracts import (
    AgentErrorCode,
    ObservationStatus,
    ToolProvenance,
    ToolResult,
    WorkflowArtifact,
)
from src.agent.persistence.redaction import sanitize_sensitive_text
from src.agent.tools.base_tool import execute_tool_compat
from src.agent.tools.molecular_docking import MolecularDocking
from src.agent.validators.result_validator import AgentResultValidator

from .completion import (
    AUTHORITY_BLOCKED,
    AUTHORITY_COMMITTED,
    AUTHORITY_COMMITTED_AMBIGUOUS,
    ArtifactCaptureAttempt,
    CompletionError,
    DockingCompletionManifest,
    DockingCompletionStore,
    DockingResultMetadata,
    PreparedDockingCompletion,
)
from .staging import (
    MAX_SMILES_BYTES,
    DockingTaskExecution,
    DockingInputStager,
    ManifestError,
    VerifiedDockingInputs,
)


RawExecutor = Callable[..., ToolResult]
_PENDING_COMPLETION_WAIT_SECONDS = 5.0
_VINA_RESULT = re.compile(
    rb"^REMARK VINA RESULT:\s+([^\s]+)\s+([^\s]+)\s+([^\s]+)\s*$"
)
_MAX_PDBQT_LINE_BYTES = 64 * 1024


class _PreparedCompletionPending(RuntimeError):
    pass


@dataclass
class _CompletionCommitState:
    committed: bool = False
    completion: DockingCompletionManifest | None = None
    durability_synced: bool = False
    authority_state: str = AUTHORITY_BLOCKED
    response_variants: dict[tuple[bool, bool], dict[str, Any]] = field(
        default_factory=dict
    )
    unavailable_variants: dict[bool, dict[str, Any]] = field(default_factory=dict)
    authority_unavailable_variants: dict[
        tuple[bool, bool], dict[str, Any]
    ] = field(default_factory=dict)
    authority_ambiguous_variants: dict[
        tuple[bool, bool], dict[str, Any]
    ] = field(default_factory=dict)


class DockingExecution:
    """Execute or reuse docking only under a verified staging lease."""

    def __init__(
        self,
        root: str | os.PathLike[str],
        raw_executor: RawExecutor | None = None,
        *,
        allowed_output_root: str | os.PathLike[str] | None = None,
    ) -> None:
        self._stager = DockingInputStager(root)
        self._completions = DockingCompletionStore(root)
        if raw_executor is not None and allowed_output_root is None:
            raise ValueError("injected docking executor requires an allowed output root")
        output_root = (
            Path.cwd() / "temp_docking"
            if allowed_output_root is None
            else Path(allowed_output_root)
        )
        self._allowed_output_root = Path(os.path.abspath(output_root))
        if raw_executor is not None:
            self.raw_executor = raw_executor
        else:
            backend = os.environ.get("MEDCHAT_DOCKING_EXECUTION_BACKEND", "local")
            if backend == "local":
                self.raw_executor = self._execute_local_tool
            elif backend == "opensandbox":
                from src.docking.sandbox_runner import SandboxDockingRunner

                self.raw_executor = SandboxDockingRunner.from_env(
                    self._allowed_output_root
                ).execute
            else:
                raise ValueError("invalid docking execution backend")

    def __repr__(self) -> str:
        return f"{type(self).__name__}(root=<redacted>)"

    @staticmethod
    def _execute_local_tool(payload: Mapping[str, Any], **control: Any) -> ToolResult:
        return execute_tool_compat(MolecularDocking(), payload, **control)

    # Compatibility identity for the existing Temporal process-boundary guard.
    _execute_production_tool = _execute_local_tool

    def run_verified(
        self,
        task_id: str,
        input_manifest: str | os.PathLike[str],
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
        if not isinstance(input_manifest, (str, os.PathLike)):
            return _failure(
                AgentErrorCode.INVALID_INPUT,
                "A docking input manifest path is required.",
            )
        _emit_progress(progress_callback, "environment_check", 2)
        pending_deadline: float | None = None
        while True:
            prepared: PreparedDockingCompletion | None = None
            commit_state: _CompletionCommitState | None = None
            try:
                with self._stager.task_execution(
                    task_id,
                    input_manifest,
                    lease_timeout_seconds=lease_timeout_seconds,
                    cancel_check=_cancel_check(cancel_event),
                ) as execution:
                    _emit_progress(progress_callback, "input_verification", 5)
                    outcome = self._run_under_lease(
                        execution.inputs,
                        attempt=attempt,
                        progress_callback=progress_callback,
                        cancel_event=cancel_event,
                    )
                    if isinstance(outcome, PreparedDockingCompletion):
                        prepared = outcome
                        commit_state = _CompletionCommitState()
                        try:
                            outcome = self._finalize_prepared(
                                task_id,
                                prepared,
                                execution=execution,
                                commit_state=commit_state,
                                cancel_event=cancel_event,
                            )
                        except BaseException:
                            if not commit_state.committed:
                                self._completions.abort(prepared)
                                prepared = None
                            raise
                        if not commit_state.committed:
                            prepared = None
                return outcome
            except _PreparedCompletionPending:
                if pending_deadline is None:
                    pending_deadline = (
                        time.monotonic() + _PENDING_COMPLETION_WAIT_SECONDS
                    )
                if _is_cancelled(cancel_event) or time.monotonic() >= pending_deadline:
                    return _failure(
                        AgentErrorCode.INVALID_OUTPUT,
                        "Docking completion verification failed.",
                        details={"reason": "completion_invalid"},
                    )
                time.sleep(0.02)
                continue
            except ManifestError as exc:
                if commit_state is not None and commit_state.committed:
                    return self._resolve_post_commit_exception(
                        task_id,
                        commit_state,
                    )
                if _is_cancelled(cancel_event):
                    return _failure(
                        AgentErrorCode.CANCELLED,
                        "Docking execution was cancelled before verification completed.",
                        status=ObservationStatus.CANCELLED,
                    )
                return _failure(
                    AgentErrorCode.INVALID_INPUT,
                    "Docking input verification failed.",
                    details={
                        "reason": (
                            "input_hash_mismatch"
                            if exc.reason_code == "manifest_integrity_failed"
                            else "input_invalid"
                        )
                    },
                )
            except CompletionError:
                if commit_state is not None and commit_state.committed:
                    return self._resolve_post_commit_exception(
                        task_id,
                        commit_state,
                    )
                return _failure(
                    AgentErrorCode.INVALID_OUTPUT,
                    "Docking completion verification failed.",
                    details={"reason": "completion_invalid"},
                )
            except asyncio.CancelledError:
                if commit_state is not None and commit_state.committed:
                    return self._resolve_post_commit_exception(
                        task_id,
                        commit_state,
                    )
                return _failure(
                    AgentErrorCode.CANCELLED,
                    "Docking execution was cancelled.",
                    status=ObservationStatus.CANCELLED,
                )
            except Exception:
                if commit_state is not None and commit_state.committed:
                    return self._resolve_post_commit_exception(
                        task_id,
                        commit_state,
                    )
                return _failure(
                    AgentErrorCode.INTERNAL_ERROR,
                    "Docking execution failed before a verified result was produced.",
                    details={"reason": "process_failed"},
                )

    def run_verified_locator(
        self,
        task_id: str,
        manifest_locator: str,
        **control: Any,
    ) -> dict[str, Any]:
        """Resolve a safe logical locator locally, then run verified docking."""

        try:
            manifest_path = self._stager.resolve_manifest_locator(
                task_id,
                manifest_locator,
            )
        except ManifestError as exc:
            return _failure(
                AgentErrorCode.INVALID_INPUT,
                "Docking input locator is invalid.",
                details={
                    "reason": (
                        "input_hash_mismatch"
                        if exc.reason_code == "manifest_integrity_failed"
                        else "input_invalid"
                    )
                },
            )
        return self.run_verified(task_id, manifest_path, **control)

    def _run_under_lease(
        self,
        inputs: VerifiedDockingInputs,
        *,
        attempt: int,
        progress_callback: Callable[..., Any] | None,
        cancel_event: Any,
    ) -> dict[str, Any] | PreparedDockingCompletion:
        inputs.verify_integrity()
        input_hash = _input_hash(inputs)
        config_hash = inputs.config_hash
        self._completions.cleanup_stale_artifact_attempts(inputs.task_id)

        if self._completions.has_invalidation_marker(inputs.task_id):
            return _failure(
                AgentErrorCode.INVALID_OUTPUT,
                "Docking completion ownership is uncertain.",
                details={"reason": "ownership_uncertain"},
            )
        if self._completions.has_manifest(inputs.task_id):
            completion = self._completions.load_verified(
                inputs.task_id,
                input_hash=input_hash,
                config_hash=config_hash,
            )
            inputs.verify_integrity()
            reuse_payload = _build_payload(inputs)
            try:
                if not _safe_provenance_identifiers(
                    _completion_provenance(completion),
                    _sensitive_values(inputs, reuse_payload),
                ):
                    return _failure(
                        AgentErrorCode.INVALID_OUTPUT,
                        "Docking completion provenance failed verification.",
                        details={"reason": "provenance_rejected"},
                    )
            finally:
                reuse_payload.clear()
            return _trusted_result(completion, reused=True)
        if self._completions.has_artifact_residue(inputs.task_id):
            raise _PreparedCompletionPending()
        if _is_cancelled(cancel_event):
            return _failure(
                AgentErrorCode.CANCELLED,
                "Docking execution was cancelled before the scientific tool started.",
                status=ObservationStatus.CANCELLED,
            )

        payload = _build_payload(inputs)
        sensitive_values = _sensitive_values(inputs, payload)
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
                details={"reason": "process_failed"},
            )
        finally:
            payload.clear()

        _emit_progress(progress_callback, "result_parsing", 75)
        if not isinstance(tool_result, ToolResult):
            return _failure(
                AgentErrorCode.INVALID_OUTPUT,
                "Docking tool returned an invalid result contract.",
                details={"reason": "completion_invalid"},
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
                details={
                    "reason": _tool_failure_reason(
                        tool_result.error.code if tool_result.error else None,
                        tool_result.error.details if tool_result.error else None,
                    )
                },
            )

        inputs.verify_integrity()
        _attach_verified_input_evidence(tool_result, inputs)
        _emit_progress(progress_callback, "scientific_validation", 90)
        validated = AgentResultValidator().validate_tool_result(tool_result)
        if not validated.success or validated.status != ObservationStatus.SUCCEEDED:
            return _failure(
                AgentErrorCode.INVALID_OUTPUT,
                "Docking evidence failed scientific validation.",
                details={"reason": "validator_rejected"},
            )
        provenance = validated.provenance
        if (
            provenance is None
            or provenance.tool_name != "molecular_docking"
            or provenance.demo_mode is not False
            or provenance.fallback_used is not False
            or validated.quality.get("real_execution") is not True
            or not _safe_provenance_identifiers(provenance, sensitive_values)
        ):
            return _failure(
                AgentErrorCode.INVALID_OUTPUT,
                "Docking result lacks trusted real-execution provenance.",
                details={"reason": "provenance_rejected"},
            )
        if _is_cancelled(cancel_event):
            return _failure(
                AgentErrorCode.CANCELLED,
                "Docking execution was cancelled before artifact commit.",
                status=ObservationStatus.CANCELLED,
            )
        inputs.verify_integrity()

        pose_source, pose_count, best_energy = _validated_scientific_values(validated)
        pose_job_root = self._allowed_output_root / f"docking_{inputs.task_id}"
        pose_parser = _VinaPoseStreamValidator(
            expected_pose_count=pose_count,
            expected_best_energy=best_energy,
        )
        pose_size, pose_sha256 = self._completions.inspect_authoritative_pose(
            pose_source,
            containment_root=pose_job_root,
            chunk_consumer=pose_parser.feed,
        )
        pose_parser.finish()
        _emit_progress(progress_callback, "artifact_commit", 98)
        artifact_attempt = self._completions.begin_artifact_attempt(inputs.task_id)
        try:
            result_metadata = self._safe_result_metadata(
                validated,
                inputs,
                pose_source=pose_source,
                sensitive_values=sensitive_values,
                artifact_attempt=artifact_attempt,
            )
            return self._completions.prepare(
                inputs.task_id,
                input_hash=input_hash,
                config_hash=config_hash,
                tool_version=provenance.tool_version,
                model_name=provenance.model_name,
                model_version=provenance.model_version,
                demo_mode=provenance.demo_mode,
                fallback_used=provenance.fallback_used,
                real_execution=True,
                pose_source=pose_source,
                pose_containment_root=pose_job_root,
                expected_pose_size=pose_size,
                expected_pose_sha256=pose_sha256,
                pose_count=pose_count,
                best_energy=best_energy,
                validator_status="succeeded",
                attempt=attempt,
                result_metadata=result_metadata,
                artifact_attempt=artifact_attempt,
            )
        finally:
            self._completions.discard_artifact_attempt(artifact_attempt)

    def _safe_result_metadata(
        self,
        result: ToolResult,
        inputs: VerifiedDockingInputs,
        *,
        pose_source: Path,
        sensitive_values: tuple[str, ...],
        artifact_attempt: ArtifactCaptureAttempt,
    ) -> DockingResultMetadata:
        warnings = _safe_warnings(result.warnings, sensitive_values)
        evidence = _safe_evidence(result.evidence, sensitive_values)
        artifacts = []
        omitted_artifact = False
        for artifact_index, artifact in enumerate(result.artifacts[:9], start=1):
            if len(artifacts) >= 8:
                omitted_artifact = True
                break
            try:
                artifact_path = Path(artifact.path)
                if artifact_path.resolve(strict=False) == pose_source.resolve(strict=False):
                    continue
            except (OSError, RuntimeError, TypeError, ValueError):
                omitted_artifact = True
                continue
            record = self._completions.capture_additional_artifact(
                artifact_attempt,
                artifact_index=artifact_index,
                path=artifact.path,
            )
            if record is None:
                omitted_artifact = True
                continue
            artifacts.append(record)
        if omitted_artifact and "additional_artifact_omitted" not in warnings:
            warnings = warnings[:31] + ["additional_artifact_omitted"]

        elapsed_ms = result.elapsed_ms
        if (
            type(elapsed_ms) is not int
            or not 0 <= elapsed_ms <= 7 * 24 * 60 * 60 * 1000
        ):
            elapsed_ms = None
        quality = {
            "real_execution": True,
            "engine": _safe_optional_text(
                result.quality.get("engine"), sensitive_values, 128
            ),
            "execution_status": _safe_optional_text(
                result.quality.get("execution_status"), sensitive_values, 128
            ),
            "validated": bool(result.quality.get("validated")),
        }
        if result.quality.get("execution_backend") == "opensandbox":
            quality.update(
                {
                    "execution_backend": "opensandbox",
                    "secure_runtime": result.quality.get("secure_runtime"),
                    "sandbox_image_digest": result.quality.get(
                        "sandbox_image_digest"
                    ),
                    "cleanup_status": result.quality.get("cleanup_status"),
                }
            )
        return DockingResultMetadata(
            elapsed_ms=elapsed_ms,
            formatted=_sanitize_text(result.formatted, sensitive_values, 2048),
            warnings=warnings,
            evidence=evidence,
            artifacts=artifacts,
            quality=quality,
        )

    def _finalize_prepared(
        self,
        task_id: str,
        prepared: PreparedDockingCompletion,
        *,
        execution: DockingTaskExecution,
        commit_state: _CompletionCommitState,
        cancel_event: Any,
    ) -> dict[str, Any]:
        try:
            commit_state.response_variants = {
                (True, False): _trusted_result(
                    prepared.completion,
                    reused=False,
                ),
                (False, False): _trusted_result(
                    prepared.completion,
                    reused=False,
                    extra_warnings=["completion_directory_sync_unconfirmed"],
                ),
                (True, True): _trusted_result(
                    prepared.completion,
                    reused=False,
                    extra_warnings=["completion_lease_release_unconfirmed"],
                ),
                (False, True): _trusted_result(
                    prepared.completion,
                    reused=False,
                    extra_warnings=[
                        "completion_directory_sync_unconfirmed",
                        "completion_lease_release_unconfirmed",
                    ],
                ),
            }
            commit_state.unavailable_variants = {
                True: _trusted_result(
                    prepared.completion,
                    reused=False,
                    extra_warnings=[
                        "completion_post_commit_verification_unavailable",
                        "completion_lease_release_unconfirmed",
                    ],
                    extra_quality={"completion_durability": "uncertain"},
                ),
                False: _trusted_result(
                    prepared.completion,
                    reused=False,
                    extra_warnings=[
                        "completion_directory_sync_unconfirmed",
                        "completion_post_commit_verification_unavailable",
                        "completion_lease_release_unconfirmed",
                    ],
                    extra_quality={"completion_durability": "uncertain"},
                ),
            }
            commit_state.authority_unavailable_variants = {
                (True, False): _trusted_result(
                    prepared.completion,
                    reused=False,
                    extra_warnings=["completion_authority_commit_unconfirmed"],
                    extra_quality={"completion_durability": "uncertain"},
                ),
                (False, False): _trusted_result(
                    prepared.completion,
                    reused=False,
                    extra_warnings=[
                        "completion_directory_sync_unconfirmed",
                        "completion_authority_commit_unconfirmed",
                    ],
                    extra_quality={"completion_durability": "uncertain"},
                ),
                (True, True): _trusted_result(
                    prepared.completion,
                    reused=False,
                    extra_warnings=[
                        "completion_authority_commit_unconfirmed",
                        "completion_lease_release_unconfirmed",
                    ],
                    extra_quality={"completion_durability": "uncertain"},
                ),
                (False, True): _trusted_result(
                    prepared.completion,
                    reused=False,
                    extra_warnings=[
                        "completion_directory_sync_unconfirmed",
                        "completion_authority_commit_unconfirmed",
                        "completion_lease_release_unconfirmed",
                    ],
                    extra_quality={"completion_durability": "uncertain"},
                ),
            }
            commit_state.authority_ambiguous_variants = {
                (True, False): _trusted_result(
                    prepared.completion,
                    reused=False,
                    extra_warnings=[
                        "completion_authority_delete_ambiguous_reusable"
                    ],
                    extra_quality={
                        "completion_durability": "reusable",
                        "completion_authority": "committed",
                    },
                ),
                (False, False): _trusted_result(
                    prepared.completion,
                    reused=False,
                    extra_warnings=[
                        "completion_directory_sync_unconfirmed",
                        "completion_authority_delete_ambiguous_reusable",
                    ],
                    extra_quality={
                        "completion_durability": "uncertain",
                        "completion_authority": "committed",
                    },
                ),
                (True, True): _trusted_result(
                    prepared.completion,
                    reused=False,
                    extra_warnings=[
                        "completion_authority_delete_ambiguous_reusable",
                        "completion_lease_release_unconfirmed",
                    ],
                    extra_quality={
                        "completion_durability": "reusable",
                        "completion_authority": "committed",
                    },
                ),
                (False, True): _trusted_result(
                    prepared.completion,
                    reused=False,
                    extra_warnings=[
                        "completion_directory_sync_unconfirmed",
                        "completion_authority_delete_ambiguous_reusable",
                        "completion_lease_release_unconfirmed",
                    ],
                    extra_quality={
                        "completion_durability": "uncertain",
                        "completion_authority": "committed",
                    },
                ),
            }
            manifest = execution.close_inputs_and_verify_manifest()
            execution.assert_ready_to_finalize()
            if (
                _manifest_input_hash(manifest) != prepared.completion.input_hash
                or manifest["config_hash"] != prepared.completion.config_hash
            ):
                raise ManifestError("manifest_integrity_failed")
            if _is_cancelled(cancel_event):
                self._completions.abort(prepared)
                return _failure(
                    AgentErrorCode.CANCELLED,
                    "Docking execution was cancelled before artifact commit.",
                    status=ObservationStatus.CANCELLED,
                )
            (
                commit_state.completion,
                commit_state.durability_synced,
                commit_state.authority_state,
            ) = self._completions.finalize(prepared)
            commit_state.committed = True
        except BaseException:
            if not commit_state.committed:
                self._completions.abort(prepared)
            raise
        if commit_state.authority_state not in {
            AUTHORITY_COMMITTED,
            AUTHORITY_COMMITTED_AMBIGUOUS,
        }:
            return commit_state.authority_unavailable_variants[
                (commit_state.durability_synced, False)
            ]
        if commit_state.authority_state == AUTHORITY_COMMITTED_AMBIGUOUS:
            return commit_state.authority_ambiguous_variants[
                (commit_state.durability_synced, False)
            ]
        return commit_state.response_variants[
            (commit_state.durability_synced, False)
        ]

    def _resolve_post_commit_exception(
        self,
        task_id: str,
        commit_state: _CompletionCommitState,
    ) -> dict[str, Any]:
        completion = commit_state.completion
        if not commit_state.committed or completion is None:
            raise CompletionError("completion_io_error")
        if commit_state.authority_state not in {
            AUTHORITY_COMMITTED,
            AUTHORITY_COMMITTED_AMBIGUOUS,
        }:
            return commit_state.authority_unavailable_variants[
                (commit_state.durability_synced, True)
            ]
        try:
            verified = self._completions.load_verified(
                task_id,
                input_hash=completion.input_hash,
                config_hash=completion.config_hash,
            )
            if verified != completion:
                raise CompletionError("completion_schema_invalid")
        except Exception:
            try:
                marker_persisted, quarantined = (
                    self._completions.invalidate_published(task_id)
                )
            except Exception:
                marker_persisted = False
                quarantined = False
            if not marker_persisted and not quarantined:
                return commit_state.unavailable_variants[
                    commit_state.durability_synced
                ]
            return _failure(
                AgentErrorCode.INVALID_OUTPUT,
                (
                    "Docking completion ownership is uncertain."
                    if not quarantined
                    else "Docking completion could not be strictly reverified."
                ),
                details={
                    "reason": (
                        "ownership_uncertain"
                        if not quarantined
                        else "completion_invalidated"
                    )
                },
            )
        if commit_state.authority_state == AUTHORITY_COMMITTED_AMBIGUOUS:
            return commit_state.authority_ambiguous_variants[
                (commit_state.durability_synced, True)
            ]
        return commit_state.response_variants[
            (commit_state.durability_synced, True)
        ]

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


class _VinaPoseStreamValidator:
    def __init__(
        self,
        *,
        expected_pose_count: int,
        expected_best_energy: float,
    ) -> None:
        self._expected_pose_count = expected_pose_count
        self._expected_best_energy = expected_best_energy
        self._pending = b""
        self._models = 0
        self._energy_count = 0
        self._minimum_energy = math.inf
        self._in_model = False
        self._model_has_atom = False
        self._model_energy: float | None = None

    def feed(self, chunk: bytes) -> None:
        if not isinstance(chunk, bytes) or not chunk:
            raise CompletionError("completion_artifact_invalid")
        cursor = 0
        if self._pending:
            newline = chunk.find(b"\n")
            if newline < 0:
                if len(self._pending) + len(chunk) > _MAX_PDBQT_LINE_BYTES:
                    raise CompletionError("completion_artifact_invalid")
                self._pending += chunk
                return
            if len(self._pending) + newline > _MAX_PDBQT_LINE_BYTES:
                raise CompletionError("completion_artifact_invalid")
            self._consume_complete_line(self._pending + chunk[:newline])
            self._pending = b""
            cursor = newline + 1

        while True:
            newline = chunk.find(b"\n", cursor)
            if newline < 0:
                tail = chunk[cursor:]
                if len(tail) > _MAX_PDBQT_LINE_BYTES:
                    raise CompletionError("completion_artifact_invalid")
                self._pending = tail
                return
            if newline - cursor > _MAX_PDBQT_LINE_BYTES:
                raise CompletionError("completion_artifact_invalid")
            self._consume_complete_line(chunk[cursor:newline])
            cursor = newline + 1

    def _consume_complete_line(self, line: bytes) -> None:
        if line.endswith(b"\r"):
            line = line[:-1]
        self._consume_line(line)

    def _consume_line(self, line: bytes) -> None:
        try:
            line.decode("ascii", errors="strict")
        except UnicodeDecodeError:
            raise CompletionError("completion_artifact_invalid") from None
        if line.startswith(b"MODEL"):
            if self._in_model or line.split() != [
                b"MODEL",
                str(self._models + 1).encode("ascii"),
            ]:
                raise CompletionError("completion_artifact_invalid")
            self._models += 1
            self._in_model = True
            self._model_has_atom = False
            self._model_energy = None
            return
        if line == b"ENDMDL":
            if (
                not self._in_model
                or not self._model_has_atom
                or self._model_energy is None
            ):
                raise CompletionError("completion_artifact_invalid")
            self._energy_count += 1
            self._minimum_energy = min(
                self._minimum_energy,
                self._model_energy,
            )
            self._in_model = False
            return
        if not self._in_model:
            if line.strip():
                raise CompletionError("completion_artifact_invalid")
            return
        match = _VINA_RESULT.fullmatch(line)
        if match is not None:
            if self._model_energy is not None:
                raise CompletionError("completion_artifact_invalid")
            try:
                values = tuple(float(item) for item in match.groups())
            except (OverflowError, ValueError):
                raise CompletionError("completion_artifact_invalid") from None
            if not all(math.isfinite(item) for item in values):
                raise CompletionError("completion_artifact_invalid")
            self._model_energy = values[0]
        elif line.startswith((b"ATOM  ", b"HETATM")):
            self._model_has_atom = True

    def finish(self) -> None:
        if len(self._pending) > _MAX_PDBQT_LINE_BYTES:
            raise CompletionError("completion_artifact_invalid")
        if self._pending:
            line = self._pending
            self._pending = b""
            self._consume_complete_line(line)
        if (
            self._in_model
            or self._models == 0
            or self._energy_count != self._models
            or self._models != self._expected_pose_count
            or not math.isclose(
                self._minimum_energy,
                self._expected_best_energy,
                rel_tol=0.0,
                abs_tol=1e-3,
            )
        ):
            raise CompletionError("completion_artifact_invalid")


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


def _manifest_input_hash(manifest: Mapping[str, Any]) -> str:
    identity = {
        "ligand": {
            "mode": manifest["ligand_mode"],
            "sha256": manifest["ligand"]["sha256"],
            "size": manifest["ligand"]["size"],
        },
        "receptor": {
            "sha256": manifest["receptor"]["sha256"],
            "size": manifest["receptor"]["size"],
        },
    }
    encoded = json.dumps(
        identity,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _sensitive_values(
    inputs: VerifiedDockingInputs,
    payload: Mapping[str, Any],
) -> tuple[str, ...]:
    values = {
        str(inputs.manifest_path),
        str(inputs.manifest_path.parent),
        str(inputs.receptor_path),
        str(inputs.ligand_path),
    }
    smiles = payload.get("smiles")
    if isinstance(smiles, str) and smiles:
        values.add(smiles)
    return tuple(sorted(values, key=len, reverse=True))


def _sanitize_text(value: Any, secrets: tuple[str, ...], limit: int) -> str:
    sanitized, _changed = sanitize_sensitive_text(
        value,
        max_chars=limit,
        sensitive_values=secrets,
    )
    return sanitized


def _safe_optional_text(
    value: Any,
    secrets: tuple[str, ...],
    limit: int,
) -> str | None:
    sanitized = _sanitize_text(value, secrets, limit)
    return sanitized or None


def _safe_warnings(values: Any, secrets: tuple[str, ...]) -> list[str]:
    warnings: list[str] = []
    for value in values if isinstance(values, list) else []:
        warning = _sanitize_text(value, secrets, 256)
        if warning and warning not in warnings:
            warnings.append(warning)
        if len(warnings) == 32:
            break
    return warnings


def _safe_evidence(values: Any, secrets: tuple[str, ...]) -> list[dict[str, Any]]:
    allowed = {
        "source",
        "description",
        "method",
        "version",
        "identifier",
        "type",
        "label",
        "sha256",
    }
    evidence: list[dict[str, Any]] = []
    for value in values if isinstance(values, list) else []:
        if not isinstance(value, dict):
            continue
        record: dict[str, Any] = {}
        for key in allowed:
            item = value.get(key)
            if isinstance(item, str):
                item = _sanitize_text(item, secrets, 256)
                if item:
                    record[key] = item
            elif type(item) in (int, float) and math.isfinite(float(item)):
                record[key] = item
            elif type(item) is bool:
                record[key] = item
        if record:
            evidence.append(record)
        if len(evidence) == 32:
            break
    return evidence


def _safe_provenance_identifiers(
    provenance: ToolProvenance,
    secrets: tuple[str, ...],
) -> bool:
    for value in (
        provenance.tool_name,
        provenance.tool_version,
        provenance.model_name,
        provenance.model_version,
        provenance.input_digest,
        provenance.output_digest,
    ):
        if value is None:
            continue
        sanitized, changed = sanitize_sensitive_text(
            value,
            max_chars=max(1, len(value)),
            sensitive_values=secrets,
        )
        if changed or sanitized != value:
            return False
    return True


def _completion_provenance(
    completion: DockingCompletionManifest,
) -> ToolProvenance:
    return ToolProvenance(
        tool_name="molecular_docking",
        tool_version=completion.tool_version,
        model_name=completion.model_name,
        model_version=completion.model_version,
        demo_mode=completion.demo_mode,
        fallback_used=completion.fallback_used,
    )


def _trusted_result(
    completion: DockingCompletionManifest,
    *,
    reused: bool,
    extra_warnings: list[str] | None = None,
    extra_quality: dict[str, Any] | None = None,
) -> dict[str, Any]:
    relative_pose = completion.pose.path
    metadata = completion.result_metadata
    artifacts = [
        WorkflowArtifact(
            artifact_type="docking_pose",
            path=relative_pose,
            label="Verified docking pose",
            mime_type="chemical/x-pdbqt",
            metadata={"sha256": completion.pose.sha256},
        )
    ]
    artifacts.extend(
        WorkflowArtifact(
            artifact_type=artifact.artifact_type,
            path=artifact.path,
            label=artifact.label,
            mime_type=artifact.mime_type,
            metadata=dict(artifact.metadata),
        )
        for artifact in metadata.artifacts
    )
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
        formatted=metadata.formatted,
        elapsed_ms=metadata.elapsed_ms,
        warnings=list(dict.fromkeys([*metadata.warnings, *(extra_warnings or [])])),
        evidence=[dict(record) for record in metadata.evidence],
        artifacts=artifacts,
        quality={
            **metadata.quality,
            "validator_status": completion.validator_status,
            "completion_schema": completion.schema,
            "attempt": completion.attempt,
            **(extra_quality or {}),
        },
        provenance=ToolProvenance(
            tool_name="molecular_docking",
            tool_version=completion.tool_version,
            model_name=completion.model_name,
            model_version=completion.model_version,
            demo_mode=completion.demo_mode,
            fallback_used=completion.fallback_used,
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
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = ToolResult.error_result(
        "molecular_docking",
        code,
        message,
        details=details,
        status=status,
    ).to_legacy_dict()
    result["completion"] = None
    result["reused_completion"] = False
    return result


def _emit_progress(
    callback: Callable[..., Any] | None,
    phase: str,
    progress: float,
) -> None:
    if callback is None:
        return
    try:
        callback(phase, progress)
    except Exception:
        pass


def _tool_failure_reason(
    code: AgentErrorCode | None,
    details: Mapping[str, Any] | None = None,
) -> str:
    if code is AgentErrorCode.TOOL_TIMEOUT:
        return "execution_timeout"
    if code in {
        AgentErrorCode.TOOL_UNAVAILABLE,
        AgentErrorCode.EXTERNAL_TOOL_UNAVAILABLE,
        AgentErrorCode.MODEL_UNAVAILABLE,
    }:
        return "environment_unavailable"
    if code is AgentErrorCode.INVALID_INPUT:
        if details and details.get("reason") == "smiles_not_supported_by_opensandbox":
            return "smiles_not_supported_by_opensandbox"
        return "input_invalid"
    if code is AgentErrorCode.VALIDATION_ERROR:
        return "validator_rejected"
    if code in {AgentErrorCode.INVALID_OUTPUT, AgentErrorCode.EMPTY_RESULT}:
        return "artifact_invalid"
    return "process_failed"


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
