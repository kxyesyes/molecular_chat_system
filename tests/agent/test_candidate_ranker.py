import json
import copy

import pytest

from src.agent.tools.candidate_ranker import CandidateRanker
from src.agent.tooling import build_tool_registry
from tests.agent.test_family_activity_tool import family_row


def _payload(*, top_n=2):
    return {
        "metadata": {"docking_top_n": top_n},
        "outputs": {
            "molecules": [
                {"candidate_id": "candidate-ethanol", "smiles": "CCO"},
                {"candidate_id": "candidate-ethylamine", "smiles": "CCN"},
                {"candidate_id": "candidate-propane", "smiles": "CCC"},
            ],
            "properties": [
                {"smiles": "CCO", "properties": {"qed": 0.72, "logp": 0.1}},
                {"smiles": "CCN", "properties": {"qed": 0.65, "logp": 0.0}},
                {"smiles": "CCC", "properties": {"qed": 0.40, "logp": 1.4}},
            ],
            "admet": [],
            "activity": [],
        },
    }


def test_candidate_ranker_returns_deterministic_top_n_without_docking_claims():
    result = CandidateRanker().execute(_payload())

    assert result["success"] is True
    assert len(result["data"]["top_candidates"]) == 2
    assert [item["rank"] for item in result["data"]["top_candidates"]] == [1, 2]
    assert all(
        "ranking_evidence" in item
        for item in result["data"]["top_candidates"]
    )
    assert all(
        item["docking_ready_for_preparation"] is True
        for item in result["data"]["top_candidates"]
    )
    serialized = json.dumps(result, ensure_ascii=False).casefold()
    assert "binding_energy" not in serialized
    assert "kcal/mol" not in serialized


def test_candidate_ranker_joins_evidence_by_canonical_smiles():
    payload = _payload(top_n=1)
    payload["outputs"]["molecules"] = [
        {"candidate_id": "candidate-ethanol", "smiles": "OCC"},
    ]
    payload["outputs"]["properties"] = [
        {"smiles": "CCO", "properties": {"qed": 0.72, "logp": 0.1}},
    ]

    result = CandidateRanker().execute(payload)

    assert result["success"] is True
    candidate = result["data"]["top_candidates"][0]
    assert candidate["canonical_smiles"] == "CCO"
    assert candidate["candidate_id"] == "candidate-ethanol"


def test_candidate_ranker_fails_closed_on_evidence_for_unknown_candidate():
    payload = _payload()
    payload["outputs"]["properties"].append(
        {"smiles": "c1ccccc1", "properties": {"qed": 0.9, "logp": 2.0}}
    )

    result = CandidateRanker().execute(payload)

    assert result["success"] is False
    assert result["data"] is None
    assert result["error_code"] == "invalid_candidate_evidence"


@pytest.mark.parametrize("qed", [-0.01, 1.01, float("nan"), float("inf")])
def test_candidate_ranker_rejects_invalid_qed_instead_of_clamping(qed):
    payload = _payload(top_n=1)
    payload["outputs"]["properties"][0]["properties"]["qed"] = qed

    result = CandidateRanker().execute(payload)

    assert result["success"] is False
    assert result["error_code"] == "invalid_property_evidence"


def test_candidate_without_real_property_evidence_is_not_rankable():
    payload = _payload(top_n=3)
    payload["outputs"]["properties"] = payload["outputs"]["properties"][:2]

    result = CandidateRanker().execute(payload)

    assert result["success"] is True
    assert len(result["data"]["ranked_candidates"]) == 2
    assert result["data"]["unrankable_candidates"] == [
        {
            "candidate_id": "candidate-propane",
            "canonical_smiles": "CCC",
            "reason": "missing_real_property_evidence",
        }
    ]
    assert any("property" in warning.casefold() for warning in result["warnings"])


