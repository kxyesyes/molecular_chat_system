from __future__ import annotations

import copy

import pytest


def _bundle(*, species=None, endpoint="pIC50", units="pIC50", readiness="endpoint_ready"):
    models = {}
    for task in ("classification", "regression"):
        model_endpoint = "activity" if task == "classification" else endpoint
        model_units = "probability" if task == "classification" else units
        models[task] = {
            "model_id": f"{task}-model",
            "target_id": "pde-family",
            "task_type": task,
            "endpoint": model_endpoint,
            "units": model_units,
            "scientific_readiness": readiness,
            "demo_mode": False,
            "fallback_used": False,
        }
        if species is not None:
            models[task]["species"] = species
    return {
        "bundle_id": "pde-bundle-v1",
        "family_id": "pde-family",
        "label_threshold": 5.0,
        "probability_threshold": 0.5,
        "scope": {"species": "mixed"},
        "models": models,
    }


def test_request_selection_pins_target_endpoint_units_species_and_validation():
    from src.activity.request_selection import ActivityModelRequest, select_family_bundle

    bundle = _bundle(species="human")
    request = ActivityModelRequest.from_mapping(
        {"target": "PDE5A", "species": "human", "endpoint": "pIC50", "units": "pIC50"}
    )

    selected = select_family_bundle(bundle, request)

    assert selected.family_id == "pde-family"
    assert selected.endpoint == "pIC50"
    assert selected.units == "pIC50"
    assert selected.species == "human"
    assert selected.validation == "endpoint_ready"
    assert selected.identity
    assert selected.identity == select_family_bundle(copy.deepcopy(bundle), request).identity


@pytest.mark.parametrize(
    "request_payload, expected",
    [
        ({"target": "PDE5A", "endpoint": "pKi", "units": "pKi"}, "endpoint"),
        ({"target": "PDE5A", "species": "mouse"}, "species"),
        ({"target": "PDE5A", "validation": "legacy_unvalidated"}, "validation"),
    ],
)
def test_request_selection_fails_closed_without_substituting_another_model(request_payload, expected):
    from src.activity.request_selection import ActivityModelRequest, ModelSelectionError, select_family_bundle

    with pytest.raises(ModelSelectionError, match=expected):
        select_family_bundle(_bundle(species="human"), ActivityModelRequest.from_mapping(request_payload))


def test_request_selection_rejects_demo_or_fallback_bundle_even_when_shape_matches():
    from src.activity.request_selection import ActivityModelRequest, ModelSelectionError, select_family_bundle

    bundle = _bundle()
    bundle["models"]["regression"]["demo_mode"] = True

    with pytest.raises(ModelSelectionError, match="demo"):
        select_family_bundle(bundle, ActivityModelRequest.from_mapping({"target": "PDE"}))


def test_request_selection_identity_changes_when_request_or_model_changes():
    from src.activity.request_selection import ActivityModelRequest, select_family_bundle

    bundle = _bundle(species="human")
    human = select_family_bundle(
        bundle, ActivityModelRequest.from_mapping({"target": "PDE", "species": "human"})
    )
    mouse_bundle = _bundle(species="mouse")
    mouse = select_family_bundle(
        mouse_bundle, ActivityModelRequest.from_mapping({"target": "PDE", "species": "mouse"})
    )

    assert human.identity != mouse.identity


@pytest.mark.parametrize("scope", [None, "unknown"])
def test_explicit_species_requires_matching_evidence(scope):
    from src.activity.request_selection import ActivityModelRequest, ModelSelectionError, select_family_bundle

    bundle = _bundle()
    bundle["scope"]["species"] = scope
    with pytest.raises(ModelSelectionError, match="species"):
        select_family_bundle(bundle, ActivityModelRequest.from_mapping({"target": "PDE", "species": "human"}))


@pytest.mark.parametrize("scope", ["mixed", "all", "any"])
def test_mixed_scope_is_valid_evidence_for_an_explicit_species(scope):
    from src.activity.request_selection import ActivityModelRequest, select_family_bundle

    bundle = _bundle()
    bundle["scope"]["species"] = scope
    selected = select_family_bundle(
        bundle, ActivityModelRequest.from_mapping({"target": "PDE", "species": "human"})
    )
    assert selected.species == "human"


@pytest.mark.parametrize("target", ["PDE5A unknown", "PDE,BuChE", "PDE_5A", "not PDE"])
def test_request_target_is_an_exact_identifier(target):
    from src.activity.request_selection import ActivityModelRequest, ModelSelectionError

    with pytest.raises(ModelSelectionError):
        ActivityModelRequest.from_mapping({"target": target})


def test_matching_legacy_validation_cannot_lower_scientific_gate():
    from src.activity.request_selection import ActivityModelRequest, ModelSelectionError, select_family_bundle

    with pytest.raises(ModelSelectionError, match="validation"):
        select_family_bundle(_bundle(readiness="legacy_unvalidated"),
                             ActivityModelRequest.from_mapping({"target": "PDE", "validation": "legacy_unvalidated"}))
