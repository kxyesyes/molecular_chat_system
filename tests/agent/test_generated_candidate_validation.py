import sys

import pytest

from src.agent.contracts import (
    AgentResult,
    CandidateRecord,
    CandidateSet,
    ObservationStatus,
    RunOutcome,
    ToolProvenance,
    ToolResult,
    build_candidate_id,
)
from src.agent.validators import (
    AgentResultValidator,
    CandidateValidationUnavailable,
    sanitize_generated_candidates,
)


def test_generator_result_keeps_only_valid_unique_canonical_smiles():
    provenance = ToolProvenance(
        tool_name="llm_molecular_generator",
        model_name="gmm-llama",
    )
    result = ToolResult.success_result(
        "llm_molecular_generator",
        data=[
            {"smiles": "CCO", "source": "llm"},
            {"smiles": "OCC", "source": "llm"},
            {"smiles": "CC(C)((", "source": "llm"},
            {"smiles": "CCN", "source": "llm"},
        ],
        quality={"requested_count": 4},
        provenance=provenance,
    )

    validated = AgentResultValidator().validate_tool_result(result)

    candidates = validated.data["candidates"]
    assert [item["smiles"] for item in candidates] == ["CCO", "CCN"]
    assert [item["source_index"] for item in candidates] == [1, 4]
    assert [item["candidate_id"].split("-")[1] for item in candidates] == [
        "001",
        "002",
    ]
    assert candidates[0]["metadata"] == {"source": "llm"}
    assert candidates[0]["generation_provenance"] == provenance.to_dict()
    assert validated.status == ObservationStatus.PARTIAL
    assert validated.data["status"] == "partial"
    assert validated.quality["requested_count"] == 4
    assert validated.quality["valid_count"] == 2
    assert validated.quality["unique_count"] == 2
    assert validated.quality["invalid_count"] == 1
    assert validated.quality["duplicate_count"] == 1
    assert validated.quality["validation_method"] == "RDKit"
    assert validated.quality["output_contract"] == "CandidateSet@1"
    assert validated.warnings == [
        "Generated 2 valid unique SMILES out of 4 requested"
    ]


def test_generator_result_fails_when_no_valid_candidate_remains():
    result = ToolResult.success_result(
        "llm_molecular_generator",
        data=[{"smiles": "CC(C)(("}, {"smiles": "not a smiles"}],
        quality={"requested_count": 2},
    )

    validated = AgentResultValidator().validate_tool_result(result)

    assert validated.success is False
    assert validated.status == ObservationStatus.FAILED
    assert validated.data["status"] == "failed"
    assert validated.data["candidates"] == []
    assert validated.formatted == ""
    assert validated.error.code.value == "invalid_output"
    assert validated.quality["raw_count"] == 2
    assert validated.quality["actual_count"] == 0
    assert validated.quality["partial_generation"] is True
    assert validated.quality["validated"] is True
    assert validated.quality["validation_available"] is True
    assert validated.quality["validation_method"] == "RDKit"


def test_generator_result_rejects_valid_unique_candidates_beyond_request():
    result = ToolResult.success_result(
        "llm_molecular_generator",
        data=[{"smiles": "CCO"}, {"smiles": "CCN"}, {"smiles": "CCC"}],
        formatted="unsafe excess candidate CCC",
        quality={"requested_count": 2},
    )

    validated = AgentResultValidator().validate_tool_result(result)

    assert validated.success is True
    assert validated.status == ObservationStatus.SUCCEEDED
    assert [item["smiles"] for item in validated.data["candidates"]] == [
        "CCO",
        "CCN",
    ]
    assert validated.data["rejected"] == [
        {"source_index": 3, "smiles": "CCC", "reason": "excess_candidate"}
    ]
    assert validated.quality["invalid_count"] == 0
    assert validated.quality["duplicate_count"] == 0
    assert validated.quality["raw_count"] == 3
    assert validated.quality["actual_count"] == 2
    assert validated.quality["partial_generation"] is False
    assert "CCC" not in validated.formatted


