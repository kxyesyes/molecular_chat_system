"""Activity-only, non-projecting views around the existing compat executor.

These checks establish transport and observation contracts, not the authenticity
of model weights. Domain services still own parsing, inference and alignment.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, ClassVar, Literal

from pydantic import (
    BaseModel, BeforeValidator, ConfigDict, Field, ValidationError,
    field_validator, model_validator,
)

from src.agent.contracts import (
    AgentErrorCode, AgentExecutionError, ObservationStatus, ToolProvenance,
    ToolResult, WorkflowArtifact,
)
from src.agent.validators.domain_validators import ActivityResultValidator

from .adapters import LegacyPythonToolAdapter


class ActivityStructuredInput(BaseModel):
    model_config = ConfigDict(strict=True, extra="allow", revalidate_instances="always")
    query: str = ""
    smiles: str | list[str] | tuple[str, ...] | None = None
    target: str | None = None

    @model_validator(mode="after")
    def supplied_fields(self):
        if not self.model_fields_set.intersection({"query", "smiles"}):
            raise ValueError("Activity input is missing")
        if any(getattr(self, key) is None for key in ("smiles", "target")
               if key in self.model_fields_set):
            raise ValueError("Explicit null activity field")
        return self


class ActivityPredictInput(ActivityStructuredInput):
    query: str | ActivityStructuredInput = ""

    @model_validator(mode="after")
    def unambiguous_wrapper(self):
        if isinstance(self.query, ActivityStructuredInput) and self.model_fields_set.intersection({"smiles", "target"}):
            raise ValueError("Ambiguous activity wrapper")
        return self


def _require_dict(value: Any) -> dict:
    if not isinstance(value, dict):
        raise ValueError("Activity record must be a dictionary")
    return value


class ActivityRow(BaseModel):
    model_config = ConfigDict(strict=True, extra="allow", revalidate_instances="always")
    smiles: str
    success: bool


FiniteNumber = Annotated[float, Field(allow_inf_nan=False)]


class ActivitySingleRow(ActivityRow):
    task_type: Literal["regression", "classification"] | None = None
    endpoint: str | None = None
    units: str | None = None
    value: FiniteNumber | None = None
    probability: Annotated[float, Field(allow_inf_nan=False, ge=0, le=1)] | None = None
    error: str | None = None
    model_provenance: dict[str, Any] | None = None

    @model_validator(mode="after")
    def observed_prediction(self):
        if self.success or self.value is not None or self.probability is not None:
            # Pure metadata validation only: never construct/load a predictor.
            from src.activity.predictor import _validate_prediction_metadata

            metadata = _validate_prediction_metadata(self.model_provenance)
            if (metadata.get("demo_mode") is not False
                    or metadata.get("fallback_used", False) is not False
                    or any(metadata[key] != getattr(self, key)
                           for key in ("task_type", "endpoint", "units"))
                    or (self.task_type == "regression" and self.value is None)
                    or (self.task_type == "classification" and self.probability is None)
                    or (self.success and self.error is not None)):
                raise ValueError("Invalid activity observation")
        elif not self.error:
            raise ValueError("Missing activity failure evidence")
        return self


class ActivityErrorFields(BaseModel):
    model_config = ConfigDict(strict=True, extra="allow")
    code: str | None = None
    message: str | None = None
    details: dict[str, Any] | None = None


class ActivityArtifactFields(BaseModel):
    """Validate supplied fields without replacing compat's legacy defaults."""

    model_config = ConfigDict(strict=True, extra="allow")
    artifact_type: str = "file"
    path: str = ""
    label: str = ""
    mime_type: str | None = None
    metadata: dict[str, Any] | None = None


def _artifact_view(value):
    ActivityArtifactFields.model_validate(vars(value) if isinstance(value, WorkflowArtifact) else _require_dict(value))
    return value


def _provenance_view(value):
    if value is not None:
        provenance = ToolProvenance.from_dict(vars(value) if isinstance(value, ToolProvenance) else value)
        if provenance.tool_name != "activity_predictor":
            raise ValueError("Invalid activity provenance identity")
    return value


def _error_view(value):
    if isinstance(value, AgentExecutionError):
        if not isinstance(value.code, AgentErrorCode):
            raise ValueError("Invalid activity error code")
        ActivityErrorFields.model_validate(vars(value))
    return value


