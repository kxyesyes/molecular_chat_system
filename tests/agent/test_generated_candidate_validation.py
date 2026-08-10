from src.agent.contracts import ObservationStatus, ToolResult
from src.agent.validators import AgentResultValidator


def test_generator_result_keeps_only_valid_unique_canonical_smiles():
    result = ToolResult.success_result(
        "llm_molecular_generator",
        data=[
            {"smiles": "CCO", "source": "llm"},
            {"smiles": "OCC", "source": "llm"},
            {"smiles": "CC(C)((", "source": "llm"},
            {"smiles": "CCN", "source": "llm"},
        ],
        quality={"requested_count": 4},
    )

    validated = AgentResultValidator().validate_tool_result(result)

    assert [item["smiles"] for item in validated.data] == ["CCO", "CCN"]
    assert validated.status == ObservationStatus.PARTIAL
    assert validated.quality["valid_count"] == 2
    assert validated.quality["unique_count"] == 2
    assert validated.quality["invalid_count"] == 1
    assert validated.quality["duplicate_count"] == 1


def test_generator_result_fails_when_no_valid_candidate_remains():
    result = ToolResult.success_result(
        "llm_molecular_generator",
        data=[{"smiles": "CC(C)(("}, {"smiles": "not a smiles"}],
        quality={"requested_count": 2},
    )

    validated = AgentResultValidator().validate_tool_result(result)

    assert validated.success is False
    assert validated.data is None
    assert validated.error.code.value == "invalid_output"
