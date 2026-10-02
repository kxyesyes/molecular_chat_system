"""Request-local activity model selection and identity binding.

The registry owns artifact integrity and active bundle selection.  This module
adds the missing request boundary: one request is checked against one verified
family bundle, endpoint, units, optional species scope, and validation level.
The returned identity is safe to persist in provenance/checkpoint metadata and
does not depend on later registry mutations.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping

from src.target_identifiers import BUCHE_ALIASES, PDE_ALIASES


class ModelSelectionError(ValueError):
    """A request cannot be served by the selected scientific model bundle."""


_ENDPOINT_ALIASES = {
    "pic50": "pIC50",
    "pki": "pKi",
    "pec50": "pEC50",
    "pkd": "pKd",
    "ic50": "IC50",
    "ki": "Ki",
    "ec50": "EC50",
    "kd": "Kd",
}
_P_ACTIVITY = frozenset({"pIC50", "pKi", "pEC50", "pKd"})


def _resolve_family(value: Any) -> str:
    if not isinstance(value, str):
        raise ModelSelectionError("target family is required or ambiguous")
    token = value.strip().casefold()
    if token in PDE_ALIASES:
        return "pde-family"
    if token in BUCHE_ALIASES:
        return "buche-family"
    raise ModelSelectionError("target family is required or ambiguous")


def _canonical_endpoint(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ModelSelectionError(f"{field} is required")
    try:
        return _ENDPOINT_ALIASES[value.strip().casefold()]
    except KeyError:
        raise ModelSelectionError(f"unsupported {field}") from None


def _canonical_units(value: Any, endpoint: str) -> str:
    if value is None:
        return endpoint
    if not isinstance(value, str) or not value.strip():
        raise ModelSelectionError("units is invalid")
    units = value.strip()
    if units.casefold() in {item.casefold() for item in _P_ACTIVITY}:
        units = next(item for item in _P_ACTIVITY if item.casefold() == units.casefold())
    if endpoint in _P_ACTIVITY and units != endpoint:
        raise ModelSelectionError("endpoint and units do not match")
    return units


def _normalise_species(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ModelSelectionError("species is invalid")
    return value.strip().casefold()


def _declared_species(model: Mapping[str, Any], bundle: Mapping[str, Any]) -> set[str] | None:
    value = model.get("species", model.get("species_scope"))
    if value is None and isinstance(bundle.get("scope"), Mapping):
        value = bundle["scope"].get("species")
    if value is None:
        return None
    values = value if isinstance(value, (list, tuple, set)) else [value]
    normalised = {str(item).strip().casefold() for item in values if str(item).strip()}
    return normalised or None


@dataclass(frozen=True)
class ActivityModelRequest:
    family_id: str
    endpoint: str = "pIC50"
    units: str = "pIC50"
    species: str | None = None
    validation: str = "endpoint_ready"

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None) -> "ActivityModelRequest":
        data = dict(payload or {})
        target = data.get("target", data.get("target_id"))
        try:
            family_id = _resolve_family(target)
        except (TypeError, ValueError):
            raise ModelSelectionError("target family is required or ambiguous") from None
        endpoint = _canonical_endpoint(data.get("endpoint", "pIC50"), "endpoint")
        units = _canonical_units(data.get("units"), endpoint)
        species = _normalise_species(data.get("species"))
        validation = data.get("validation", data.get("validation_level", "endpoint_ready"))
        if validation != "endpoint_ready":
            raise ModelSelectionError("validation is invalid")
        return cls(family_id, endpoint, units, species, validation.strip().casefold())


def _matches_species(request: ActivityModelRequest, model: Mapping[str, Any], bundle: Mapping[str, Any]) -> bool:
    if request.species is None:
        return True
    declared = _declared_species(model, bundle)
    if declared and declared & {"mixed", "any", "all"}:
        return True
    return declared == {request.species}


@dataclass(frozen=True)
class SelectedActivityModel:
    family_id: str
    endpoint: str
    units: str
    species: str | None
    validation: str
    identity: str


def select_family_bundle(bundle: Mapping[str, Any], request: ActivityModelRequest) -> SelectedActivityModel:
    """Fail closed unless both stages satisfy the exact request contract."""
    if request.validation != "endpoint_ready":
        raise ModelSelectionError("validation must be endpoint_ready")
    if not isinstance(bundle, Mapping) or bundle.get("family_id") != request.family_id:
        raise ModelSelectionError("target family model unavailable")
    models = bundle.get("models")
    if not isinstance(models, Mapping) or set(models) != {"classification", "regression"}:
        raise ModelSelectionError("validation: family model bundle is incomplete")

    for task, model in models.items():
        if not isinstance(model, Mapping):
            raise ModelSelectionError("validation: family model metadata is invalid")
        if model.get("target_id") != request.family_id:
            raise ModelSelectionError("target family model mismatch")
        if model.get("scientific_readiness") != request.validation:
            raise ModelSelectionError("validation: model readiness does not match request")
        if model.get("demo_mode") is not False or model.get("fallback_used") is not False:
            raise ModelSelectionError("demo/fallback model is not eligible")
        if not _matches_species(request, model, bundle):
            raise ModelSelectionError("species: no model matches requested species")
        expected_endpoint = "activity" if task == "classification" else request.endpoint
        expected_units = "probability" if task == "classification" else request.units
        if model.get("endpoint") != expected_endpoint or model.get("units") != expected_units:
            raise ModelSelectionError("endpoint: no model matches requested endpoint or units")

    identity_payload = {
        "family_id": request.family_id,
        "bundle_id": bundle.get("bundle_id"),
        "endpoint": request.endpoint,
        "units": request.units,
        "species": request.species,
        "validation": request.validation,
        "models": {
            task: {
                "model_id": model.get("model_id"),
                "weights_sha256": model.get("weights_sha256"),
                "model_card_sha256": model.get("model_card_sha256"),
            }
            for task, model in sorted(models.items())
        },
    }
    identity = hashlib.sha256(
        json.dumps(identity_payload, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")
    ).hexdigest()
    return SelectedActivityModel(
        family_id=request.family_id,
        endpoint=request.endpoint,
        units=request.units,
        species=request.species,
        validation=request.validation,
        identity=identity,
    )