@pytest.mark.parametrize(
    "trust_marker",
    [
        {"demo_mode": True, "fallback_used": False},
        {"demo_mode": False, "fallback_used": True},
        {"demo_mode": "false", "fallback_used": False},
    ],
)
def test_candidate_ranker_ignores_untrusted_activity_values(trust_marker):
    payload = _payload(top_n=1)
    payload["outputs"]["activity"] = [
        {
            "smiles": "CCO",
            "success": True,
            "probability": 0.99,
            "model_provenance": trust_marker,
        }
    ]

    result = CandidateRanker().execute(payload)

    assert result["success"] is True
    evidence = next(
        item
        for item in result["data"]["ranked_candidates"]
        if item["canonical_smiles"] == "CCO"
    )["ranking_evidence"]
    assert evidence["activity_score"] is None
    assert "activity" in evidence["missing_evidence"]
    assert any("activity" in warning.casefold() for warning in result["warnings"])


def test_candidate_ranker_uses_trusted_normalized_activity_and_explicit_admet_risks():
    payload = _payload(top_n=1)
    payload["outputs"]["admet"] = [
        {
            "smiles": "CCO",
            "admet": {
                "prediction_method": "validated-model",
                "risk_count": 1,
                "total_endpoints": 4,
            },
        }
    ]
    payload["outputs"]["activity"] = [
        {
            "smiles": "CCO",
            "success": True,
            "normalized_activity": 0.8,
            "model_provenance": {
                "model_id": "rg-mpnn-test",
                "demo_mode": False,
                "fallback_used": False,
            },
        }
    ]

    result = CandidateRanker().execute(payload)

    ethanol = next(
        item
        for item in result["data"]["ranked_candidates"]
        if item["canonical_smiles"] == "CCO"
    )
    evidence = ethanol["ranking_evidence"]
    assert evidence["admet_score"] == pytest.approx(0.75)
    assert evidence["activity_score"] == pytest.approx(0.8)
    assert evidence["missing_evidence"] == []


@pytest.mark.parametrize("invalid_activity", [-0.01, 1.01, float("nan"), float("inf")])
def test_candidate_ranker_does_not_clamp_invalid_activity_probability(invalid_activity):
    payload = _payload(top_n=1)
    payload["outputs"]["activity"] = [
        {
            "smiles": "CCO",
            "success": True,
            "normalized_activity": invalid_activity,
            "model_provenance": {
                "model_id": "rg-mpnn-test",
                "demo_mode": False,
                "fallback_used": False,
            },
        }
    ]

    result = CandidateRanker().execute(payload)

    assert result["success"] is True
    evidence = result["data"]["ranked_candidates"][0]["ranking_evidence"]
    assert evidence["activity_score"] is None
    assert "activity" in evidence["missing_evidence"]


def test_candidate_ranker_does_not_promote_missing_optional_evidence():
    payload = {
        "metadata": {"docking_top_n": 2},
        "outputs": {
            "molecules": [
                {"candidate_id": "candidate-incomplete", "smiles": "CCO"},
                {"candidate_id": "candidate-complete", "smiles": "CCN"},
            ],
            "properties": [
                {"smiles": "CCO", "properties": {"qed": 0.95, "logp": 1.0}},
                {"smiles": "CCN", "properties": {"qed": 0.70, "logp": 1.0}},
            ],
            "admet": [
                {
                    "smiles": "CCN",
                    "admet": {
                        "prediction_method": "validated-model",
                        "risk_count": 0,
                        "total_endpoints": 4,
                    },
                }
            ],
            "activity": [
                {
                    "smiles": "CCN",
                    "success": True,
                    "normalized_activity": 0.95,
                    "model_provenance": {
                        "model_id": "rg-mpnn-test",
                        "demo_mode": False,
                        "fallback_used": False,
                    },
                }
            ],
        },
    }

    result = CandidateRanker().execute(payload)

    assert result["success"] is True
    assert [row["candidate_id"] for row in result["data"]["ranked_candidates"]] == [
        "candidate-complete",
        "candidate-incomplete",
    ]
    incomplete = result["data"]["ranked_candidates"][1]
    assert incomplete["assessment_status"] == "needs_assessment"
    assert incomplete["ranking_evidence"]["weights_used"] == {
        "properties": 0.5,
        "admet": None,
        "activity": None,
    }
    assert incomplete["docking_ready_for_preparation"] is True


