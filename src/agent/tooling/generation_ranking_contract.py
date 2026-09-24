"""Non-projecting generation/ranking views; domain producers still own science.

All output checks and inherited data redaction run in the invocation worker.
The caller never serializes a validation view back into a scientific result.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Annotated, Any, ClassVar, Literal

from pydantic import (
    BaseModel, BeforeValidator, ConfigDict, Field, TypeAdapter, ValidationError,
    model_validator,
)

from src.agent.contracts import (
    AgentErrorCode, AgentExecutionError, CandidateSet, ObservationStatus,
    ToolProvenance, ToolResult, WorkflowArtifact,
)
from src.agent.contracts.generation_request import validate_generation_count
from src.agent.validators.molecule_candidates import CandidateValidationUnavailable

from .adapters import LegacyPythonToolAdapter


_GEN = "llm_molecular_generator"
_RANK = "candidate_ranker"


class _View(BaseModel):
    model_config = ConfigDict(strict=True, extra="allow", revalidate_instances="always")


def _finite(value):
    try:
        valid = type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError("Expected finite number")
    return value


_Finite = Annotated[float, BeforeValidator(_finite), Field(allow_inf_nan=False)]
_Unit = Annotated[_Finite, Field(ge=0, le=1)]
_NUMBER = TypeAdapter(_Finite)
_OPTIONAL_NUMBER = TypeAdapter(_Finite | None)
_UNIT = TypeAdapter(_Unit)
_OPTIONAL_UNIT = TypeAdapter(_Unit | None)
_COUNT = TypeAdapter(Annotated[int, Field(ge=0)])
_POSITIVE = TypeAdapter(Annotated[int, Field(ge=1)])
_INT = TypeAdapter(int)
_TEXT = TypeAdapter(str)
_ID = TypeAdapter(Annotated[str, Field(min_length=1)])
_OPTIONAL_TEXT = TypeAdapter(str | None)
_BOOL = TypeAdapter(bool)
_OPTIONAL_BOOL = TypeAdapter(bool | None)
_STRINGS = TypeAdapter(list[str])


def _mapping(value):
    if not isinstance(value, Mapping):
        raise ValueError("Expected mapping")
    return value


def _dict(value):
    if not isinstance(value, dict):
        raise ValueError("Expected dictionary")
    return value


def _fields(value, fields, *, required=True):
    _mapping(value)
    for key, view in fields.items():
        if key in value:
            view.validate_python(value[key], strict=True)
        elif required:
            raise ValueError("Missing known field")


def _input_fields(value):
    if isinstance(value, BaseModel):
        return {**{key: getattr(value, key) for key in value.model_fields_set},
                **(value.model_extra or {})}
    return value


class _GenerationRequest(_View):
    query: str = ""
    metadata: Mapping[str, Any] | None = None
    outputs: Mapping[str, Any] | None = None

    @model_validator(mode="after")
    def metadata_types(self):
        if self.metadata is not None:
            if "requested_count" in self.metadata:
                validate_generation_count(self.metadata["requested_count"])
            if "temperature" in self.metadata:
                _NUMBER.validate_python(self.metadata["temperature"], strict=True)
        return self


class GenerationInput(_GenerationRequest):
    query: str | _GenerationRequest = ""

    @model_validator(mode="after")
    def outer_wrapper(self):
        if isinstance(self.query, _GenerationRequest) and set(_input_fields(self)) != {"query"}:
            raise ValueError("Ambiguous generation wrapper")
        return self


def _sequence(value):
    if not isinstance(value, (list, tuple)):
        raise ValueError("Expected record sequence")
    return value


def _ranking_inputs(outputs):
    _mapping(outputs)
    molecules = outputs.get("molecules")
    rows = molecules.get("candidates") if isinstance(molecules, Mapping) else molecules
    for row in _sequence(rows):
        _fields(row, {"smiles": _TEXT, "canonical_smiles": _TEXT, "candidate_id": _TEXT}, required=False)
        if not {"smiles", "canonical_smiles"}.intersection(row):
            raise ValueError("Missing candidate SMILES")
    for slot in ("properties", "admet", "activity"):
        value = outputs.get(slot)
        if value is None:
            continue
        rows = value["data"] if isinstance(value, Mapping) and isinstance(value.get("data"), list) else value
        for row in _sequence(rows):
            _fields(row, {"smiles": _TEXT})
            if slot == "activity":
                _fields(row, {"success": _BOOL, "normalized_activity": _OPTIONAL_NUMBER,
                              "probability": _OPTIONAL_NUMBER}, required=False)
                provenance = row.get("model_provenance")
                if provenance is not None:
                    _fields(provenance, {"model_id": _OPTIONAL_TEXT, "model_path": _OPTIONAL_TEXT,
                                        "weights_sha256": _OPTIONAL_TEXT,
                                        "demo_mode": _OPTIONAL_BOOL, "fallback_used": _OPTIONAL_BOOL},
                            required=False)
            elif row.get(slot) is not None:
                fields = ({"qed": _OPTIONAL_NUMBER, "logp": _OPTIONAL_NUMBER} if slot == "properties"
                          else {"prediction_method": _TEXT, "risk_count": _INT, "total_endpoints": _INT,
                                "demo_mode": _OPTIONAL_BOOL, "fallback_used": _OPTIONAL_BOOL})
                _fields(row[slot], fields, required=False)


class _RankingRequest(_View):
    query: str = ""
    metadata: Mapping[str, Any]
    outputs: Mapping[str, Any]

    @model_validator(mode="after")
    def domain_types(self):
        _fields(self.metadata, {"docking_top_n": _POSITIVE})
        _ranking_inputs(self.outputs)
        return self


class RankingInput(_View):
    query: str | _RankingRequest = ""
    metadata: Mapping[str, Any] | None = None
    outputs: Mapping[str, Any] | None = None

    @model_validator(mode="after")
    def outer_wrapper(self):
        fields = _input_fields(self)
        if isinstance(self.query, _RankingRequest):
            if set(fields) != {"query"}:
                raise ValueError("Ambiguous ranking wrapper")
        else:
            _RankingRequest.model_validate(fields)
        return self


def _generation_data(data):
    if isinstance(data, Mapping) and {"version", "requested_count", "candidates", "status"}.issubset(data):
        from src.agent.validators.result_validator import AgentResultValidator

        candidate_set = CandidateSet.from_dict(data)
        AgentResultValidator._revalidate_candidate_set(candidate_set, trusted_checkpoint=False)
        return True
    lists = [data]
    if isinstance(data, dict):
        lists = [data[key] for key in ("molecules", "candidates", "data") if key in data]
        if not lists:
            raise ValueError("Missing generated candidate list")
    for rows in lists:
        if not isinstance(rows, list):
            raise ValueError("Expected generated candidate list")
        for row in rows:
            if isinstance(row, str):
                continue
            _dict(row)
            _fields(row, {"smiles": _TEXT})
            _fields(row, {"source": _TEXT, "model": _TEXT}, required=False)
    return False


def _generation_quality(quality, canonical):
    _fields(quality, dict.fromkeys(("requested_count", "actual_count", "raw_count", "valid_count",
                                   "unique_count", "invalid_count", "duplicate_count"), _COUNT), required=False)
    _fields(quality, {"model": _TEXT, "output_contract": _TEXT, "validation_method": _OPTIONAL_TEXT,
                      "partial_generation": _BOOL, "validated": _BOOL, "validation_available": _BOOL},
            required=False)
    if not canonical and "requested_count" in quality:
        validate_generation_count(quality["requested_count"])


def _ranking_row(row):
    _dict(row)
    _fields(row, {"candidate_id": _ID, "canonical_smiles": _ID, "score": _UNIT,
                  "rank": _POSITIVE, "missing_evidence": _STRINGS, "docking_ready_for_preparation": _BOOL})
    evidence = _mapping(row.get("ranking_evidence"))
    _fields(evidence, {"property_score": _UNIT, "admet_score": _OPTIONAL_UNIT,
                       "activity_score": _OPTIONAL_UNIT, "missing_evidence": _STRINGS})
    weights = _mapping(evidence.get("weights_used"))
    _fields(weights, {"properties": _UNIT, "admet": _OPTIONAL_UNIT, "activity": _OPTIONAL_UNIT})
    missing = [key for key in ("admet", "activity") if evidence[key + "_score"] is None]
    if (row["missing_evidence"] != evidence["missing_evidence"] or row["missing_evidence"] != missing
            or any((weights[key] is None) != (key in missing) for key in ("admet", "activity"))):
        raise ValueError("Conflicting ranking evidence availability")


def _ranking_data(data):
    _dict(data)
    _fields(data, {"requested_top_n": _POSITIVE, "ranked_candidate_count": _COUNT})
    for key in ("top_candidates", "ranked_candidates", "unrankable_candidates"):
        if not isinstance(data.get(key), list):
            raise ValueError("Missing ranking output list")
    ranked, top, unrankable = (data[key] for key in ("ranked_candidates", "top_candidates", "unrankable_candidates"))
    for row in ranked + top:
        _ranking_row(row)
    for row in unrankable:
        _dict(row)
        _fields(row, {"candidate_id": _ID, "canonical_smiles": _ID, "reason": _TEXT})
    smiles = [row["canonical_smiles"] for row in ranked]
    if (data["ranked_candidate_count"] != len(ranked)
            or [row["rank"] for row in ranked] != list(range(1, len(ranked) + 1))
            or len(set(smiles)) != len(smiles)
            or top != ranked[:data["requested_top_n"]]
            or set(smiles).intersection(row["canonical_smiles"] for row in unrankable)):
        raise ValueError("Conflicting ranking output")


class _ErrorFields(_View):
    code: str | None = None
    message: str | None = None
    details: dict[str, Any] | None = None


def _error_view(value):
    if isinstance(value, AgentExecutionError):
        if not isinstance(value.code, AgentErrorCode):
            raise ValueError("Invalid error code")
        _ErrorFields.model_validate(vars(value))
    return value


class _ArtifactFields(_View):
    artifact_type: str = "file"
    path: str = ""
    label: str = ""
    mime_type: str | None = None
    metadata: dict[str, Any] | None = None


def _artifact_view(value):
    _ArtifactFields.model_validate(vars(value) if isinstance(value, WorkflowArtifact) else _dict(value))
    return value


class _RawOutput(_View):
    tool_name: str | None = None
    success: bool = False
    status: ObservationStatus | None = Field(default=None, strict=False)
    data: Any = None
    error: str | Annotated[_ErrorFields, BeforeValidator(_dict)] | None = None
    message: str = ""
    formatted: str = ""
    elapsed_ms: Annotated[int, Field(ge=0)] | None = None
    warnings: list[str] | None = None
    evidence: list[dict[str, Any]] | None = None
    artifacts: list[Annotated[dict[str, Any] | WorkflowArtifact, BeforeValidator(_artifact_view)]] | None = None
    quality: dict[str, Any] | None = None
    provenance: dict[str, Any] | ToolProvenance | None = None

    @model_validator(mode="after")
    def observations(self, info):
        name = (info.context or {}).get("tool_name", self.tool_name)
        if name not in {_GEN, _RANK} or ("tool_name" in self.model_fields_set and self.tool_name != name):
            raise ValueError("Invalid observation identity")
        if self.provenance is not None:
            provenance = ToolProvenance.from_dict(
                vars(self.provenance) if isinstance(self.provenance, ToolProvenance) else self.provenance)
            if provenance.tool_name != name:
                raise ValueError("Invalid provenance identity")
        if self.success:
            if (self.data is None or self.error is not None
                    or self.status not in {None, ObservationStatus.SUCCEEDED, ObservationStatus.PARTIAL}):
                raise ValueError("Invalid success observation")
        elif self.status is ObservationStatus.SUCCEEDED:
            raise ValueError("Invalid failure status")
        if name == _GEN:
            canonical = _generation_data(self.data) if self.data is not None else False
            _generation_quality(self.quality or {}, canonical)
        else:
            if self.data is not None:
                _ranking_data(self.data)
                if self.success and not self.data["ranked_candidates"]:
                    raise ValueError("Empty ranking success")
            _fields(self.quality or {}, {
                "output_contract": TypeAdapter(Literal["CandidateRanking@1"]),
                "deterministic": _BOOL,
                "scientific_claim_scope": TypeAdapter(Literal["candidate_prioritization"]),
            }, required=False)
            for item in self.evidence or []:
                if item.get("type") == "deterministic_candidate_ranking":
                    _fields(item, {"method": TypeAdapter(Literal["property_admet_activity_weighted_normalization"]),
                                   "ranked_candidate_count": _COUNT})
                    if self.data is not None and item["ranked_candidate_count"] != self.data["ranked_candidate_count"]:
                        raise ValueError("Conflicting ranking evidence count")
        return self


class _Output(_RawOutput):
    schema_version: ClassVar[str] = "1"
    success: bool
    status: ObservationStatus = Field(strict=True)
    error: Annotated[AgentExecutionError | None, BeforeValidator(_error_view)] = None
    warnings: list[str]
    evidence: list[dict[str, Any]]
    artifacts: list[Annotated[WorkflowArtifact, BeforeValidator(_artifact_view)]]
    quality: dict[str, Any]
    provenance: ToolProvenance | None = None


class GenerationOutput(_Output):
    tool_name: Literal["llm_molecular_generator"]


class RankingOutput(_Output):
    tool_name: Literal["candidate_ranker"]


class _InvalidOutput(Exception):
    """Our checks only; never reinterpret a caller validator's exception."""


