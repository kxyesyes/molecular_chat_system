"""Non-projecting RAG validation views, not a second result normalizer.

Structural provenance validation does not establish scientific authenticity;
that remains the responsibility of the manifest-validated retrieval service.
"""
from __future__ import annotations

from typing import Annotated, Any, ClassVar

from pydantic import (
    BaseModel, BeforeValidator, ConfigDict, Field, ValidationError, model_validator,
)

from src.agent.contracts import (
    AgentErrorCode, AgentExecutionError, ObservationStatus, ToolProvenance,
    ToolResult, WorkflowArtifact,
)

from .adapters import LegacyPythonToolAdapter


class RAGSearchInput(BaseModel):
    model_config = ConfigDict(strict=True, revalidate_instances="always")
    query: str


NonemptyText = Annotated[str, Field(min_length=1)]
NonnegativeIndex = Annotated[int, Field(ge=0)]


def _require_dict(value: Any) -> dict:
    # Validation views are not transport values. Even a valid BaseModel must not
    # survive non-projecting validation as a non-serializable retrieval record.
    if not isinstance(value, dict):
        raise ValueError("RAG field must be a dictionary")
    return value


class RAGRecordProvenance(BaseModel):
    model_config = ConfigDict(strict=True, extra="allow")
    source_path: NonemptyText
    source_sha256: NonemptyText
    index_sha256: NonemptyText
    embedding_model: NonemptyText
    manifest_schema_version: Annotated[int, Field(ge=1)]
    builder_version: NonemptyText
    vector_label: NonnegativeIndex


class RAGRecord(BaseModel):
    model_config = ConfigDict(strict=True, extra="allow")
    source_index: NonnegativeIndex
    similarity_score: Annotated[float, Field(allow_inf_nan=False)]
    provenance: Annotated[RAGRecordProvenance, BeforeValidator(_require_dict)]


class RAGErrorFields(BaseModel):
    """Legacy structured errors need not supply every canonical error field."""

    model_config = ConfigDict(strict=True, extra="allow")
    code: str | None = None
    message: str | None = None
    details: dict[str, Any] | None = None


class RAGArtifactFields(BaseModel):
    """Validate supplied fields; compat still owns legacy artifact defaults."""

    model_config = ConfigDict(strict=True, extra="allow")
    artifact_type: str = "file"
    path: str = ""
    label: str = ""
    mime_type: str | None = None
    metadata: dict[str, Any] | None = None


class RAGRawOutput(BaseModel):
    """Inspect legacy envelopes before compat can discard failed result data."""

    model_config = ConfigDict(strict=True, extra="allow", revalidate_instances="always")
    success: bool = False
    status: ObservationStatus | None = Field(default=None, strict=False)
    data: list[Annotated[RAGRecord, BeforeValidator(_require_dict)]] | None = None
    error: str | Annotated[RAGErrorFields, BeforeValidator(_require_dict)] | None = None
    message: str = ""
    formatted: str = ""
    warnings: list[str] | None = None
    evidence: list[dict[str, Any]] | None = None
    artifacts: list[
        Annotated[RAGArtifactFields, BeforeValidator(_require_dict)] | WorkflowArtifact
    ] | None = None
    quality: dict[str, Any] | None = None
    provenance: dict[str, Any] | ToolProvenance | None = None

    @model_validator(mode="after")
    def consistent_observation(self):
        # Do not infer or rewrite statuses: execute_tool_compat owns normalization.
        if self.success:
            if self.error is not None or self.status not in {
                None, ObservationStatus.SUCCEEDED, ObservationStatus.PARTIAL,
            } or self.data is None:
                raise ValueError("Invalid RAG observation")
        elif self.status is ObservationStatus.SUCCEEDED:
            raise ValueError("Invalid RAG observation")
        return self


class RAGSearchOutput(RAGRawOutput):
    """Versioned validation view of the existing canonical ToolResult envelope."""

    schema_version: ClassVar[str] = "1"
    success: bool
    status: ObservationStatus = Field(strict=True)
    error: AgentExecutionError | None = None
    warnings: list[str]
    evidence: list[dict[str, Any]]
    artifacts: list[WorkflowArtifact]
    quality: dict[str, Any]
    provenance: ToolProvenance | None = None


class _InvalidRAGOutput(Exception):
    """Private marker separating our validation from the caller's validator."""


class RAGToolAdapter(LegacyPythonToolAdapter):
    def _validate_input(self, input_data: Any) -> Any:
        if isinstance(input_data, str):
            input_data = {"query": input_data}
        return self.spec.input_schema.model_validate(input_data).query

    def _input_validation_error(self, exc: ValidationError) -> ToolResult:
        # ValidationError.errors() includes the rejected query; never expose it.
        return ToolResult.error_result(
            self.spec.name, AgentErrorCode.INVALID_INPUT,
            "RAG input validation failed",
        )

    def _invalid_output(self, elapsed_ms=None) -> ToolResult:
        return ToolResult.error_result(
            self.spec.name, AgentErrorCode.INVALID_OUTPUT,
            "RAG output validation failed", elapsed_ms=elapsed_ms,
        )

    def _invoke_guarded(self, payload: Any, raw_validator) -> Any:
        def validate(raw):
            if raw_validator is not None:
                raw_validator(raw)
            try:
                if isinstance(raw, ToolResult):
                    self.spec.output_schema.model_validate(vars(raw))
                elif isinstance(raw, dict):
                    RAGRawOutput.model_validate(raw)
                else:
                    RAGRawOutput.model_validate({"success": True, "data": raw})
            except ValidationError:
                raise _InvalidRAGOutput from None

        try:
            # Executes once, inside the existing worker/slot/deadline. The legacy
            # adapter alone performs compat normalization and provenance handling.
            return super()._invoke_guarded(payload, validate)
        except _InvalidRAGOutput:
            return self._invalid_output()

    def _validate_output(self, result: ToolResult) -> ToolResult:
        try:
            self.spec.output_schema.model_validate(vars(result))
        except ValidationError:
            return self._invalid_output(result.elapsed_ms)
        # Never project a model_dump into data or metadata (including CSV extras).
        return result
