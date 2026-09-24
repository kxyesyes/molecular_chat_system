"""Non-projecting docking transport views; execution and normalization stay legacy."""
from __future__ import annotations

import math
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Annotated, Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from src.agent.contracts import (
    AgentErrorCode, AgentExecutionError, ObservationStatus, ToolProvenance,
    ToolResult, WorkflowArtifact,
)
from src.agent.validators.domain_validators import DockingResultValidator
from src.agent.validators.result_validator import AgentResultValidator

from .adapters import LegacyPythonToolAdapter


class _View(BaseModel):
    model_config = ConfigDict(strict=True, extra="allow", revalidate_instances="always")


_INPUT_FIELDS = frozenset({"receptor_path", "ligand_path", "smiles", "center", "size"})


class _StructuredInput(_View):
    receptor_path: str | None = None
    ligand_path: str | None = None
    smiles: str | None = None
    center: list[Any] | tuple[Any, ...] | None = None
    size: list[Any] | tuple[Any, ...] | None = None
    query: str = ""

    @field_validator("receptor_path", "ligand_path", "smiles")
    @classmethod
    def nonempty(cls, value):
        if value is None or not value.strip():
            raise ValueError("Missing docking input")
        return value

    @field_validator("center", "size")
    @classmethod
    def triplet(cls, value, info):
        if value is None or len(value) != 3:
            raise ValueError("Invalid docking box")
        for item in value:
            if type(item) not in (int, float, str):
                raise ValueError("Invalid docking coordinate")
            try:
                number = float(item)
            except (ValueError, OverflowError):
                raise ValueError("Invalid docking coordinate") from None
            if not math.isfinite(number) or (info.field_name == "size" and number <= 0):
                raise ValueError("Invalid docking coordinate")
        return value

    @model_validator(mode="after")
    def complete_structure(self):
        if not (self.receptor_path and (self.ligand_path or self.smiles)
                and self.center is not None and self.size is not None):
            raise ValueError("Incomplete docking structure")
        return self


class DockingInput(_StructuredInput):
    query: str | _StructuredInput = ""

    @model_validator(mode="after")
    def complete_structure(self):
        structured = self.model_fields_set.intersection(_INPUT_FIELDS)
        if isinstance(self.query, _StructuredInput):
            if structured:
                raise ValueError("Ambiguous docking input")
        elif structured:
            super().complete_structure()
        elif "query" not in self.model_fields_set:
            raise ValueError("Missing docking query")
        return self


class _ErrorFields(_View):
    code: str | None = None
    message: str | None = None
    details: dict[str, Any] | None = None


class _ArtifactFields(_View):
    artifact_type: str = "file"
    path: str = ""
    label: str = ""
    mime_type: str | None = None
    metadata: dict[str, Any] | None = None


class _Pose(_View):
    binding_energy: Annotated[float, Field(allow_inf_nan=False)]
    pose_file: Annotated[str, Field(min_length=1)] | None = None
    output_file: Annotated[str, Field(min_length=1)] | None = None


class _Science(_View):
    total_poses: Annotated[int, Field(gt=0)]
    best_pose: dict[str, Any]
    pose_file: Annotated[str, Field(min_length=1)] | None = None
    output_file: Annotated[str, Field(min_length=1)] | None = None
    binding_energy: Annotated[float, Field(allow_inf_nan=False)] | None = None
    results: list[dict[str, Any]] | None = None
    success: bool | None = None

    @model_validator(mode="after")
    def finite_poses(self):
        for pose in [self.best_pose] + (self.results or []):
            _Pose.model_validate(pose)
        return self


_SCIENCE_KEYS = frozenset({"binding_energy", "best_pose", "total_poses",
                           "pose_file", "output_file", "results"})


