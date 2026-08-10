from src.agent.contracts import ToolResult
from src.agent.validators.result_validator import AgentResultValidator


def test_validator_rejects_invalid_generated_smiles_candidate():
    validator = AgentResultValidator()
    result = ToolResult.success_result(
        tool_name="llm_molecular_generator",
        data={"molecules": [{"smiles": "not-a-smiles"}]},
        message="generation completed",
    )

    validated = validator.validate_tool_result(result)

    assert validated.success is False
    assert validated.data is None
    assert validated.error.code.value == "invalid_output"
    assert validated.status.value == "failed"


def test_validator_warns_missing_artifact_path():
    validator = AgentResultValidator()
    result = ToolResult.success_result(
        tool_name="molecular_docking",
        data={"pose_file": "outputs/missing_pose.pdbqt"},
        message="docking completed",
    )

    validated = validator.validate_tool_result(result)

    assert any("file does not exist" in warning for warning in validated.warnings)