def test_candidate_ranker_marks_missing_required_evidence_as_not_satisfied():
    payload = _payload(top_n=1)
    payload["metadata"]["required_evidence"] = ["properties", "activity"]

    result = CandidateRanker().execute(payload)

    assert result["success"] is True
    candidate = result["data"]["top_candidates"][0]
    assert candidate["assessment_status"] == "requirements_not_met"
    assert candidate["missing_required_evidence"] == ["activity"]
    assert candidate["docking_ready_for_preparation"] is False
    assert any("required" in warning.casefold() for warning in result["warnings"])


def test_candidate_ranker_uses_trusted_family_activity_probability():
    payload = _payload(top_n=1)
    payload["outputs"]["activity"] = [
        family_row(activity_probability=0.91, activity_class="有活性", predicted_pIC50=7.1)
    ]

    result = CandidateRanker().execute(payload)

    assert result["success"] is True
    ethanol = next(
        item
        for item in result["data"]["ranked_candidates"]
        if item["canonical_smiles"] == "CCO"
    )
    assert ethanol["ranking_evidence"]["activity_score"] == pytest.approx(0.91)


def test_candidate_ranker_rejects_activity_evidence_from_another_target():
    payload = _payload(top_n=1)
    payload["metadata"].update({"target": "BuChE", "endpoint": "pIC50", "units": "pIC50"})
    payload["outputs"]["activity"] = [
        {
            "smiles": "CCO",
            "success": True,
            "activity_probability": 0.99,
            "requested_target": "PDE5A",
            "units": "pIC50",
            "provenance": {
                "request": {"family_id": "pde-family", "endpoint": "pIC50", "units": "pIC50"},
                "models": {
                    "classification": {
                        "model_id": "pde-classifier",
                        "weights_sha256": "a" * 64,
                        "demo_mode": False,
                        "fallback_used": False,
                    },
                    "regression": {
                        "model_id": "pde-regressor",
                        "weights_sha256": "b" * 64,
                        "demo_mode": False,
                        "fallback_used": False,
                    },
                },
            },
        }
    ]

    result = CandidateRanker().execute(payload)

    assert result["success"] is False
    assert result["error_code"] == "activity_scope_mismatch"
    assert result["data"] is None


@pytest.mark.parametrize(
    ("field", "actual"),
    [("endpoint", "pKi"), ("units", "nM")],
)
def test_candidate_ranker_rejects_activity_evidence_from_another_endpoint_or_unit(field, actual):
    payload = _payload(top_n=1)
    payload["metadata"].update({"target": "PDE5A", "endpoint": "pIC50", "units": "pIC50"})
    payload["outputs"]["activity"] = [
        {
            "smiles": "CCO",
            "success": True,
            "activity_probability": 0.99,
            "requested_target": "PDE5A",
            "endpoint": actual if field == "endpoint" else "pIC50",
            "units": actual if field == "units" else "pIC50",
            "provenance": {
                "models": {
                    "classification": {
                        "model_id": "pde-classifier",
                        "weights_sha256": "a" * 64,
                        "demo_mode": False,
                        "fallback_used": False,
                    }
                }
            },
        }
    ]

    result = CandidateRanker().execute(payload)

    assert result["success"] is False
    assert result["error_code"] == "activity_scope_mismatch"


