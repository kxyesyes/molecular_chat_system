from dataclasses import FrozenInstanceError

import pytest

from src.agent import contracts


class _NonJsonObject:
    pass


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


def _direct_candidate(**overrides):
    values = {
        "candidate_id": contracts.build_candidate_id(1, "CCO"),
        "source_index": 1,
        "original_smiles": "C(C)O",
        "canonical_smiles": "CCO",
        "validation": {"valid": True, "method": "RDKit"},
        "generation_provenance": {},
        "metadata": {},
    }
    values.update(overrides)
    return contracts.CandidateRecord(**values)


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


def test_candidate_record_accepts_valid_direct_construction():
    candidate = _direct_candidate()

    assert candidate.candidate_id == contracts.build_candidate_id(1, "CCO")
    assert candidate.source_index == 1


@pytest.mark.parametrize("source_index", [True, False, 0, -1, 1.5])
def test_direct_candidate_record_rejects_invalid_source_index(source_index):
    with pytest.raises(ValueError):
        _direct_candidate(source_index=source_index)


@pytest.mark.parametrize("smiles_field", ["original_smiles", "canonical_smiles"])
@pytest.mark.parametrize("value", [None, "", "   "])
def test_direct_candidate_record_rejects_empty_smiles(smiles_field, value):
    with pytest.raises(ValueError):
        _direct_candidate(**{smiles_field: value})


@pytest.mark.parametrize(
    "candidate_id",
    [
        "candidate-001-ab1de819",
        "cand-000-ab1de819",
        "cand-001-DEADBEEF",
        "cand-001-deadbeef",
    ],
)
def test_direct_candidate_record_rejects_invalid_or_mismatched_id(candidate_id):
    with pytest.raises(ValueError):
        _direct_candidate(candidate_id=candidate_id)


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
    validation = {"valid": True, "method": "RDKit"}
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

    validation["method"] = "changed"
    provenance["models"][0]["versions"].append("2")
    metadata["tags"].append("mutated")
    metadata["scores"]["qed"][0] = 0.1

    with pytest.raises(TypeError):
        candidate.metadata["new"] = "value"
    with pytest.raises(TypeError):
        candidate.generation_provenance["models"][0]["name"] = "changed"

    first_payload = candidate.to_dict()
    first_payload["validation"]["method"] = "payload-change"
    first_payload["generation_provenance"]["models"][0]["versions"].append("3")
    first_payload["metadata"]["tags"].append("payload-change")
    first_payload["metadata"]["scores"]["qed"][0] = 0.2

    second_payload = candidate.to_dict()
    assert second_payload["validation"] == {"valid": True, "method": "RDKit"}
    assert second_payload["generation_provenance"]["models"] == [
        {"name": "gmm-llama", "versions": ["1"]}
    ]
    assert second_payload["metadata"] == {
        "tags": ["generated"],
        "scores": {"qed": [0.7]},
    }


@pytest.mark.parametrize(
    "field_name",
    ["validation", "generation_provenance", "metadata"],
)
def test_candidate_record_json_mappings_reject_non_string_keys(field_name):
    with pytest.raises(ValueError):
        _direct_candidate(**{field_name: {1: "not-json"}})


@pytest.mark.parametrize(
    "invalid_leaf",
    [
        {"not", "json"},
        b"bytes",
        bytearray(b"bytes"),
        _NonJsonObject(),
    ],
)
def test_candidate_record_json_mappings_reject_non_json_leaves(invalid_leaf):
    with pytest.raises(ValueError):
        _direct_candidate(metadata={"nested": [invalid_leaf]})


@pytest.mark.parametrize("non_finite", [float("nan"), float("inf"), float("-inf")])
def test_candidate_record_json_mappings_reject_non_finite_floats(non_finite):
    with pytest.raises(ValueError):
        _direct_candidate(metadata={"score": non_finite})