class ActivityRawOutput(BaseModel):
    """Validate raw observations before failed data can move into raw_result."""

    model_config = ConfigDict(strict=True, extra="allow", revalidate_instances="always")
    tool_name: Literal["activity_predictor"] = "activity_predictor"
    success: bool = False
    status: ObservationStatus | None = Field(default=None, strict=False)
    data: list[Annotated[dict[str, Any], BeforeValidator(_require_dict)]] | None = None
    error: str | Annotated[ActivityErrorFields, BeforeValidator(_require_dict)] | None = None
    message: str = ""
    formatted: str = ""
    warnings: list[str] | None = None
    evidence: list[dict[str, Any]] | None = None
    artifacts: list[Annotated[dict[str, Any] | WorkflowArtifact, BeforeValidator(_artifact_view)]] | None = None
    quality: dict[str, Any] | None = None
    provenance: Annotated[dict[str, Any] | ToolProvenance | None, BeforeValidator(_provenance_view)] = None

    @field_validator("data")
    @classmethod
    def validate_rows(cls, rows):
        for row in rows or []:
            _validate_prediction_row(row)
        return rows

    @model_validator(mode="after")
    def consistent_observations(self):
        if self.success:
            if (self.error is not None or self.status not in {
                    None, ObservationStatus.SUCCEEDED, ObservationStatus.PARTIAL,
            } or not self.data or not any(row["success"] for row in self.data)):
                raise ValueError("Invalid activity success")
        elif self.status is ObservationStatus.SUCCEEDED:
            raise ValueError("Invalid activity status")

        # Reuse the full domain gate, including historical activity_score/pic50
        # claims. No thresholds, stage rules or hash algorithms are duplicated.
        # Evidence snapshots are independent observations, not a second batch
        # whose status must equal this envelope. Only the named prediction slot
        # is scientific data; arbitrary evidence/quality extensions stay opaque.
        predictions = [entry["prediction"] for entry in self.evidence or []
                       if "prediction" in entry]
        for row in predictions:
            _validate_prediction_row(row)
        observation = ToolResult("activity_predictor", self.success, "",
                                 data=(self.data or []) + predictions,
                                 quality=self.quality or {})
        if ActivityResultValidator().validate(observation):
            raise ValueError("Invalid activity domain observation")
        if self.data and any(_is_family(row) for row in self.data):
            from src.activity.prediction_service import summarize_predictions

            summary = summarize_predictions(self.data)
            expected_status = {"passed": ObservationStatus.SUCCEEDED,
                               "partial": ObservationStatus.PARTIAL,
                               "failed": ObservationStatus.FAILED}[summary["status"]]
            if (self.success is not summary["success"]
                    or (self.status is not None and self.status is not expected_status)):
                raise ValueError("Invalid activity batch summary")
        return self


def _is_family(row):
    return any(key in row for key in ("family_id", "predicted_pIC50", "activity_probability", "activity_class"))


def _validate_prediction_row(row):
    _require_dict(row)
    ActivityRow.model_validate(row)
    # Family identity cannot exempt overlapping single-model scientific claims.
    # Units alone are shared with family predictions and are not a discriminator.
    if not _is_family(row) or any(key in row for key in (
            "task_type", "value", "probability", "model_provenance")):
        ActivitySingleRow.model_validate(row)


class ActivityPredictOutput(ActivityRawOutput):
    """Versioned validation view; never serialized back into the observation."""

    schema_version: ClassVar[str] = "1"
    tool_name: Literal["activity_predictor"]
    success: bool
    status: ObservationStatus = Field(strict=True)
    error: Annotated[AgentExecutionError | None, BeforeValidator(_error_view)] = None
    warnings: list[str]
    evidence: list[dict[str, Any]]
    artifacts: list[Annotated[WorkflowArtifact, BeforeValidator(_artifact_view)]]
    quality: dict[str, Any]
    provenance: Annotated[ToolProvenance | None, BeforeValidator(_provenance_view)] = None


class _InvalidActivityOutput(Exception):
    """Private marker: caller validation errors retain their existing meaning."""


class ActivityToolAdapter(LegacyPythonToolAdapter):
    def _validate_observation(self, raw):
        # Compat can receive already-normalized failures with no top-level data.
        # Follow only its known error.details.raw_result envelope chain, with no
        # recursive metadata walk. At most 16 embedded snapshots are permitted.
        seen = set()
        for _ in range(17):
            if id(raw) in seen:
                raise ValueError("Cyclic activity result snapshot")
            seen.add(id(raw))
            if isinstance(raw, ToolResult):
                self.spec.output_schema.model_validate(vars(raw))
                error = raw.error
            else:
                ActivityRawOutput.model_validate(_require_dict(raw))
                error = raw.get("error")
            details = (error.details if isinstance(error, AgentExecutionError) else
                       error.get("details") if isinstance(error, dict) else None)
            if not isinstance(details, dict) or "raw_result" not in details:
                return
            raw = details["raw_result"]
        raise ValueError("Activity result snapshot depth exceeded")

    def _validate_input(self, input_data: Any) -> Any:
        if isinstance(input_data, str):
            input_data = {"query": input_data}
        elif isinstance(input_data, Mapping):
            input_data = dict(input_data)
        model = self.spec.input_schema.model_validate(input_data)
        # Only model inputs need serialization. For transport mappings, preserve
        # the supplied structured payload, tuples, extras and missing query.
        if isinstance(input_data, BaseModel):
            input_data = model.model_dump(exclude_unset=True)
        if isinstance(model.query, ActivityStructuredInput):
            nested = input_data["query"]
            return nested.model_dump(exclude_unset=True) if isinstance(nested, BaseModel) else nested
        if model.model_fields_set.intersection({"smiles", "target"}):
            return input_data
        return model.query

    def _input_validation_error(self, exc: ValidationError) -> ToolResult:
        return ToolResult.error_result(self.spec.name, AgentErrorCode.INVALID_INPUT,
                                       "Activity input validation failed")

    def _invalid_output(self, elapsed_ms=None) -> ToolResult:
        return ToolResult.error_result(self.spec.name, AgentErrorCode.INVALID_OUTPUT,
                                       "Activity output validation failed", elapsed_ms=elapsed_ms)

    def _invoke_guarded(self, payload: Any, raw_validator) -> Any:
        def validate(raw):
            if raw_validator is not None:
                raw_validator(raw)
            try:
                self._validate_observation(raw)
            except (ValidationError, ValueError, TypeError, AttributeError, OverflowError):
                # Malformed nested domain values must not escape as payload-
                # bearing errors. The caller's validator is outside this catch.
                raise _InvalidActivityOutput from None

        try:
            return super()._invoke_guarded(payload, validate)
        except _InvalidActivityOutput:
            return self._invalid_output()

    def _validate_output(self, result: ToolResult) -> ToolResult:
        try:
            self._validate_observation(result)
        except (ValidationError, ValueError, TypeError, AttributeError, OverflowError):
            return self._invalid_output(result.elapsed_ms)
        return result