def test_candidate_ranker_rejects_mixed_activity_scopes_without_expected_scope():
    payload = _payload(top_n=1)
    first = {
        "smiles": "CCO",
        "success": True,
        "activity_probability": 0.99,
        "requested_target": "PDE5A",
        "units": "pIC50",
        "provenance": {
            "models": {
                "classification": {
                    "model_id": "pde-classifier",
                    "weights_sha256": "a" * 64,
                    "demo_mode": False,
                    "fallback_used": False,
                }
            }
        },
    }
    payload["outputs"]["activity"] = [first, {**first, "smiles": "CCN", "requested_target": "BuChE"}]

    result = CandidateRanker().execute(payload)

    assert result["success"] is False
    assert result["error_code"] == "activity_scope_mismatch"


def test_candidate_ranker_ignores_demo_or_fallback_admet_scores():
    payload = _payload(top_n=1)
    payload["outputs"]["admet"] = [
        {
            "smiles": "CCO",
            "admet": {
                "prediction_method": "demo",
                "risk_count": 0,
                "total_endpoints": 4,
                "demo_mode": True,
            },
        }
    ]

    result = CandidateRanker().execute(payload)

    ethanol = next(
        item
        for item in result["data"]["ranked_candidates"]
        if item["canonical_smiles"] == "CCO"
    )
    assert ethanol["ranking_evidence"]["admet_score"] is None
    assert "admet" in ethanol["missing_evidence"]


def test_candidate_ranker_breaks_equal_scores_by_canonical_smiles():
    payload = _payload(top_n=3)
    payload["outputs"]["properties"] = [
        {"smiles": item["smiles"], "properties": {"qed": 0.5, "logp": 2.0}}
        for item in payload["outputs"]["molecules"]
    ]

    first = CandidateRanker().execute(payload)
    second = CandidateRanker().execute(payload)

    first_smiles = [
        item["canonical_smiles"] for item in first["data"]["ranked_candidates"]
    ]
    second_smiles = [
        item["canonical_smiles"] for item in second["data"]["ranked_candidates"]
    ]
    assert first_smiles == sorted(first_smiles)
    assert second_smiles == first_smiles


@pytest.mark.parametrize("top_n", [0, -1, 1.5, "3", True])
def test_candidate_ranker_rejects_invalid_top_n(top_n):
    result = CandidateRanker().execute(_payload(top_n=top_n))

    assert result["success"] is False
    assert result["error_code"] == "invalid_top_n"


def test_candidate_ranker_is_owned_by_molecular_design_specialist():
    registry = build_tool_registry([CandidateRanker()])

    assert registry.resolve("candidate_ranker", agent_name="molecular_design")


def test_mixed_activity_ranking_preserves_conflicts_without_scoring_them():
    payload = _payload(top_n=3)
    payload["metadata"]["required_evidence"] = ["activity"]
    rows = [
        family_row(smiles="CCO", activity_probability=0.8, activity_class="有活性",
                   predicted_pIC50=6.2, execution_status="passed"),
        family_row(smiles="CCN", activity_probability=0.1, predicted_pIC50=6.2,
                   success=False, status="partial", execution_status="passed",
                   classification_regression_consistent=False),
        family_row(smiles="CCC", activity_probability=0.9, activity_class="有活性",
                   predicted_pIC50=4.2, success=False, status="partial",
                   execution_status="passed", classification_regression_consistent=False),
    ]
    payload["outputs"]["activity"] = {"status": "partial", "success": False, "data": rows}
    before = copy.deepcopy(payload)
    result = CandidateRanker().execute(payload)
    assert result["success"] is True
    ranked = {row["canonical_smiles"]: row for row in result["data"]["ranked_candidates"]}
    accepted = ranked["CCO"]["ranking_evidence"]
    assert accepted["activity_score"] == 0.8
    assert accepted["activity"]["used"] is True
    assert accepted["activity"]["exclusion_reason"] is None
    assert accepted["weights_used"] == {"properties": 0.5, "admet": None, "activity": 0.25}
    for raw in rows[1:]:
        candidate = ranked[raw["smiles"]]
        evidence = candidate["ranking_evidence"]
        activity = evidence["activity"]
        assert evidence["activity_score"] is None
        assert evidence["weights_used"] == {"properties": 0.5, "admet": None, "activity": None}
        assert candidate["score"] == pytest.approx(evidence["property_score"] * 0.5)
        assert activity["used"] is False
        assert activity["exclusion_reason"] == "classification_regression_conflict"
        assert activity["raw_values"]["activity_probability"] == raw["activity_probability"]
        assert activity["raw_values"]["predicted_pIC50"] == raw["predicted_pIC50"]
        assert activity["execution_status"] == "passed"
        assert activity["review_status"] == "needs_review"
        assert candidate["missing_required_evidence"] == ["activity"]
        assert candidate["docking_ready_for_preparation"] is False
    assert payload == before


