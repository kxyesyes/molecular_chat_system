"""Analysis-only validation views, never projections or scientific calculations.

The three legacy producers consume strings. Their named scientific payloads live
in data rows, not evidence: current producers and ADMETResultValidator define no
scientific evidence slots. Arbitrary evidence/quality/extensions stay opaque.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, ClassVar, Literal

from pydantic import (
    BaseModel, BeforeValidator, ConfigDict, Field, TypeAdapter, ValidationError,
    model_validator,
)

from src.agent.contracts import (
    AgentErrorCode, AgentExecutionError, ObservationStatus, ToolProvenance,
    ToolResult, WorkflowArtifact,
)
from src.agent.validators.domain_validators import ADMETResultValidator

from .adapters import LegacyPythonToolAdapter


class _View(BaseModel):
    model_config = ConfigDict(strict=True, extra="allow", revalidate_instances="always")


class AnalysisInput(_View):
    query: str


# Shared strict leaf views keep rule-detail dictionaries readable without one
# DTO per descriptor/rule/section. Validate only; never return coerced values.
_NUMBER = TypeAdapter(Annotated[float, Field(allow_inf_nan=False)])
_COUNT = TypeAdapter(Annotated[int, Field(ge=0)])
_UNIT = TypeAdapter(Annotated[float, Field(allow_inf_nan=False, ge=0, le=1)])
_NONNEGATIVE = TypeAdapter(Annotated[float, Field(allow_inf_nan=False, ge=0)])
_SA = TypeAdapter(Annotated[float, Field(allow_inf_nan=False, ge=1, le=10)])
_VIOLATIONS = TypeAdapter(Annotated[int, Field(ge=0, le=4)])
_TEXT = TypeAdapter(str)
_IDENTIFIER = TypeAdapter(Annotated[str, Field(min_length=1)])
_BOOL = TypeAdapter(bool)
_UNCOMPUTED_ALERT = TypeAdapter(bool | None)
_DICT = TypeAdapter(dict[str, Any])
_STRINGS = TypeAdapter(list[str])


def _require_dict(value):
    if not isinstance(value, dict):
        raise ValueError("Analysis record must be a dictionary")
    return value


def _fields(value, fields, *, required=True):
    _require_dict(value)
    for key, view in fields.items():
        if key not in value:
            if required:
                raise ValueError("Missing analysis field")
        else:
            view.validate_python(value[key], strict=True)


_DESCRIPTORS = dict(molecular_weight=_NUMBER, logp=_NUMBER, tpsa=_NUMBER,
                    hba=_COUNT, hbd=_COUNT, rotatable_bonds=_COUNT)


def _properties(value):
    _fields(value, dict(_DESCRIPTORS, molecular_formula=_IDENTIFIER, qed=_UNIT))


def _assessment(value):
    _fields(value, dict(qed_score=_UNIT, molecular_properties=_DICT,
                       lipinski_rule_of_five=_DICT, veber_rules=_DICT,
                       lead_likeness=_DICT, overall_assessment=_DICT))
    _fields(value["molecular_properties"], dict(_DESCRIPTORS, aromatic_rings=_COUNT, heteroatoms=_COUNT))
    lipinski = value["lipinski_rule_of_five"]
    _fields(lipinski, dict(violations=_STRINGS, violation_count=_VIOLATIONS,
                           compliance=_BOOL, details=_DICT))
    veber = value["veber_rules"]
    _fields(veber, dict(tpsa_compliance=_BOOL, rotatable_bonds_compliance=_BOOL,
                       overall_compliance=_BOOL, details=_DICT))
    lead = value["lead_likeness"]
    _fields(lead, dict(molecular_weight_compliance=_BOOL, logp_compliance=_BOOL,
                      overall_compliance=_BOOL, details=_DICT))
    for rules, names, limit in (
        (lipinski, ("molecular_weight", "logp", "hba", "hbd"), {"limit": _NUMBER}),
        (veber, ("tpsa", "rotatable_bonds"), {"limit": _NUMBER}),
        (lead, ("molecular_weight", "logp"), {"range": _TEXT}),
    ):
        _fields(rules["details"], dict.fromkeys(names, _DICT))
        for name in names:
            _fields(rules["details"][name], {"value": _DESCRIPTORS[name], "pass": _BOOL, **limit})
    overall = value["overall_assessment"]
    _fields(overall, dict(score=_UNIT, grade=_TEXT, category=_TEXT, component_scores=_DICT))
    _fields(overall["component_scores"], dict.fromkeys(("qed", "lipinski", "veber", "lead_likeness"), _UNIT))


_ADMET_SECTIONS = {
    "physicochemical": dict(
        formula=_IDENTIFIER, molecular_weight=_NUMBER, num_heavy_atoms=_COUNT,
        num_aromatic_atoms=_COUNT, sp3_carbon_ratio=_UNIT, num_rotatable_bonds=_COUNT,
        num_h_donors=_COUNT, num_h_acceptors=_COUNT, molar_refractivity=_NUMBER, tpsa=_NUMBER),
    "solubility": dict(log_s_esol=_NUMBER, solubility_esol=_NONNEGATIVE, class_esol=_TEXT),
    "lipophilicity": dict(wlogp=_NUMBER),
    "pharmacokinetics": dict(gastrointestinal_absorption=_TEXT,
                            blood_brain_barrier_permeant=_BOOL, skin_permeability_logkp=_NUMBER),
    "druglikeness": dict(lipinski=_TEXT, veber=_TEXT, ghose=_DICT),
    "medicinal": dict(pains=_UNCOMPUTED_ALERT, brenk=_UNCOMPUTED_ALERT, zinc=_UNCOMPUTED_ALERT,
                      synthetic_accessibility=_SA, leadlikeness=_DICT),
}
_METHOD = TypeAdapter(Literal["rdkit_rules", "adme_py"])


def _admet(value):
    _fields(value, dict(prediction_method=_METHOD, backend_version=_IDENTIFIER,
                        **dict.fromkeys(_ADMET_SECTIONS, _DICT)))
    for section, fields in _ADMET_SECTIONS.items():
        _fields(value[section], fields, required=value["prediction_method"] == "rdkit_rules")


_PAYLOADS = {
    "property_calculator": ("properties", _properties),
    "drug_likeness_assessment": ("assessment", _assessment),
    "admet_predictor": ("admet", _admet),
}


class _ErrorFields(_View):
    code: str | None = None
    message: str | None = None
    details: dict[str, Any] | None = None


def _error_view(value):
    if isinstance(value, AgentExecutionError):
        if not isinstance(value.code, AgentErrorCode):
            raise ValueError("Invalid analysis error code")
        _ErrorFields.model_validate(vars(value))
    return value


class _ArtifactFields(_View):
    artifact_type: str = "file"
    path: str = ""
    label: str = ""
    mime_type: str | None = None
    metadata: dict[str, Any] | None = None


def _artifact_view(value):
    _ArtifactFields.model_validate(vars(value) if isinstance(value, WorkflowArtifact) else _require_dict(value))
    return value


class _AnalysisRawOutput(_View):
    tool_name: str | None = None
    success: bool = False
    status: ObservationStatus | None = Field(default=None, strict=False)
    data: list[dict[str, Any]] | None = None
    error: str | Annotated[_ErrorFields, BeforeValidator(_require_dict)] | None = None
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
        if name not in _PAYLOADS or ("tool_name" in self.model_fields_set and self.tool_name != name):
            raise ValueError("Invalid analysis tool identity")
        if self.provenance is not None:
            provenance = ToolProvenance.from_dict(
                vars(self.provenance) if isinstance(self.provenance, ToolProvenance) else self.provenance)
            if provenance.tool_name != name:
                raise ValueError("Invalid analysis provenance identity")
        slot, validate = _PAYLOADS[name]
        for row in self.data or []:
            _fields(row, {"smiles": _IDENTIFIER, slot: _DICT})
            validate(row[slot])
        if self.success:
            if (not self.data or self.error is not None or self.status not in
                    {None, ObservationStatus.SUCCEEDED, ObservationStatus.PARTIAL}):
                raise ValueError("Invalid analysis success")
        elif self.status is ObservationStatus.SUCCEEDED:
            raise ValueError("Invalid analysis failure status")
        # Reuse domain method/provenance policy; structural checks are not proof
        # of a real backend, experimental evidence, or an installed version.
        if name == "admet_predictor" and ADMETResultValidator().validate(
                ToolResult(name, self.success, "", data=self.data)):
            raise ValueError("Invalid ADMET domain observation")
        return self


class _AnalysisOutput(_AnalysisRawOutput):
    schema_version: ClassVar[str] = "1"
    success: bool
    status: ObservationStatus = Field(strict=True)
    error: Annotated[AgentExecutionError | None, BeforeValidator(_error_view)] = None
    warnings: list[str]
    evidence: list[dict[str, Any]]
    artifacts: list[Annotated[WorkflowArtifact, BeforeValidator(_artifact_view)]]
    quality: dict[str, Any]
    provenance: ToolProvenance | None = None


class PropertyOutput(_AnalysisOutput):
    tool_name: Literal["property_calculator"]


class DrugLikenessOutput(_AnalysisOutput):
    tool_name: Literal["drug_likeness_assessment"]


class ADMETOutput(_AnalysisOutput):
    tool_name: Literal["admet_predictor"]


ANALYSIS_OUTPUT_SCHEMAS = {
    "property_calculator": PropertyOutput,
    "drug_likeness_assessment": DrugLikenessOutput,
    "admet_predictor": ADMETOutput,
}


class _InvalidAnalysisOutput(Exception):
    """Only our checks use this marker; caller errors keep their classification."""


class AnalysisToolAdapter(LegacyPythonToolAdapter):
    def _validate_input(self, input_data: Any) -> str:
        if isinstance(input_data, str):
            input_data = {"query": input_data}
        elif isinstance(input_data, Mapping):
            input_data = dict(input_data)
        return self.spec.input_schema.model_validate(input_data).query

    def _input_validation_error(self, exc: ValidationError) -> ToolResult:
        return ToolResult.error_result(self.spec.name, AgentErrorCode.INVALID_INPUT,
                                       "Analysis input validation failed")

    def _invalid_output(self, elapsed_ms=None) -> ToolResult:
        if type(elapsed_ms) is not int or elapsed_ms < 0:
            elapsed_ms = None
        return ToolResult.error_result(self.spec.name, AgentErrorCode.INVALID_OUTPUT,
                                       "Analysis output validation failed", elapsed_ms=elapsed_ms)

    def _validate_observation(self, raw):
        # Follow only compat's known failure snapshot chain, not arbitrary
        # metadata. Permit at most 16 embedded snapshots; reject cycles.
        seen = set()
        context = {"tool_name": self.spec.name}
        for _ in range(17):
            if id(raw) in seen:
                raise ValueError("Cyclic analysis snapshot")
            seen.add(id(raw))
            if isinstance(raw, ToolResult):
                self.spec.output_schema.model_validate(vars(raw), context=context)
                error = raw.error
            else:
                _AnalysisRawOutput.model_validate(_require_dict(raw), context=context)
                error = raw.get("error")
            details = (error.details if isinstance(error, AgentExecutionError) else
                       error.get("details") if isinstance(error, dict) else None)
            if not isinstance(details, dict) or "raw_result" not in details:
                return
            raw = details["raw_result"]
        raise ValueError("Analysis snapshot depth exceeded")

    def _invoke_guarded(self, payload: Any, raw_validator) -> Any:
        def validate(raw):
            if raw_validator is not None:
                raw_validator(raw)
            try:
                self._validate_observation(raw)
            except (ValidationError, ValueError, TypeError, AttributeError, OverflowError):
                raise _InvalidAnalysisOutput from None

        try:
            result = super()._invoke_guarded(payload, validate)
        except _InvalidAnalysisOutput:
            return self._invalid_output()
        # Include the existing data redaction in the worker, then validate the
        # whole normalized result. Redaction is not assumed to preserve known
        # fields (e.g. a custom sensitive_fields policy could redact a number).
        result = super()._normalize(result, None)
        try:
            self._validate_observation(result)
        except (ValidationError, ValueError, TypeError, AttributeError, OverflowError):
            return self._invalid_output(result.elapsed_ms)
        return result

    def _normalize(self, raw: ToolResult, elapsed_ms: int) -> ToolResult:
        # The worker already normalized/redacted/validated every producer
        # result. Outside its deadline, only attach the framework's integer
        # duration; never transform or traverse scientific payloads here.
        if raw.elapsed_ms is None:
            raw.elapsed_ms = elapsed_ms
        return raw

    def _validate_output(self, result: ToolResult) -> ToolResult:
        # Inherited execute reaches this after worker completion/slot release.
        # Producer results were checked in _invoke_guarded; other results are
        # framework-created no-data errors (timeout, capacity, dependency, etc.).
        # Neither path introduces unchecked scientific fields on this thread.
        return result