def test_empty_generated_output_defaults_to_failed_zero_request_candidate_set():
    sanitized = sanitize_generated_candidates([])

    assert sanitized.candidate_set.requested_count == 0
    assert sanitized.candidate_set.status == ObservationStatus.FAILED
    assert sanitized.candidate_set.to_dict()["candidates"] == []
    assert sanitized.candidates == []
    assert sanitized.valid_count == 0


@pytest.mark.parametrize("item", [{}, {"smiles": ""}, {"smiles": None}])
def test_sanitizer_preserves_empty_invalid_smiles_rejection(item):
    candidate_set = sanitize_generated_candidates([item], 1).candidate_set

    assert candidate_set.status == ObservationStatus.FAILED
    assert candidate_set.invalid_count == 1
    assert candidate_set.to_dict()["rejected"] == [
        {"source_index": 1, "smiles": "", "reason": "invalid_smiles"}
    ]


def test_empty_invalid_candidate_does_not_discard_following_valid_candidate():
    candidate_set = sanitize_generated_candidates(
        [{"smiles": ""}, {"smiles": "CCO"}],
        1,
    ).candidate_set

    assert candidate_set.status == ObservationStatus.SUCCEEDED
    assert candidate_set.invalid_count == 1
    assert [item.source_index for item in candidate_set.candidates] == [2]
    assert [item.canonical_smiles for item in candidate_set.candidates] == ["CCO"]


def test_invalid_candidate_metadata_is_rejected_without_crashing():
    sanitized = sanitize_generated_candidates(
        [{"smiles": "CCO", "tags": {"not", "json"}}],
        requested_count=1,
    )

    candidate_set = sanitized.candidate_set
    assert candidate_set.status == ObservationStatus.FAILED
    assert candidate_set.valid_count == 0
    assert candidate_set.invalid_count == 1
    assert candidate_set.to_dict()["rejected"] == [
        {
            "source_index": 1,
            "smiles": "CCO",
            "reason": "invalid_candidate_metadata",
        }
    ]


def test_invalid_metadata_does_not_reserve_canonical_smiles():
    sanitized = sanitize_generated_candidates(
        [
            {"smiles": "CCO", "tags": {"not", "json"}},
            {"smiles": "OCC", "source": "llm"},
        ],
        requested_count=1,
    )

    candidate_set = sanitized.candidate_set
    candidate = candidate_set.to_dict()["candidates"][0]
    assert candidate_set.status == ObservationStatus.SUCCEEDED
    assert candidate_set.valid_count == 1
    assert candidate_set.invalid_count == 1
    assert candidate_set.duplicate_count == 0
    assert candidate["source_index"] == 2
    assert candidate["candidate_id"].startswith("cand-001-")


def test_requested_count_accepts_positional_argument():
    sanitized = sanitize_generated_candidates([], 0)

    assert sanitized.candidate_set.requested_count == 0
    assert sanitized.candidate_set.status == ObservationStatus.FAILED


def test_rdkit_parse_exception_is_rejected_as_invalid_smiles(monkeypatch):
    import rdkit

    class RaisingChem:
        @staticmethod
        def MolFromSmiles(_smiles):
            raise ValueError("parse failure")

    monkeypatch.setattr(rdkit, "Chem", RaisingChem, raising=False)

    sanitized = sanitize_generated_candidates([{"smiles": "CCO"}])

    assert sanitized.candidate_set.invalid_count == 1
    assert sanitized.candidate_set.to_dict()["rejected"][0]["reason"] == (
        "invalid_smiles"
    )


def test_rdkit_canonicalization_exception_is_rejected_as_invalid_smiles(monkeypatch):
    import rdkit

    class RaisingChem:
        @staticmethod
        def MolFromSmiles(_smiles):
            return object()

        @staticmethod
        def MolToSmiles(_mol):
            raise ValueError("canonicalization failure")

    monkeypatch.setattr(rdkit, "Chem", RaisingChem, raising=False)

    sanitized = sanitize_generated_candidates([{"smiles": "CCO"}])

    assert sanitized.candidate_set.invalid_count == 1
    assert sanitized.candidate_set.to_dict()["rejected"][0]["reason"] == (
        "invalid_smiles"
    )