class _ValidationUnavailable(Exception):
    """Only domain checks, not caller callbacks, may produce this marker."""


class _CandidateContractAdapter(LegacyPythonToolAdapter):
    def _validate_input(self, input_data):
        payload = {"query": input_data} if isinstance(input_data, str) else _input_fields(input_data)
        if isinstance(payload, Mapping) and isinstance(payload.get("query"), BaseModel):
            payload = {**payload, "query": _input_fields(payload["query"])}
        view = self.spec.input_schema.model_validate(payload)
        if isinstance(view.query, BaseModel):
            return payload["query"]
        if self.spec.name == _GEN and set(payload) == {"query"}:
            return payload["query"]
        return payload

    def _input_validation_error(self, exc):
        return ToolResult.error_result(self.spec.name, AgentErrorCode.INVALID_INPUT,
                                       "Candidate tool input validation failed")

    def _invalid_output(self, elapsed_ms=None):
        if type(elapsed_ms) is not int or elapsed_ms < 0:
            elapsed_ms = None
        return ToolResult.error_result(self.spec.name, AgentErrorCode.INVALID_OUTPUT,
                                       "Candidate tool output validation failed", elapsed_ms=elapsed_ms)

    def _validate_observation(self, raw):
        seen = set()
        context = {"tool_name": self.spec.name}
        for _ in range(17):
            if id(raw) in seen:
                raise ValueError("Cyclic candidate snapshot")
            seen.add(id(raw))
            if isinstance(raw, ToolResult):
                self.spec.output_schema.model_validate(vars(raw), context=context)
                error = raw.error
            else:
                _RawOutput.model_validate(_dict(raw), context=context)
                error = raw.get("error")
            details = (error.details if isinstance(error, AgentExecutionError) else
                       error.get("details") if isinstance(error, dict) else None)
            if not isinstance(details, dict) or "raw_result" not in details:
                return
            raw = details["raw_result"]
        raise ValueError("Candidate snapshot depth exceeded")

    def _invoke_guarded(self, payload, raw_validator):
        def validate(raw):
            if raw_validator is not None:
                raw_validator(raw)
            try:
                self._validate_observation(raw)
            except CandidateValidationUnavailable:
                raise _ValidationUnavailable from None
            except (ValidationError, ValueError, TypeError, AttributeError, OverflowError):
                raise _InvalidOutput from None

        try:
            result = super()._invoke_guarded(payload, validate)
        except _InvalidOutput:
            return self._invalid_output()
        except _ValidationUnavailable:
            return self._validation_unavailable()
        # Mirror the approved analysis lifecycle, without coupling to that tool.
        result = super()._normalize(result, None)
        try:
            self._validate_observation(result)
        except CandidateValidationUnavailable:
            return self._validation_unavailable()
        except (ValidationError, ValueError, TypeError, AttributeError, OverflowError):
            return self._invalid_output(result.elapsed_ms)
        return result

    def _validation_unavailable(self):
        return ToolResult.error_result(self.spec.name, AgentErrorCode.TOOL_UNAVAILABLE,
                                       "Candidate validation unavailable", status=ObservationStatus.UNAVAILABLE)

    def _normalize(self, raw, elapsed_ms):
        if raw.elapsed_ms is None:
            raw.elapsed_ms = elapsed_ms
        return raw

    def _validate_output(self, result):
        # Already checked in worker; framework timeout/capacity errors have no data.
        return result


class GenerationToolAdapter(_CandidateContractAdapter):
    pass


class RankingToolAdapter(_CandidateContractAdapter):
    pass