def test_candidate_record_json_mappings_reject_mapping_cycles():
    cyclic = {}
    cyclic["self"] = cyclic

    with pytest.raises(ValueError):
        _direct_candidate(metadata=cyclic)


def test_candidate_record_json_mappings_reject_sequence_cycles():
    cyclic = []
    cyclic.append(cyclic)

    with pytest.raises(ValueError):
        _direct_candidate(metadata={"items": cyclic})


def test_candidate_set_serializes_counts_candidates_rejections_and_partial_status():
    candidate = contracts.CandidateRecord.from_smiles(
        candidate_index=1,
        source_index=4,
        original_smiles="C(C)O",
        canonical_smiles="CCO",
        generation_provenance={"tool_name": "llm_molecular_generator"},
        metadata={},
    )
    rejected = (
        {"source_index": 1, "smiles": "bad", "reason": "invalid_smiles"},
        {"source_index": 2, "smiles": "OCC", "reason": "duplicate_smiles"},
        {"source_index": 3, "smiles": "C(C)O", "reason": "duplicate_smiles"},
    )
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
        "rejected": [
            {"source_index": 1, "smiles": "bad", "reason": "invalid_smiles"},
            {"source_index": 2, "smiles": "OCC", "reason": "duplicate_smiles"},
            {
                "source_index": 3,
                "smiles": "C(C)O",
                "reason": "duplicate_smiles",
            },
        ],
        "status": "partial",
    }

    with pytest.raises(FrozenInstanceError):
        candidate_set.requested_count = 5


def test_candidate_set_recursively_freezes_rejected_and_thaws_fresh_payloads():
    rejected = [
        {
            "source_index": 2,
            "smiles": "bad",
            "reason": "invalid_smiles",
            "details": {"codes": ["invalid_smiles"]},
        }
    ]
    candidate_set = contracts.CandidateSet(
        requested_count=2,
        candidates=(_candidate(),),
        invalid_count=1,
        rejected=rejected,
        status=contracts.ObservationStatus.PARTIAL,
    )

    rejected[0]["details"]["codes"].append("mutated")
    with pytest.raises(TypeError):
        candidate_set.rejected[0]["details"]["new"] = "value"

    first_payload = candidate_set.to_dict()
    first_payload["rejected"][0]["details"]["codes"].append("payload-change")

    assert candidate_set.to_dict()["rejected"] == [
        {
            "source_index": 2,
            "smiles": "bad",
            "reason": "invalid_smiles",
            "details": {"codes": ["invalid_smiles"]},
        }
    ]