@pytest.mark.parametrize("requested_count", [True, 1.5, "2"])
def test_explicit_invalid_requested_count_type_is_rejected(requested_count):
    with pytest.raises(TypeError, match="requested_count"):
        sanitize_generated_candidates([], requested_count=requested_count)


def test_negative_requested_count_is_rejected():
    with pytest.raises(ValueError, match="requested_count"):
        sanitize_generated_candidates([], requested_count=-1)


def test_generator_rdkit_unavailable_returns_structured_unavailable_set(monkeypatch):
    from src.agent.validators import result_validator as validator_module

    def unavailable(*_args, **_kwargs):
        raise CandidateValidationUnavailable("RDKit unavailable")

    monkeypatch.setattr(validator_module, "sanitize_generated_candidates", unavailable)
    result = ToolResult.success_result(
        "llm_molecular_generator",
        data=[{"smiles": "unvalidated"}],
        formatted="unvalidated",
        quality={"requested_count": 2},
    )

    validated = AgentResultValidator().validate_tool_result(result)

    assert validated.success is False
    assert validated.status == ObservationStatus.UNAVAILABLE
    assert validated.data["status"] == "unavailable"
    assert validated.data["requested_count"] == 2
    assert validated.data["candidates"] == []
    assert validated.formatted == ""
    assert validated.error.code.value == "tool_unavailable"
    assert validated.quality["output_contract"] == "CandidateSet@1"


def test_generator_candidate_set_validation_is_idempotent_and_safe():
    result = ToolResult.success_result(
        "llm_molecular_generator",
        data=[
            {"smiles": "CCO"},
            {"smiles": "OCC"},
            {"smiles": "not a smiles"},
            {"smiles": "CCN"},
        ],
        formatted="raw: CCO OCC not a smiles CCN",
        warnings=[
            "Only generated 2 valid unique SMILES out of requested 3.",
            "model warmed",
        ],
        quality={
            "requested_count": 3,
            "actual_count": 99,
            "model": "gmm-llama:latest",
        },
        provenance=ToolProvenance(
            tool_name="llm_molecular_generator",
            model_name="gmm-llama:latest",
            input_digest="input-digest",
        ),
    )
    validator = AgentResultValidator()

    first = validator.validate_tool_result(result)
    first_data = first.data
    first_quality = dict(first.quality)
    first_warnings = list(first.warnings)
    first_formatted = first.formatted
    second = validator.validate_tool_result(first)

    assert second.data == first_data
    assert second.quality == first_quality
    assert second.warnings == first_warnings == [
        "model warmed",
        "Generated 2 valid unique SMILES out of 3 requested",
    ]
    assert second.formatted == first_formatted
    assert "not a smiles" not in second.formatted
    assert "OCC" not in second.formatted
    assert [item["source_index"] for item in second.data["candidates"]] == [1, 4]
    assert second.quality | {} == {
        "requested_count": 3,
        "actual_count": 2,
        "model": "gmm-llama:latest",
        "raw_count": 4,
        "valid_count": 2,
        "unique_count": 2,
        "invalid_count": 1,
        "duplicate_count": 1,
        "partial_generation": True,
        "output_contract": "CandidateSet@1",
        "validated": True,
        "validation_available": True,
        "validation_method": "RDKit",
    }


@pytest.mark.parametrize("requested_count", ["2", True, 1.5, -1])
def test_generator_invalid_requested_count_returns_failed_candidate_set(
    requested_count,
):
    result = ToolResult.success_result(
        "llm_molecular_generator",
        data=[{"smiles": "CCO"}],
        formatted="unvalidated CCO",
        quality={"requested_count": requested_count, "actual_count": 7},
    )

    validated = AgentResultValidator().validate_tool_result(result)

    assert validated.success is False
    assert validated.status == ObservationStatus.FAILED
    assert validated.error.code.value == "invalid_output"
    assert validated.data["status"] == "failed"
    assert validated.data["requested_count"] == 1
    assert validated.data["candidates"] == []
    assert validated.formatted == ""
    assert validated.quality == {
        "requested_count": 1,
        "actual_count": 0,
        "raw_count": 1,
        "valid_count": 0,
        "unique_count": 0,
        "invalid_count": 0,
        "duplicate_count": 0,
        "partial_generation": True,
        "output_contract": "CandidateSet@1",
        "validated": False,
        "validation_available": False,
        "validation_method": None,
    }


