from dataclasses import FrozenInstanceError

import pytest

from src.agent import contracts


def _candidate(
    candidate_index=1,
    canonical_smiles="CCO",
    *,
    source_index=None,
    original_smiles=None,
    generation_provenance=None,
    metadata=None,
):
    return contracts.CandidateRecord.from_smiles(
        candidate_index=candidate_index,
        source_index=source_index if source_index is not None else candidate_index,
        original_smiles=(
            original_smiles if original_smiles is not None else canonical_smiles
        ),
        canonical_smiles=canonical_smiles,
        generation_provenance=generation_provenance,
        metadata=metadata,
    )


def test_build_candidate_id_is_stable_for_candidate_index_and_canonical_smiles():
    first = contracts.build_candidate_id(1, "CCO")
    second = contracts.build_candidate_id(1, "CCO")

    assert first == second
    assert first == "cand-001-ab1de819"


def test_build_candidate_id_changes_with_candidate_index_or_canonical_smiles():
    baseline = contracts.build_candidate_id(1, "CCO")

    assert contracts.build_candidate_id(2, "CCO") != baseline
    assert contracts.build_candidate_id(1, "CCN") != baseline


@pytest.mark.parametrize("candidate_index", [True, False, 0, -1, 1.5])
def test_build_candidate_id_rejects_invalid_candidate_index(candidate_index):
    with pytest.raises(ValueError):
        contracts.build_candidate_id(candidate_index, "CCO")


@pytest.mark.parametrize("canonical_smiles", ["", "   "])
def test_build_candidate_id_rejects_empty_canonical_smiles(canonical_smiles):
    with pytest.raises(ValueError):
        contracts.build_candidate_id(1, canonical_smiles)


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


def test_candidate_id_ignores_source_index_and_original_smiles():
    first = _candidate(
        candidate_index=1,
        source_index=3,
        original_smiles="C(C)O",
        canonical_smiles="CCO",
    )
    second = _candidate(
        candidate_index=1,
        source_index=7,
        original_smiles="OCC",
        canonical_smiles="CCO",
    )

    assert first.candidate_id == second.candidate_id


@pytest.mark.parametrize("source_index", [True, False, 0, -1, 1.5])
def test_candidate_record_rejects_invalid_source_index(source_index):
    with pytest.raises(ValueError):
        _candidate(source_index=source_index)


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


def test_candidate_record_recursively_freezes_inputs_and_thaws_fresh_payloads():
    validation = {"valid": True, "checks": [{"method": "RDKit"}]}
    provenance = {"models": [{"name": "gmm-llama", "versions": ["1"]}]}
    metadata = {"tags": ["generated"], "scores": {"qed": [0.7]}}
    candidate = contracts.CandidateRecord(
        candidate_id=contracts.build_candidate_id(1, "CCO"),
        source_index=1,
        original_smiles="C(C)O",
        canonical_smiles="CCO",
        validation=validation,
        generation_provenance=provenance,
        metadata=metadata,
    )

    validation["checks"][0]["method"] = "changed"
    provenance["models"][0]["versions"].append("2")
    metadata["tags"].append("mutated")
    metadata["scores"]["qed"][0] = 0.1

    with pytest.raises(TypeError):
        candidate.metadata["new"] = "value"
    with pytest.raises(TypeError):
        candidate.generation_provenance["models"][0]["name"] = "changed"

    first_payload = candidate.to_dict()
    first_payload["validation"]["checks"][0]["method"] = "payload-change"
    first_payload["generation_provenance"]["models"][0]["versions"].append("3")
    first_payload["metadata"]["tags"].append("payload-change")
    first_payload["metadata"]["scores"]["qed"][0] = 0.2

    second_payload = candidate.to_dict()
    assert second_payload["validation"]["checks"] == [{"method": "RDKit"}]
    assert second_payload["generation_provenance"]["models"] == [
        {"name": "gmm-llama", "versions": ["1"]}
    ]
    assert second_payload["metadata"] == {
        "tags": ["generated"],
        "scores": {"qed": [0.7]},
    }


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


