from src.agent.contracts import ObservationStatus, ToolResult
from src.agent.validators.result_validator import AgentResultValidator


def test_validator_rejects_invalid_generated_smiles_candidate():
    validator = AgentResultValidator()
    result = ToolResult.success_result(
        tool_name="llm_molecular_generator",
        data={"molecules": [{"smiles": "not-a-smiles"}]},
        message="generation completed",
        quality={"requested_count": 1},
    )

    validated = validator.validate_tool_result(result)

    assert validated.success is False
    assert isinstance(validated.data, dict)
    assert validated.data["status"] == "failed"
    assert validated.data["requested_count"] == 1
    assert validated.data["valid_count"] == 0
    assert validated.data["candidates"] == []
    assert validated.data["rejected"] == [
        {
            "source_index": 1,
            "smiles": "not-a-smiles",
            "reason": "invalid_smiles",
        }
    ]
    assert validated.error.code.value == "invalid_output"
    assert validated.status == ObservationStatus.FAILED


def test_validator_warns_missing_artifact_path():
    validator = AgentResultValidator()
    result = ToolResult.success_result(
        tool_name="molecular_docking",
        data={"pose_file": "outputs/missing_pose.pdbqt"},
        message="docking completed",
    )

    validated = validator.validate_tool_result(result)

    assert any("file does not exist" in warning for warning in validated.warnings)