def test_invalid_requested_count_precedes_rdkit_unavailable(monkeypatch):
    monkeypatch.setitem(sys.modules, "rdkit", None)
    result = ToolResult.success_result(
        "llm_molecular_generator",
        data=[{"smiles": "CCO"}],
        quality={"requested_count": "1"},
    )

    validated = AgentResultValidator().validate_tool_result(result)

    assert validated.error.code.value == "invalid_output"
    assert validated.status == ObservationStatus.FAILED
    assert validated.quality["validation_available"] is False
    assert validated.quality["validation_method"] is None


def test_generated_envelope_uses_first_non_empty_candidate_alias():
    sanitized = sanitize_generated_candidates(
        {
            "molecules": [],
            "candidates": [{"smiles": "CCO"}],
            "data": [{"smiles": "CCN"}],
        }
    )

    assert sanitized.candidate_set.status == ObservationStatus.SUCCEEDED
    assert [item["smiles"] for item in sanitized.candidates] == ["CCO"]


def test_generator_unavailable_quality_is_fully_normalized(monkeypatch):
    from src.agent.validators import result_validator as validator_module

    def unavailable(*_args, **_kwargs):
        raise CandidateValidationUnavailable("RDKit unavailable")

    monkeypatch.setattr(validator_module, "sanitize_generated_candidates", unavailable)
    result = ToolResult.success_result(
        "llm_molecular_generator",
        data=[{"smiles": "unvalidated"}],
        quality={"requested_count": 2, "actual_count": 99},
    )

    validated = AgentResultValidator().validate_tool_result(result)

    assert validated.quality == {
        "requested_count": 2,
        "actual_count": 0,
        "raw_count": 1,
        "valid_count": 0,
        "unique_count": 0,
        "invalid_count": 0,
        "duplicate_count": 0,
        "partial_generation": True,
        "output_contract": "CandidateSet@1",
        "validated": False,
        "validation_available": False,
        "validation_method": None,
    }


@pytest.mark.parametrize(
    ("status", "error_code"),
    [
        (ObservationStatus.FAILED, "invalid_output"),
        (ObservationStatus.UNAVAILABLE, "tool_unavailable"),
        (ObservationStatus.INVALID_INPUT, "invalid_input"),
        (ObservationStatus.REJECTED, "validation_error"),
        (ObservationStatus.CANCELLED, "cancelled"),
    ],
)
def test_non_success_candidate_set_status_is_preserved_and_never_completes(
    status,
    error_code,
):
    candidate_set = CandidateSet(
        requested_count=1,
        candidates=(),
        status=status,
    )
    result = ToolResult.success_result(
        "llm_molecular_generator",
        data=candidate_set.to_dict(),
    )

    validated = AgentResultValidator().validate_tool_result(result)
    agent_result = AgentResult.from_tool_results(
        trace_id="non-success-candidate-set",
        skill_name="molecular_design",
        tool_results=[validated],
    )

    assert validated.success is False
    assert validated.status == status
    assert validated.data["status"] == status.value
    assert validated.error.code.value == error_code
    assert agent_result.success is False
    assert agent_result.outcome != RunOutcome.COMPLETED