@pytest.mark.parametrize("case,reason", [
    ("missing", "missing_activity_evidence"),
    ("failed", "activity_execution_failed"),
    ("provenance", "invalid_activity_provenance"),
    ("demo", "invalid_activity_provenance"),
    ("fallback", "invalid_activity_provenance"),
    ("forged_flag", "invalid_activity_evidence"),
    ("forged_success", "invalid_activity_evidence"),
    ("forged_normalized", "invalid_activity_evidence"),
    ("forged_identity", "invalid_activity_provenance"),
])
def test_activity_exclusion_diagnostics_do_not_trust_forged_rows(case, reason):
    payload = _payload(top_n=3)
    row = family_row(execution_status="passed")
    if case == "failed":
        row.update(success=False, status="failed", execution_status="failed",
                   activity_probability=None, activity_class=None, predicted_pIC50=None,
                   classification_regression_consistent=None, errors={"classification": "failed"})
    elif case == "provenance":
        row["provenance"] = {}
    elif case in {"demo", "fallback"}:
        row["provenance"]["models"]["classification"][case + ("_mode" if case == "demo" else "_used")] = True
    elif case == "forged_flag":
        row["classification_regression_consistent"] = False
    elif case == "forged_success":
        row["predicted_pIC50"] = 7.0
    elif case == "forged_normalized":
        row["normalized_activity"] = 0.99
    elif case == "forged_identity":
        row["provenance"]["request"]["identity"] = "0" * 64
    payload["outputs"]["activity"] = [] if case == "missing" else [row]
    result = CandidateRanker().execute(payload)
    assert result["success"] is True
    candidate = next(item for item in result["data"]["ranked_candidates"] if item["canonical_smiles"] == "CCO")
    evidence = candidate["ranking_evidence"]
    assert evidence["activity_score"] is None
    assert evidence["activity"]["used"] is False
    assert evidence["activity"]["exclusion_reason"] == reason
    if case not in {"missing", "failed"}:
        assert evidence["activity"]["raw_values"] == {}


def test_activity_classification_only_is_retained_but_not_ranked():
    payload = _payload()
    payload["outputs"]["activity"] = [family_row(
        success=False, status="partial", execution_status="partial", predicted_pIC50=None,
        classification_regression_consistent=None, errors={"regression": "unavailable"},
    )]
    result = CandidateRanker().execute(payload)
    candidate = next(item for item in result["data"]["ranked_candidates"] if item["canonical_smiles"] == "CCO")
    assert candidate["ranking_evidence"]["activity_score"] is None
    details = candidate["ranking_evidence"]["activity"]
    assert details["exclusion_reason"] == "activity_incomplete"
    assert details["raw_values"]["activity_probability"] == 0.0
    assert "predicted_pIC50" not in details["raw_values"]


