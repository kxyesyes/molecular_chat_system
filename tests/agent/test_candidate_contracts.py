from dataclasses import FrozenInstanceError

import pytest

from src.agent import contracts


def test_build_candidate_id_is_stable_for_candidate_index_and_canonical_smiles():
    first = contracts.build_candidate_id(1, "CCO")
    second = contracts.build_candidate_id(1, "CCO")

    assert first == second
    assert first == "cand-001-ab1de819"


def test_candidate_record_keeps_source_index_separate_from_candidate_index():
    candidate = contracts.CandidateRecord.from_smiles(
        candidate_index=1,
        source_index=3,
        original_smiles="C(C)O",
        canonical_smiles="CCO",
        generation_provenance={"tool_name": "llm_molecular_generator"},
        metadata={"temperature": 0.2},
    )

    assert candidate.candidate_id.startswith("cand-001-")
    assert candidate.source_index == 3
    assert candidate.validation == {"valid": True, "method": "RDKit"}


def test_candidate_record_serializes_all_fields_with_legacy_smiles_alias():
    candidate = contracts.CandidateRecord.from_smiles(
        candidate_index=2,
        source_index=4,
        original_smiles="OCC",
        canonical_smiles="CCO",
        generation_provenance={"model": "gmm-llama"},
        metadata={"rank": 2},
    )

    assert candidate.to_dict() == {
        "candidate_id": "cand-002-ab1de819",
        "source_index": 4,
        "original_smiles": "OCC",
        "canonical_smiles": "CCO",
        "validation": {"valid": True, "method": "RDKit"},
        "generation_provenance": {"model": "gmm-llama"},
        "metadata": {"rank": 2},
        "smiles": "CCO",
    }

    with pytest.raises(FrozenInstanceError):
        candidate.source_index = 5


def test_candidate_set_serializes_counts_candidates_rejections_and_partial_status():
    candidate = contracts.CandidateRecord.from_smiles(
        candidate_index=1,
        source_index=3,
        original_smiles="C(C)O",
        canonical_smiles="CCO",
        generation_provenance={"tool_name": "llm_molecular_generator"},
        metadata={},
    )
    rejected = ({"source_index": 1, "reason": "invalid_smiles"},)
    candidate_set = contracts.CandidateSet(
        requested_count=4,
        candidates=(candidate,),
        invalid_count=1,
        duplicate_count=2,
        rejected=rejected,
        status=contracts.ObservationStatus.PARTIAL,
    )

    assert candidate_set.valid_count == 1
    assert candidate_set.unique_count == 1
    assert candidate_set.to_dict() == {
        "version": "1",
        "requested_count": 4,
        "valid_count": 1,
        "unique_count": 1,
        "invalid_count": 1,
        "duplicate_count": 2,
        "candidates": [candidate.to_dict()],
        "rejected": [{"source_index": 1, "reason": "invalid_smiles"}],
        "status": "partial",
    }

    with pytest.raises(FrozenInstanceError):
        candidate_set.requested_count = 5