@pytest.mark.parametrize(
    "canonical_smiles",
    ["OCC", "CCO\n|" + chr(96) + "unsafe"],
)
def test_serialized_candidate_set_is_rejected_when_rdkit_canonical_disagrees(
    canonical_smiles,
):
    candidate = CandidateRecord(
        candidate_id=build_candidate_id(1, canonical_smiles),
        source_index=1,
        original_smiles=canonical_smiles,
        canonical_smiles=canonical_smiles,
        validation={"valid": True, "method": "RDKit"},
        generation_provenance={},
        metadata={},
    )
    candidate_set = CandidateSet(
        requested_count=1,
        candidates=(candidate,),
    )
    result = ToolResult.success_result(
        "llm_molecular_generator",
        data=candidate_set.to_dict(),
        formatted=f"unsafe {canonical_smiles}",
    )

    validated = AgentResultValidator().validate_tool_result(result)

    assert validated.success is False
    assert validated.status == ObservationStatus.FAILED
    assert validated.error.code.value == "invalid_output"
    assert validated.data["candidates"] == []
    assert validated.formatted == ""
    assert canonical_smiles not in validated.formatted


def test_candidate_markdown_table_escapes_cells_after_scientific_validation(
    monkeypatch,
):
    import rdkit

    canonical_smiles = "CCO|" + chr(96) + "\r\nN"

    class FakeChem:
        @staticmethod
        def MolFromSmiles(_smiles):
            return object()

        @staticmethod
        def MolToSmiles(_mol):
            return canonical_smiles

    monkeypatch.setattr(rdkit, "Chem", FakeChem, raising=False)
    candidate = CandidateRecord(
        candidate_id=build_candidate_id(1, canonical_smiles),
        source_index=1,
        original_smiles=canonical_smiles,
        canonical_smiles=canonical_smiles,
        validation={"valid": True, "method": "RDKit"},
        generation_provenance={},
        metadata={},
    )
    result = ToolResult.success_result(
        "llm_molecular_generator",
        data=CandidateSet(
            requested_count=1,
            candidates=(candidate,),
        ).to_dict(),
    )

    validated = AgentResultValidator().validate_tool_result(result)

    assert validated.success is True
    assert "| Candidate ID | Canonical SMILES |" in validated.formatted
    assert "\\|" in validated.formatted
    assert "\\" + chr(96) in validated.formatted
    assert "\r" not in validated.formatted
    assert canonical_smiles not in validated.formatted


def _candidate_set_with_forged_rejection(reason):
    if reason in {"invalid_smiles", "invalid_candidate_metadata"}:
        return CandidateSet(
            requested_count=1,
            candidates=(),
            invalid_count=1,
            rejected=(
                {"source_index": 1, "smiles": "CCO", "reason": reason},
            ),
            status=ObservationStatus.FAILED,
        )

    candidate = CandidateRecord.from_smiles(
        candidate_index=1,
        source_index=1,
        original_smiles="CCO",
        canonical_smiles="CCO",
    )
    return CandidateSet(
        requested_count=2,
        candidates=(candidate,),
        duplicate_count=1 if reason == "duplicate_smiles" else 0,
        rejected=(
            {"source_index": 2, "smiles": "CCN", "reason": reason},
        ),
        status=ObservationStatus.PARTIAL,
    )


@pytest.mark.parametrize(
    "reason",
    [
        "invalid_smiles",
        "duplicate_smiles",
        "excess_candidate",
        "invalid_candidate_metadata",
    ],
)
def test_untrusted_candidate_set_rejects_forged_rejection_reason(reason):
    result = ToolResult.success_result(
        "llm_molecular_generator",
        data=_candidate_set_with_forged_rejection(reason).to_dict(),
    )

    validated = AgentResultValidator().validate_tool_result(result)

    assert validated.success is False
    assert validated.status == ObservationStatus.FAILED
    assert validated.error.code.value == "invalid_output"
    assert validated.data["rejected"] == []
    assert "Invalid generated candidate output" in validated.message


def test_trusted_checkpoint_may_restore_invalid_candidate_metadata_rejection():
    candidate_set = _candidate_set_with_forged_rejection(
        "invalid_candidate_metadata"
    )
    result = ToolResult.success_result(
        "llm_molecular_generator",
        data=candidate_set.to_dict(),
    )

    validated = AgentResultValidator().validate_tool_result(
        result,
        trusted_checkpoint=True,
    )

    assert validated.status == ObservationStatus.FAILED
    assert validated.data == candidate_set.to_dict()
    assert validated.data["rejected"][0]["reason"] == (
        "invalid_candidate_metadata"
    )