@pytest.mark.parametrize("marker", ["demo_mode", "fallback_used"])
@pytest.mark.parametrize("container", [None, "quality", "provenance"])
def test_ranker_rejects_demo_or_fallback_activity_envelope(marker, container):
    payload = _payload()
    payload["metadata"]["required_evidence"] = ["activity"]
    payload["outputs"]["activity"] = {
        "status": "partial", "success": False,
        "data": [family_row()],
    }
    evidence = payload["outputs"]["activity"]
    if container is None:
        evidence[marker] = True
    else:
        evidence[container] = {marker: True}
    result = CandidateRanker().execute(payload)
    assert result["success"] is False
    assert result["error_code"] == "invalid_candidate_evidence"


def test_relabelled_family_activity_does_not_satisfy_requested_target_evidence():
    payload = _payload()
    payload["metadata"].update(target="BuChE", required_evidence=["activity"])
    payload["outputs"]["activity"] = [family_row(
        requested_target="BuChE", activity_probability=0.8, activity_class="有活性",
        predicted_pIC50=6.2, execution_status="passed",
    )]
    result = CandidateRanker().execute(payload)
    assert result["success"] is True
    candidate = next(item for item in result["data"]["ranked_candidates"] if item["canonical_smiles"] == "CCO")
    assert candidate["ranking_evidence"]["activity_score"] is None
    assert candidate["ranking_evidence"]["activity"]["exclusion_reason"] == "activity_scope_mismatch"
    assert candidate["ranking_evidence"]["activity"]["raw_values"] == {}
    assert candidate["missing_required_evidence"] == ["activity"]
    assert candidate["docking_ready_for_preparation"] is False


@pytest.mark.parametrize("status_field", ["status", "execution_status"])
@pytest.mark.parametrize("prediction", [
    {"probability": 5.0, "model_provenance": {"demo_mode": True}},
    {"probability": 0.8},
    {"normalized_activity": 5.0, "model_provenance": {
        "model_id": "synthetic-model", "demo_mode": False, "fallback_used": False}},
    # Canonical failure rows may carry errors, but do not certify predictions.
    {"task_type": "classification", "endpoint": "activity", "units": "probability",
     "probability": 0.8, "error": "synthetic stage failure", "model_provenance": {
         "model_id": "synthetic-model", "weights_sha256": "a" * 64,
         "task_type": "classification", "endpoint": "activity", "units": "probability",
         "demo_mode": False, "fallback_used": False}},
])
def test_unvalidated_nonfamily_partial_does_not_republish_raw_activity(status_field, prediction):
    payload = _payload()
    payload["outputs"]["activity"] = [
        {"smiles": "CCO", "success": False, status_field: "partial", **prediction},
    ]
    before = copy.deepcopy(payload)

    result = CandidateRanker().execute(payload)

    assert result["success"] is True
    candidate = next(item for item in result["data"]["ranked_candidates"] if item["canonical_smiles"] == "CCO")
    evidence = candidate["ranking_evidence"]
    assert evidence["activity_score"] is None
    assert evidence["activity"]["used"] is False
    assert evidence["activity"]["raw_values"] == {}
    assert evidence["activity"]["exclusion_reason"] == "invalid_activity_evidence"
    assert payload == before


def test_successful_canonical_classification_remains_usable_and_visible():
    payload = _payload()
    payload["outputs"]["activity"] = [{
        "smiles": "CCO", "success": True, "task_type": "classification",
        "endpoint": "activity", "units": "probability", "probability": 0.8,
        "model_provenance": {
            "model_id": "synthetic-model", "weights_sha256": "a" * 64,
            "task_type": "classification", "endpoint": "activity", "units": "probability",
            "demo_mode": False, "fallback_used": False,
        },
    }]

    result = CandidateRanker().execute(payload)

    candidate = next(item for item in result["data"]["ranked_candidates"] if item["canonical_smiles"] == "CCO")
    evidence = candidate["ranking_evidence"]
    assert evidence["activity_score"] == 0.8
    assert evidence["activity"]["used"] is True
    assert evidence["activity"]["raw_values"] == {"probability": 0.8}