class _RawOutput(_View):
    tool_name: Literal["molecular_docking"] = "molecular_docking"
    success: bool = False
    status: ObservationStatus | None = Field(default=None, strict=False)
    data: dict[str, Any] | None = None
    error: str | dict[str, Any] | AgentExecutionError | None = None
    message: str = ""
    formatted: str = ""
    elapsed_ms: Annotated[int, Field(ge=0)] | None = None
    warnings: list[str] | None = None
    evidence: list[dict[str, Any]] | None = None
    artifacts: list[dict[str, Any] | WorkflowArtifact] | None = None
    quality: dict[str, Any] | None = None
    provenance: dict[str, Any] | ToolProvenance | None = None

    @field_validator("status", mode="before")
    @classmethod
    def status_type(cls, value):
        if value is not None and not isinstance(value, (str, ObservationStatus)):
            raise ValueError("Invalid docking status type")
        return value

    @model_validator(mode="after")
    def observation(self, info):
        if ((self.success and (self.error is not None or self.status not in {
                None, ObservationStatus.SUCCEEDED, ObservationStatus.PARTIAL}))
                or (not self.success and self.status is ObservationStatus.SUCCEEDED)):
            raise ValueError("Conflicting docking status")
        if isinstance(self.error, AgentExecutionError):
            if not isinstance(self.error.code, AgentErrorCode):
                raise ValueError("Invalid docking error")
            error = _ErrorFields.model_validate(vars(self.error))
        elif isinstance(self.error, dict):
            error = _ErrorFields.model_validate(self.error)
        else:
            error = None
        artifacts = []
        for artifact in self.artifacts or []:
            fields = vars(artifact) if isinstance(artifact, WorkflowArtifact) else artifact
            view = _ArtifactFields.model_validate(fields)
            artifacts.append(WorkflowArtifact(view.artifact_type, view.path, view.label,
                                               view.mime_type, view.metadata or {}))
        provenance = self.provenance
        if provenance is not None:
            provenance = ToolProvenance.from_dict(
                vars(provenance) if isinstance(provenance, ToolProvenance) else provenance)
            if provenance.tool_name != self.tool_name or provenance.demo_mode or provenance.fallback_used:
                raise ValueError("Invalid docking provenance")
        data, quality = self.data or {}, self.quality or {}
        # Compat moves failed data into the known raw_result chain. A validated
        # descendant can support remaining claims, but is never restored here.
        context = info.context or {}
        proof_data = data if _SCIENCE_KEYS.intersection(data) else context.get("snapshot_data") or data
        requires_sandbox = (context.get("requires_opensandbox", False)
                            or data.get("execution_backend") == "opensandbox"
                            or quality.get("execution_backend") == "opensandbox")
        # These exact record locations are known scientific carriers; never
        # descend into diagnostics, extensions or arbitrary metadata.
        carriers = [data, quality, error.details or {} if error else {}, *(self.evidence or [])]
        for fields in carriers:
            if any(key in fields and fields[key] is not False for key in ("demo_mode", "fallback_used")):
                raise ValueError("Invalid docking provenance")
        claims = [fields for fields in carriers if _SCIENCE_KEYS.intersection(fields)]
        if self.success and not _SCIENCE_KEYS.intersection(data):
            raise ValueError("Missing docking scientific result")
        observation = ToolResult(self.tool_name, self.success, "", data=data,
                                 quality=(dict(quality, execution_backend="opensandbox") if requires_sandbox else quality),
                                 artifacts=artifacts, provenance=provenance)
        for fields in claims:
            # A secondary carrier may reference the already supplied result's
            # proof. This temporary view never replaces/restores output data.
            candidate = dict(proof_data, **{key: fields[key] for key in _SCIENCE_KEYS if key in fields})
            science = _Science.model_validate(candidate)
            if self.success and science.success is False:
                raise ValueError("Conflicting docking science status")
            if any("real_execution" in record and record["real_execution"] is not True
                   for record in carriers):
                raise ValueError("Conflicting docking execution evidence")
            observation.data = candidate
            if DockingResultValidator().validate(observation):
                raise ValueError("Invalid docking scientific evidence")
            # Validate every explicitly named pose path, not only the domain
            # validator's preferred path. Reuse its local/hash proof unchanged.
            paths = {pose[key] for pose in [candidate, science.best_pose] + (science.results or [])
                     for key in ("pose_file", "output_file") if pose.get(key) is not None}
            for path in paths:
                observation.data = dict(candidate, best_pose=dict(science.best_pose, pose_file=path))
                if DockingResultValidator().validate(observation):
                    raise ValueError("Invalid docking pose evidence")
        for artifact in artifacts:
            if artifact.artifact_type == "docking_pose":
                if not Path(artifact.path).is_file():
                    raise ValueError("Invalid docking artifact")
                if (requires_sandbox
                        and not DockingResultValidator._has_opensandbox_trust_contract(observation, artifact.path)):
                    raise ValueError("Invalid docking artifact proof")
        return self


class DockingOutput(_RawOutput):
    schema_version: ClassVar[str] = "1"
    tool_name: Literal["molecular_docking"]
    success: bool
    status: ObservationStatus = Field(strict=True)
    error: AgentExecutionError | None = None
    warnings: list[str]
    evidence: list[dict[str, Any]]
    artifacts: list[WorkflowArtifact]
    quality: dict[str, Any]
    provenance: ToolProvenance | None = None


