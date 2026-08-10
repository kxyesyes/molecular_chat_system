import pytest

from src.agent.contracts import ObservationStatus, ToolProvenance, ToolResult
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


def test_generator_result_rejects_valid_unique_candidates_beyond_request():
    result = ToolResult.success_result(
        "llm_molecular_generator",
        data=[{"smiles": "CCO"}, {"smiles": "CCN"}, {"smiles": "CCC"}],
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


def test_empty_generated_output_defaults_to_failed_zero_request_candidate_set():
    sanitized = sanitize_generated_candidates([])

    assert sanitized.candidate_set.requested_count == 0
    assert sanitized.candidate_set.status == ObservationStatus.FAILED
    assert sanitized.candidate_set.to_dict()["candidates"] == []
    assert sanitized.candidates == []
    assert sanitized.valid_count == 0


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
