"""Non-projecting target boundaries; shape/evidence checks are not authenticity.

Lookup outcomes belong to this adapter, not the shared observation enum. No
provider, parser, similarity algorithm or scientific asset is loaded here.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, ClassVar, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError, model_validator

from src.agent.contracts import (
    AgentErrorCode, AgentExecutionError, ObservationStatus, ToolProvenance,
    ToolResult, WorkflowArtifact,
)
from src.agent.tools.base_tool import execute_tool_compat
from src.agent.validators.domain_validators import TargetEvidenceValidator

from .adapters import LegacyPythonToolAdapter


class _View(BaseModel):
    model_config = ConfigDict(strict=True, extra="allow", revalidate_instances="always")


class TargetRecordInput(_View):
    gene_symbol: str | None = None
    target_gene: str | None = None
    uniprot_id: str | None = None
    target_name: str | None = None
    protein_name: str | None = None


def _dict(value):
    if not isinstance(value, dict):
        raise ValueError("Expected a target dictionary")
    return value


RecordInput = Annotated[TargetRecordInput, BeforeValidator(_dict)]
TargetQuery = str | RecordInput | list[str | RecordInput] | tuple[str | RecordInput, ...]


class TargetSearchInput(_View):
    query: TargetQuery


class ReverseTargetInput(_View):
    query: str


def _number(value):
    # Explicit guard also works on the minimum supported Pydantic 2.5 profile.
    if type(value) not in (int, float):
        raise ValueError("Expected a numeric observation")
    return value


Finite = Annotated[float, BeforeValidator(_number), Field(allow_inf_nan=False)]
Similarity = Annotated[Finite, Field(ge=0, le=1)]
Count = Annotated[int, Field(ge=0)]


class SourceFields(_View):
    id: int | str | None = None
    status: str | None = None
    source: str | None = None
    source_record_id: str | None = None
    source_url: str | None = None
    url: str | None = None
    retrieved_at: str | None = None
    expires_at: str | None = None
    stale: bool | None = None


class StructureFields(SourceFields):
    structure_id: str | None = None
    pdb_id: str | None = None
    structure_type: str | None = None
    method: str | None = None
    resolution: Finite | None = None
    score: Finite | None = None
    chain_ids: list[str] | None = None
    ligand_ids: list[str] | None = None
    organism: str | None = None
    title: str | None = None
    file_format: str | None = None
    local_file_path: str | None = None
    download_url: str | None = None
    is_downloaded: bool | None = None
    docking_recommended: bool | None = None
    is_preferred: bool | None = None
    quality_note: str | None = None
    recommendation_level: str | None = None
    recommendation_reasons: list[str] | None = None
    warnings: list[str] | None = None
    docking_grade: str | None = None
    docking_grade_label: str | None = None


class TargetRecord(TargetRecordInput, SourceFields):
    target_id: int | str | None = None
    target_identifier: str | None = None
    target_chembl_id: str | None = None
    structure_id: str | None = None
    pdb_id: str | None = None
    organism: str | None = None
    match_reason: str | None = None
    source_query: str | None = None
    structure_count: Count | None = None
    downloaded_structure_count: Count | None = None
    experimental_structure_count: Count | None = None
    alphafold_structure_count: Count | None = None
    has_experimental_structure: bool | None = None
    has_alphafold_structure: bool | None = None
    structure_evidence_status: str | None = None
    target_stale: bool | None = None
    structures_stale: bool | None = None
    target_retrieved_at: str | None = None
    target_expires_at: str | None = None
    structures_retrieved_at: str | None = None
    structures_expires_at: str | None = None
    recommended_structures: list[Annotated[StructureFields, BeforeValidator(_dict)]] = Field(default_factory=list)


class AssayFields(_View):
    type: str | None = None
    relation: str | None = None
    value: Finite | None = None
    units: str | None = None


class ReverseRecord(TargetRecord):
    final_similarity: Similarity | None = None
    morgan_similarity: Similarity | None = None
    maccs_similarity: Similarity | None = None
    similar_count: Count | None = None
    assay: Annotated[AssayFields, BeforeValidator(_dict)] | None = None
    standard_type: str | None = None
    standard_relation: str | None = None
    standard_value: Finite | None = None
    standard_units: str | None = None

    @model_validator(mode="after")
    def supplied_measurements(self):
        for key in ("final_similarity", "morgan_similarity", "maccs_similarity", "similar_count", "assay"):
            if key in self.model_fields_set and getattr(self, key) is None:
                raise ValueError("Explicit null reverse-target measurement")
        if "target_identifier" in self.model_fields_set and not (self.target_identifier or "").strip():
            raise ValueError("Empty reverse-target identity")
        return self


class QualityFields(_View):
    lookup_status: str | None = None
    lookup_path: list[str] | None = None
    service_statuses: list[str] | None = None
    retryable: bool | None = None


class ErrorFields(_View):
    code: str | None = None
    message: str | None = None
    details: dict[str, Any] | None = None


class ArtifactFields(_View):
    artifact_type: str = "file"
    path: str = ""
    label: str = ""
    mime_type: str | None = None
    metadata: dict[str, Any] | None = None


def _artifact(value):
    ArtifactFields.model_validate(vars(value) if isinstance(value, WorkflowArtifact) else _dict(value))
    return value


def _error(value):
    if value is not None:
        if not isinstance(value, AgentExecutionError) or not isinstance(value.code, AgentErrorCode):
            raise ValueError("Invalid target error")
        ErrorFields.model_validate(vars(value))
    return value


LOOKUP_STATUS = {"resolved": "succeeded", "not_found": "succeeded", "ambiguous": "invalid_input",
                 "unavailable": "unavailable", "partial": "partial"}


class TargetRawOutput(_View):
    tool_name: Literal["target_database_search"] = "target_database_search"
    success: bool = False
    status: str | None = None
    data: list[dict[str, Any]] | None = None
    error: str | Annotated[ErrorFields, BeforeValidator(_dict)] | None = None
    error_code: str | None = None
    message: str = ""
    formatted: str = ""
    elapsed_ms: Count | None = None
    warnings: list[str] | None = None
    evidence: list[dict[str, Any]] | None = None
    artifacts: list[Annotated[dict[str, Any] | WorkflowArtifact, BeforeValidator(_artifact)]] | None = None
    quality: dict[str, Any] | None = None
    provenance: dict[str, Any] | ToolProvenance | None = None
    lookup_path: list[str] | None = None

    @model_validator(mode="after")
    def observation(self):
        target = self.tool_name == "target_database_search"
        statuses = {status.value for status in ObservationStatus}
        if target:
            statuses.update(LOOKUP_STATUS)
        if self.status is not None and self.status not in statuses:
            raise ValueError("Invalid target observation status")
        effective = LOOKUP_STATUS.get(self.status, self.status) if target else self.status
        if (self.success and (self.error is not None or effective not in {None, "succeeded", "partial"})
                or not self.success and effective == "succeeded"):
            raise ValueError("Conflicting target observation")
        if target and ((self.status == "resolved" and not self.data)
                       or (self.status == "not_found" and self.data)):
            raise ValueError("Lookup outcome contradicts records")
        quality = self.quality or {}
        QualityFields.model_validate(quality)
        if target:
            if "lookup_status" in quality:
                lookup = quality["lookup_status"]
                if lookup not in LOOKUP_STATUS:
                    raise ValueError("Unknown lookup metadata")
                # Raw domain statuses must match exactly. Canonical envelopes
                # retain the domain status while exposing its mapped observation.
                if ((self.status in {"resolved", "not_found", "ambiguous"} and lookup != self.status)
                        or LOOKUP_STATUS[lookup] != (effective or ("succeeded" if self.success else "failed"))):
                    raise ValueError("Conflicting lookup metadata")
                if ((lookup == "resolved" and not self.data)
                        or (lookup == "not_found" and self.data)):
                    raise ValueError("Lookup metadata contradicts records")
            if "lookup_path" in self.model_fields_set and "lookup_path" in quality and quality["lookup_path"] != self.lookup_path:
                raise ValueError("Conflicting lookup path")
        row_view = TargetRecord if target else ReverseRecord
        for row in self.data or []:
            row_view.model_validate(_dict(row))
        for entry in self.evidence or []:
            SourceFields.model_validate(entry)
        if self.provenance is not None:
            provenance = ToolProvenance.from_dict(vars(self.provenance) if isinstance(self.provenance, ToolProvenance) else self.provenance)
            if provenance.tool_name != self.tool_name:
                raise ValueError("Invalid target provenance identity")
        observation = ToolResult(self.tool_name, self.success, "", data=self.data,
                                 evidence=self.evidence or [], quality=quality)
        if TargetEvidenceValidator().validate(observation):
            raise ValueError("Invalid target evidence")
        return self


class ReverseRawOutput(TargetRawOutput):
    tool_name: Literal["reverse_target_predictor"] = "reverse_target_predictor"


class TargetSearchOutput(TargetRawOutput):
    schema_version: ClassVar[str] = "1"
    tool_name: Literal["target_database_search"]
    success: bool
    status: ObservationStatus = Field(strict=True)
    error: Annotated[AgentExecutionError | None, BeforeValidator(_error)] = None
    warnings: list[str]
    evidence: list[dict[str, Any]]
    artifacts: list[Annotated[WorkflowArtifact, BeforeValidator(_artifact)]]
    quality: dict[str, Any]
    provenance: ToolProvenance | None = None


class ReverseTargetOutput(TargetSearchOutput):
    tool_name: Literal["reverse_target_predictor"]


class TargetToolAdapter(LegacyPythonToolAdapter):
    def _validate_input(self, input_data: Any) -> Any:
        if isinstance(input_data, BaseModel):
            input_data = input_data.model_dump(exclude_unset=True)
        wrapped = isinstance(input_data, Mapping) and "query" in input_data
        view = input_data if wrapped else {"query": input_data}
        self.spec.input_schema.model_validate(view)
        return input_data["query"] if wrapped else input_data

    def _input_validation_error(self, exc: ValidationError) -> ToolResult:
        return ToolResult.error_result(self.spec.name, AgentErrorCode.INVALID_INPUT,
                                       "Target input validation failed")

    def _invalid_output(self) -> ToolResult:
        return ToolResult.error_result(self.spec.name, AgentErrorCode.INVALID_OUTPUT,
                                       "Target output validation failed")

    def _validate_observation(self, raw):
        seen = set()
        for _ in range(17):
            if id(raw) in seen:
                raise ValueError("Cyclic target snapshot")
            seen.add(id(raw))
            if isinstance(raw, ToolResult):
                self.spec.output_schema.model_validate(vars(raw))
                error = raw.error
            else:
                schema = TargetRawOutput if self.spec.name == "target_database_search" else ReverseRawOutput
                schema.model_validate(_dict(raw))
                error = raw.get("error")
            details = (error.details if isinstance(error, AgentExecutionError) else
                       error.get("details") if isinstance(error, dict) else None)
            if not isinstance(details, dict) or "raw_result" not in details:
                return
            raw = details["raw_result"]
        raise ValueError("Target snapshot depth exceeded")

    def _map_lookup(self, raw):
        if not isinstance(raw, dict) or self.spec.name != "target_database_search":
            return raw
        mapped = dict(raw)
        quality = dict(raw.get("quality") or {})
        if raw.get("status") in LOOKUP_STATUS:
            quality["lookup_status"] = raw["status"]
            mapped["status"] = LOOKUP_STATUS[raw["status"]]
        if "lookup_path" in raw:
            quality["lookup_path"] = raw["lookup_path"]
        mapped["quality"] = quality
        return mapped

    def _invoke_guarded(self, payload: Any, raw_validator) -> ToolResult:
        raw = self.tool.execute(payload)
        # Outside the catch: caller exceptions keep the existing classification.
        if raw_validator is not None:
            raw_validator(raw)
        try:
            self._validate_observation(raw)
            mapped = self._map_lookup(raw)
            name = self.spec.name

            class CompletedInvocation:
                def __init__(self):
                    self.name = name

                def execute(self, _payload):
                    return mapped

            # Compat fills missing canonical timing in place with the completed
            # proxy's duration. Restore the producer's value (including None/0)
            # so only the outer framework supplies missing full-invocation time.
            elapsed_ms = raw.elapsed_ms if isinstance(raw, ToolResult) else None
            result = execute_tool_compat(CompletedInvocation(), payload)
            result.elapsed_ms = elapsed_ms
            if isinstance(raw, dict):
                # Compat keeps partial text but drops other failure text. Keep
                # ambiguity/provider explanations visible without changing status.
                result.formatted = raw.get("formatted", "")
            # Redaction is part of normalization too: keep it within the same
            # worker/deadline, then validate the actual returned observation.
            result = super()._normalize(result, None)
            self._validate_observation(result)
            return result
        except (ValidationError, ValueError, TypeError, AttributeError, OverflowError):
            return self._invalid_output()

    def _normalize(self, raw: ToolResult, elapsed_ms: int) -> ToolResult:
        # Only framework timing is attached outside the worker. No payload walk
        # or mutable validation marker is needed to bypass a second traversal.
        if raw.elapsed_ms is None:
            raw.elapsed_ms = elapsed_ms
        return raw

    def _validate_output(self, result: ToolResult) -> ToolResult:
        # Raw + compat + canonical checks already ran in the same worker/slot.
        # Adapter-generated timeout/provider/capacity errors are trusted envelopes.
        return result