def test_candidate_set_recursively_freezes_rejected_and_thaws_fresh_payloads():
    rejected = [{"source_index": 2, "details": {"codes": ["invalid_smiles"]}}]
    candidate_set = contracts.CandidateSet(
        requested_count=2,
        candidates=(_candidate(),),
        rejected=rejected,
        status=contracts.ObservationStatus.PARTIAL,
    )

    rejected[0]["details"]["codes"].append("mutated")
    with pytest.raises(TypeError):
        candidate_set.rejected[0]["details"]["new"] = "value"

    first_payload = candidate_set.to_dict()
    first_payload["rejected"][0]["details"]["codes"].append("payload-change")

    assert candidate_set.to_dict()["rejected"] == [
        {"source_index": 2, "details": {"codes": ["invalid_smiles"]}}
    ]


@pytest.mark.parametrize("requested_count", [True, False, 0, -1, 1.5])
def test_candidate_set_rejects_invalid_requested_count(requested_count):
    with pytest.raises(ValueError):
        contracts.CandidateSet(
            requested_count=requested_count,
            candidates=(),
            status=contracts.ObservationStatus.FAILED,
        )


@pytest.mark.parametrize(
    "count_field",
    [
        {"invalid_count": -1},
        {"duplicate_count": -1},
    ],
)
def test_candidate_set_rejects_negative_counts(count_field):
    with pytest.raises(ValueError):
        contracts.CandidateSet(
            requested_count=1,
            candidates=(),
            status=contracts.ObservationStatus.FAILED,
            **count_field,
        )


def test_candidate_set_rejects_more_candidates_than_requested():
    with pytest.raises(ValueError):
        contracts.CandidateSet(
            requested_count=1,
            candidates=(_candidate(1, "CCO"), _candidate(2, "CCN")),
            status=contracts.ObservationStatus.FAILED,
        )


def test_candidate_set_rejects_duplicate_candidate_ids():
    first = _candidate(1, "CCO")
    duplicate_id = contracts.CandidateRecord(
        candidate_id=first.candidate_id,
        source_index=2,
        original_smiles="CCN",
        canonical_smiles="CCN",
        validation={"valid": True},
        generation_provenance={},
        metadata={},
    )

    with pytest.raises(ValueError):
        contracts.CandidateSet(
            requested_count=2,
            candidates=(first, duplicate_id),
            status=contracts.ObservationStatus.FAILED,
        )


def test_candidate_set_rejects_duplicate_canonical_smiles():
    with pytest.raises(ValueError):
        contracts.CandidateSet(
            requested_count=2,
            candidates=(_candidate(1, "CCO"), _candidate(2, "CCO")),
            status=contracts.ObservationStatus.FAILED,
        )


@pytest.mark.parametrize(
    ("requested_count", "candidates", "status"),
    [
        (1, (), contracts.ObservationStatus.SUCCEEDED),
        (2, (_candidate(),), contracts.ObservationStatus.SUCCEEDED),
        (1, (_candidate(),), contracts.ObservationStatus.PARTIAL),
        (2, (), contracts.ObservationStatus.PARTIAL),
    ],
)
def test_candidate_set_rejects_status_count_mismatches(
    requested_count,
    candidates,
    status,
):
    with pytest.raises(ValueError):
        contracts.CandidateSet(
            requested_count=requested_count,
            candidates=candidates,
            status=status,
        )


def test_candidate_set_version_is_fixed_and_not_accepted_at_initialization():
    candidate_set = contracts.CandidateSet(
        requested_count=1,
        candidates=(_candidate(),),
    )

    assert candidate_set.version == "1"
    with pytest.raises(TypeError):
        contracts.CandidateSet(
            requested_count=1,
            candidates=(_candidate(),),
            version="2",
        )


@pytest.mark.parametrize(
    "status",
    [
        contracts.ObservationStatus.FAILED,
        contracts.ObservationStatus.UNAVAILABLE,
    ],
)
def test_candidate_set_does_not_overconstrain_other_statuses(status):
    candidate_set = contracts.CandidateSet(
        requested_count=1,
        candidates=(),
        status=status,
    )

    assert candidate_set.status == status