@pytest.mark.parametrize("requested_count", [True, False, -1, 1.5])
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
    first = _candidate(
        candidate_index=1,
        source_index=1,
        original_smiles="C(C)O",
        canonical_smiles="CCO",
    )
    duplicate_id = _candidate(
        candidate_index=1,
        source_index=2,
        original_smiles="OCC",
        canonical_smiles="CCO",
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


@pytest.mark.parametrize("status", ["failed", None])
def test_candidate_set_rejects_non_observation_status(status):
    with pytest.raises(ValueError):
        contracts.CandidateSet(
            requested_count=1,
            candidates=(),
            status=status,
        )


@pytest.mark.parametrize(
    "status",
    [
        contracts.ObservationStatus.FAILED,
        contracts.ObservationStatus.UNAVAILABLE,
        contracts.ObservationStatus.INVALID_INPUT,
    ],
)
def test_candidate_set_allows_zero_requested_for_non_success_statuses(status):
    candidate_set = contracts.CandidateSet(
        requested_count=0,
        candidates=(),
        status=status,
    )

    assert candidate_set.requested_count == 0
    assert candidate_set.candidates == ()


@pytest.mark.parametrize(
    "status",
    [
        contracts.ObservationStatus.SUCCEEDED,
        contracts.ObservationStatus.PARTIAL,
    ],
)
def test_candidate_set_rejects_zero_requested_for_success_statuses(status):
    with pytest.raises(ValueError):
        contracts.CandidateSet(
            requested_count=0,
            candidates=(),
            status=status,
        )


def test_candidate_set_normalizes_candidate_lists_to_tuple():
    candidate = _candidate()
    candidate_set = contracts.CandidateSet(
        requested_count=1,
        candidates=[candidate],
    )

    assert candidate_set.candidates == (candidate,)
    assert isinstance(candidate_set.candidates, tuple)


def test_candidate_set_rejects_non_candidate_record_items():
    with pytest.raises(ValueError):
        contracts.CandidateSet(
            requested_count=1,
            candidates=[{"candidate_id": "not-a-record"}],
            status=contracts.ObservationStatus.FAILED,
        )


@pytest.mark.parametrize(
    "invalid_rejected",
    [
        ({1: "non-string-key"},),
        ({"leaf": {"not", "json"}},),
        ({"score": float("inf")},),
    ],
)
def test_candidate_set_rejected_rejects_invalid_json(invalid_rejected):
    with pytest.raises(ValueError):
        contracts.CandidateSet(
            requested_count=1,
            candidates=(),
            rejected=invalid_rejected,
            status=contracts.ObservationStatus.FAILED,
        )


def test_candidate_set_rejected_rejects_cycles():
    cyclic = {}
    cyclic["self"] = cyclic

    with pytest.raises(ValueError):
        contracts.CandidateSet(
            requested_count=1,
            candidates=(),
            rejected=(cyclic,),
            status=contracts.ObservationStatus.FAILED,
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


def test_candidate_record_and_set_strict_dict_round_trip():
    candidate = _candidate(
        candidate_index=1,
        source_index=2,
        original_smiles="OCC",
        canonical_smiles="CCO",
        generation_provenance={"model_name": "gmm-llama:latest"},
        metadata={"source": "llm"},
    )
    candidate_set = contracts.CandidateSet(
        requested_count=2,
        candidates=(candidate,),
        invalid_count=1,
        rejected=(
            {
                "source_index": 1,
                "smiles": "bad",
                "reason": "invalid_smiles",
            },
        ),
        status=contracts.ObservationStatus.PARTIAL,
    )

    restored_candidate = contracts.CandidateRecord.from_dict(candidate.to_dict())
    restored_set = contracts.CandidateSet.from_dict(candidate_set.to_dict())

    assert restored_candidate.to_dict() == candidate.to_dict()
    assert restored_set.to_dict() == candidate_set.to_dict()


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("version", "2"),
        ("status", "unknown"),
        ("valid_count", 2),
        ("unique_count", 0),
        ("requested_count", "2"),
        ("candidates", ()),
    ],
)
def test_candidate_set_from_dict_rejects_malformed_payload(
    field_name,
    invalid_value,
):
    payload = contracts.CandidateSet(
        requested_count=1,
        candidates=(_candidate(),),
    ).to_dict()
    payload[field_name] = invalid_value

    with pytest.raises(ValueError):
        contracts.CandidateSet.from_dict(payload)


def test_candidate_record_from_dict_rejects_alias_mismatch_and_extra_fields():
    payload = _candidate().to_dict()
    payload["smiles"] = "CCN"
    with pytest.raises(ValueError):
        contracts.CandidateRecord.from_dict(payload)

    payload = _candidate().to_dict()
    payload["unexpected"] = True
    with pytest.raises(ValueError):
        contracts.CandidateRecord.from_dict(payload)


@pytest.mark.parametrize(
    "validation",
    [
        {"valid": False, "method": "RDKit"},
        {"valid": None, "method": "RDKit"},
        {"valid": 1, "method": "RDKit"},
        {"valid": True, "method": "rdkit"},
        {"valid": True, "method": "RDKit", "extra": True},
    ],
)
def test_candidate_record_requires_exact_rdkit_success_validation(validation):
    with pytest.raises(ValueError):
        _direct_candidate(validation=validation)

    payload = _candidate().to_dict()
    payload["validation"] = validation
    with pytest.raises(ValueError):
        contracts.CandidateRecord.from_dict(payload)