def _input_fields(value):
    """Read explicitly supplied model fields without serializing runtime objects."""
    if isinstance(value, BaseModel):
        return {**{key: getattr(value, key) for key in value.model_fields_set},
                **(value.model_extra or {})}
    return dict(value) if isinstance(value, Mapping) else value


class _InvalidDockingOutput(Exception):
    pass


class _DockingCompletion:
    """Worker-local handoff to the output gate, never accepted as provider data."""

    def __init__(self, result, proof):
        self.result, self.proof = result, proof

    @property
    def success(self):
        return self.result.success


class DockingToolAdapter(LegacyPythonToolAdapter):
    def _validate_input(self, input_data):
        payload = {"query": input_data} if isinstance(input_data, str) else _input_fields(input_data)
        if isinstance(payload, dict) and isinstance(payload.get("query"), BaseModel):
            payload["query"] = _input_fields(payload["query"])
        model = self.spec.input_schema.model_validate(payload)
        if isinstance(model.query, _StructuredInput):
            return _input_fields(payload["query"])
        return payload if model.model_fields_set.intersection(_INPUT_FIELDS) else model.query

    def _input_validation_error(self, exc):
        return ToolResult.error_result(self.spec.name, AgentErrorCode.INVALID_INPUT,
                                       "Docking input validation failed")

    def _invalid_output(self, elapsed_ms=None):
        # The shared scrub owns all rejected-result cleanup. Never restore a
        # payload, provenance or raw_result after this gate.
        if type(elapsed_ms) is not int or elapsed_ms < 0:
            elapsed_ms = None
        result = ToolResult.error_result(self.spec.name, AgentErrorCode.INVALID_OUTPUT,
                                         "Docking output validation failed", elapsed_ms=elapsed_ms)
        return AgentResultValidator._scrub_docking_failure(result, rejected=True)

    def _validate_observation(self, raw, *, proof_context=None):
        seen, chain = set(), []
        for _ in range(17):
            if id(raw) in seen:
                raise ValueError("Cyclic docking snapshot")
            seen.add(id(raw))
            if isinstance(raw, ToolResult):
                chain.append((self.spec.output_schema, vars(raw)))
                error = raw.error
            elif isinstance(raw, dict):
                chain.append((_RawOutput, raw))
                error = raw.get("error")
            else:
                raise ValueError("Invalid docking envelope")
            details = (error.details if isinstance(error, AgentExecutionError) else
                       error.get("details") if isinstance(error, dict) else None)
            if not isinstance(details, dict) or "raw_result" not in details:
                break
            raw = details["raw_result"]
        else:
            raise ValueError("Docking snapshot depth exceeded")
        # Validate leaves first: only accepted science can become validation
        # context for an ancestor. No output/model_dump projection is involved.
        context = dict(proof_context or {})
        for schema, fields in reversed(chain):
            view = schema.model_validate(fields, context=context)
            if view.data is not None and _SCIENCE_KEYS.intersection(view.data):
                context["snapshot_data"] = view.data
            # Related validated snapshots may strengthen, never downgrade, the
            # artifact trust requirement. Diagnostic/local data cannot erase it.
            context["requires_opensandbox"] = (
                context.get("requires_opensandbox", False)
                or (view.data or {}).get("execution_backend") == "opensandbox"
                or (view.quality or {}).get("execution_backend") == "opensandbox")
        return context

    def _invoke_guarded(self, payload, raw_validator):
        proof = None
        def validate(raw):
            nonlocal proof
            if raw_validator is not None:
                raw_validator(raw)
            try:
                proof = self._validate_observation(raw)
            except (ValidationError, ValueError, TypeError, AttributeError, OverflowError, OSError):
                raise _InvalidDockingOutput from None
        try:
            # Canonical provider objects must not be changed by base redaction
            # or elapsed-time bookkeeping; compat remains the only normalizer.
            result = super()._invoke_guarded(payload, validate)
            return _DockingCompletion(deepcopy(result), deepcopy(proof))
        except _InvalidDockingOutput:
            return self._invalid_output()

    def _normalize(self, raw, elapsed_ms):
        if isinstance(raw, _DockingCompletion):
            # Use the inherited normalization/timing/redaction exactly once.
            return _DockingCompletion(super()._normalize(raw.result, elapsed_ms), raw.proof)
        return super()._normalize(raw, elapsed_ms)

    def _validate_output(self, result):
        proof = None
        if isinstance(result, _DockingCompletion):
            result, proof = result.result, result.proof
        try:
            self._validate_observation(result, proof_context=proof)
        except (ValidationError, ValueError, TypeError, AttributeError, OverflowError, OSError):
            return self._invalid_output(result.elapsed_ms)
        return result
