from src.agent.contracts import ToolResult
from src.agent.validators.result_validator import AgentResultValidator


def test_validator_marks_invalid_smiles_candidate():
    validator = AgentResultValidator()
    result = ToolResult.success_result(
        tool_name="llm_molecular_generator",
        data={"molecules": [{"smiles": "not-a-smiles"}]},
        message="generation completed",
    )

    validated = validator.validate_tool_result(result)

    assert validated.success is True
    assert any("Invalid SMILES" in warning for warning in validated.warnings)


def test_validator_warns_missing_artifact_path():
    validator = AgentResultValidator()
    result = ToolResult.success_result(
        tool_name="molecular_docking",
        data={"pose_file": "outputs/missing_pose.pdbqt"},
        message="docking completed",
    )

    validated = validator.validate_tool_result(result)

    assert any("file does not exist" in warning for warning in validated.warnings)