def test_candidate_set_requires_candidate_ids_to_follow_candidate_order():
    with pytest.raises(ValueError):
        contracts.CandidateSet(
            requested_count=1,
            candidates=(_candidate(candidate_index=2, source_index=1),),
        )


@pytest.mark.parametrize(
    ("candidate_source", "rejected"),
    [
        (
            1,
            (
                {
                    "source_index": 1,
                    "smiles": "invalid",
                    "reason": "invalid_smiles",
                },
            ),
        ),
        (2, ()),
    ],
)
def test_candidate_set_requires_unique_contiguous_source_indexes(
    candidate_source,
    rejected,
):
    with pytest.raises(ValueError):
        contracts.CandidateSet(
            requested_count=2,
            candidates=(_candidate(source_index=candidate_source),),
            invalid_count=1 if rejected else 0,
            rejected=rejected,
            status=contracts.ObservationStatus.PARTIAL,
        )


@pytest.mark.parametrize(
    "rejected",
    [
        {"source_index": 1, "reason": "invalid_smiles"},
        {"source_index": True, "smiles": "bad", "reason": "invalid_smiles"},
        {"source_index": 1, "smiles": "bad", "reason": "unknown"},
    ],
)
def test_candidate_set_rejects_invalid_rejected_record_schema(rejected):
    with pytest.raises(ValueError):
        contracts.CandidateSet(
            requested_count=1,
            candidates=(),
            invalid_count=1,
            rejected=(rejected,),
            status=contracts.ObservationStatus.FAILED,
        )


def test_candidate_set_allows_empty_smiles_only_for_invalid_smiles_rejection():
    candidate_set = contracts.CandidateSet(
        requested_count=1,
        candidates=(),
        invalid_count=1,
        rejected=(
            {"source_index": 1, "smiles": "", "reason": "invalid_smiles"},
        ),
        status=contracts.ObservationStatus.FAILED,
    )

    assert candidate_set.to_dict()["rejected"] == [
        {"source_index": 1, "smiles": "", "reason": "invalid_smiles"}
    ]

    with pytest.raises(ValueError):
        contracts.CandidateSet(
            requested_count=1,
            candidates=(),
            rejected=(
                {
                    "source_index": 1,
                    "smiles": "",
                    "reason": "excess_candidate",
                },
            ),
            status=contracts.ObservationStatus.FAILED,
        )


@pytest.mark.parametrize(
    ("invalid_count", "duplicate_count"),
    [(0, 0), (2, 1), (1, 0)],
)
def test_candidate_set_counts_must_match_rejected_reasons(
    invalid_count,
    duplicate_count,
):
    rejected = (
        {"source_index": 1, "smiles": "bad", "reason": "invalid_smiles"},
        {"source_index": 2, "smiles": "OCC", "reason": "duplicate_smiles"},
    )
    with pytest.raises(ValueError):
        contracts.CandidateSet(
            requested_count=2,
            candidates=(),
            invalid_count=invalid_count,
            duplicate_count=duplicate_count,
            rejected=rejected,
            status=contracts.ObservationStatus.FAILED,
        )


@pytest.mark.parametrize(
    "status",
    [
        contracts.ObservationStatus.FAILED,
        contracts.ObservationStatus.UNAVAILABLE,
        contracts.ObservationStatus.INVALID_INPUT,
        contracts.ObservationStatus.REJECTED,
        contracts.ObservationStatus.CANCELLED,
    ],
)
def test_non_success_candidate_set_statuses_require_empty_candidates(status):
    with pytest.raises(ValueError):
        contracts.CandidateSet(
            requested_count=1,
            candidates=(_candidate(),),
            status=status,
        )
