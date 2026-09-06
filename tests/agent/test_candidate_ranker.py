import json

import pytest

from src.agent.tools.candidate_ranker import CandidateRanker
from src.agent.tooling import build_tool_registry


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
